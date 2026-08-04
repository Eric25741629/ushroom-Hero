"""賞金之路 Escort 的純 WS 協定層。

客戶端已公開完整的 Escort battle start/result API：
  19970 ``escort_info_c2s``       讀取 NPC 清單
  19972 ``escort_battle_start_c2s`` 取得 seed/角色/怪物戰鬥資料
  19973 ``escort_battle_result_c2s`` 回報官方模擬結果

本模組只負責封包與輕量解析；戰鬥計算由 ``escort_fight`` 委派給官方
BattleMainServer，避免自行重寫戰鬥規則。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

CMD_MAIN_INFO = 19969
CMD_INFO = 19970
CMD_ROLE_INFO_DETAIL = 19971
CMD_BATTLE_START = 19972
CMD_BATTLE_RESULT = 19973
CMD_ERROR = 0x0201

TYPE_ROLE = 1
TYPE_MONSTER = 2


@dataclass(frozen=True)
class EscortMonster:
    """``p_escort_monster_info`` 的 WS 讀側欄位。"""

    id: int
    config_id: int = 0
    task_id: int = 0
    energy: int = 0
    route_id: int = 0
    power: int = 0
    etime: int = 0


@dataclass(frozen=True)
class EscortInfo:
    success: bool
    robbing_count: int = 0
    monsters: tuple[EscortMonster, ...] = ()
    error: str | None = None
    fields: dict = field(default_factory=dict, compare=False)
    raw: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True)
class EscortBattleStart:
    success: bool
    code: int = 0
    type: int = TYPE_MONSTER
    target_id: int = 0
    seed: int = 0
    body: bytes = field(default=b"", repr=False, compare=False)
    error: str | None = None


@dataclass(frozen=True)
class EscortBattleResult:
    success: bool
    code: int = 0
    type: int = TYPE_MONSTER
    target_id: int = 0
    result: int | None = None
    energy: int | None = None
    fields: dict = field(default_factory=dict, compare=False)
    error: str | None = None


def build_battle_start_body(type_: int, target_id: int) -> bytes:
    """Build ``{type#1, target_id#2}`` for an Escort battle start."""
    return codec.pb_uint(1, int(type_)) + codec.pb_uint(2, int(target_id))


def build_battle_result_body(type_: int, target_id: int, result: int) -> bytes:
    """Build ``{type#1, target_id#2, result#3}`` for an Escort result."""
    return (
        codec.pb_uint(1, int(type_))
        + codec.pb_uint(2, int(target_id))
        + codec.pb_uint(3, int(result))
    )


def _as_int(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, int) else default


def _parse_monster(blob: bytes) -> Optional[EscortMonster]:
    d = codec.walk_dict(blob)
    # role_info 也會出現在 escort_info_s2c；monster 的 field 6/7 是數字，
    # role_info 的 field 6/7 則是名稱字串，因此用型別辨識避免誤收角色。
    if not all(isinstance(d.get(n), int) for n in (1, 2, 3, 4, 5, 6, 7)):
        return None
    monster_id = d.get(1)
    if not isinstance(monster_id, int):
        return None
    return EscortMonster(
        id=int(monster_id),
        config_id=_as_int(d.get(2)),
        task_id=_as_int(d.get(3)),
        energy=_as_int(d.get(4)),
        route_id=_as_int(d.get(5)),
        power=_as_int(d.get(6)),
        etime=_as_int(d.get(7)),
    )


def _collect_monsters(blob: bytes, *, depth: int = 0) -> list[EscortMonster]:
    """從 repeated message 或包了一層 list 的 body 找 monster records。"""
    if depth > 2:
        return []
    found: list[EscortMonster] = []
    direct = _parse_monster(blob)
    if direct is not None:
        return [direct]
    for _field_no, value in codec.walk(blob):
        if isinstance(value, (bytes, bytearray)):
            found.extend(_collect_monsters(bytes(value), depth=depth + 1))
    return found


def parse_info(cmd: int, body: bytes) -> EscortInfo:
    if cmd == CMD_ERROR:
        code = codec.walk_dict(body).get(1)
        return EscortInfo(success=False, error=f"server error {code}", raw=body)
    if cmd != CMD_INFO:
        return EscortInfo(success=False, error=f"unexpected cmd 0x{cmd:04x}", raw=body)
    d = codec.walk_dict(body)
    monsters: list[EscortMonster] = []
    seen: set[int] = set()
    # live descriptor: monster_list 是 field 6（repeated p_escort_monster_info）。
    for field_no, value in codec.walk(body):
        if field_no == 6 and isinstance(value, (bytes, bytearray)):
            for monster in _collect_monsters(bytes(value)):
                if monster.id not in seen:
                    seen.add(monster.id)
                    monsters.append(monster)
    return EscortInfo(
        success=True,
        # escort_info_s2c 沒有 code；live descriptor: robbing_count#1。
        robbing_count=_as_int(d.get(1)),
        monsters=tuple(monsters),
        fields=d,
        raw=body,
    )


def parse_battle_start(cmd: int, body: bytes) -> EscortBattleStart:
    if cmd == CMD_ERROR:
        code = codec.walk_dict(body).get(1)
        return EscortBattleStart(success=False, error=f"server error {code}", body=body)
    if cmd != CMD_BATTLE_START:
        return EscortBattleStart(success=False, error=f"unexpected cmd 0x{cmd:04x}", body=body)
    d = codec.walk_dict(body)
    code = _as_int(d.get(1))
    if code != 0:
        return EscortBattleStart(
            success=False,
            code=code,
            type=_as_int(d.get(2), TYPE_MONSTER),
            target_id=_as_int(d.get(3)),
            seed=_as_int(d.get(4)),
            body=body,
            error=f"battle start code={code}",
        )
    return EscortBattleStart(
        success=True,
        code=code,
        type=_as_int(d.get(2), TYPE_MONSTER),
        target_id=_as_int(d.get(3)),
        seed=_as_int(d.get(4)),
        body=body,
    )


def parse_battle_result(cmd: int, body: bytes) -> EscortBattleResult:
    if cmd == CMD_ERROR:
        code = codec.walk_dict(body).get(1)
        return EscortBattleResult(success=False, error=f"server error {code}")
    if cmd != CMD_BATTLE_RESULT:
        return EscortBattleResult(success=False, error=f"unexpected cmd 0x{cmd:04x}")
    d = codec.walk_dict(body)
    code = _as_int(d.get(1))
    return EscortBattleResult(
        success=code == 0,
        code=code,
        type=_as_int(d.get(2), TYPE_MONSTER),
        target_id=_as_int(d.get(3)),
        result=_as_int(d.get(4)) if isinstance(d.get(4), int) else None,
        energy=_as_int(d.get(5)) if isinstance(d.get(5), int) else None,
        fields=d,
        error=None if code == 0 else f"battle result code={code}",
    )


def fetch_info(client: WSGameClient, *, timeout: float | None = None) -> EscortInfo:
    cmd, body = client.call_for(
        CMD_INFO, b"", expect_cmds=(CMD_INFO, CMD_ERROR), timeout=timeout
    )
    return parse_info(cmd, body)


def start_battle(
    client: WSGameClient,
    target_id: int,
    *,
    type_: int = TYPE_MONSTER,
    timeout: float | None = None,
) -> EscortBattleStart:
    cmd, body = client.call_for(
        CMD_BATTLE_START,
        build_battle_start_body(type_, target_id),
        expect_cmds=(CMD_BATTLE_START, CMD_ERROR),
        timeout=timeout,
    )
    return parse_battle_start(cmd, body)


def report_result(
    client: WSGameClient,
    target_id: int,
    result: int,
    *,
    type_: int = TYPE_MONSTER,
    timeout: float | None = None,
) -> EscortBattleResult:
    cmd, body = client.call_for(
        CMD_BATTLE_RESULT,
        build_battle_result_body(type_, target_id, result),
        expect_cmds=(CMD_BATTLE_RESULT, CMD_ERROR),
        timeout=timeout,
    )
    return parse_battle_result(cmd, body)

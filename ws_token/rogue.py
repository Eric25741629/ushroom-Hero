"""萬神試煉 (roguelike, module 76 "rogue") — 本周積分獎勵一鍵領取 over pure WS.

The live 萬神試煉 is the roguelike RogueView (module 76), NOT the older dungeon
type=23 in ``ws_token.dungeon``. Its weekly 積分 (points) milestone rewards are
all granted by ONE empty-body request:

  rogue_week_reward_c2s  19482 (0x4C1A)  c2s {}            (empty body)
  rogue_week_reward_s2c  19482           s2c { get_list#1:uint32[],
                                               reward_list#2:p_reward[] }
  p_reward                                   { gtid#1:int32, num#2:int64 }

LIVE-captured 2026-06-13 (小寶 7fe98fc6, CDP 9226) and cross-checked against
docs/protocol/ROGUE_PROTO_SCHEMA.json:
  c2s 19482 {}  ->  s2c 19482 {1=[1..10], 2=[(1501,200),(2,1800),(1291,1),(1,10000)]}

The s2c carries NO code field, so any 19482 reply is success; an empty get_list
just means "nothing left to claim this week" (still success).

NOTE (2026-06-26): a DORMANT event (not open / not unlocked / nothing to claim)
does NOT reliably send the 0x0201 error frame this used to assume — live it sends
no frame at all, so ``call_for`` times out. The runner (``runner._run_rogue``)
treats that WSTimeoutError as a benign skip with a short probe timeout, the same
way it handles a dormant guild 尋寶 event.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_WEEK_REWARD = 19482    # 0x4C1A rogue.rogue_week_reward_c2s/_s2c (empty c2s body)
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}

# 萬神試煉 battle 相關（2026-07-28 live-verified 7fe98fc6）
CMD_INFO    = 0x4C01   # rogue_info_c2s/s2c  {point#1, score#2, ...}
CMD_ENTER   = 0x4C02   # rogue_main_enter_c2s {return_type#1=1}
CMD_OVER    = 0x4C03   # rogue_main_over_c2s  {return_type#1=0}
CMD_COMBAT  = 0x4C04   # rogue_main_combat_c2s {} → s2c {code#1,seed#?,atk_data#?,def_data#?}
CMD_RESULT  = 0x4C05   # rogue_main_result_c2s {result#1,precent#2}
CMD_SCIENCE_INFO = 0x4C16  # s2c {science_info#1,science_point#2,point_max#3}
CMD_STATUS  = 0x4C20   # rogue_status_c2s {} → s2c {status#1}（1=有進行中 run）

# 開新局必經的「開局獎勵(重造)」步驟 —— live 實測 2026-07-17(5556 node-emit)：
# UI 對應 RogueRemakeRewardView「進入遊戲」btnEnter → 確認窗「是否確認進入本次萬神
# 試煉」→確定。enter(0x4C02) 只是「開啟新一局」，combat 前還必須送這一組才能真正
# 進場；缺這兩步時 enter 後直接 combat 會被 server 拒（"server error 2"，2026-07-28
# live 於 7fe98fc6 重現三次才定位到本因）。
# 數值未在 ROGUE_PROTO_SCHEMA.json 的 cmd_ids 表列出（該表序列止於 19491），但 schema
# nested 區塊有 rogue_start_reward_info/refresh/confirm 三型定義；19492/19494 取自
# docs/superpowers/plans/2026-07-17-wanshen-h5-node-ws-plan.md §3.1 live 實抓 TX。
CMD_START_REWARD_INFO    = 19492  # 0x4C24 rogue_start_reward_info_c2s {} → s2c {base_reward_list,drop_reward_list,refresh_times,cost_list}
CMD_START_REWARD_REFRESH = 19493  # 0x4C25 rogue_start_reward_refresh_c2s {pos_list}（不用，跳過）
CMD_START_REWARD_CONFIRM = 19494  # 0x4C26 rogue_start_reward_confirm_c2s {} → s2c {reward_list}


@dataclass(frozen=True)
class WeekRewardResult:
    """Outcome of rogue_week_reward (本周積分獎勵一鍵領取) or its failure."""

    success: bool                       # True iff a rogue_week_reward_s2c arrived
    claimed: tuple[int, ...] = ()       # get_list: milestone indices claimed now
    rewards: dict[int, int] = field(default_factory=dict)  # {gtid: num}
    response_cmd: int = 0
    fields: dict = field(compare=False, default_factory=dict)
    error_code: int | None = None
    error: str | None = None


def _collect_rewards(body: bytes, reward_field: int) -> dict[int, int]:
    """Decode every p_reward {gtid#1, num#2} on ``reward_field`` into {gtid: num}."""
    rewards: dict[int, int] = {}
    for fnum, v in codec.walk(body):
        if fnum == reward_field and isinstance(v, (bytes, bytearray)):
            r = codec.walk_dict(bytes(v))
            gtid = r.get(1)
            if isinstance(gtid, int):
                num = r.get(2)
                rewards[gtid] = int(num) if isinstance(num, int) else 0
    return rewards


def parse_week_reward(cmd: int, body: bytes) -> WeekRewardResult:
    """rogue_week_reward_s2c {get_list#1:uint32[], reward_list#2:p_reward[]}.

    No code field — any 19482 reply is success (empty get_list = already claimed).
    A 0x0201 frame is a server error (failure).
    """
    if cmd == CMD_WEEK_REWARD:
        claimed = tuple(v for fnum, v in codec.walk(body)
                        if fnum == 1 and isinstance(v, int))
        return WeekRewardResult(
            success=True, claimed=claimed,
            rewards=_collect_rewards(body, 2),
            response_cmd=cmd, fields=codec.walk_dict(body))
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return WeekRewardResult(success=False, response_cmd=cmd,
                                fields=codec.walk_dict(body), error_code=ec,
                                error=f"server error code={ec}")
    return WeekRewardResult(success=False, response_cmd=cmd,
                            fields=codec.walk_dict(body),
                            error=f"unexpected response cmd 0x{cmd:04x}")


def claim_week_reward(client: WSGameClient, *,
                      timeout: float | None = None) -> WeekRewardResult:
    """一鍵領取本周積分獎勵: send rogue_week_reward (19482) with an empty body.

    Replies on 19482 (rewards, possibly empty) or 0x0201 (event not open).
    """
    cmd, body = client.call_for(
        CMD_WEEK_REWARD, b"",
        expect_cmds=(CMD_WEEK_REWARD, CMD_ERROR), timeout=timeout)
    result = parse_week_reward(cmd, body)
    if result.success:
        logger.info("ws_token rogue: week_reward claimed=%s rewards=%s",
                    result.claimed, result.rewards)
    else:
        logger.warning("ws_token rogue: week_reward failed cmd=0x%04x code=%s",
                       cmd, result.error_code)
    return result


# ─── 萬神試煉 battle 相關 dataclasses（2026-07-28）────────────────────────────

@dataclass(frozen=True)
class RogueInfo:
    """rogue_info_s2c 解析結果。"""
    success: bool
    point: int = 0      # field 1 — 試煉之心剩餘點數
    score: int = 0      # field 2
    error: str | None = None
    fields: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class RogueStatus:
    """rogue_status_s2c 解析結果。"""
    has_active_run: bool   # True iff status field 1 == 1
    raw_status: int = 0
    error: str | None = None


@dataclass(frozen=True)
class RogueScienceCap:
    """神樹祝福「本周獲取上限」進度。"""
    success: bool
    current: int = 0
    cap: int = 0
    error: str | None = None
    fields: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class RogueEnter:
    """rogue_main_enter_s2c 解析結果。"""
    success: bool
    code: int = 0
    error: str | None = None
    fields: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class RogueCombat:
    """rogue_main_combat_s2c 解析結果。body 是原始 bytes，傳給 B page sim。"""
    success: bool
    code: int = 0
    body: bytes = field(default=b"", repr=False, compare=False)
    error: str | None = None


@dataclass(frozen=True)
class RogueResultAck:
    """rogue_main_result_s2c 解析結果（server 確認收到 result）。"""
    success: bool
    code: int = 0
    error: str | None = None
    fields: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class RogueOver:
    """rogue_main_over_s2c 解析結果。"""
    success: bool
    code: int = 0
    error: str | None = None
    fields: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class RogueStartReward:
    """rogue_start_reward_info_s2c / rogue_start_reward_confirm_s2c 共用解析結果。

    兩者依 schema 皆無 code 欄位，任何非 error 回覆即成功。
    """
    success: bool
    error: str | None = None
    fields: dict = field(compare=False, default_factory=dict)


# ─── body builders ───────────────────────────────────────────────────────────

def build_enter_c2s(return_type: int = 1) -> bytes:
    """rogue_main_enter_c2s {return_type#1}。return_type=1 開新局。"""
    return codec.pb_uint(1, int(return_type))


def build_over_c2s(return_type: int = 0) -> bytes:
    """rogue_main_over_c2s {return_type#1}。"""
    return codec.pb_uint(1, int(return_type))


def build_result_c2s(result: int, precent: int) -> bytes:
    """rogue_main_result_c2s {result#1, precent#2}。
    result=0 → 攻方勝；result=1 → 失敗（同 local_sim 語意）。
    """
    return codec.pb_uint(1, int(result)) + codec.pb_uint(2, int(precent))


# ─── parsers ─────────────────────────────────────────────────────────────────

def parse_info(body: bytes) -> RogueInfo:
    """rogue_info_s2c 依 ROGUE_PROTO_SCHEMA.json：id#1, point#2, end_time#3,
    score#4, get_list#5, rank#6, attr_list#7, ext_list#8。
    舊版誤把 field1/2 當 point/score（實際是 id/point），2026-07-27 修正。
    """
    d = codec.walk_dict(body)
    return RogueInfo(
        success=True,
        point=int(d.get(2) or 0),
        score=int(d.get(4) or 0),
        fields=d,
    )


def parse_status(body: bytes) -> RogueStatus:
    d = codec.walk_dict(body)
    raw = int(d.get(1) or 0)
    return RogueStatus(has_active_run=(raw == 1), raw_status=raw)


def parse_science_cap(cmd: int, body: bytes) -> RogueScienceCap:
    """解析 0x4C16；live 對照 UI 確認 science_point/point_max = current/cap。"""
    fields = codec.walk_dict(body)
    if cmd == CMD_ERROR:
        ec = fields.get(1)
        return RogueScienceCap(success=False, error=f"server error {ec}", fields=fields)
    if cmd != CMD_SCIENCE_INFO:
        return RogueScienceCap(
            success=False, error=f"unexpected cmd 0x{cmd:04x}", fields=fields
        )
    current = fields.get(2)
    cap = fields.get(3)
    if not isinstance(current, int) or not isinstance(cap, int) or cap <= 0:
        return RogueScienceCap(
            success=False, error="science cap fields missing", fields=fields
        )
    return RogueScienceCap(success=True, current=int(current), cap=int(cap), fields=fields)


def parse_enter(cmd: int, body: bytes) -> RogueEnter:
    """rogue_main_enter_s2c 依 ROGUE_PROTO_SCHEMA.json 沒有 code 欄位：
    field 1 = other_info(p_other_role_info 訊息)、2/3 = skill_list/my_list、
    4 = level、5/6 = ext_list/reward_list。任何 CMD_ENTER 回覆即成功
    （同 rogue_week_reward_s2c 模式：無 code，有回覆=成功）。
    """
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return RogueEnter(success=False, error=f"server error {ec}")
    if cmd != CMD_ENTER:
        return RogueEnter(success=False, error=f"unexpected cmd 0x{cmd:04x}")
    d = codec.walk_dict(body)
    return RogueEnter(success=True, fields=d)


def parse_combat(cmd: int, body: bytes) -> RogueCombat:
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return RogueCombat(success=False, error=f"server error {ec}", body=body)
    if cmd != CMD_COMBAT:
        return RogueCombat(success=False, error=f"unexpected cmd 0x{cmd:04x}", body=body)
    d = codec.walk_dict(body)
    code = int(d.get(1) or 0)
    if code not in (0,):
        return RogueCombat(success=False, code=code, error=f"combat code={code}", body=body)
    return RogueCombat(success=True, code=code, body=body)


def parse_result_ack(cmd: int, body: bytes) -> RogueResultAck:
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return RogueResultAck(success=False, error=f"server error {ec}")
    if cmd != CMD_RESULT:
        return RogueResultAck(success=False, error=f"unexpected cmd 0x{cmd:04x}")
    d = codec.walk_dict(body)
    code = int(d.get(1) or 0)
    if code not in (0,):
        return RogueResultAck(success=False, code=code, error=f"result code={code}", fields=d)
    return RogueResultAck(success=True, code=code, fields=d)


def parse_over(cmd: int, body: bytes) -> RogueOver:
    """rogue_main_over_s2c 依 schema 沒有 code 欄位：field 1 = rogue_report(訊息)、
    2 = reward_list。任何 CMD_OVER 回覆即成功（同 parse_enter）。
    """
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return RogueOver(success=False, error=f"server error {ec}")
    if cmd != CMD_OVER:
        return RogueOver(success=False, error=f"unexpected cmd 0x{cmd:04x}")
    d = codec.walk_dict(body)
    return RogueOver(success=True, fields=d)


def parse_start_reward(cmd: int, body: bytes, *, expect_cmd: int) -> RogueStartReward:
    """rogue_start_reward_info_s2c / rogue_start_reward_confirm_s2c 共用解析。

    兩者依 schema 皆無 code 欄位，任何非 error 回覆即成功。
    """
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return RogueStartReward(success=False, error=f"server error {ec}")
    if cmd != expect_cmd:
        return RogueStartReward(success=False, error=f"unexpected cmd 0x{cmd:04x}")
    return RogueStartReward(success=True, fields=codec.walk_dict(body))


# ─── send helpers ─────────────────────────────────────────────────────────────

def fetch_info(client: WSGameClient, *, timeout: float | None = None) -> RogueInfo:
    body = client.call(CMD_INFO, b"", timeout=timeout)
    return parse_info(body)


def fetch_status(client: WSGameClient, *, timeout: float | None = None) -> RogueStatus:
    body = client.call(CMD_STATUS, b"", timeout=timeout)
    return parse_status(body)


def fetch_science_cap(
    client: WSGameClient, *, timeout: float | None = None
) -> RogueScienceCap:
    """讀取神樹祝福「本周獲取上限 current/cap」。"""
    cmd, body = client.call_for(
        CMD_SCIENCE_INFO,
        b"",
        expect_cmds=(CMD_SCIENCE_INFO, CMD_ERROR),
        timeout=timeout,
    )
    return parse_science_cap(cmd, body)


def enter_run(
    client: WSGameClient,
    return_type: int = 1,
    *,
    timeout: float | None = None,
) -> RogueEnter:
    cmd, body = client.call_for(
        CMD_ENTER,
        build_enter_c2s(return_type),
        expect_cmds=(CMD_ENTER, CMD_ERROR),
        timeout=timeout,
    )
    return parse_enter(cmd, body)


def fetch_start_reward_info(
    client: WSGameClient,
    *,
    timeout: float | None = None,
) -> RogueStartReward:
    """rogue_start_reward_info_c2s（開局獎勵資訊，UI: RogueRemakeRewardView）。"""
    cmd, body = client.call_for(
        CMD_START_REWARD_INFO,
        b"",
        expect_cmds=(CMD_START_REWARD_INFO, CMD_ERROR),
        timeout=timeout,
    )
    return parse_start_reward(cmd, body, expect_cmd=CMD_START_REWARD_INFO)


def confirm_start_reward(
    client: WSGameClient,
    *,
    timeout: float | None = None,
) -> RogueStartReward:
    """rogue_start_reward_confirm_c2s（確認進入本次萬神試煉，enter 後、combat 前必經）。"""
    cmd, body = client.call_for(
        CMD_START_REWARD_CONFIRM,
        b"",
        expect_cmds=(CMD_START_REWARD_CONFIRM, CMD_ERROR),
        timeout=timeout,
    )
    return parse_start_reward(cmd, body, expect_cmd=CMD_START_REWARD_CONFIRM)


def start_combat(
    client: WSGameClient,
    *,
    timeout: float | None = None,
) -> RogueCombat:
    cmd, body = client.call_for(
        CMD_COMBAT,
        b"",
        expect_cmds=(CMD_COMBAT, CMD_ERROR),
        timeout=timeout,
    )
    return parse_combat(cmd, body)


def report_result(
    client: WSGameClient,
    result: int,
    precent: int,
    *,
    timeout: float | None = None,
) -> RogueResultAck:
    cmd, body = client.call_for(
        CMD_RESULT,
        build_result_c2s(result, precent),
        expect_cmds=(CMD_RESULT, CMD_ERROR),
        timeout=timeout,
    )
    return parse_result_ack(cmd, body)


def end_run(
    client: WSGameClient,
    return_type: int = 0,
    *,
    timeout: float | None = None,
) -> RogueOver:
    cmd, body = client.call_for(
        CMD_OVER,
        build_over_c2s(return_type),
        expect_cmds=(CMD_OVER, CMD_ERROR),
        timeout=timeout,
    )
    return parse_over(cmd, body)

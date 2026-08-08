"""純 WS 穿越深淵之門（WorldBoss / 地獄之門）。

這個玩法不是 ``ws_token.dungeon.TYPE_ABYSS``：它是 ``ChapterType.WorldBoss``
(13)，入口用 3597，戰鬥結果用 3592，主頁 DONE 收尾用 6593。WS 帳號負責
進場與結算；官方 BattleMainServer 只在獨立的 B 計算頁計算傷害，不使用 ADB 或 UI。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from battle_calc.worldboss import (
    close_raw_cdp_runtime,
    open_raw_cdp_runtime,
    simulate_start_body,
)
from ws_token import codec
from ws_token.arena_fight import close_b_runtime, open_b_runtime
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

TYPE_WORLD_BOSS = 13
LEVEL_WORLD_BOSS = 1
CMD_RESULT = 3592             # dungeon.dungeon_battle_result_c2s/s2c (H5)
CMD_GENERIC_RESULT = 3587     # dungeon.dungeon_result_c2s/s2c；僅保留相容解析
CMD_WORLD_BOSS_INFO = 3594    # dungeon.dungeon_world_boss_info_c2s/s2c
CMD_BATTLE_MORE_START = 3597  # dungeon.dungeon_battle_more_start_c2s/s2c
CMD_FINISH_WORLD_BOSS = 6593  # act_cross_limited_rank：主頁 DONE 收尾，空 body
CMD_ERROR = 0x0201

_RESULT_ACK_TIMEOUT_SEC = 3.0
_RESULT_CONFIRM_POLL_SEC = 0.5
_SESSION_SETTLE_SEC = 8.0
_SETTLEMENT_DELAY_SEC = 5 * 60.0


@dataclass(frozen=True)
class WorldBossInfo:
    success: bool
    is_open: int = 0
    start_time: int = 0
    end_time: int = 0
    role_name: str = ""
    today_buff: int = 0
    total_hurt: str = "0"
    my_rank: int = 0
    my_hurt: str = "0"
    times: int = 0
    is_first: int = 0
    pending_result: int = 0
    error_code: int | None = None
    error: str | None = None
    fields: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class WorldBossStart:
    success: bool
    type: int = TYPE_WORLD_BOSS
    dungeon_id: int = 0
    battle_checkout: int = 0
    random_seed: int = 0
    roles: int = 0
    has_deal_role: bool = False
    error_code: int | None = None
    error: str | None = None
    body: bytes = field(default=b"", repr=False, compare=False)
    fields: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class WorldBossResult:
    success: bool
    type: int = TYPE_WORLD_BOSS
    dungeon_id: int = 0
    result: int = 0
    rewards: dict[int, int] = field(default_factory=dict)
    ext: tuple[tuple[int, int], ...] = ()
    sext: tuple[tuple[int, str], ...] = ()
    error_code: int | None = None
    error: str | None = None
    fields: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class WorldBossRun:
    success: bool
    info: WorldBossInfo | None = None
    start: WorldBossStart | None = None
    result: WorldBossResult | None = None
    after_info: WorldBossInfo | None = None
    after_info_error: str | None = None
    simulation: dict[str, Any] = field(default_factory=dict)
    skipped: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "skipped": self.skipped,
            "error": self.error,
            "is_open": self.info.is_open if self.info else None,
            "times": self.info.times if self.info else None,
            "info_error_code": self.info.error_code if self.info else None,
            "dungeon_id": self.start.dungeon_id if self.start else None,
            "start_error_code": self.start.error_code if self.start else None,
            "hp_num": self.simulation.get("hp_num"),
            "last_hurt_num": self.simulation.get("last_hurt_num"),
            "hurt_num": self.simulation.get("hurt_num"),
            "frames": self.simulation.get("frames"),
            "sim_ms": self.simulation.get("ms"),
            "rewards": self.result.rewards if self.result else {},
            "result_code": self.result.error_code if self.result else None,
            "after_info_success": self.after_info.success if self.after_info else None,
            "after_is_open": self.after_info.is_open if self.after_info else None,
            "after_times": self.after_info.times if self.after_info else None,
            "after_my_hurt": self.after_info.my_hurt if self.after_info else None,
            "after_my_rank": self.after_info.my_rank if self.after_info else None,
            "after_pending_result": (
                self.after_info.pending_result if self.after_info else None
            ),
            "after_info_error": self.after_info_error,
        }


def _as_int(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, int) else default


def _as_string(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return default


def _parse_key_values(body: bytes, field_no: int) -> tuple[tuple[int, int], ...]:
    values: list[tuple[int, int]] = []
    for number, raw in codec.walk(body):
        if number != field_no or not isinstance(raw, (bytes, bytearray)):
            continue
        item = codec.walk_dict(bytes(raw))
        values.append((_as_int(item.get(1)), _as_int(item.get(2))))
    return tuple(values)


def _parse_key_strings(body: bytes, field_no: int) -> tuple[tuple[int, str], ...]:
    values: list[tuple[int, str]] = []
    for number, raw in codec.walk(body):
        if number != field_no or not isinstance(raw, (bytes, bytearray)):
            continue
        item = codec.walk_dict(bytes(raw))
        values.append((_as_int(item.get(1)), _as_string(item.get(2))))
    return tuple(values)


def build_start_body(
    type_: int = TYPE_WORLD_BOSS,
    level: int = LEVEL_WORLD_BOSS,
) -> bytes:
    """dungeon_battle_more_start_c2s ``{type#1, level#2}``."""

    return codec.pb_uint(1, int(type_)) + codec.pb_uint(2, int(level))


def build_result_body(
    type_: int,
    dungeon_id: int,
    *,
    result: int = 0,
    args: Iterable[Sequence[int]] = (),
) -> bytes:
    """dungeon_battle_result_c2s ``{type, dungeon_id, result, args#6}``.

    目前 H5 的 DungeonControl.reqDungeonBattleResult 使用 3592；args 是
    field 6，field 4/5 保留給 manual_operators/operators，WorldBoss 純 WS
    不填這兩個欄位。
    """

    body = (codec.pb_uint(1, int(type_))
            + codec.pb_uint(2, int(dungeon_id))
            + codec.pb_uint(3, int(result))
            # 官方 ChapterWorldBossCC 明確傳入 manual_operators=0；不可省略。
            + codec.pb_uint(4, 0))
    for key, value in args:
        body += codec.pb_msg(
            6,
            codec.pb_uint(1, int(key)) + codec.pb_uint(2, int(value)),
        )
    return body


def parse_info(cmd: int, body: bytes) -> WorldBossInfo:
    fields = codec.walk_dict(body)
    if cmd == CMD_ERROR:
        code = fields.get(1)
        return WorldBossInfo(
            success=False,
            error_code=_as_int(code) if isinstance(code, int) else None,
            error=f"server error code={code}",
            fields=fields,
        )
    if cmd != CMD_WORLD_BOSS_INFO:
        return WorldBossInfo(
            success=False,
            error=f"unexpected response cmd 0x{cmd:04x}",
            fields=fields,
        )
    return WorldBossInfo(
        success=True,
        is_open=_as_int(fields.get(1)),
        start_time=_as_int(fields.get(2)),
        end_time=_as_int(fields.get(3)),
        role_name=_as_string(fields.get(4)),
        today_buff=_as_int(fields.get(6)),
        total_hurt=_as_string(fields.get(7), "0"),
        my_rank=_as_int(fields.get(8)),
        my_hurt=_as_string(fields.get(9), "0"),
        times=_as_int(fields.get(10)),
        is_first=_as_int(fields.get(11)),
        pending_result=_as_int(fields.get(12)),
        fields=fields,
    )


def parse_start(cmd: int, body: bytes) -> WorldBossStart:
    fields = codec.walk_dict(body)
    if cmd == CMD_ERROR:
        code = fields.get(1)
        return WorldBossStart(
            success=False,
            error_code=_as_int(code) if isinstance(code, int) else None,
            error=f"server error code={code}",
            body=body,
            fields=fields,
        )
    if cmd != CMD_BATTLE_MORE_START:
        return WorldBossStart(
            success=False,
            error=f"unexpected response cmd 0x{cmd:04x}",
            body=body,
            fields=fields,
        )
    code = _as_int(fields.get(1))
    roles = sum(1 for number, raw in codec.walk(body)
                if number == 6 and isinstance(raw, (bytes, bytearray)))
    deal_role = fields.get(7)
    return WorldBossStart(
        success=code == 0,
        type=_as_int(fields.get(2), TYPE_WORLD_BOSS),
        dungeon_id=_as_int(fields.get(3)),
        battle_checkout=_as_int(fields.get(4)),
        random_seed=_as_int(fields.get(5)),
        roles=roles,
        has_deal_role=isinstance(deal_role, (bytes, bytearray)),
        error_code=code or None,
        error=None if code == 0 else f"battle_more_start code={code}",
        body=body,
        fields=fields,
    )


def parse_result(cmd: int, body: bytes) -> WorldBossResult:
    fields = codec.walk_dict(body)
    if cmd == CMD_ERROR:
        code = fields.get(1)
        return WorldBossResult(
            success=False,
            error_code=_as_int(code) if isinstance(code, int) else None,
            error=f"server error code={code}",
            fields=fields,
        )
    if cmd not in (CMD_RESULT, CMD_GENERIC_RESULT):
        return WorldBossResult(
            success=False,
            error=f"unexpected response cmd 0x{cmd:04x}",
            fields=fields,
        )
    code = _as_int(fields.get(1))
    rewards: dict[int, int] = {}
    reward_field = 5 if cmd == CMD_RESULT else 6
    for number, raw in codec.walk(body):
        if number != reward_field or not isinstance(raw, (bytes, bytearray)):
            continue
        reward = codec.walk_dict(bytes(raw))
        rewards[_as_int(reward.get(1))] = _as_int(reward.get(2))
    return WorldBossResult(
        success=code == 0,
        type=_as_int(
            fields.get(2 if cmd == CMD_RESULT else 3),
            TYPE_WORLD_BOSS,
        ),
        dungeon_id=_as_int(fields.get(3 if cmd == CMD_RESULT else 2)),
        result=_as_int(fields.get(4)),
        rewards=rewards,
        ext=_parse_key_values(body, 6) if cmd == CMD_RESULT else (),
        sext=_parse_key_strings(body, 7),
        error_code=code or None,
        error=None if code == 0 else f"dungeon_battle_result code={code}",
        fields=fields,
    )


def fetch_info(client: WSGameClient, *, timeout: float | None = None) -> WorldBossInfo:
    cmd, body = client.call_for(
        CMD_WORLD_BOSS_INFO,
        b"",
        expect_cmds=(CMD_WORLD_BOSS_INFO, CMD_ERROR),
        timeout=timeout,
    )
    return parse_info(cmd, body)


def start(
    client: WSGameClient,
    *,
    type_: int = TYPE_WORLD_BOSS,
    level: int = LEVEL_WORLD_BOSS,
    timeout: float | None = None,
) -> WorldBossStart:
    cmd, body = client.call_for(
        CMD_BATTLE_MORE_START,
        build_start_body(type_, level),
        expect_cmds=(CMD_BATTLE_MORE_START, CMD_ERROR),
        timeout=timeout,
    )
    return parse_start(cmd, body)


def submit_result(
    client: WSGameClient,
    *,
    type_: int,
    dungeon_id: int,
    result: int,
    hp_num: int | None = None,
    last_hurt_num: int | None = None,
    timeout: float | None = None,
) -> WorldBossResult:
    if (hp_num is None) != (last_hurt_num is None):
        raise ValueError("hp_num and last_hurt_num must be provided together")
    if int(result) != 0 and hp_num is not None:
        raise ValueError("abandon result must not include damage fields")
    args: tuple[tuple[int, int], ...] = ()
    if hp_num is not None:
        args = ((1, int(hp_num)), (4, int(last_hurt_num)))
    body = build_result_body(
        type_,
        dungeon_id,
        result=result,
        args=args,
    )
    # H5 uses netManager.send() and does not wait for this mutation.  The
    # server usually emits 3592/0x0201, but a missing ack must not cause a
    # second settlement packet: the authoritative confirmation is 3594.
    ack_timeout = _RESULT_ACK_TIMEOUT_SEC
    if timeout is not None:
        try:
            ack_timeout = min(ack_timeout, max(0.1, float(timeout)))
        except (TypeError, ValueError):
            pass
    try:
        cmd, response = client.call_for(
            CMD_RESULT,
            body,
            expect_cmds=(CMD_RESULT, CMD_ERROR),
            timeout=ack_timeout,
        )
    except WSTimeoutError:
        logger.info(
            "WorldBoss 3592 已送出但未收到 ack，改由 3594 確認 result=%s dungeon_id=%s",
            result,
            dungeon_id,
        )
        return WorldBossResult(
            success=False,
            type=int(type_),
            dungeon_id=int(dungeon_id),
            result=int(result),
            error="settlement ack timeout; awaiting 3594 confirmation",
            fields={"sent": True},
        )
    return parse_result(cmd, response)


def finish_worldboss(
    client: WSGameClient,
    *,
    timeout: float | None = None,
) -> WorldBossResult:
    """觸發主頁 ``DONE`` 的官方收尾：6593 空 body -> 3587 結算獎勵。"""

    cmd, body = client.call_for(
        CMD_FINISH_WORLD_BOSS,
        b"",
        expect_cmds=(CMD_GENERIC_RESULT, CMD_ERROR),
        timeout=timeout,
    )
    return parse_result(cmd, body)


def _counter_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _result_state_changed(
    before: WorldBossInfo,
    after: WorldBossInfo | None,
    *,
    result: int,
) -> bool:
    """確認伺服器已套用一次結算，不依賴本地估算傷害。"""

    if after is None or not after.success:
        return False
    if after.times < before.times:
        return True
    return (
        int(result) == 0
        and _counter_int(after.my_hurt) > _counter_int(before.my_hurt)
    )


def _fetch_after_result_info(
    client: WSGameClient,
    *,
    timeout: float | None,
    before: WorldBossInfo | None = None,
    result: int | None = None,
    wait_for_change: bool = True,
) -> tuple[WorldBossInfo | None, str | None]:
    """結算後回查狀態；只有伺服器狀態變更才可確認成功。"""

    try:
        wait_sec = max(0.5, min(15.0, float(timeout or 15.0)))
    except (TypeError, ValueError):
        wait_sec = 15.0
    deadline = time.monotonic() + wait_sec
    last: WorldBossInfo | None = None
    try:
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            current = fetch_info(
                client,
                timeout=min(remaining, timeout or 15.0),
            )
            last = current
            if (
                before is None
                or result is None
                or not wait_for_change
                or _result_state_changed(before, current, result=result)
            ):
                return current, None
            if time.monotonic() >= deadline:
                return last, (
                    f"settlement state not updated (times={current.times}, "
                    f"my_hurt={current.my_hurt})"
                )
            time.sleep(_RESULT_CONFIRM_POLL_SEC)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("WorldBoss 結算後 3594 回查失敗: %s", error)
        return last, error


def _run_after_start(
    client: WSGameClient,
    started: WorldBossStart,
    page: Any,
    *,
    max_frames: int,
    speed_scale: float,
    realtime: bool,
    simulation_timeout_sec: float,
    settlement_delay_sec: float,
    battle_started_at: float,
    timeout: float | None,
    before_info: WorldBossInfo,
) -> WorldBossRun:
    def _abandon(reason: str, simulation: dict[str, Any]) -> WorldBossRun:
        """Close an accepted battle when the local calculator cannot finish."""
        try:
            abandoned = submit_result(
                client,
                type_=started.type,
                dungeon_id=started.dungeon_id,
                result=1,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return WorldBossRun(
                success=False,
                info=before_info,
                start=started,
                simulation=simulation,
                error=f"{reason}; abandon failed: {exc}",
            )
        after_info, after_info_error = _fetch_after_result_info(
            client,
            timeout=timeout,
            before=before_info,
            result=1,
            wait_for_change=False,
        )
        abandon_error = abandoned.error
        if after_info_error:
            abandon_error = "; ".join(
                part for part in (abandon_error, after_info_error) if part
            ) or reason
        return WorldBossRun(
            success=False,
            info=before_info,
            start=started,
            result=abandoned,
            after_info=after_info,
            after_info_error=after_info_error,
            simulation=simulation,
            error=abandon_error or reason,
        )

    try:
        simulation = simulate_start_body(
            page,
            started.body,
            max_frames=max_frames,
            speed_scale=speed_scale,
            realtime=realtime,
            simulation_timeout_sec=simulation_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 — convert calculator failure to a safe abandon
        simulation = {"ok": False, "err": str(exc)}

    if not simulation.get("ok") or not simulation.get("complete", True):
        # The server has already consumed the entry. Close the server-side
        # battle instead of leaving the account stuck in a pending battle.
        return _abandon(
            f"calculator failed: {simulation.get('err')}",
            simulation,
        )

    try:
        hp_num = int(simulation.get("hp_num") or 0)
        last_hurt_num = int(simulation.get("last_hurt_num") or 0)
    except (TypeError, ValueError) as exc:
        return _abandon(
            f"invalid calculator damage: {exc}",
            simulation,
        )
    _wait_for_settlement_delay(battle_started_at, settlement_delay_sec)
    try:
        reported = submit_result(
            client,
            type_=started.type,
            dungeon_id=started.dungeon_id,
            result=0,
            hp_num=hp_num,
            last_hurt_num=last_hurt_num,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — 結算未確認時不得宣稱成功
        after_info, after_info_error = _fetch_after_result_info(
            client,
            timeout=timeout,
            before=before_info,
            result=0,
            wait_for_change=False,
        )
        return WorldBossRun(
            success=False,
            info=before_info,
            start=started,
            after_info=after_info,
            after_info_error=after_info_error,
            simulation=simulation,
            error=f"settlement failed: {exc}",
        )
    after_info, after_info_error = _fetch_after_result_info(
        client,
        timeout=timeout,
        before=before_info,
        result=0,
        wait_for_change=reported.error_code is None,
    )
    confirmed = (
        reported.error_code is None
        and _result_state_changed(before_info, after_info, result=0)
    )
    if not confirmed:
        confirmed_result = WorldBossResult(
            success=False,
            type=started.type,
            dungeon_id=started.dungeon_id,
            result=0,
            rewards=reported.rewards,
            ext=reported.ext,
            sext=reported.sext,
            error=reported.error or after_info_error or "settlement not confirmed",
            error_code=reported.error_code,
            fields=reported.fields,
        )
        return WorldBossRun(
            success=False,
            info=before_info,
            start=started,
            result=confirmed_result,
            after_info=after_info,
            after_info_error=after_info_error,
            simulation=simulation,
            error=confirmed_result.error,
        )
    try:
        finished = finish_worldboss(client, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — DONE 未收尾時不得宣稱整輪完成
        return WorldBossRun(
            success=False,
            info=before_info,
            start=started,
            result=reported,
            after_info=after_info,
            after_info_error=after_info_error,
            simulation=simulation,
            error=f"finish 6593 failed: {exc}",
        )
    if not finished.success:
        return WorldBossRun(
            success=False,
            info=before_info,
            start=started,
            result=finished,
            after_info=after_info,
            after_info_error=after_info_error,
            simulation=simulation,
            error=finished.error or "finish 6593 rejected",
        )
    return WorldBossRun(
        success=True,
        info=before_info,
        start=started,
        result=finished,
        after_info=after_info,
        after_info_error=after_info_error,
        simulation=simulation,
        error=None,
    )


def _wait_for_session_handoff(client: WSGameClient, settle_sec: float) -> None:
    """讓剛登入的 WS session 完成伺服器端 mutation gate 交接。"""

    connected_at = getattr(client, "connection_started_at", None)
    if connected_at is None:
        return
    try:
        remaining = float(settle_sec) - (time.time() - float(connected_at))
    except (TypeError, ValueError):
        return
    if remaining > 0:
        time.sleep(remaining)


def _wait_for_settlement_delay(started_at: float, delay_sec: float) -> float:
    """從伺服器接受進場起至少等指定秒數，再送出成功結算。"""

    remaining = max(0.0, float(delay_sec) - (time.monotonic() - started_at))
    if remaining > 0:
        logger.info("WorldBoss 等待 %.1f 秒後送出 3592 完成結算", remaining)
        time.sleep(remaining)
    return remaining


def run_with_b(
    client: WSGameClient,
    *,
    prefer_ephemeral: bool = False,
    cdp_port: Optional[int] = None,
    game_url: Optional[str] = None,
    headless: bool = True,
    ready_timeout_sec: float = 90.0,
    max_frames: int = 30_000,
    speed_scale: float = 2.0,
    realtime: bool = True,
    simulation_timeout_sec: float = 330.0,
    settlement_delay_sec: float = _SETTLEMENT_DELAY_SEC,
    session_settle_sec: float = _SESSION_SETTLE_SEC,
    timeout: float | None = None,
) -> WorldBossRun:
    """Open and validate B before the WS session performs the WorldBoss calls."""

    if not prefer_ephemeral and not cdp_port:
        return WorldBossRun(success=False, skipped="no B cdp_port")
    pw = browser = page = None
    kind = None
    try:
        if prefer_ephemeral:
            pw, browser, page, kind = open_b_runtime(
                prefer_ephemeral=True,
                cdp_port=None,
                game_url=game_url,
                headless=headless,
                ready_timeout_sec=ready_timeout_sec,
            )
        else:
            try:
                pw, browser, page, kind = open_raw_cdp_runtime(int(cdp_port))
            except Exception as cdp_exc:  # noqa: BLE001
                # 裝置的 debug port 不代表頁面一定已啟動；CDP 拒絕連線時
                # 自動開一個無 profile 的 headless B 頁，避免所有裝置卡在 10061。
                logger.warning(
                    "WorldBoss B 頁 CDP %s 連線失敗，改用 ephemeral headless: %s",
                    cdp_port,
                    cdp_exc,
                )
                try:
                    pw, browser, page, kind = open_b_runtime(
                        prefer_ephemeral=True,
                        cdp_port=None,
                        game_url=game_url,
                        headless=headless,
                        ready_timeout_sec=ready_timeout_sec,
                    )
                except Exception as ephemeral_exc:  # noqa: BLE001
                    raise RuntimeError(
                        f"CDP {cdp_port} failed: {cdp_exc}; "
                        f"ephemeral fallback failed: {ephemeral_exc}"
                    ) from ephemeral_exc
    except Exception as exc:  # noqa: BLE001 — 未進場，不可送放棄結算
        return WorldBossRun(success=False, error=f"B page failed: {exc}")

    try:
        # 同帳號 H5 session 被 WS 登入接管後，mutation gate 需要短暫時間完成
        # handoff；實機可見 3594 已成功但緊接的 3597 仍回 173。
        _wait_for_session_handoff(client, session_settle_sec)

        info = fetch_info(client, timeout=timeout)
        if not info.success:
            return WorldBossRun(success=False, info=info, error=info.error)
        if info.is_open != 1:
            return WorldBossRun(
                success=False,
                info=info,
                skipped=f"event closed (is_open={info.is_open})",
            )
        if info.times <= 0:
            return WorldBossRun(
                success=False,
                info=info,
                skipped=f"no attempts left (times={info.times})",
            )

        started = start(client, timeout=timeout)
        if not started.success:
            return WorldBossRun(
                success=False,
                info=info,
                start=started,
                error=started.error,
            )
        battle_started_at = time.monotonic()
        return _run_after_start(
            client,
            started,
            page,
            max_frames=max_frames,
            speed_scale=speed_scale,
            realtime=realtime,
            simulation_timeout_sec=simulation_timeout_sec,
            settlement_delay_sec=settlement_delay_sec,
            battle_started_at=battle_started_at,
            timeout=timeout,
            before_info=info,
        )
    finally:
        if kind == "raw_cdp" and page is not None:
            close_raw_cdp_runtime(page)
        elif kind is not None:
            close_b_runtime(pw, browser, kind=kind)

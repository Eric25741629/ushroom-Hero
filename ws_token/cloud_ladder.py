"""雲纏天梯每週挑戰純 WS 實作。

Live 5556（2026-07-30）確認：

* ``dungeon_battle_more_start`` 以 ``type=32`` 開戰。
* 自動戰鬥的官方結算本來就是 ``manual_operators=0`` 且不帶 operators。
* 收到 start 後可立即送 ``dungeon_battle_result``；server 會以
  ``dungeon_result_s2c`` 回覆，無須啟動 H5 戰鬥畫面。
* 2／3／4 號位有關卡即死機制，統一把我方與戰友放在 1／5 號安全位。
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import json_manager
from ws_token import codec
from ws_token.abort import WSRunAborted

logger = logging.getLogger(__name__)

TYPE_DOUBLE_LADDER = 32

CMD_ERROR = 0x0201
CMD_DUNGEON_RESULT = 0x0E03
CMD_BATTLE_RESULT = 0x0E08
CMD_BATTLE_START = 0x0E0D
CMD_ENTRANCE_INFO = 0x0E14
CMD_DC_INFO = 0x0E15

CMD_TEAMMATE_INFO = 0x4002
CMD_USE_TEAMMATE = 0x4004
CMD_LEVEL_INFO = 0x400E
CMD_CHANGE_POS = 0x400F

WEEKLY_RECORD = "cloud_fighting_weekly"
EXCLUDED_DEVICE = "emulator-5558"
SAFE_POSITIONS: tuple[tuple[int, int], ...] = ((1, 1), (5, 2))
MAX_FIGHTS_PER_RUN = 200
ERROR_ACTIVITY_CLOSED = 173
_TZ = datetime.timezone(datetime.timedelta(hours=8))


class CloudLadderError(RuntimeError):
    """協定成功送出但 server 未完成預期狀態轉移。"""


@dataclass(frozen=True)
class LadderState:
    now_level: int
    max_level: int
    teammate_id: int
    my_hp: int
    my_max_hp: int
    teammate_hp: int
    teammate_max_hp: int

    @property
    def completed(self) -> bool:
        return self.now_level > self.max_level


def _as_int(value) -> int:
    return int(value) if isinstance(value, int) else 0


def _error_code(body: bytes) -> int:
    return _as_int(codec.walk_dict(body).get(1))


def is_due(device: str, now: Optional[datetime.datetime] = None) -> tuple[bool, str]:
    """每 ISO 週一次；週一凌晨三點前等待重置，其餘時間可補跑。"""
    now = now or datetime.datetime.now(_TZ)
    if now.weekday() == 0 and now.hour < 3:
        return False, "monday_before_03"
    try:
        rec = json_manager.return_time(device, name=WEEKLY_RECORD)
    except Exception:
        rec = None
    if rec and isinstance(rec, dict) and rec.get("timestamp"):
        try:
            last = datetime.datetime.fromtimestamp(float(rec["timestamp"]), now.tzinfo)
            if tuple(last.isocalendar()[:2]) == tuple(now.isocalendar()[:2]):
                return False, "already_this_week"
        except (TypeError, ValueError, OSError):
            pass
    return True, "due"


def read_entrance(client, *, timeout: float | None = None) -> dict:
    cmd, body = client.call_for(
        CMD_ENTRANCE_INFO,
        b"",
        expect_cmds=(CMD_ENTRANCE_INFO, CMD_ERROR),
        timeout=timeout,
    )
    if cmd == CMD_ERROR:
        error = _error_code(body)
        if error == ERROR_ACTIVITY_CLOSED:
            return {
                "is_open": 0,
                "end_time": 0,
                "start_time": 0,
                "max_reward_num": 0,
                "error_code": error,
            }
        raise CloudLadderError(f"entrance_info error={error}")
    fields = codec.walk_dict(body)
    return {
        "is_open": _as_int(fields.get(1)),
        "end_time": _as_int(fields.get(2)),
        "start_time": _as_int(fields.get(3)),
        "max_reward_num": _as_int(fields.get(5)),
    }


def read_state(client, *, timeout: float | None = None) -> LadderState:
    cmd, body = client.call_for(
        CMD_DC_INFO,
        b"",
        expect_cmds=(CMD_DC_INFO, CMD_ERROR),
        timeout=timeout,
    )
    if cmd == CMD_ERROR:
        raise CloudLadderError(f"dc_info error={_error_code(body)}")
    fields = codec.walk_dict(body)
    return LadderState(
        now_level=_as_int(fields.get(1)),
        max_level=_as_int(fields.get(2)),
        my_hp=_as_int(fields.get(6)),
        my_max_hp=_as_int(fields.get(7)),
        teammate_id=_as_int(fields.get(8)),
        teammate_hp=_as_int(fields.get(9)),
        teammate_max_hp=_as_int(fields.get(10)),
    )


def _decode_teammates(body: bytes) -> list[dict]:
    teammates: list[dict] = []
    for field, value in codec.walk(body):
        if field != 4 or not isinstance(value, (bytes, bytearray)):
            continue
        row = codec.walk_dict(bytes(value))
        raw_name = row.get(2)
        teammates.append({
            "role_id": _as_int(row.get(1)),
            "name": (
                bytes(raw_name).decode("utf-8", errors="replace")
                if isinstance(raw_name, (bytes, bytearray))
                else ""
            ),
            "level": _as_int(row.get(3)),
            "power": _as_int(row.get(5)),
        })
    return teammates


def ensure_teammate(client, state: LadderState, *,
                    timeout: float | None = None) -> Optional[int]:
    """已有戰友就沿用；否則從候選中選戰力最高者。"""
    if state.teammate_id:
        return state.teammate_id
    cmd, body = client.call_for(
        CMD_TEAMMATE_INFO,
        b"",
        expect_cmds=(CMD_TEAMMATE_INFO, CMD_ERROR),
        timeout=timeout,
    )
    if cmd == CMD_ERROR:
        raise CloudLadderError(f"teammate_info error={_error_code(body)}")
    candidates = [x for x in _decode_teammates(body) if x["role_id"]]
    if not candidates:
        return None
    selected = max(candidates, key=lambda x: (x["power"], x["level"]))
    request = codec.pb_uint(1, selected["role_id"])
    reply_cmd, reply = client.call_for(
        CMD_USE_TEAMMATE,
        request,
        expect_cmds=(CMD_USE_TEAMMATE, CMD_ERROR),
        timeout=timeout,
    )
    if reply_cmd == CMD_ERROR:
        raise CloudLadderError(f"use_teammate error={_error_code(reply)}")
    code = _as_int(codec.walk_dict(reply).get(2))
    if code:
        raise CloudLadderError(f"use_teammate code={code}")
    logger.info("cloud_ladder: selected teammate %s(%s), power=%s",
                selected["name"], selected["role_id"], selected["power"])
    return selected["role_id"]


def _decode_positions(body: bytes) -> tuple[tuple[int, int], ...]:
    out: list[tuple[int, int]] = []
    for field, value in codec.walk(body):
        if field == 1 and isinstance(value, (bytes, bytearray)):
            pair = codec.walk_dict(bytes(value))
            out.append((_as_int(pair.get(1)), _as_int(pair.get(2))))
    return tuple(out)


def _positions_body(positions: tuple[tuple[int, int], ...]) -> bytes:
    return b"".join(
        codec.pb_msg(
            1,
            codec.pb_uint(1, position) + codec.pb_uint(2, role_index),
        )
        for position, role_index in positions
    )


def ensure_safe_positions(client, *,
                          timeout: float | None = None) -> tuple[tuple[int, int], ...]:
    cmd, body = client.call_for(
        CMD_LEVEL_INFO,
        b"",
        expect_cmds=(CMD_LEVEL_INFO, CMD_ERROR),
        timeout=timeout,
    )
    if cmd == CMD_ERROR:
        raise CloudLadderError(f"level_info error={_error_code(body)}")
    current = _decode_positions(body)
    if set(current) == set(SAFE_POSITIONS):
        return SAFE_POSITIONS
    reply_cmd, reply = client.call_for(
        CMD_CHANGE_POS,
        _positions_body(SAFE_POSITIONS),
        expect_cmds=(CMD_CHANGE_POS, CMD_ERROR),
        timeout=timeout,
    )
    if reply_cmd == CMD_ERROR:
        raise CloudLadderError(f"change_pos error={_error_code(reply)}")
    code = _as_int(codec.walk_dict(reply).get(1))
    if code:
        raise CloudLadderError(f"change_pos code={code}")
    logger.info("cloud_ladder: position %s -> %s", current, SAFE_POSITIONS)
    return SAFE_POSITIONS


def fight_once(client, level: int, *, timeout: float | None = None) -> dict:
    start_body = (
        codec.pb_uint(1, TYPE_DOUBLE_LADDER)
        + codec.pb_uint(2, level)
    )
    cmd, body = client.call_for(
        CMD_BATTLE_START,
        start_body,
        expect_cmds=(CMD_BATTLE_START, CMD_ERROR),
        timeout=timeout,
    )
    if cmd == CMD_ERROR:
        raise CloudLadderError(f"battle_start level={level} error={_error_code(body)}")
    start = codec.walk_dict(body)
    code = _as_int(start.get(1))
    dungeon_id = _as_int(start.get(3))
    if code or not dungeon_id:
        raise CloudLadderError(
            f"battle_start level={level} code={code} dungeon_id={dungeon_id}")

    result_body = (
        codec.pb_uint(1, TYPE_DOUBLE_LADDER)
        + codec.pb_uint(2, dungeon_id)
        + codec.pb_uint(3, 0)  # 0 = 勝利
        + codec.pb_uint(4, 0)  # 0 = 官方自動戰鬥，不帶手動 operators
    )
    reply_cmd, reply = client.call_for(
        CMD_BATTLE_RESULT,
        result_body,
        expect_cmds=(CMD_DUNGEON_RESULT, CMD_BATTLE_RESULT, CMD_ERROR),
        timeout=timeout,
    )
    if reply_cmd == CMD_ERROR:
        raise CloudLadderError(
            f"battle_result level={level} error={_error_code(reply)}")
    result = codec.walk_dict(reply)
    result_code = _as_int(result.get(1))
    battle_result = _as_int(result.get(4))
    if result_code or battle_result:
        raise CloudLadderError(
            f"battle_result level={level} code={result_code} result={battle_result}")
    return {
        "level": level,
        "dungeon_id": dungeon_id,
        "seed": _as_int(start.get(5)),
        "response_cmd": reply_cmd,
    }


def run_weekly(
    client,
    device: str,
    *,
    now: Optional[datetime.datetime] = None,
    timeout: float | None = None,
    max_fights: int = MAX_FIGHTS_PER_RUN,
    should_abort: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int, int], None]] = None,
) -> dict:
    """本週尚未完成時，從目前關卡一路純 WS 推到最高關。"""
    if device == EXCLUDED_DEVICE:
        return {"skipped": "emulator-5558 uses H5"}
    due, reason = is_due(device, now)
    # 舊 H5 流程即使挑戰失敗/只打一半也會寫 weekly marker。若 marker 說本週
    # 做過，仍以 server 的 now_level/max_level 為真相；未通關就接著補打。
    if not due and reason != "already_this_week":
        return {"skipped": reason}

    entrance = read_entrance(client, timeout=timeout)
    if not entrance["is_open"]:
        if entrance.get("error_code") == ERROR_ACTIVITY_CLOSED:
            # 173 是 server 權威的「本期活動已結束」，H5 也不可能補做。視為本週
            # 已處理並寫 marker，避免 browser-skip 因這項不可能任務反覆冷啟 H5。
            json_manager.time_recording(device, name=WEEKLY_RECORD)
            return {"completed": False, "activity_closed": True}
        return {"skipped": "activity_not_open"}
    state = read_state(client, timeout=timeout)
    if not due and state.completed:
        return {"skipped": "already_this_week"}
    start_level = state.now_level
    if not state.completed:
        teammate = ensure_teammate(client, state, timeout=timeout)
        if not teammate:
            raise CloudLadderError("no teammate available")
        ensure_safe_positions(client, timeout=timeout)

    fights = 0
    results: list[dict] = []
    while not state.completed:
        if should_abort is not None and should_abort():
            raise WSRunAborted("cloud_ladder interrupted")
        if fights >= max(1, int(max_fights)):
            raise CloudLadderError(
                f"max_fights reached: {fights}, level={state.now_level}")
        previous = state.now_level
        results.append(fight_once(client, previous, timeout=timeout))
        fights += 1
        state = read_state(client, timeout=timeout)
        if state.now_level <= previous:
            raise CloudLadderError(
                f"level did not advance: before={previous}, after={state.now_level}")
        if progress is not None:
            progress(fights, state.now_level, state.max_level)

    json_manager.time_recording(device, name=WEEKLY_RECORD)
    logger.info(
        "cloud_ladder: %s weekly completed, fights=%d level=%d>%d",
        device, fights, state.now_level, state.max_level,
    )
    return {
        "completed": True,
        "fights": fights,
        "start_level": start_level,
        "final_level": state.now_level,
        "max_level": state.max_level,
        "results": results,
    }

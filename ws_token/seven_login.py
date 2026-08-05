"""七日登入獎勵的純 WS 查詢與領取流程。

協議已由 H5 client source/live CDP 確認：
- ``0x0314`` ``role.role_seven_login_info_c2s``：空 body 查詢
- ``0x0315`` ``role.role_seven_login_reward_c2s``：``day`` field 1
- ``0x0201``：一般錯誤回覆；重複領取視為 benign skip

這個模組不依賴畫面上的七日獎勵視窗。先讀 server state，再只送一個目前
可領的 day；因此每日喚醒重跑也不會重複消耗或讓 pipeline 中斷。
"""
from __future__ import annotations

from typing import Any

from utils.protobuf_walk import walk_fields
from ws_token import codec

CMD_INFO = 0x0314
CMD_REWARD = 0x0315
CMD_ERROR = 0x0201
MIN_REWARD_DAY = 1


def build_info_body() -> bytes:
    """七日登入資訊查詢沒有 request fields。"""
    return b""


def build_reward_body(day: int) -> bytes:
    """編碼 ``role_seven_login_reward_c2s { day: 1 }``。"""
    day = int(day)
    if day < MIN_REWARD_DAY:
        raise ValueError(f"seven-login day must be >= {MIN_REWARD_DAY}, got {day}")
    return codec.pb_uint(1, day)


def _decode_packed_varints(body: bytes) -> list[int]:
    values: list[int] = []
    i = 0
    while i < len(body):
        value = 0
        shift = 0
        while i < len(body):
            byte = body[i]
            i += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                values.append(value)
                break
            shift += 7
            if shift > 63:
                raise ValueError("seven-login packed varint is too long")
    return values


def parse_info(body: bytes) -> dict[str, Any]:
    """解析 ``info_s2c { day=1, get_day=2 repeated, if_day_first=3 }``。"""
    day = 0
    get_day: list[int] = []
    if_day_first: int | None = None
    for field, wire, value in walk_fields(body):
        if field == 1 and wire == 0:
            day = int(value)
        elif field == 2 and wire == 0:
            get_day.append(int(value))
        elif field == 2 and wire == 2:
            get_day.extend(_decode_packed_varints(bytes(value)))
        elif field == 3 and wire == 0:
            if_day_first = int(value)
    return {
        "day": day,
        "get_day": get_day,
        "if_day_first": if_day_first,
    }


def parse_error(body: bytes) -> dict[str, Any]:
    """解析一般錯誤回覆，未知欄位保留為 None。"""
    error_code: int | None = None
    for field, wire, value in walk_fields(body):
        if field == 1 and wire == 0:
            error_code = int(value)
            break
    return {"error_code": error_code}


def next_claimable_day(info: dict[str, Any]) -> int | None:
    """回傳第一個已解鎖、但尚未領取的七日獎勵 day。"""
    try:
        current_day = max(0, int(info.get("day") or 0))
    except (TypeError, ValueError):
        current_day = 0
    claimed = {int(day) for day in (info.get("get_day") or ())}
    for day in range(1, current_day + 1):
        if day not in claimed:
            return day
    return None


def _call_client(client, cmd: int, body: bytes) -> tuple[int, bytes]:
    return client.call_for(cmd, body, expect_cmds=(cmd, CMD_ERROR))


def _call_page(page, cmd: int, body: bytes) -> tuple[int, bytes]:
    from utils.web_game_api import WebGameAPI

    return WebGameAPI(page).call_raw_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR)
    )


def _apply(transport, call, *, device: str = "") -> dict[str, Any]:
    """共用查詢→最多一次領取邏輯；``transport`` 只用於診斷。"""
    try:
        info_cmd, info_body = call(CMD_INFO, build_info_body())
        if info_cmd == CMD_ERROR:
            return {"skipped": "info_error", **parse_error(info_body)}
        info = parse_info(info_body)
        day = next_claimable_day(info)
        result: dict[str, Any] = {
            "day": info["day"],
            "claimed_days": tuple(info["get_day"]),
        }
        if day is None:
            return {"skipped": "not_claimable", **result}

        reward_cmd, reward_body = call(CMD_REWARD, build_reward_body(day))
        if reward_cmd == CMD_REWARD:
            return {"ok": True, "claimed": day, **result}
        error = parse_error(reward_body)
        return {
            "skipped": "server_reject",
            "claimed": day,
            **error,
            **result,
        }
    except Exception as exc:  # caller decides whether page/runner should log it
        return {"error": str(exc), "transport": transport, "device": device}


def apply_via_client(client, *, device: str = "") -> dict[str, Any]:
    """透過既有 ``WSGameClient`` 查詢並領取。"""
    return _apply("ws", lambda cmd, body: _call_client(client, cmd, body),
                  device=device)


def apply_via_page(page, *, device: str = "") -> dict[str, Any]:
    """透過已登入 H5 page 的純遊戲 WS 查詢並領取。"""
    return _apply("page", lambda cmd, body: _call_page(page, cmd, body),
                  device=device)


def apply(client=None, *, page=None, device: str = "") -> dict[str, Any]:
    """選擇 page 或 pure client；沒有 transport 時安全略過。"""
    if page is not None:
        return apply_via_page(page, device=device)
    if client is not None:
        return apply_via_client(client, device=device)
    return {"skipped": "no_transport"}

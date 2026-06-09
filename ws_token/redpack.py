"""Red packet (紅包) task over a logged-in WSGameClient — list + grab.

Pure WS, LIVE-verified (2026-06-09: claimed a real bag, num=102). The grab is
built and framed by this code (codec + client), NOT the in-game netManager —
which refutes REDPACK_SCHEMA.md's old "must use netManager.send" claim. The
real blocker was a field-order bug: the live schema is

  red_grab_c2s       { type#1:uint32, id#2:uint64 }          (type FIRST, then id)
  red_grab_s2c       { code#1, num#2(amount), red_envelope#3:p_red }
  red_brief_list_s2c { send_list#1:p_brief_red[], grab_list#2:p_brief_red[] }
  p_brief_red        { id#1, cfg_id#2, state#3, role_id#4, name#5, expiry_time#6, min#7, max#8 }

and the grab `type` = configRed_packet[cfg_id].type (NOT a brief field) — bundled
in ws_token/data/red_packet_types.json. See docs/protocol/REDPACK_SCHEMA.md.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_BRIEF_LIST = 0x2605   # red.red_brief_list_c2s/s2c (empty request body)
CMD_GRAB = 0x2603         # red.red_grab_c2s/s2c
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code=1}
ERR_ALREADY_CLAIMED = 2   # also returned when the grab body is malformed

_TYPE_MAP_PATH = Path(__file__).resolve().parent / "data" / "red_packet_types.json"
_type_map: dict[int, int] | None = None


def _load_type_map() -> dict[int, int]:
    global _type_map
    if _type_map is None:
        try:
            raw = json.loads(_TYPE_MAP_PATH.read_text(encoding="utf-8"))
            _type_map = {int(k): int(v) for k, v in raw.items()}
        except (OSError, ValueError):
            logger.warning("ws_token redpack: type map missing at %s", _TYPE_MAP_PATH)
            _type_map = {}
    return _type_map


def grab_type_for(cfg_id: int) -> int:
    """The grab `type` = configRed_packet[cfg_id].type (from the bundled map).

    Defaults to 0 for an unknown cfg_id (real claimable bags are always in the
    game's config, so this should not happen for live bags).
    """
    t = _load_type_map().get(int(cfg_id))
    if t is None:
        logger.warning("ws_token redpack: cfg_id %s not in type map; default type=0",
                       cfg_id)
        return 0
    return t


@dataclass(frozen=True)
class RedBag:
    """One claimable entry from red_brief_list_s2c.grab_list (p_brief_red)."""

    bag_id: int          # id #1
    cfg_id: int          # #2 -> configRed_packet key (drives grab `type`)
    state: int           # #3
    sender_id: int       # role_id #4
    sender_name: str     # #5
    expiry_time: int     # #6 (unix seconds)
    min_amount: int      # #7
    max_amount: int      # #8
    raw: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class GrabResult:
    bag_id: int
    success: bool            # True iff 0x2603 with code==0
    response_cmd: int
    response_body: bytes
    fields: dict
    amount: int | None = None      # red_grab_s2c.num
    error_code: int | None = None
    error: str | None = None


def _parse_brief(entry: bytes) -> RedBag:
    d = codec.walk_dict(entry)
    return RedBag(
        bag_id=_as_int(d.get(1)),
        cfg_id=_as_int(d.get(2)),
        state=_as_int(d.get(3)),
        sender_id=_as_int(d.get(4)),
        sender_name=_as_str(d.get(5)),
        expiry_time=_as_int(d.get(6)),
        min_amount=_as_int(d.get(7)),
        max_amount=_as_int(d.get(8)),
        raw=d,
    )


def parse_brief_list(body: bytes) -> list[RedBag]:
    """red_brief_list_s2c: claimable bags are grab_list (field 2)."""
    return [_parse_brief(bytes(v)) for fnum, v in codec.walk(body)
            if fnum == 2 and isinstance(v, (bytes, bytearray))]


def build_grab_body(bag_id: int, type_: int) -> bytes:
    """red_grab_c2s {type#1:uint32, id#2:uint64} — type FIRST, then id."""
    return codec.pb_uint(1, type_) + codec.pb_uint(2, bag_id)


def parse_grab_result(cmd: int, body: bytes, *, bag_id: int = 0) -> GrabResult:
    f = codec.walk_dict(body)
    if cmd == CMD_GRAB:  # red_grab_s2c {code#1, num#2, red_envelope#3}
        code = _as_int(f.get(1))
        if code == 0:
            return GrabResult(bag_id, True, cmd, body, f, amount=_as_int(f.get(2)))
        return GrabResult(bag_id, False, cmd, body, f, error_code=code,
                          error=f"grab code={code}")
    if cmd == CMD_ERROR:
        ec = f.get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return GrabResult(bag_id, False, cmd, body, f, error_code=ec,
                          error=f"server error code={ec}")
    return GrabResult(bag_id, False, cmd, body, f,
                      error=f"unexpected response cmd 0x{cmd:04x}")


def list_red_bags(client: WSGameClient, *, timeout: float | None = None) -> list[RedBag]:
    return parse_brief_list(client.call(CMD_BRIEF_LIST, b"", timeout=timeout))


def grab(client: WSGameClient, bag: RedBag, *, timeout: float | None = None) -> GrabResult:
    """Grab one bag. Reply may be 0x2603 (code/num) or 0x0201 (error)."""
    type_ = grab_type_for(bag.cfg_id)
    cmd, body = client.call_for(
        CMD_GRAB, build_grab_body(bag.bag_id, type_),
        expect_cmds=(CMD_GRAB, CMD_ERROR), timeout=timeout)
    return parse_grab_result(cmd, body, bag_id=bag.bag_id)


def grab_claimable(client: WSGameClient, *, spacing: float = 0.2) -> dict:
    """List grab_list and grab each. Returns {attempted, claimed, results}."""
    bags = list_red_bags(client)
    results: list[GrabResult] = []
    claimed = 0
    for bag in bags:
        result = grab(client, bag)
        results.append(result)
        if result.success:
            claimed += 1
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token redpack: attempted=%d claimed=%d", len(bags), claimed)
    return {"attempted": len(bags), "claimed": claimed, "results": results}


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _as_str(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(v).decode("utf-8", "replace")
    return "" if v is None else str(v)

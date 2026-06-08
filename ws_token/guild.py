"""Family hall (家族大廳) daily tasks over a logged-in WSGameClient.

Pure WS, no App / no screen. Same shape as ws_token/redpack.py: frozen
dataclasses for parsed s2c, ``parse_*`` decoders, ``build_*_body`` c2s encoders,
single-action orchestrators (``client.call`` / ``call_for``) and guard-driven
batch orchestrators.

Field numbers are the authoritative exported truth — verify against
docs/protocol/GUILD_PROTO_SCHEMA.json (cmd ids + c2s/s2c) and
docs/protocol/TYPE_PROTO_SCHEMA.json (p_guild_treasure_box / p_guild_help /
p_guild_help_status / p_pos). The s2c field layout lives on the dataclasses
below (and is byte-locked by tests/test_ws_token_guild.py).

All Phase-2 live unknowns are driven by reading s2c fields + guard loops, never
hard-coded magic numbers. Each one is tagged ``# live-confirm:`` inline.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_DONATE = 7441          # guild.guild_donate_c2s/s2c
CMD_HELP_INFO = 7452       # guild.guild_help_info_c2s/s2c
CMD_HELP = 7454            # guild.guild_help_c2s/s2c
CMD_HELP_STATUS = 7456     # guild.guild_help_status_c2s/s2c
CMD_TREASURE_INFO = 7459   # guild.guild_treasure_info_c2s/s2c
CMD_TREASURE_OPEN = 7460   # guild.guild_treasure_open_c2s/s2c
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}

# live-confirm: the query `type` for guild_help_info. The status table
# (guild_help_status) enumerates which (type, sub_type) help channels are open;
# 0 is a safe default request value pending a live read. Callers can override.
DEFAULT_HELP_TYPE = 0

# live-confirm: per-day caps for donate / help are server-enforced and reported
# back in the s2c (donate_count, daily_count + new_daily_count). The guard loops
# stop on the server's signal; these are only sanity ceilings so a misbehaving
# server cannot spin us forever.
_DONATE_MAX_ATTEMPTS = 10
_HELP_MAX_ATTEMPTS = 50


# --- parsed results ---------------------------------------------------------

@dataclass(frozen=True)
class DonateResult:
    """guild_donate_s2c {donate_sum#1, donate_week#2, donate_count#3}."""
    donate_sum: int
    donate_week: int
    donate_count: int
    success: bool
    response_cmd: int
    fields: dict = field(compare=False, default_factory=dict)
    error_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class TreasureBox:
    """One p_guild_treasure_box {n#1, pos#2, open_num#3, open_limit#4, role_list#5}."""
    n: int                 # box index within the round
    pos: tuple[int, int]   # p_pos {x#1, y#2}
    open_num: int          # how many times this box has been opened
    open_limit: int        # max opens for this box
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def has_room(self) -> bool:
        # live-confirm: open semantics assumed `open_num < open_limit` => openable.
        return self.open_num < self.open_limit


@dataclass(frozen=True)
class TreasureInfo:
    """guild_treasure_info_s2c (round + per-box state)."""
    is_new: int
    round: int
    cfg_id: int
    my_open: int           # live-confirm: personal open allowance this round
    countdown: int
    box_list: list[TreasureBox]
    raw: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class OpenResult:
    """guild_treasure_open_s2c {round#1, n#2, code#3} or 0x0201."""
    round: int
    n: int
    success: bool
    response_cmd: int
    code: int | None = None
    error_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class GuildHelp:
    """One p_guild_help entry (a guildmate's help request)."""
    help_id: int           # id #1 (uint64) -> guild_help_c2s.help_id
    role_id: int           # #2
    name: str              # #3 (head#4 skipped)
    type: int              # #5
    sub_type: int          # #6
    end_time: int          # #7
    help_num: int          # #8 (helpers#9 skipped)
    raw: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class HelpInfo:
    """guild_help_info_s2c {type#1, daily_count#2, help_list#3:p_guild_help[]}."""
    help_type: int
    daily_count: int       # live-confirm: count *used* today (drives the guard)
    help_list: list[GuildHelp]
    raw: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class HelpResult:
    """guild_help_s2c {new_daily_count#1} or 0x0201."""
    help_id: int
    success: bool
    response_cmd: int
    new_daily_count: int | None = None
    error_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class HelpStatus:
    """One p_guild_help_status {type#1, sub_type#2, status#3}."""
    type: int
    sub_type: int
    status: int


# --- build c2s bodies -------------------------------------------------------

def build_help_info_body(help_type: int) -> bytes:
    """guild_help_info_c2s {type#1:uint32}."""
    return codec.pb_uint(1, help_type)


def build_help_body(help_id: int) -> bytes:
    """guild_help_c2s {help_id#1:uint64}."""
    return codec.pb_uint(1, help_id)


def build_treasure_open_body(round_: int, n: int) -> bytes:
    """guild_treasure_open_c2s {round#1:uint32, n#2:uint32} — round first, then n."""
    return codec.pb_uint(1, round_) + codec.pb_uint(2, n)


# --- parse s2c --------------------------------------------------------------

def parse_donate(cmd: int, body: bytes) -> DonateResult:
    f = codec.walk_dict(body)
    if cmd == CMD_DONATE:
        return DonateResult(
            donate_sum=_as_int(f.get(1)),
            donate_week=_as_int(f.get(2)),
            donate_count=_as_int(f.get(3)),
            success=True, response_cmd=cmd, fields=f)
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        return DonateResult(0, 0, 0, False, cmd, fields=f, error_code=ec,
                            error=f"server error code={ec}")
    return DonateResult(0, 0, 0, False, cmd, fields=f,
                        error=f"unexpected response cmd 0x{cmd:04x}")


def _parse_box(entry: bytes) -> TreasureBox:
    d = codec.walk_dict(entry)
    pos_raw = d.get(2)
    if isinstance(pos_raw, (bytes, bytearray)):
        p = codec.walk_dict(bytes(pos_raw))
        pos = (_as_int(p.get(1)), _as_int(p.get(2)))
    else:
        pos = (0, 0)
    return TreasureBox(
        n=_as_int(d.get(1)),
        pos=pos,
        open_num=_as_int(d.get(3)),
        open_limit=_as_int(d.get(4)),
        raw=d,
    )


def parse_treasure_info(body: bytes) -> TreasureInfo:
    f = codec.walk_dict(body)
    boxes = [_parse_box(bytes(v)) for fnum, v in codec.walk(body)
             if fnum == 6 and isinstance(v, (bytes, bytearray))]
    return TreasureInfo(
        is_new=_as_int(f.get(1)),
        round=_as_int(f.get(2)),
        cfg_id=_as_int(f.get(3)),
        my_open=_as_int(f.get(4)),
        countdown=_as_int(f.get(5)),
        box_list=boxes,
        raw=f,
    )


def parse_open_result(cmd: int, body: bytes, *, round_: int = 0, n: int = 0) -> OpenResult:
    f = codec.walk_dict(body)
    if cmd == CMD_TREASURE_OPEN:
        code = _as_int(f.get(3))
        r_round = _as_int(f.get(1)) or round_
        r_n = _as_int(f.get(2)) or n
        if code == 0:
            return OpenResult(r_round, r_n, True, cmd, code=0)
        return OpenResult(r_round, r_n, False, cmd, code=code, error_code=code,
                          error=f"open code={code}")
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        return OpenResult(round_, n, False, cmd, error_code=ec,
                          error=f"server error code={ec}")
    return OpenResult(round_, n, False, cmd,
                      error=f"unexpected response cmd 0x{cmd:04x}")


def _parse_help(entry: bytes) -> GuildHelp:
    d = codec.walk_dict(entry)
    return GuildHelp(
        help_id=_as_int(d.get(1)),
        role_id=_as_int(d.get(2)),
        name=_as_str(d.get(3)),
        type=_as_int(d.get(5)),
        sub_type=_as_int(d.get(6)),
        end_time=_as_int(d.get(7)),
        help_num=_as_int(d.get(8)),
        raw=d,
    )


def parse_help_info(body: bytes) -> HelpInfo:
    f = codec.walk_dict(body)
    helps = [_parse_help(bytes(v)) for fnum, v in codec.walk(body)
             if fnum == 3 and isinstance(v, (bytes, bytearray))]
    return HelpInfo(
        help_type=_as_int(f.get(1)),
        daily_count=_as_int(f.get(2)),
        help_list=helps,
        raw=f,
    )


def parse_help_result(cmd: int, body: bytes, *, help_id: int = 0) -> HelpResult:
    f = codec.walk_dict(body)
    if cmd == CMD_HELP:
        return HelpResult(help_id, True, cmd, new_daily_count=_as_int(f.get(1)))
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        return HelpResult(help_id, False, cmd, error_code=ec,
                          error=f"server error code={ec}")
    return HelpResult(help_id, False, cmd,
                      error=f"unexpected response cmd 0x{cmd:04x}")


def parse_help_status(body: bytes) -> list[HelpStatus]:
    out: list[HelpStatus] = []
    for fnum, v in codec.walk(body):
        if fnum == 1 and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            out.append(HelpStatus(
                type=_as_int(d.get(1)),
                sub_type=_as_int(d.get(2)),
                status=_as_int(d.get(3)),
            ))
    return out


# --- single-action orchestrators --------------------------------------------

def donate(client: WSGameClient, *, timeout: float | None = None) -> DonateResult:
    """Send guild_donate (empty body); server picks the donation tier."""
    cmd, body = client.call_for(CMD_DONATE, b"", expect_cmds=(CMD_DONATE, CMD_ERROR),
                                timeout=timeout)
    return parse_donate(cmd, body)


def list_treasure(client: WSGameClient, *, timeout: float | None = None) -> TreasureInfo:
    return parse_treasure_info(client.call(CMD_TREASURE_INFO, b"", timeout=timeout))


def open_treasure(client: WSGameClient, round_: int, n: int, *,
                  timeout: float | None = None) -> OpenResult:
    """Open box ``n`` of ``round_``. Reply is 7460 (code) or 0x0201 (error)."""
    cmd, body = client.call_for(
        CMD_TREASURE_OPEN, build_treasure_open_body(round_, n),
        expect_cmds=(CMD_TREASURE_OPEN, CMD_ERROR), timeout=timeout)
    return parse_open_result(cmd, body, round_=round_, n=n)


def list_help(client: WSGameClient, help_type: int = DEFAULT_HELP_TYPE, *,
              timeout: float | None = None) -> list[GuildHelp]:
    body = client.call(CMD_HELP_INFO, build_help_info_body(help_type), timeout=timeout)
    return parse_help_info(body).help_list


def list_help_status(client: WSGameClient, *,
                     timeout: float | None = None) -> list[HelpStatus]:
    return parse_help_status(client.call(CMD_HELP_STATUS, b"", timeout=timeout))


def help_one(client: WSGameClient, help_id: int, *,
             timeout: float | None = None) -> HelpResult:
    """Send guild_help for one request. Reply is 7454 (count) or 0x0201."""
    cmd, body = client.call_for(
        CMD_HELP, build_help_body(help_id),
        expect_cmds=(CMD_HELP, CMD_ERROR), timeout=timeout)
    return parse_help_result(cmd, body, help_id=help_id)


# --- batch orchestrators (guard-driven, no hard-coded caps) -----------------

def donate_until_capped(client: WSGameClient, *, max_attempts: int = _DONATE_MAX_ATTEMPTS,
                        spacing: float = 0.2) -> dict:
    """Donate until the server stops accepting it: an error (0x0201) or a
    ``donate_count`` plateau (the daily-cap signal). ``max_attempts`` is only a
    runaway guard; the real cap is live-confirm (read from donate_count).
    """
    donated = 0
    prev_count = -1
    final_count = 0
    for _ in range(max_attempts):
        result = donate(client)
        if not result.success:
            logger.info("ws_token guild: donate stopped on error code=%s after %d",
                        result.error_code, donated)
            return {"donated": donated, "final_count": final_count,
                    "stopped_reason": "error", "error_code": result.error_code}
        final_count = result.donate_count
        if result.donate_count <= prev_count:
            # live-confirm: plateau in donate_count == daily cap reached.
            return {"donated": donated, "final_count": final_count,
                    "stopped_reason": "no_increase", "error_code": None}
        prev_count = result.donate_count
        donated += 1
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token guild: donate hit max_attempts=%d (donated=%d)",
                max_attempts, donated)
    return {"donated": donated, "final_count": final_count,
            "stopped_reason": "max_attempts", "error_code": None}


def open_all_treasure(client: WSGameClient, *, spacing: float = 0.2) -> dict:
    """Open every box with room, capped by the per-round ``my_open`` allowance
    (read from the s2c, not hard-coded — live-confirm). Skips boxes already at
    ``open_limit``; stops early on a server error.
    """
    info = list_treasure(client)
    opened = 0
    results: list[OpenResult] = []
    for box in info.box_list:
        if opened >= info.my_open:
            return {"opened": opened, "round": info.round, "results": results,
                    "stopped_reason": "my_open_reached", "error_code": None}
        if not box.has_room:
            continue
        result = open_treasure(client, info.round, box.n)
        results.append(result)
        if not result.success:
            logger.info("ws_token guild: open stopped on error code=%s after %d",
                        result.error_code or result.code, opened)
            return {"opened": opened, "round": info.round, "results": results,
                    "stopped_reason": "error",
                    "error_code": result.error_code if result.error_code is not None
                    else result.code}
        opened += 1
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token guild: opened=%d round=%d", opened, info.round)
    return {"opened": opened, "round": info.round, "results": results,
            "stopped_reason": "exhausted", "error_code": None}


def help_all(client: WSGameClient, *, help_type: int = DEFAULT_HELP_TYPE,
             daily_limit: int | None = None, spacing: float = 0.2,
             max_attempts: int = _HELP_MAX_ATTEMPTS) -> dict:
    """Answer guildmate help requests, capped by ``daily_limit - daily_count``
    (both server-read). ``daily_limit`` is the live-confirm per-day cap; when
    None, every listed request is tried and the 0x0201 error / list exhaustion
    stops the loop. Stops early on a server error.
    """
    info_body = client.call(CMD_HELP_INFO, build_help_info_body(help_type))
    info = parse_help_info(info_body)
    if daily_limit is not None:
        remaining = max(daily_limit - info.daily_count, 0)
    else:
        remaining = len(info.help_list)  # live-confirm: no cap known -> try all
    helped = 0
    results: list[HelpResult] = []
    for help_req in info.help_list:
        if helped >= remaining or helped >= max_attempts:
            return {"helped": helped, "results": results,
                    "stopped_reason": "daily_limit_reached", "error_code": None}
        result = help_one(client, help_req.help_id)
        results.append(result)
        if not result.success:
            logger.info("ws_token guild: help stopped on error code=%s after %d",
                        result.error_code, helped)
            return {"helped": helped, "results": results,
                    "stopped_reason": "error", "error_code": result.error_code}
        helped += 1
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token guild: helped=%d of %d listed", helped, len(info.help_list))
    return {"helped": helped, "results": results,
            "stopped_reason": "exhausted", "error_code": None}


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _as_opt_int(v) -> int | None:
    return int(v) if isinstance(v, int) else None


def _as_str(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(v).decode("utf-8", "replace")
    return "" if v is None else str(v)

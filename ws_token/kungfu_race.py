"""菇菇武道會膜拜冠軍 over pure WS.

Live-confirmed from the H5 client and the 7fe98fc6 action trace:

    kungfu_race_worship_c2s 16665 {} -> kungfu_race_worship_s2c 16665 {worship#1}

The server owns the event-window and once-per-period checks.  A rejected request
is therefore reported as a safe no-op instead of raising into the daily runner.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ws_token import codec
from ws_token.client import WSError, WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

CMD_INFO = 16641
CMD_CHAMPION_INFO = 16664
CMD_WORSHIP = 16665
CMD_ERROR = 0x0201


@dataclass(frozen=True)
class WorshipResult:
    """One膜拜 request result; ``worship`` is the server's field #1 value."""

    success: bool
    response_cmd: int | None = None
    worship: int | None = None
    error_code: int | None = None
    error: str | None = None


def _as_int(value) -> int | None:
    return int(value) if isinstance(value, int) else None


def _error_code(body: bytes) -> int | None:
    try:
        return _as_int(codec.walk_dict(body).get(1))
    except Exception:  # noqa: BLE001 — malformed server error is still a no-op
        return None


def parse_worship(response_cmd: int, body: bytes) -> WorshipResult:
    """Decode either ``16665 {worship#1}`` or ``0x0201 {error_code#1}``."""
    if response_cmd == CMD_ERROR:
        return WorshipResult(
            success=False,
            response_cmd=response_cmd,
            error_code=_error_code(body),
        )
    if response_cmd != CMD_WORSHIP:
        return WorshipResult(
            success=False,
            response_cmd=response_cmd,
            error="unexpected response command",
        )
    try:
        worship = _as_int(codec.walk_dict(body).get(1))
    except Exception:  # noqa: BLE001 — preserve a successful ack even if payload changes
        worship = None
    return WorshipResult(success=True, response_cmd=response_cmd, worship=worship)


def worship(client: WSGameClient, *, timeout: float = 6.0) -> WorshipResult:
    """膜拜冠軍 once; safe to call every WS pass.

    The request is intentionally empty.  Event availability and duplicate claims
    are checked by the server, so no UI state or browser session is required.
    """
    try:
        response_cmd, body = client.call_for(
            CMD_WORSHIP,
            b"",
            expect_cmds=(CMD_WORSHIP, CMD_ERROR),
            timeout=timeout,
        )
    except (WSTimeoutError, WSError) as exc:
        logger.warning("kungfu_race: worship request failed: %s", exc)
        return WorshipResult(success=False, error=str(exc))
    result = parse_worship(response_cmd, body)
    if result.success:
        logger.info("kungfu_race: worship acknowledged (worship=%s)", result.worship)
    else:
        logger.info("kungfu_race: worship rejected (error_code=%s)", result.error_code)
    return result

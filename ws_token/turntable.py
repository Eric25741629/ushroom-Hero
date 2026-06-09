"""Turntable (轉盤金幣 / ad-wheel) task over a logged-in WSGameClient — pure WS.

ad module 22 (docs/protocol/AD_PROTO_SCHEMA.json). Both c2s bodies are empty:

  ad_wheel_info  5635 {} -> { cd#1:uint32, num#2:uint32 }   (cd FIRST, then num)
  ad_wheel_spin  5636 {} -> { id#1:uint32 }                 (winning slot)

where ``num`` = remaining free/accumulated spins, ``cd`` = cooldown expiry
timestamp, and ``id-1`` = configTurntable index. The prize content lives in the
client config (configTurntable), NOT in the WS reply — so this layer only reports
which slot was hit. Spinning is win-on-spin: there is no separate claim step.

Only free / already-accumulated spins are consumed: ``spin_all_free`` reads the
info ``num`` once and spins that many times. Topping up spins by watching an ad
is an SDK/native flow that cannot be done over this pure-WS path, so it is not
attempted.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

CMD_INFO = 0x1603   # 5635 ad.ad_wheel_info_c2s/s2c (empty request body)
CMD_SPIN = 0x1604   # 5636 ad.ad_wheel_spin_c2s/s2c (empty request body)
CMD_ERROR = 0x0201  # generic server error/notice channel

# Live (小寶 2026-06-09): a spin is answered with a 0x0201 notice (code 173) and
# the ad_wheel_spin reply (0x1604) does NOT arrive as a synchronous call reply —
# waiting only for 0x1604 times out. 轉盤 appears to be ad-gated (the free `num`
# spins still want an ad SDK flow that pure WS cannot drive), so a spin over WS is
# declined. We surface that as "not awarded" (None) instead of crashing.
ERR_SPIN_DECLINED = 173

_DEFAULT_SPACING = 0.3
_DEFAULT_MAX_SPINS = 50


@dataclass(frozen=True)
class TurntableInfo:
    """ad_wheel_info_s2c — remaining spins and cooldown expiry."""

    num: int   # #2 — remaining free / accumulated spins
    cd: int    # #1 — cooldown expiry (unix seconds)


def parse_info(body: bytes) -> TurntableInfo:
    """ad_wheel_info_s2c {cd#1, num#2} — cd FIRST, then num."""
    f = codec.walk_dict(body)
    return TurntableInfo(num=_as_int(f.get(2)), cd=_as_int(f.get(1)))


def parse_spin(body: bytes) -> int:
    """ad_wheel_spin_s2c {id#1} — return the winning slot id (id-1 = config idx)."""
    return _as_int(codec.walk_dict(body).get(1))


def read_info(client: WSGameClient, *, timeout: float | None = None) -> TurntableInfo:
    """Send ad_wheel_info (empty body) and parse the reply."""
    return parse_info(client.call(CMD_INFO, b"", timeout=timeout))


def spin_once(client: WSGameClient, *, timeout: float | None = None) -> Optional[int]:
    """Send ad_wheel_spin (empty body); return the winning slot id, or None if the
    server declined the spin.

    The server replies with EITHER the spin result (0x1604 {id}) OR an 0x0201
    notice (live-observed code 173 = 轉盤需看廣告, not drivable over pure WS). It is
    NOT guaranteed to send 0x1604 to the call waiter, so waiting only for 0x1604
    times out. We wait for either cmd and return None on a 0x0201 notice or a
    timeout, so the caller stops gracefully instead of crashing the whole task.
    """
    try:
        cmd, body = client.call_for(CMD_SPIN, b"",
                                    expect_cmds=(CMD_SPIN, CMD_ERROR), timeout=timeout)
    except WSTimeoutError:
        logger.warning("ws_token turntable: spin got no 0x1604/0x0201 reply (timeout); "
                       "轉盤可能需看廣告, WS 無法執行")
        return None
    if cmd == CMD_ERROR:
        code = _as_int(codec.walk_dict(body).get(1))
        logger.warning("ws_token turntable: spin declined 0x0201 code=%s "
                       "(轉盤可能需看廣告, WS 無法執行)", code)
        return None
    return parse_spin(body)


def spin_all_free(
    client: WSGameClient,
    *,
    spacing: float = _DEFAULT_SPACING,
    max_spins: int = _DEFAULT_MAX_SPINS,
) -> dict:
    """Spin every free / accumulated turn, then stop.

    Reads ``num`` once and spins that many times (capped at ``max_spins``).
    Only consumes spins the account already has — ad-funded top-ups are not
    attempted (not possible over pure WS). Returns ``{spun, results}`` where
    ``results`` is the list of winning slot ids.
    """
    info = read_info(client)
    budget = min(info.num, max_spins) if info.num > 0 else 0
    results: list[int] = []
    declined = False
    for _ in range(budget):
        slot = spin_once(client)
        if slot is None:
            # Server declined (0x0201 / no reply). Stop — re-trying just wastes
            # spins and spams timeouts (轉盤 likely ad-gated, unusable over WS).
            declined = True
            break
        results.append(slot)
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token turntable: num=%d spun=%d declined=%s (max=%d)",
                info.num, len(results), declined, max_spins)
    return {"spun": len(results), "results": results, "declined": declined}


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

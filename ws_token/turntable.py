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

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_INFO = 0x1603   # 5635 ad.ad_wheel_info_c2s/s2c (empty request body)
CMD_SPIN = 0x1604   # 5636 ad.ad_wheel_spin_c2s/s2c (empty request body)

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


def spin_once(client: WSGameClient, *, timeout: float | None = None) -> int:
    """Send ad_wheel_spin (empty body) and return the winning slot id."""
    return parse_spin(client.call(CMD_SPIN, b"", timeout=timeout))


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
    for _ in range(budget):
        results.append(spin_once(client))
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token turntable: num=%d spun=%d (max=%d)",
                info.num, len(results), max_spins)
    return {"spun": len(results), "results": results}


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

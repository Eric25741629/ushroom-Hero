"""Shared parsers for the 分流 (load-distribution) device-config knobs.

Two device-config values steer how device threads are spread out in time:

- ``wake_hour_parity`` — pins a device's wake hour to even / odd local hours
  (so only half the fleet is active in any given hour).
- ``wake_minute_offset`` — staggers devices that share an hour onto a fixed
  minute (:00 / :05 / :15 …).

Both ``runtime_services.sleep_service`` (per-cycle wake alignment) and
``runtime_services.startup_sleep`` (deterministic launch order) read the same
two config values, so the *parsing* — even/odd string mapping, the int
validity range, and the ``bool`` rejection — lives here once. Callers map the
canonical "unset" (``None``) result to whatever sentinel they need:

- sleep_service uses ``None`` directly = "no constraint".
- startup_sleep maps ``None`` to a sort sentinel (parity → 2 = sorts last,
  offset → 0 = sorts first).

Pure, stdlib-only: imports nothing from this package, so it is safe to import
from anywhere in ``runtime_services`` without circular-import risk.
"""
from __future__ import annotations

from typing import Optional


def parse_hour_parity(value) -> Optional[int]:
    """Map a device-config value to an hour-parity constraint.

    ``'even'`` / ``0`` -> 0, ``'odd'`` / ``1`` -> 1; anything else
    (including ``None``) -> ``None`` (no constraint). ``bool`` is rejected so
    ``True`` / ``False`` don't sneak in as 1 / 0.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "even":
            return 0
        if v == "odd":
            return 1
        return None
    if isinstance(value, int) and value in (0, 1):
        return value
    return None


def parse_minute_offset(value) -> Optional[int]:
    """Map a device-config value to a fixed wake-minute offset.

    A non-negative int (e.g. 0, 5, 15) -> that minute; anything else
    (including ``None`` and ``bool``) -> ``None`` (no fixed offset).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None

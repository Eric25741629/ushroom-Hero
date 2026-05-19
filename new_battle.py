"""Backward-compat shim for the ``battle`` package.

Historic callers used ``import new_battle`` and ``new_battle.BattleManager``
etc. As of 2026-05-19 the code lives under ``battle/`` — keep this shim so
existing imports work; new code should ``from battle import …`` directly.
"""

from battle import *  # noqa: F401, F403
from battle._helpers import _compute_biweekly_slot_key  # re-export private used by tests  # noqa: F401

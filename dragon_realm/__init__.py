"""龍骸聖域 (dragon_realm / ActivityLhsy) 自動化 — H5-first，RPC-driven。

Not wired into runtime by default. Opt in via the ``dragon_realm_enabled`` flag
(per-device overrides global), gated by :func:`use_dragon_realm`.

Design: docs/superpowers/specs/2026-06-04-dragon-realm-automation-design.md
Protocol: docs/protocol/DRAGON_REALM_SCHEMA.md
"""
from __future__ import annotations


def use_dragon_realm(ip: str, config: dict) -> bool:
    """Feature flag: per-device ``dragon_realm_enabled`` overrides
    ``global.dragon_realm_enabled``. Defaults off."""
    dev = (config.get("devices") or {}).get(ip) or {}
    if "dragon_realm_enabled" in dev:
        return bool(dev["dragon_realm_enabled"])
    return bool((config.get("global") or {}).get("dragon_realm_enabled", False))

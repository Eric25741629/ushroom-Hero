# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

ARENA_MODES = ("animation", "local_sim", "remote_calc", "pure_ws")
WANSHEN_MODES = ("animation", "local_sim", "remote_calc", "pure_ws")
MIN_ARENA_GAP_SEC = 7.0
DEFAULT_ARENA_GAP_SEC = 7.0

_DEFAULT_GLOBAL = {
    "enabled": True,  # pure_ws 預設用免洗 B
    "mode": "ephemeral",  # ephemeral=全新瀏覽器無 profile；cdp=連既有 CDP
    "http_host": "127.0.0.1",
    "http_port": 18765,
    "cdp_port": 0,  # mode=cdp 時用；0=不用
    "timeout_sec": 15.0,
    "game_url": "https://mushroomh5.acenetgame.com/",
    "headless": True,
    "ready_timeout_sec": 90.0,
}


def coerce_battle_mode(raw: Any, default: str = "animation") -> str:
    s = str(raw or default).strip().lower()
    return s if s in ARENA_MODES else default


def coerce_wanshen_battle_mode(raw: Any, default: str = "animation") -> str:
    s = str(raw or default).strip().lower()
    return s if s in WANSHEN_MODES else default


def coerce_arena_gap_sec(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = DEFAULT_ARENA_GAP_SEC
    if v < MIN_ARENA_GAP_SEC:
        return MIN_ARENA_GAP_SEC
    if v > 120:
        return 120.0
    return v


def get_battle_calc_global(cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """讀 global.battle_calc（可傳入已 load 的 global 段）。"""
    out = dict(_DEFAULT_GLOBAL)
    if cfg is None:
        try:
            import config_manager

            cfg = config_manager.get_global_config()
        except Exception:
            cfg = {}
    raw = (cfg or {}).get("battle_calc") or {}
    if not isinstance(raw, dict):
        return out
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    if raw.get("http_host"):
        out["http_host"] = str(raw["http_host"])
    mode = str(raw.get("mode") or out["mode"]).strip().lower()
    out["mode"] = mode if mode in ("ephemeral", "cdp") else "ephemeral"
    if raw.get("game_url"):
        out["game_url"] = str(raw["game_url"]).strip()
    if "headless" in raw:
        out["headless"] = bool(raw["headless"])
    try:
        if "http_port" in raw:
            out["http_port"] = int(raw["http_port"])
        if "cdp_port" in raw:
            out["cdp_port"] = int(raw["cdp_port"])
        if "timeout_sec" in raw:
            out["timeout_sec"] = float(raw["timeout_sec"])
        if "ready_timeout_sec" in raw:
            out["ready_timeout_sec"] = float(raw["ready_timeout_sec"])
    except (TypeError, ValueError):
        pass
    return out

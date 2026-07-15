# -*- coding: utf-8 -*-
"""web_h5 戰鬥加速：走官方 speed_scale → battleMain.timeScale 管線。

官方看廣告 2 倍速：
  ChapterDataCache.updateSpeedScale(2)
  → emit BATTLE_SEED
  → Running 時 battleMain.timeScale = speed_scale

本模組在 page 上掛輕量 interval，把 speed_scale / timeScale 維持在設定值
（預設 4）。不注入 chapter.onUpdate（避免破壞 DPS 結算）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SCALE = 4.0
_MIN_SCALE = 1.0
_MAX_SCALE = 10.0

# Idempotent installer. scale<=1 clears the keeper.
_INSTALL_JS = r"""
(scale) => {
  const s = Math.max(1, Math.min(10, Number(scale) || 1));
  window.__bot_battle_speed = s;
  if (s <= 1.0001) {
    if (window.__bot_battle_speed_iv) {
      clearInterval(window.__bot_battle_speed_iv);
      window.__bot_battle_speed_iv = null;
    }
    try {
      if (typeof chapterDataCache !== 'undefined') {
        if (typeof chapterDataCache.updateSpeedScale === 'function')
          chapterDataCache.updateSpeedScale(1);
        else
          chapterDataCache.speed_scale = 1;
      }
      if (typeof battleMain !== 'undefined' && battleMain)
        battleMain.timeScale = 1;
    } catch (e) {}
    return 'cleared';
  }
  if (window.__bot_battle_speed_iv) {
    // already running; just update target
    return 'updated:' + s;
  }
  const apply = () => {
    try {
      const scale = window.__bot_battle_speed || 1;
      if (scale <= 1) return;
      if (typeof chapterDataCache !== 'undefined' && chapterDataCache) {
        if (typeof chapterDataCache.updateSpeedScale === 'function') {
          if (Number(chapterDataCache.speed_scale) !== scale)
            chapterDataCache.updateSpeedScale(scale);
        } else {
          chapterDataCache.speed_scale = scale;
        }
      }
      if (typeof battleMain !== 'undefined' && battleMain) {
        // Official path only applies when Running; force timeScale so
        // timed chapters (e.g. WorldBoss) still get 4x simulation rate.
        if (Number(battleMain.timeScale) !== scale)
          battleMain.timeScale = scale;
      }
    } catch (e) {
      window.__bot_battle_speed_err = String(e);
    }
  };
  apply();
  window.__bot_battle_speed_iv = setInterval(apply, 250);
  return 'installed:' + s;
}
"""


def coerce_battle_speed_scale(value: Any, default: float = _DEFAULT_SCALE) -> float:
    try:
        s = float(value)
    except (TypeError, ValueError):
        s = float(default)
    if s <= 0:
        s = 1.0
    return max(_MIN_SCALE, min(_MAX_SCALE, s))


def install_battle_speed(page, scale: float = _DEFAULT_SCALE) -> Optional[str]:
    """Install/update battle speed keeper on a Playwright page. Returns status string."""
    if page is None:
        return None
    scale = coerce_battle_speed_scale(scale)
    try:
        return page.evaluate(_INSTALL_JS, scale)
    except Exception as exc:
        logger.debug("install_battle_speed failed: %s", exc)
        return None


def ensure_battle_speed_on_device(device, scale: Optional[float] = None) -> Optional[str]:
    """Best-effort for PlaywrightGameDevice (or any object with ._page / .cfg)."""
    if device is None:
        return None
    page = getattr(device, "_page", None)
    if page is None:
        return None
    cfg = getattr(device, "cfg", None) or {}
    if scale is None:
        scale = cfg.get("battle_speed_scale", _DEFAULT_SCALE)
    return install_battle_speed(page, scale)

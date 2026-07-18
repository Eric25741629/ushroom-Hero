# -*- coding: utf-8 -*-
"""免洗 B 計算機：開全新瀏覽器（不帶 user profile / 不登入帳號）。

只載入遊戲 H5 runtime，供 ``BattleMainServer`` + protoRoot 解 combat body。
不讀 ``playwright_profile``、不帶 auth_state。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_GAME_URL = "https://mushroomh5.acenetgame.com/"

# 版本提示 / 維護窗等會擋載入；點「確定/刷新/關閉」清掉。
_DISMISS_JS = r"""
() => {
  const hits = [];
  const want = ['確定', '刷新', '关闭', '關閉', '知道了', 'OK'];
  const walk = (n, d) => {
    if (!n || d > 14) return;
    let lab = '';
    for (const c of (n._components || [])) {
      if (c && typeof c.string === 'string' && c.string) lab = c.string;
    }
    if (n.active && want.some(w => lab.includes(w))) {
      hits.push(n);
    }
    (n.children || []).forEach(ch => walk(ch, d + 1));
  };
  if (typeof cc === 'undefined' || !cc.director) return { clicked: 0 };
  walk(cc.director.getScene(), 0);
  let clicked = 0;
  for (const n of hits.slice(0, 4)) {
    try { n.emit('click', n); clicked++; } catch (e) {}
  }
  return { clicked, labels: hits.slice(0, 4).map(() => true) };
}
"""

_READY_JS = r"""
async () => {
  if (typeof System === 'undefined') return { ok: false, stage: 'no_System' };
  if (!window.netManager || !window.netManager.protoRoot) {
    return { ok: false, stage: 'no_protoRoot' };
  }
  try {
    await System.import('chunks:///_virtual/BattleMainServer.ts');
    await System.import('chunks:///_virtual/BattleData.ts');
    await System.import('chunks:///_virtual/BattleDataFill.ts');
  } catch (e) {
    return { ok: false, stage: 'import', err: String(e && e.message || e) };
  }
  try {
    const Type = window.netManager.protoRoot.lookupType('arena.arena_combat_s2c');
    if (!Type) return { ok: false, stage: 'no_arena_type' };
  } catch (e) {
    return { ok: false, stage: 'lookup', err: String(e && e.message || e) };
  }
  return { ok: true, stage: 'ready' };
}
"""


def _launch_browser(pw, headless: bool):
    """優先 Chrome channel（與 bot 一致），失敗退 Playwright chromium。"""
    args = [
        "--disable-dev-shm-usage",
        "--no-default-browser-check",
        "--no-first-run",
    ]
    try:
        return pw.chromium.launch(channel="chrome", headless=bool(headless), args=args)
    except Exception as e:
        logger.warning("ephemeral B chrome channel failed (%s), use bundled chromium", e)
        return pw.chromium.launch(headless=bool(headless), args=args)


def launch_ephemeral_b(
    *,
    game_url: str = DEFAULT_GAME_URL,
    headless: bool = True,
    timeout_s: float = 120.0,
    viewport: Optional[Tuple[int, int]] = (540, 960),
) -> Tuple[Any, Any, Any]:
    """啟動全新 Chromium/Chrome（無 persistent profile）。

    Returns:
        (playwright, browser, page) — 呼叫端 ``close_ephemeral(pw, browser)``
    """
    from playwright.sync_api import sync_playwright

    url = (game_url or DEFAULT_GAME_URL).strip() or DEFAULT_GAME_URL
    pw = sync_playwright().start()
    browser = None
    try:
        browser = _launch_browser(pw, headless)
        ctx_kwargs: dict = {}
        if viewport:
            ctx_kwargs["viewport"] = {
                "width": int(viewport[0]),
                "height": int(viewport[1]),
            }
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=int(max(timeout_s, 30) * 1000))
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            # 清版本/維護彈窗
            try:
                if page.evaluate("() => typeof cc !== 'undefined'"):
                    page.evaluate(_DISMISS_JS)
            except Exception:
                pass
            try:
                last = page.evaluate(_READY_JS)
            except Exception as e:  # noqa: BLE001
                last = {"ok": False, "stage": "evaluate", "err": str(e)}
            if isinstance(last, dict) and last.get("ok"):
                logger.info("ephemeral B ready url=%s headless=%s", url, headless)
                return pw, browser, page
            time.sleep(1.5)
        raise RuntimeError(f"ephemeral B not ready within {timeout_s}s: {last}")
    except Exception:
        close_ephemeral(pw, browser)
        raise


def close_ephemeral(pw, browser) -> None:
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass
    try:
        if pw is not None:
            pw.stop()
    except Exception:
        pass

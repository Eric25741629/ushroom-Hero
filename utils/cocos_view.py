"""web_h5 直開 cocos 面板：`uiMgr.openView(name)` 取代「點選單 → 捲清單 → OCR 找入口」。

為什麼要有這層：副本清單會隨版本變長（2026-07-21 新增 4 個副本），寫死捲動次數的
OCR 進場路徑會整批失效（萬神試煉 / 雲纏天梯同時中招）。直開 view 不受清單順序影響。

ADB 沒有 `uiMgr`（那是頁面內的 cocos 物件），仍走原本的 OCR 路徑。
"""

from __future__ import annotations

import time
from typing import Any

from utils.logging_utils import logger

_OPEN_JS = r"""
(name) => {
  const um = window.uiMgr;
  if (!um || typeof um.openView !== 'function') return 'no uiMgr.openView';
  try { um.openView(name); return ''; } catch (e) { return String(e); }
}
"""

_ACTIVE_JS = r"""
(name) => {
  let hit = false;
  const w = (n) => {
    if (hit || !n) return;
    if ((n.name || '') === name && n.activeInHierarchy) { hit = true; return; }
    (n.children || []).forEach(w);
  };
  w(cc.director.getScene());
  return hit;
}
"""

_CLOSE_JS = r"""
(name) => {
  const um = window.uiMgr;
  if (!um || typeof um.close !== 'function') return false;
  try { um.close(name); return true; } catch (e) { return false; }
}
"""


def is_open(page: Any, view_name: str) -> bool:
    """該 view 是否已經開著（節點存在且 activeInHierarchy）。"""
    try:
        return bool(page.evaluate(_ACTIVE_JS, view_name))
    except Exception as e:
        logger.warning("[cocos_view] is_open(%s) 例外: %s", view_name, e)
        return False


def open_view(page: Any, view_name: str, timeout: float = 8.0) -> bool:
    """開指定 cocos view 並等它 active。已經開著就直接回 True。"""
    if is_open(page, view_name):
        return True
    try:
        err = page.evaluate(_OPEN_JS, view_name)
    except Exception as e:
        logger.warning("[cocos_view] openView(%s) 例外: %s", view_name, e)
        return False
    if err:
        logger.warning("[cocos_view] openView(%s) 失敗: %s", view_name, err)
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_open(page, view_name):
            return True
        time.sleep(0.3)
    logger.warning("[cocos_view] openView(%s) 後未 active", view_name)
    return False


def close_view(page: Any, view_name: str) -> bool:
    """關掉指定 view（回主頁用）。"""
    try:
        return bool(page.evaluate(_CLOSE_JS, view_name))
    except Exception as e:
        logger.warning("[cocos_view] close(%s) 例外: %s", view_name, e)
        return False


def web_page(d: Any) -> Any:
    """web_h5 裝置 → Playwright page；其他後端 → None（呼叫端據此走 OCR）。"""
    page = getattr(d, "_page", None)
    if getattr(d, "backend_kind", None) == "web_h5" and page is not None:
        return page
    return None

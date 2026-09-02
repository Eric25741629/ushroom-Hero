"""排行榜活動任務。

目前 UI fallback：坐騎衝刺 (記錄名稱 `衝刺-發條`)；WS-first 主路徑見
`ws_token.mount_sprint`。
活動每 4 週一次,於活動週的週二~週三 (台灣時間週三 22:00 後結算) 餵養
「無限時發條」給坐騎衝排名,一個活動週只執行一次 (成功才記錄時間)。

跨後端：
  - web_h5 (Playwright/CDP)：座標點擊 + cocos `EditBox` 設定數量。
  - adb (uiautomator2)：同一組座標 + `adb shell input text` 輸入數量。
兩後端的遊戲畫面皆為 540x960,座標可共用;每一步都用 OCR 驗證,
失敗即中止且不記錄,下個迴圈重試。

座標與流程於 2026-05-25 在 5554(web) 與實機(adb) 實測確認,
詳見 docs/protocol/MOUNT_SPRINT.md。
"""

import datetime
import logging
import time

import bot_state
import config_manager
import img_tools
import json_manager
from ws_token import mount_sprint as ws_mount_sprint

logger = logging.getLogger(__name__)

_TPE = datetime.timezone(datetime.timedelta(hours=8))

# 驗證過的座標 (540x960 畫布,web_h5 與 adb 皆適用)
MOUNT_ENTRY = (221, 656)   # 主頁坐騎入口 (mountEquipItem,在神器左邊)
UPGRADE_TAB = (86, 808)    # 坐騎升級分頁 (必須先選,否則可能停在賦能分頁)
FEED_BTN = (360, 748)      # 一鍵餵養 (btnTen)
QTY_EDITBOX = (270, 478)   # 數量輸入框
USE_BTN = (270, 575)       # 使用 (btnUse)
CONFIRM_OK = (371, 556)    # 確定 (MessageView/.../btnEnsure)
CLOSE_BTN = (270, 896)     # 關閉坐騎頁 (btnClose)

SPRINT_RECORD = ws_mount_sprint.SPRINT_RECORD
ALLOWED_WEEKDAYS = list(ws_mount_sprint.ALLOWED_WEEKDAYS)
SPRINT_CLOSE_WEEKDAY = ws_mount_sprint.SPRINT_CLOSE_WEEKDAY
SPRINT_CLOSE_HOUR = ws_mount_sprint.SPRINT_CLOSE_HOUR
DEFAULT_QUANTITY = ws_mount_sprint.DEFAULT_QUANTITY

# web_h5: 設定 ItemUseView 數量框並觸發 commit 事件,讓遊戲更新內部數量。
_SET_QTY_JS = r"""
(val) => {
  if (typeof cc === 'undefined' || !cc.director) return "err:no cc";
  const sc = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || "") === p);
      if (!n) return null;
    }
    return n;
  };
  const v = find(sc, ["UIRoot", "NormalView", "ItemUseView"]);
  if (!v) return "err:no ItemUseView";
  let eb = null, node = null;
  const walk = (n) => {
    const e = n.getComponent && n.getComponent("cc.EditBox");
    if (e) { eb = e; node = n; }
    (n.children || []).forEach(walk);
  };
  walk(v);
  if (!eb) return "err:no EditBox";
  eb.string = String(val);
  try { node.emit("text-changed", eb); } catch (e) {}
  try { node.emit("editing-did-ended", eb); } catch (e) {}
  return "ok:" + eb.string;
}
"""


def is_mount_sprint_open(now=None):
    """坐騎衝刺是否在活動開放窗內：週二全天 + 週三 22:00(台灣)前。

    只判斷「窗內」，不含 4 週週期判斷(那是 should_execute_cycle 的事)。
    park_spring 與 dashboard 徽章共用此判定，避免活動結束後徽章繼續顯示。
    """
    return ws_mount_sprint.is_open(now)


def park_spring(d, ip, now=None):
    """舊版 UI fallback：缺少伺服器活動型別確認時安全跳過。"""
    # 本地 4 週記錄只能表示裝置曾做過什麼，不能證明同服本週的活動型別。
    # 坐騎衝刺僅由 ws_token.runner 的 server-authoritative WS 路徑執行。
    logger.info(f"[{ip}] 坐騎衝刺 UI fallback 已停用：缺少伺服器活動型別確認。")

def _run_feed_flow(d, ip, quantity):
    """執行餵養流程,每步以 OCR 驗證。全程成功回 True,否則中止回 False。"""
    try:
        # 1. 開坐騎頁
        _tap(d, *MOUNT_ENTRY)
        time.sleep(2.0)
        if not _verify(d, "坐騎升級", "坐騎賦能", "升一級"):
            return _abort(d, ip, "未進入坐騎頁")

        # 2. 切到「坐騎升級」分頁 (預設可能停在賦能分頁)
        _tap(d, *UPGRADE_TAB)
        time.sleep(1.2)
        if not _verify(d, "升一級", "一鍵", "祝福值"):
            return _abort(d, ip, "未在坐騎升級分頁")

        # 3. 一鍵餵養 → 開啟數量視窗
        _tap(d, *FEED_BTN)
        time.sleep(1.6)
        if not _verify(d, "發條"):
            return _abort(d, ip, "未開啟餵養視窗")

        # 4. 輸入數量
        if not _set_quantity(d, ip, quantity):
            return _abort(d, ip, "數量輸入失敗")

        # 5. 使用 → 出現確認視窗
        _tap(d, *USE_BTN)
        time.sleep(1.8)
        if not _verify(d, "消耗", "確定"):
            return _abort(d, ip, "使用後未出現確認視窗")

        # 6. 確定 (舊版漏掉此步,點在 271/331 沒中真正的 371,556)
        _tap(d, *CONFIRM_OK)
        time.sleep(2.0)

        # 7. 驗證確認視窗已消失 (確定生效)
        if _verify(d, "是否消耗"):
            return _abort(d, ip, "確認視窗仍在,確定可能未生效")

        # 8. 關閉坐騎頁
        _tap(d, *CLOSE_BTN)
        time.sleep(1.5)
        return True

    except Exception as e:
        logger.error(f"[{ip}] 坐騎衝刺流程例外: {e}")
        _safe_screenshot(d, ip)
        return False


def _set_quantity(d, ip, quantity):
    """設定餵養數量。web_h5 走 cocos EditBox;adb 走 input text + ENTER。"""
    page = getattr(d, "_page", None)
    if page is not None:
        # web_h5: 直接設定 cocos EditBox 並觸發 commit 事件
        res = page.evaluate(_SET_QTY_JS, str(int(quantity)))
        ok = isinstance(res, str) and res.startswith("ok")
        if not ok:
            logger.warning(f"[{ip}] web 設定數量失敗: {res}")
        return ok

    # adb: u2 send_keys 在 H5 webview 不可靠,改用 adb shell input。
    _tap(d, *QTY_EDITBOX)
    time.sleep(0.8)
    d.shell("input keyevent 123")          # MOVE_END
    for _ in range(8):                     # 清掉預設值 (最多 4 位數 + 餘裕)
        d.shell("input keyevent 67")       # DEL
    d.shell(f"input text {int(quantity)}")
    time.sleep(0.4)
    d.shell("input keyevent 66")           # ENTER：commit 數量並收起鍵盤
    time.sleep(0.8)
    return True


def _tap(d, x, y):
    d.click(x, y)


def _ocr_texts(d):
    try:
        img = d.screenshot(format="opencv")
    except TypeError:
        img = d.screenshot()
    return img_tools.get_all_text(img) or []


def _verify(d, *substrings):
    """畫面 OCR 中是否出現任一指定子字串 (短字串以抗 OCR 漏字)。"""
    texts = _ocr_texts(d)
    return any(any(s in t for t in texts) for s in substrings)


def _abort(d, ip, reason):
    logger.warning(f"[{ip}] 坐騎衝刺中止: {reason}")
    _safe_screenshot(d, ip)
    return False


def _safe_screenshot(d, ip):
    path = f"debug_mount_sprint_{ip}.jpg"
    try:
        page = getattr(d, "_page", None)
        if page is not None:
            page.screenshot(path=path)
        else:
            d.screenshot(path)
    except Exception:
        pass

import time
import numpy as np
import os
import re
import img_tools
from tools import click_white
from utils.logging_utils import logger


_GOODS_REWARD_VIEWS = ("GoodsGetView", "GoodsGetView2")
_HOME_IDLE_CHEST_PATH = (
    "UIRoot", "NormalView", "MainView", "subRoots", "boxRoot", "btnBox"
)
_OFFLINE_REWARD_BASE_PATH = (
    "UIRoot", "NormalView", "outlinePopView", "root", "content", "btnStart"
)
_OFFLINE_REWARD_QUICK_2H_PATH = (
    "UIRoot", "NormalView", "outlinePopView", "root", "content", "btnAd"
)


def _cocos_node_action(page, path, action: str) -> dict:
    """在已確認的 Cocos 絕對路徑上讀取或觸發節點。"""
    try:
        return page.evaluate(r"""([path, action]) => {
          if (typeof cc === 'undefined' || !cc.director)
            return {ok:false, err:'cocos_unavailable'};
          const scene = cc.director.getScene();
          if (!scene) return {ok:false, err:'scene_unavailable'};
          let node = scene;
          for (const part of path) {
            if (!node || !node.children) return {ok:false, err:'path_not_found'};
            node = node.children.find(child => (child.name || '') === part);
            if (!node) return {ok:false, err:'path_not_found'};
          }
          if (action === 'inspect') {
            const labels = [];
            const stack = [node];
            while (stack.length) {
              const current = stack.pop();
              if (!current) continue;
              const label = current.getComponent && current.getComponent(cc.Label);
              if (label && String(label.string || '').trim())
                labels.push(String(label.string).trim());
              (current.children || []).forEach(child => stack.push(child));
            }
            return {
              ok:true,
              active:!!node.activeInHierarchy,
              clickable:!!(
                (node.hasEventListener && node.hasEventListener('click')) ||
                (node.getComponent && node.getComponent(cc.Button))
              ),
              labels
            };
          }
          if (!node.activeInHierarchy)
            return {ok:false, err:'node_not_active'};
          const clickable = !!(
            (node.hasEventListener && node.hasEventListener('click')) ||
            (node.getComponent && node.getComponent(cc.Button))
          );
          if (!clickable || typeof node.emit !== 'function')
            return {ok:false, err:'click_listener_missing'};
          node.emit('click', node);
          return {ok:true, node:node.name};
        }""", [list(path), action]) or {}
    except Exception as exc:
        logger.warning("Cocos 節點操作例外 path=%s action=%s: %s", path, action, exc)
        return {ok:False, err:str(exc)}


def _view_active(page, view_names=_GOODS_REWARD_VIEWS) -> bool:
    """透過 uiMgr 判斷已知 view 是否 active，不讀畫面文字。"""
    try:
        return bool(page.evaluate(r"""(names) => {
          const um = window.uiMgr;
          if (!um || typeof um.getView !== 'function') return false;
          return names.some(name => {
            try {
              const view = um.getView(name);
              return !!(view && view.node && view.node.active);
            } catch (e) { return false; }
          });
        }""", list(view_names)))
    except Exception as exc:
        logger.warning("Cocos view 狀態查詢例外 %s: %s", view_names, exc)
        return False


def _wait_for_view(page, view_names, *, active: bool = True, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if _view_active(page, view_names) is active:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _parse_quick_remaining(labels) -> int | None:
    """讀 btnAd 的 ``(N/3)``，N 是今日剩餘次數。"""
    for value in labels or ():
        text = str(value).replace("（", "(").replace("）", ")")
        match = re.search(r"\(\s*(\d+)\s*/\s*\d+\s*\)", text)
        if match:
            return int(match.group(1))
    return None


def _parse_quick_cooldown(labels) -> int | None:
    """讀 btnAd 的 MM:SS / H:MM:SS 倒數秒數。"""
    for value in labels or ():
        match = re.search(r"(?:(\d+)\s*:)?(\d+)\s*:\s*(\d{2})", str(value))
        if not match:
            continue
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    return None


def _quick_2h_decision(state: dict) -> tuple[str, str]:
    """回傳 (decision, reason)，避免未知狀態誤點廣告按鈕。"""
    if not state.get("active"):
        return "skip", "button_inactive"
    if not state.get("clickable"):
        return "skip", "click_listener_missing"
    labels = state.get("labels") or ()
    remaining = _parse_quick_remaining(labels)
    if remaining is not None and remaining <= 0:
        return "skip", "daily_quota_exhausted"
    cooldown = _parse_quick_cooldown(labels)
    if cooldown is not None and cooldown > 0:
        return "skip", f"cooldown_{cooldown}s"
    if remaining is None and cooldown is None:
        return "skip", "state_unreadable"
    return "claim", "eligible"


def _read_quick_2h_state(page) -> dict:
    return _cocos_node_action(page, _OFFLINE_REWARD_QUICK_2H_PATH, "inspect")


def _claim_quick_2h(page) -> str:
    """嘗試領取左側 2 小時收益，回傳 claimed/skipped/failed。"""
    state = _read_quick_2h_state(page)
    decision, reason = _quick_2h_decision(state)
    if decision != "claim":
        logger.info("Cocos 2 小時收益跳過: %s", reason)
        return "skipped"

    result = _cocos_node_action(
        page, _OFFLINE_REWARD_QUICK_2H_PATH, "click"
    )
    if not result.get("ok"):
        logger.warning("Cocos 2 小時收益點擊失敗: %s", result.get("err"))
        return "failed"
    if not _wait_for_view(page, _GOODS_REWARD_VIEWS, timeout=3.0):
        logger.warning("Cocos 2 小時收益點擊後沒有恭喜獲得 popup")
        return "failed"
    if not close_goods_reward(page):
        return "failed"
    logger.info("Cocos 已領取 2 小時收益")
    return "claimed"


def close_goods_reward(page, timeout: float = 5.0) -> bool:
    """用 ``uiMgr.close`` 關閉「恭喜獲得」獎勵 popup。

    這個 popup 的標題是圖片，不一定存在可讀的 ``cc.Label``，所以不能
    用 OCR/文字搜尋當判斷依據。先用已確認的 view name 關閉，再輪詢 node
    inactive 作為結果證據；web_h5 失敗時回 False，不回 OCR。
    """
    views = list(_GOODS_REWARD_VIEWS)
    try:
        result = page.evaluate(r"""(views) => {
          const um = window.uiMgr;
          if (!um || typeof um.getView !== 'function' || typeof um.close !== 'function')
            return {found:false, closed:[], err:'uiMgr_close_unavailable'};
          const found = [], closed = [];
          for (const name of views) {
            try {
              const v = um.getView(name);
              if (v && v.node && v.node.active) {
                found.push(name);
                um.close(name);
                closed.push(name);
              }
            } catch (e) {}
          }
          return {found: found.length > 0, closed};
        }""", views) or {}
    except Exception as exc:
        logger.warning("Cocos 關閉恭喜獲得 popup 例外: %s", exc)
        return False

    if not result.get("found"):
        # popup 可能已被其他流程剛好關掉；這是成功的冪等結果。
        return True

    deadline = time.monotonic() + max(0.5, float(timeout))
    while time.monotonic() < deadline:
        try:
            active = page.evaluate(r"""(views) => {
              const um = window.uiMgr;
              if (!um || typeof um.getView !== 'function') return [];
              return views.filter(name => {
                try {
                  const v = um.getView(name);
                  return !!(v && v.node && v.node.active);
                } catch (e) { return false; }
              });
            }""", views) or []
        except Exception as exc:
            logger.warning("Cocos 驗證恭喜獲得 popup 關閉失敗: %s", exc)
            return False
        if not active:
            logger.info("Cocos 已關閉恭喜獲得 popup: %s", result.get("closed"))
            return True
        time.sleep(0.2)

    logger.warning("Cocos 恭喜獲得 popup 關閉 timeout: %s", result.get("closed"))
    return False


def _claim_base_reward(page) -> bool:
    """點擊右側一般「領取」，並關閉隨後出現的獎勵 popup。"""
    result = _cocos_node_action(page, _OFFLINE_REWARD_BASE_PATH, "click")
    if not result.get("ok"):
        logger.warning("Cocos 領取放置獎勵失敗: %s", result.get("err"))
        return False
    if _wait_for_view(page, _GOODS_REWARD_VIEWS, timeout=3.0):
        if not close_goods_reward(page):
            return False
    else:
        # 點擊已由 Cocos listener 接收，但部分帳號可能沒有可展示的物品窗；
        # 不因缺少圖片 popup 把已送出的領取請求重送。
        logger.info("Cocos 一般放置獎勵已點擊，未觀察到恭喜獲得 popup")
    logger.info("Cocos 已領取一般放置獎勵: %s", result)
    return True


def _claim_reward_dialog(page) -> bool:
    """處理已開啟的 outlinePopView：2 小時收益先行，再領一般收益。"""
    if not _wait_for_view(page, ("outlinePopView",), timeout=1.0):
        logger.warning("Cocos 離線獎勵 popup 尚未開啟")
        return False
    quick_result = _claim_quick_2h(page)
    if quick_result == "failed":
        return False
    return _claim_base_reward(page)


def claim_open_reward(page) -> bool:
    """領取登入後已開啟的離線獎勵 popup，完全走 Cocos。"""
    return _claim_reward_dialog(page)


def run_web_idle_reward(page) -> dict:
    """開啟主頁寶箱並整合 2 小時收益、一般放置收益與獎勵 popup。

    這是 web_h5 的 #1 原生入口。所有狀態透過 Cocos node/view 取得，
    不在 H5 失敗時回退 OCR；ADB 呼叫端仍使用下方 legacy ``reward``。
    """
    report = {
        "opened": False,
        "quick_2h": "not_attempted",
        "base": False,
        "success": False,
    }
    if _view_active(page, _GOODS_REWARD_VIEWS):
        if not close_goods_reward(page):
            report["error"] = "goods_popup_not_closed"
            return report
    if not _view_active(page, ("outlinePopView",)):
        result = _cocos_node_action(page, _HOME_IDLE_CHEST_PATH, "click")
        if not result.get("ok"):
            report["error"] = result.get("err", "home_chest_click_failed")
            logger.warning("Cocos 主頁寶箱點擊失敗: %s", report["error"])
            return report
        if not _wait_for_view(page, ("outlinePopView",), timeout=2.0):
            report["error"] = "offline_reward_popup_not_opened"
            logger.warning("Cocos 主頁寶箱點擊後未開啟離線獎勵 popup")
            return report
    report["opened"] = True
    report["quick_2h"] = _claim_quick_2h(page)
    if report["quick_2h"] == "failed":
        report["error"] = "quick_2h_claim_failed"
        return report
    report["base"] = _claim_base_reward(page)
    report["success"] = bool(report["base"])
    return report


def reward(d, easyocr_reader=None):
    """
    領取獎勵邏輯 (維持硬座標，使用 PaddleOCR/大腦判定)
    """
    # 使用原本的硬座標點擊進入
    d.click(162, 725)
    time.sleep(3)
    
    img = d.screenshot(format='opencv')
    
    # 這裡可以選擇用 OCR 判定或維持原本的顏色判定
    # 既然您希望保留前後端，我們可以用 OCR 判定是否出現領獎字樣
    # 但點擊位置維持硬座標
    result = img_tools.wait_for_any_text(d, ["領取", "放置獎勵"], timeout=2, click_if_found=False)
    
    if result:
        logger.info(f"偵測到獎勵介面: {result}")
        # 原本的硬座標顏色採樣點
        if abs(np.sum(img[328, 135])-np.sum([206, 237, 247])) > 12:
            if not os.path.exists("reward_get"):
                os.makedirs("reward_get")
            click_white(d)
            time.sleep(1)
            
        # 使用原本的硬座標點擊領取
        d.click(330, 725)
        time.sleep(2)
        click_white(d)
        time.sleep(1)

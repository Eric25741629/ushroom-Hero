"""菇菇雕像 (Statue) 每週五自動「一鍵消耗」任務。

User policy (2026-05-24): 每週五跑一次，數量預設 7000，可在 device cfg 覆寫。

兩種後端：
  * web_h5 — 用 cocos node tree 點擊 + 直接設 EditBox.string
  * adb    — 同樣的遊戲畫面，但只能透過螢幕座標 + OCR 找位置

實作分層：
  * _should_execute_for(today, last_recorded) — 純 date 邏輯（unit-testable）
  * _resolve_amount / _is_enabled              — config 解析（unit-testable）
  * run_statue_weekly_web(page, amount)        — web_h5 cocos flow（live test）
  * run_statue_weekly_adb(d, ip, amount)       — adb OCR flow
  * run_statue_weekly_if_due(d, ip, cfg)       — 入口（被 daily_pipeline 呼叫）
"""
from __future__ import annotations

import datetime
import time
from typing import Any, Optional

import img_tools
from utils.logging_utils import logger

_TZ = datetime.timezone(datetime.timedelta(hours=8))

# Weekday integer for Friday (datetime.date.weekday(): Mon=0..Sun=6).
_FRIDAY = 4
# Default amount per user policy (2026-05-24).
_DEFAULT_AMOUNT = 7000

# State record key (mirrors json_manager convention used by other periodic tasks).
_RECORD_NAME = "statue_weekly_last_execution"

# Cocos node paths (web_h5). Verified live 2026-05-24.
_STATUE_VIEW_BTN_COST_PATH = "/UIRoot/NormalView/StatueView/content/btnCost"
_STATUE_COST_EDITBOX_PATH = "/UIRoot/NormalView/StatueCostView/view/edit/EditBox"
_STATUE_COST_CONFIRM_PATH = "/UIRoot/NormalView/StatueCostView/view/btnCost"
# After clicking the popup's btnCost the server requires confirmation via a
# boxTips dialog ("...是否確認消耗"). Verified live 2026-05-24.
_BOXTIPS_BTN_ENSURE_PATH = "/UIRoot/TopView/MessageView/boxTips/dialog/content/buttons/btnEnsure"

# OCR ROIs per ADB step. Pixel coords on the 540×960 game viewport — the
# project pins ADB device resolution to match per user policy (2026-05-24),
# so these values transfer across devices.
#
# Each entry is a kwargs dict passed straight to `img_tools.click_str_by_server`.
# Derived from live OCR bbox capture via `tools/verify_statue_ocr.py` (5554 H5
# same UI) and padded with ~80–120px fault tolerance on each side.
#
# WHY ROIs at all:
#   * `popup_cost`: without it, step 5 (popup 消耗) substring-matches the
#     StatueView background "鍵消耗" at y≈120 and clicks the WRONG button.
#   * `input_zero`: needs x_range too because the substring "0" also matches
#     "-10" and "+10" on the same row — without an x clamp OCR would return
#     "-10" first and click_str_by_server would tap the decrement button.
_OCR_ROI = {
    "home_tab":     {"y_range": (840, 960)},                            # 家園 bottom tab
    "statue_icon":  {"y_range": (330, 630)},                            # 菇菇雕像 mid-screen
    "statue_cost":  {"y_range": ( 50, 250)},                            # StatueView 鍵消耗 (top-left)
    # input "0" — y must skip the 「剩餘NNNN」 label at y≈458-483 (digits in
    # the remaining counter would substring-match '0' if included); x clamps
    # out the -10 / +10 buttons on the same row.
    "input_zero":   {"y_range": (488, 560), "x_range": (200, 350)},
    "popup_cost":   {"y_range": (520, 720)},                            # popup 消耗 submit (skip bg)
    "boxtips_ok":   {"y_range": (480, 660)},                            # boxTips 確定
    # When 家園 is active, the 家園 bottom-tab cell RELABELS to 「關閉」
    # (cocos toggle behavior — clicking again closes 家園 → 主頁面). So the
    # last step of return-to-main is: OCR-click "關閉" in the bottom tab band.
    "home_close":   {"y_range": (840, 960)},                            # 關閉 in bottom tab
}

# Bottom-right red 退出 (close) button on StatueView. Verified live coords
# (492, 902) via cocos node `/UIRoot/NormalView/StatueView/content/btnClose`
# — 2026-05-24 on 5554 540×960. Tap-area is roughly 44×44 around centre,
# fault tolerance is built into the coord (centre of button).
_STATUE_RED_EXIT_COORD = (492, 902)
# Anywhere outside the centred popup body closes the popup via its imgMask.
# Top-left corner is well outside any of the dialogs we open.
_OUTSIDE_POPUP_COORD = (50, 50)


# ────────────────────────────────────────────────────────────────────
# Pure scheduling / config logic — unit tested
# ────────────────────────────────────────────────────────────────────


def _should_execute_for(
    *,
    today: datetime.date,
    last_recorded: Optional[datetime.date],
) -> bool:
    """Pure: Friday-only, once per day."""
    if today.weekday() != _FRIDAY:
        return False
    if last_recorded is None:
        return True
    # Already ran today (or future-dated bad data) — skip.
    return last_recorded < today


def _is_enabled(cfg: dict) -> bool:
    sw = (cfg or {}).get("statue_weekly") or {}
    return bool(sw.get("enabled"))


def _resolve_amount(cfg: dict) -> int:
    sw = (cfg or {}).get("statue_weekly") or {}
    raw = sw.get("amount", _DEFAULT_AMOUNT)
    try:
        amt = int(raw)
    except (TypeError, ValueError):
        amt = _DEFAULT_AMOUNT
    return max(amt, 0)


# ────────────────────────────────────────────────────────────────────
# State integration
# ────────────────────────────────────────────────────────────────────


def _parse_recorded_date(rec: Optional[dict]) -> Optional[datetime.date]:
    """Coerce json_manager record → date. Returns None if unparseable."""
    if not rec:
        return None
    from json_manager.base import _parse_recorded_date as _parse
    return _parse(rec)


def _should_execute_for_ip(ip: str, today: Optional[datetime.date] = None) -> bool:
    """Resolve last_recorded via json_manager and apply pure rule."""
    from json_manager import return_time
    if today is None:
        today = datetime.datetime.now(_TZ).date()
    rec = return_time(ip, name=_RECORD_NAME)
    return _should_execute_for(today=today, last_recorded=_parse_recorded_date(rec))


def _record_executed(ip: str) -> None:
    """Stamp now as the last execution. Other periodic tasks call this on
    success only — we mirror that so a failed click flow can retry next cycle."""
    from json_manager import time_recording
    time_recording(ip, name=_RECORD_NAME)


# ────────────────────────────────────────────────────────────────────
# web_h5 cocos flow
# ────────────────────────────────────────────────────────────────────


def run_statue_weekly_web(page: Any, amount: int) -> bool:
    """Drive the StatueView → StatueCostView flow on web_h5.

    1. main → home_tab → FarmStatue
    2. Click StatueView/content/btnCost (label 一鍵消耗) → StatueCostView opens
    3. Set view.costNum = amount (the submit button reads this, not EditBox.string)
    4. Mouse-click StatueCostView's btnCost (label 消耗) — cc.Button uses
       clickEvents fired by the touch system, not node.emit('click')
    5. Confirm the "...但不進行雕像詞條抽取..." boxTips dialog if present

    ALWAYS returns to 主頁面 in `finally`, including on failure paths —
    leaving the device on the statue page would break the next pipeline task.

    Returns True iff the submit click + boxTips confirm both went through.
    """
    if amount <= 0:
        logger.info("[statue_weekly] amount=0, skipping")
        return False

    success = False
    try:
        # Navigate to StatueView via existing cocos helpers
        try:
            from utils.cocos_navigator import CocosNavigator, COCOS_PATHS
            nav = CocosNavigator(page)
            if not nav.goto_main():
                logger.warning("[statue_weekly] goto_main failed")
                return False
            time.sleep(0.8)
            nav._click_path(COCOS_PATHS["home_tab"]); time.sleep(1.5)
            nav._click_path(COCOS_PATHS["statue_node"]); time.sleep(2.5)
        except Exception as e:
            logger.warning(f"[statue_weekly] navigation to StatueView failed: {e}")
            return False

        if not _view_active(page, "StatueView"):
            logger.warning("[statue_weekly] StatueView did not open")
            return False

        # Click btnCost — opens StatueCostView popup
        if not _click_path(page, _STATUE_VIEW_BTN_COST_PATH):
            logger.warning("[statue_weekly] click btnCost failed")
            return False
        time.sleep(2.0)
        if not _view_active(page, "StatueCostView"):
            logger.warning("[statue_weekly] StatueCostView did not open")
            return False

        # Set quantity by writing the view's internal `costNum` field — the submit
        # button reads costNum, NOT EditBox.string. (Setting EditBox alone sends 0.)
        if not _set_statue_cost_amount(page, amount):
            logger.warning("[statue_weekly] set cost amount failed")
            return False
        time.sleep(0.5)

        # Click the popup's 消耗 confirm button via real mouse click.
        if not _click_node_via_mouse(page, _STATUE_COST_CONFIRM_PATH):
            logger.warning("[statue_weekly] click confirm 消耗 failed")
            return False
        time.sleep(2.0)

        # Handle the "提示" boxTips that the game shows on first daily use:
        # "...直接消耗果蔬貢品...但不進行雕像詞條抽取，是否確認消耗".
        # If the user previously toggled "今日不再提示", boxTips is absent — skip.
        _confirm_boxtips_if_present(page)
        time.sleep(2.5)
        logger.info(f"[statue_weekly] submitted amount={amount} via web_h5")
        success = True
        return True
    finally:
        # MANDATORY: always return to 主頁面 — even on failure. The next
        # pipeline task (stage_guard) requires it.
        _close_post_action_popups(page)
        if not success:
            logger.info("[statue_weekly] failure cleanup: returned to 主頁面")


def _click_node_via_mouse(page: Any, path: str) -> bool:
    """Resolve a cocos node's world position to canvas-space CSS coords and
    issue a real mouse.click — so cc.Button.clickEvents actually fires."""
    # Honor pause requests at the chokepoint. If the cocos view stack changed
    # during the pause, raises TaskAborted to abort the H5 statue flow.
    from utils import pause_guard
    pause_guard.check()
    try:
        coords = page.evaluate(r"""([path]) => {
          if (typeof cc === 'undefined' || !cc.director) return null;
          const find = (root, parts) => { let n=root; for (const p of parts) { if(!n||!n.children) return null; n=n.children.find(c=>(c.name||'')===p); if(!n) return null; } return n; };
          const node = find(cc.director.getScene(), path.split('/').filter(Boolean));
          if (!node || !node.activeInHierarchy) return null;
          const wp = new cc.Vec3(); node.getWorldPosition(wp);
          const v = cc.view.getVisibleSize();
          const canvas = document.querySelector('canvas');
          if (!canvas) return null;
          const r = canvas.getBoundingClientRect();
          return {
            x: Math.round(r.left + wp.x * r.width / v.width),
            y: Math.round(r.top + (v.height - wp.y) * r.height / v.height),
          };
        }""", [path])
        if not coords:
            logger.debug(f"[statue_weekly] mouse-click coords resolve failed: {path}")
            return False
        page.mouse.click(int(coords["x"]), int(coords["y"]))
        return True
    except Exception as e:
        logger.debug(f"[statue_weekly] mouse-click exception: {e}")
        return False


def _confirm_boxtips_if_present(page: Any) -> bool:
    """If a boxTips confirmation dialog is open, click btnEnsure. Returns
    True iff we confirmed something."""
    try:
        active = bool(page.evaluate(r"""() => {
          const find = (r,p)=>{let n=r;for(const x of p){if(!n||!n.children)return null;n=n.children.find(c=>(c.name||'')===x);if(!n)return null;}return n;};
          const bt = find(cc.director.getScene(), ['UIRoot','TopView','MessageView','boxTips']);
          return !!(bt && bt.active);
        }"""))
    except Exception:
        return False
    if not active:
        return False
    return _click_node_via_mouse(page, _BOXTIPS_BTN_ENSURE_PATH)


def _view_active(page: Any, view_name: str) -> bool:
    try:
        return bool(page.evaluate(
            "((n) => { const v = window.uiMgr && window.uiMgr.getView && window.uiMgr.getView(n); return !!(v && v.node && v.node.active); })",
            view_name,
        ))
    except Exception:
        return False


def _click_path(page: Any, path: str) -> bool:
    """Emit click on the cocos node at the given absolute path."""
    try:
        res = page.evaluate(r"""([path]) => {
          if (typeof cc === 'undefined' || !cc.director) return {ok:false, err:'no_cc'};
          const find = (root, parts) => { let n=root; for (const p of parts) { if(!n||!n.children) return null; n=n.children.find(c=>(c.name||'')===p); if(!n) return null; } return n; };
          const node = find(cc.director.getScene(), path.split('/').filter(Boolean));
          if (!node) return {ok:false, err:'path_not_found'};
          node.emit('click', node);
          return {ok:true};
        }""", [path])
        return bool(res and res.get("ok"))
    except Exception as e:
        logger.debug(f"[statue_weekly] click {path}: {e}")
        return False


def _set_statue_cost_amount(page: Any, amount: int) -> bool:
    """Set the StatueCostView's internal cost amount + sync the visible EditBox.

    The view holds the submit-time amount in its OWN `view.costNum` field
    (verified live 2026-05-24). Setting only `EditBox.string` doesn't update
    `costNum`, so the submit click sends 0 and the server no-ops. We set
    `costNum` directly (clamped to [minNum, maxNum]) and update the EditBox
    + txtNum label so the UI matches.
    """
    try:
        res = page.evaluate(r"""([amount]) => {
          if (typeof cc === 'undefined' || !cc.director) return {ok:false, err:'no_cc'};
          const v = window.uiMgr && window.uiMgr.getView && window.uiMgr.getView('StatueCostView');
          if (!v) return {ok:false, err:'StatueCostView not loaded'};
          // Clamp to game-enforced bounds. maxNum reflects current resource balance.
          const lo = typeof v.minNum === 'number' ? v.minNum : 0;
          const hi = typeof v.maxNum === 'number' ? v.maxNum : amount;
          const clamped = Math.max(lo, Math.min(hi, amount));
          v.costNum = clamped;
          // Sync the visible EditBox (so what the user sees matches what we'll submit)
          try { if (v.editBox && typeof v.editBox.string !== 'undefined') v.editBox.string = String(clamped); } catch(e){}
          // Update the remaining-balance label so the view doesn't look stale
          try {
            if (v.txtNum && v.txtNum.string !== undefined) {
              v.txtNum.string = '(剩餘' + (hi - clamped) + ')';
            }
          } catch(e){}
          return {ok:true, costNum:v.costNum, requested:amount, clamped, minNum:lo, maxNum:hi};
        }""", [int(amount)])
        if not res or not res.get("ok"):
            logger.debug(f"[statue_weekly] set_cost_amount failed: {res}")
            return False
        if int(res.get("clamped", 0)) != int(amount):
            logger.warning(
                f"[statue_weekly] amount clamped: requested {amount} → "
                f"applied {res.get('clamped')} (max={res.get('maxNum')})"
            )
        return int(res.get("costNum", 0)) > 0
    except Exception as e:
        logger.debug(f"[statue_weekly] set_cost_amount exception: {e}")
        return False


def _close_post_action_popups(page: Any) -> None:
    """Best-effort close of common post-submit overlays + return to main so
    the next pipeline task doesn't have to navigate through them."""
    try:
        page.evaluate(r"""() => {
          const um = window.uiMgr;
          if (!um || !um.getView || !um.close) return;
          for (const v of ['StatueCostView','StatueView','GoodsGetView','GoodsGetView2','ItemTipsView','RewardView','RewardShowView']) {
            try { if (um.getView(v)) um.close(v); } catch(e){}
          }
          // Dismiss TopView/MessageView/boxTips if visible
          const find = (root, parts) => { let n=root; for (const p of parts) { if(!n||!n.children) return null; n=n.children.find(c=>(c.name||'')===p); if(!n) return null; } return n; };
          const bt = find(cc.director.getScene(), ['UIRoot','TopView','MessageView','boxTips']);
          if (bt && bt.active) bt.active = false;
        }""")
    except Exception:
        pass
    # Final safety: nav back to main so stage_guard finds 主頁面 on next task.
    try:
        from utils.cocos_navigator import CocosNavigator
        CocosNavigator(page).goto_main()
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────
# adb OCR flow
# ────────────────────────────────────────────────────────────────────


def run_statue_weekly_adb(d: Any, ip: str, amount: int) -> bool:
    """Drive the same StatueView flow on an ADB device via OCR + clicks.

    ADB devices render the same game UI as web_h5 but can't use cocos clicks,
    so we locate each element by OCR (img_tools.click_str_by_server) and use
    `d.click(x,y)` for spots that don't have a label.

    ALWAYS returns to 主頁面 in `finally` (via _adb_return_to_main), even on
    failure — the next pipeline task requires being on 主頁面.
    """
    if amount <= 0:
        logger.info(f"[{ip}] [statue_weekly] amount=0, skipping")
        return False

    success = False
    try:
        # Step 1: 家園 (bottom tab). ROI=bottom 120px so OCR ignores all the
        # mid-screen text and just looks at the tab bar.
        if not img_tools.click_str_by_server(d, "家園", **_OCR_ROI["home_tab"]):
            logger.info(f"[{ip}] [statue_weekly] 「家園」OCR miss, retrying after 1s")
            time.sleep(1.0)
            if not img_tools.click_str_by_server(d, "家園", **_OCR_ROI["home_tab"]):
                logger.warning(f"[{ip}] [statue_weekly] cannot reach 家園 via OCR")
                return False
        time.sleep(2.0)

        # Step 2: 菇菇雕像 building icon (mid-screen on 家園).
        if not img_tools.click_str_by_server(d, "菇菇雕像", **_OCR_ROI["statue_icon"]):
            logger.warning(f"[{ip}] [statue_weekly] OCR cannot locate 菇菇雕像")
            return False
        time.sleep(2.5)

        # Step 3: 一鍵消耗 (StatueView btnCost). OCR sometimes drops 「一」
        # (live-verified 2026-05-24), so we search the shorter substring
        # 「鍵消耗」 which matches both forms. ROI=top 200px confines the
        # OCR to StatueView's button area.
        if not img_tools.click_str_by_server(d, "鍵消耗", **_OCR_ROI["statue_cost"]):
            logger.warning(f"[{ip}] [statue_weekly] OCR cannot locate 鍵消耗 (一鍵消耗)")
            return False
        time.sleep(2.0)

        # Step 4: tap the popup's EditBox ("0" placeholder), then type amount.
        # ROI = popup mid-band; if 「0」 OCR misses, fall back to a coord-tap.
        if not img_tools.click_str_by_server(d, "0", **_OCR_ROI["input_zero"]):
            logger.info(f"[{ip}] [statue_weekly] '0' OCR miss, tapping fallback coord")
            d.click(270, 510)
        time.sleep(0.8)
        try:
            d.send_keys(str(amount), clear=True)
        except Exception as e:
            logger.warning(f"[{ip}] [statue_weekly] send_keys failed: {e}")
            return False
        time.sleep(0.8)

        # Step 5: confirm 「消耗」 (popup submit button). **ROI is critical**
        # here — without it, OCR finds the StatueView background "鍵消耗"
        # label at y≈120 (substring match for "消耗") and click_str_by_server
        # would click the WRONG button. y_range=(520,720) confines OCR to
        # the popup's bottom band where the submit button lives.
        if not img_tools.click_str_by_server(d, "消耗", **_OCR_ROI["popup_cost"]):
            logger.info(f"[{ip}] [statue_weekly] '消耗' OCR miss, retrying")
            if not img_tools.click_str_by_server(d, "消耗", **_OCR_ROI["popup_cost"]):
                logger.warning(f"[{ip}] [statue_weekly] cannot click confirm 消耗")
                return False
        time.sleep(2.0)

        # Step 6: confirm the "...但不進行雕像詞條抽取..." boxTips. ROI=mid
        # dialog band. Best-effort — absent when user toggled 今日不再提示.
        if img_tools.click_str_by_server(d, "確定", **_OCR_ROI["boxtips_ok"]):
            time.sleep(1.5)
        else:
            logger.debug(f"[{ip}] [statue_weekly] no boxTips '確定' visible — likely suppressed by 今日不再提示")

        logger.info(f"[{ip}] [statue_weekly] submitted amount={amount} via adb")
        success = True
        return True
    finally:
        _adb_return_to_main(d, ip, success=success)


def _adb_return_to_main(d: Any, ip: str, *, success: bool) -> None:
    """Return to 主頁面 via the actual close path the user described
    (2026-05-24): dismiss any floating popup → tap StatueView's bottom-right
    red 退出 button → OCR-click 「關閉」in the bottom tab.

    Concretely:
      1. Best-effort dismiss boxTips if visible (OCR 確定 in the dialog band)
      2. Tap (50, 50) — outside any centred popup → triggers `imgMask` to
         close StatueCostView / similar
      3. Tap (492, 902) — StatueView's red 退出 button (icon, not OCR text)
      4. OCR-click 「關閉」in the bottom tab — when 家園 is active that
         cell relabels from 「家園」 to 「關閉」, and clicking it toggles
         the home tab off → back to 主頁面

    Pure clicks only: no `d.press("back")`. ROI used where the target is a
    text label; fixed-coord tap used where the target is an icon button
    (no OCR string to anchor on).
    """
    # 1. Dismiss any lingering boxTips (e.g., the 「不進行詞條抽取」 confirm
    # ack if step 6 failed midway).
    try:
        if img_tools.click_str_by_server(d, "確定", **_OCR_ROI["boxtips_ok"]):
            time.sleep(1.0)
    except Exception as e:
        logger.debug(f"[{ip}] [statue_weekly] boxTips 確定 dismiss raised: {e}")

    # 2. Tap outside the popup body → imgMask closes StatueCostView (or any
    # other centred dialog still open). Safe no-op if no popup is open.
    try:
        d.click(*_OUTSIDE_POPUP_COORD)
        time.sleep(0.8)
    except Exception as e:
        logger.debug(f"[{ip}] [statue_weekly] outside-popup tap raised: {e}")

    # 3. Tap StatueView's bottom-right red 退出 button (icon only — no OCR
    # anchor available, so this is a hard-coded coord per user policy 2026-
    # 05-24: ADB resolution is pinned to 540×960 to match this).
    try:
        d.click(*_STATUE_RED_EXIT_COORD)
        time.sleep(1.5)
    except Exception as e:
        logger.debug(f"[{ip}] [statue_weekly] StatueView red 退出 tap raised: {e}")

    # 4. Now on 家園. The bottom-tab cell where 「家園」 normally sits has
    # relabelled to 「關閉」 (toggle behaviour). OCR-find it and tap → 主頁面.
    try:
        if img_tools.click_str_by_server(d, "關閉", **_OCR_ROI["home_close"]):
            time.sleep(1.5)
        else:
            logger.warning(f"[{ip}] [statue_weekly] cannot OCR-click 關閉 — caller stage_guard will recover")
    except Exception as e:
        logger.warning(f"[{ip}] [statue_weekly] 關閉 tab click raised: {e}")

    if not success:
        logger.info(f"[{ip}] [statue_weekly] failure cleanup: popup→exit→關閉 sequence done")


# ────────────────────────────────────────────────────────────────────
# Entry point (called by daily_pipeline)
# ────────────────────────────────────────────────────────────────────


def run_statue_weekly_if_due(d: Any, ip: str, cfg: Optional[dict] = None) -> None:
    """Top-level guard: enabled? Friday? not yet today? then run.

    Picks the web_h5 vs adb flow based on cfg.backend.
    """
    if cfg is None:
        import config_manager
        cfg = config_manager.get_device_config(ip) or {}

    if not _is_enabled(cfg):
        return

    if not _should_execute_for_ip(ip):
        logger.info(f"[{ip}] 菇菇雕像每週: 排程跳過（非週五或本週已執行）")
        return

    amount = _resolve_amount(cfg)
    backend = str(cfg.get("backend", "")).lower()
    logger.info(f"[{ip}] 菇菇雕像每週: 觸發執行 amount={amount} backend={backend}")

    try:
        if backend == "web_h5":
            page = getattr(d, "_page", None)
            if page is None:
                logger.warning(f"[{ip}] [statue_weekly] web_h5 backend has no page")
                return
            from utils import pause_guard
            pause_guard.bind(ip=ip, page=page)
            try:
                ok = run_statue_weekly_web(page, amount)
            except pause_guard.TaskAborted as exc:
                logger.info(f"[{ip}] 雕像每週 aborted: {exc}")
                return
            finally:
                pause_guard.unbind()
        else:
            ok = run_statue_weekly_adb(d, ip, amount)
    except Exception as e:
        logger.exception(f"[{ip}] [statue_weekly] unhandled exception: {e}")
        return

    if ok:
        _record_executed(ip)
        logger.info(f"[{ip}] 菇菇雕像每週: 完成並記錄執行時間")
    else:
        logger.warning(f"[{ip}] 菇菇雕像每週: 執行失敗，本週稍後將再次嘗試")

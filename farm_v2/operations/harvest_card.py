"""Weekly harvest card flow - 15 rounds of premium-seed planting with fertilize loop.

Replaces the old Mon/Wed/Fri run_weekly_card flow. Runs once per week.

Flow:
  cancel work -> navigate to carpark shop -> buy harvest card
  -> navigate back to farm -> 15x (plant premium seed, fertilize until mature, harvest)
  -> re-enable work
"""

from __future__ import annotations

import time
import random
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import uiautomator2 as uiauto

import img_tools
from farm_v2.config import (
    COORD,
    TIMING,
    HARVEST_CARD_BUY_COUNT,
    CROPS_PER_CYCLE,
    PLANTS_PER_CARD,
    FERTILIZER_FREE_CLAIMS,
)
from farm_v2.operations.base import click_with_jitter, wait_jitter
from farm_v2.operations.weekly_card import check_if_parttime
from farm_v2 import web_farm

logger = logging.getLogger("farm_v2.harvest_card")


def _page_of(d: "uiauto.Device"):
    """Return the Playwright page for a web_h5 device, or None for adb."""
    return getattr(d, "_page", None)


# ---------------------------------------------------------------------------
# Work (打工) helpers
# ---------------------------------------------------------------------------

def _cancel_work_if_active(d: "uiauto.Device") -> bool:
    """Cancel 打工 only if it is actually running. Returns True if cancelled.

    Gate on the in-scene ``check_if_parttime`` (it reads the farm screen pixels —
    no panel, no cross-scene 總覽) so we don't pop the work panel for nothing when
    no worker is active.
    """
    if not check_if_parttime(d):
        logger.info("[harvest_card] 未在打工，無需取消")
        return False

    # Open the in-scene work panel (modal on PlantMainView) and cancel.
    click_with_jitter(d, COORD["work_button"][0], COORD["work_button"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))

    # Step 2: look for 取消打工 inside the panel
    found = img_tools.wait_for_any_text(
        d, ["取消打工"],
        timeout=5, click_if_found=True,
    )
    if found:
        logger.info("[harvest_card] cancelled active work")
        time.sleep(wait_jitter(TIMING["long"]))
        # dismiss any confirmation
        img_tools.wait_for_any_text(
            d, ["確認", "確定"],
            timeout=3, click_if_found=True,
        )
        time.sleep(wait_jitter(TIMING["medium"]))
        return True

    logger.info("[harvest_card] work not active, nothing to cancel")
    # close panel via X button
    click_with_jitter(d, COORD["close"][0], COORD["close"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))
    return False


def _enable_work(d: "uiauto.Device") -> bool:
    """Open work panel, find and click 開始打工."""
    click_with_jitter(d, COORD["work_button"][0], COORD["work_button"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))

    found = img_tools.wait_for_any_text(
        d, ["開始打工", "开始打工"],
        timeout=5, click_if_found=True,
    )
    if found:
        logger.info("[harvest_card] work re-enabled")
        time.sleep(wait_jitter(TIMING["long"]))
        # panel auto-closes or show 取消打工, close it
        click_with_jitter(d, COORD["close"][0], COORD["close"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["medium"]))
        return True

    cancel_visible = img_tools.wait_for_any_text(
        d, ["取消打工"],
        timeout=2, click_if_found=False,
    )
    if cancel_visible:
        logger.info("[harvest_card] work already active")
    else:
        logger.warning("[harvest_card] could not find start work button")

    click_with_jitter(d, COORD["close"][0], COORD["close"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["medium"]))
    return True


# ---------------------------------------------------------------------------
# Navigation helpers (dual-backend: cocos fast-path + coordinate fallback)
# ---------------------------------------------------------------------------

def _navigate_farm_to_home(d: "uiauto.Device", device_ip: Optional[str] = None) -> None:
    """Exit farm (PlantMainView) and go to home tab."""
    from utils.cocos_navigator import try_cocos_navigate

    result = try_cocos_navigate(d, device_ip, "main")
    if result is True:
        time.sleep(1.0)
        return

    click_with_jitter(d, COORD["farm_tab"][0], COORD["farm_tab"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))
    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))


def _navigate_home_to_carpark(d: "uiauto.Device", device_ip: Optional[str] = None) -> bool:
    """From home tab, enter carpark (ParkingMainView)."""
    from utils.cocos_navigator import try_cocos_navigate, COCOS_PATHS

    page = getattr(d, "_page", None)
    if page is not None:
        try:
            from utils.cocos_navigator import CocosNavigator
            nav = CocosNavigator(page)
            # Clear popups (公告/獎勵/boxTips) and confirm we're on 主頁面 before
            # emit-clicking into the carpark — otherwise the click fires through
            # a still-open popup and leaves it stranded.
            nav.dismiss_blocking_popups()
            if not nav.goto_main():
                nav.dismiss_blocking_popups()
                if not nav.goto_main():
                    logger.warning("[harvest_card] 無法回到主頁面（彈窗未清除？），改用 OCR 進車位")
                    raise RuntimeError("not on main after popup sweep")
            nav._click_path(COCOS_PATHS["home_tab"])
            time.sleep(1.5)
            nav._click_path(COCOS_PATHS["carpark_node"])
            time.sleep(2.5)
            # Verify ParkingMainView actually opened — on server lag the
            # carpark_node click can silently no-op (node not yet loaded), and
            # returning True regardless would run the shop flow on the wrong
            # screen. If it didn't open, fall through to the OCR fallback.
            from utils.carpark_state import parking_view_is_open
            if parking_view_is_open(page):
                return True
            logger.warning("[harvest_card] carpark 未開啟，改用 OCR 進車位")
        except Exception as e:
            logger.warning(f"[harvest_card] cocos carpark nav failed: {e}")

    found = img_tools.click_str_by_server(d, "車位", wait_timeout=5)
    if found:
        time.sleep(3.0)
        return True

    logger.warning("[harvest_card] failed to navigate to carpark")
    return False


def _open_carpark_shop(d: "uiauto.Device") -> bool:
    """Open ParkingShopView from ParkingMainView."""
    page = getattr(d, "_page", None)
    if page is not None:
        try:
            page.evaluate(r"""() => {
              const find = (root, parts) => {
                let n = root;
                for (const p of parts) {
                  if (!n || !n.children) return null;
                  n = n.children.find(c => (c.name||'') === p);
                  if (!n) return null;
                }
                return n;
              };
              const btn = find(cc.director.getScene(),
                ['UIRoot','NormalView','ParkingMainView','bottom','btnShop']);
              if (btn) btn.emit('click', btn);
            }""")
            time.sleep(2.5)
            return True
        except Exception as e:
            logger.warning(f"[harvest_card] cocos shop btn failed: {e}")

    if COORD["carpark_shop_btn"] != (0, 0):
        click_with_jitter(d, COORD["carpark_shop_btn"][0], COORD["carpark_shop_btn"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["very_long"]))
        return True

    found = img_tools.click_str_by_server(d, "商店", wait_timeout=5)
    if found:
        time.sleep(2.5)
        return True

    return False


def _buy_one_harvest_card(d: "uiauto.Device") -> bool:
    """Buy a single 菜園豐收卡 in ParkingShopView.

    The card is at content[1]/ScrollView/content[3] — needs scrolling to be visible.
    Uses cocos emit-click for web_h5, OCR scroll+click for ADB.

    Returns True only when a purchase confirm dialog was seen + confirmed (i.e. a
    card actually went through). Returns False when the card/buy button can't be
    found OR the buy click produced no confirm (e.g. purchase limit reached) —
    the caller uses that as the signal to stop buying more.
    """
    page = getattr(d, "_page", None)
    if page is not None:
        try:
            bought = page.evaluate(r"""() => {
              const find = (root, parts) => {
                let n = root;
                for (const p of parts) {
                  if (!n || !n.children) return null;
                  n = n.children.find(c => (c.name||'') === p);
                  if (!n) return null;
                }
                return n;
              };
              const shop = find(cc.director.getScene(),
                ['UIRoot','NormalView','ParkingMainView','container','ParkingShopView']);
              if (!shop) return 'shop not found';
              // content[1] = 限時點神卡 section, item[3] = 菜園豐收卡
              const section = find(shop, ['content','ScrollView','view','content']);
              if (!section) return 'section not found';
              // Find the section with 菜園豐收卡 (walk all sections)
              const outer = find(shop, ['content','ScrollView','view']);
              if (!outer) return 'outer not found';
              const outerContent = outer.children && outer.children.find(c => c.name === 'content');
              if (!outerContent) return 'outerContent not found';
              // Section 1 has the cards
              const sec1 = outerContent.children && outerContent.children[1];
              if (!sec1) return 'sec1 not found';
              const innerSv = find(sec1, ['ScrollView','view','content']);
              if (!innerSv) return 'innerSv not found';
              // item[3] = 菜園豐收卡
              const item = innerSv.children && innerSv.children[3];
              if (!item) return 'item not found';
              const buyBtn = find(item, ['btnBuy']);
              if (buyBtn) { buyBtn.emit('click', buyBtn); return 'clicked'; }
              return 'btnBuy not found';
            }""")
            if bought == "clicked":
                time.sleep(2.0)
                confirm = img_tools.wait_for_any_text(
                    d, ["購買", "確認", "確定"],
                    timeout=5, click_if_found=True,
                )
                if confirm:
                    time.sleep(wait_jitter(TIMING["long"]))
                from tools import click_white
                click_white(d)
                time.sleep(1.0)
                if confirm:
                    logger.info("[harvest_card] harvest card purchased via cocos")
                return bool(confirm)
            logger.warning(f"[harvest_card] cocos buy failed: {bought}")
        except Exception as e:
            logger.warning(f"[harvest_card] cocos buy exception: {e}")

    # ADB fallback: scroll shop and OCR find the card
    for scroll_attempt in range(5):
        found = img_tools.click_str_by_server(d, "菜園豐收卡", wait_timeout=3)
        if not found:
            found = img_tools.click_str_by_server(d, "豐收卡", wait_timeout=2)
        if found:
            time.sleep(wait_jitter(TIMING["long"]))
            purchase = img_tools.wait_for_any_text(
                d, ["購買", "確認", "確定"],
                timeout=5, click_if_found=True,
            )
            if purchase:
                time.sleep(wait_jitter(TIMING["long"]))
            confirm = img_tools.wait_for_any_text(
                d, ["確認", "確定"],
                timeout=3, click_if_found=True,
            )
            if confirm:
                time.sleep(wait_jitter(TIMING["long"]))
            from tools import click_white
            click_white(d)
            time.sleep(1.0)
            if purchase or confirm:
                logger.info("[harvest_card] harvest card purchased via OCR")
            return bool(purchase or confirm)
        d.swipe(0.5, 0.7, 0.5, 0.4, 1)
        time.sleep(1.5)

    logger.error("[harvest_card] cannot find harvest card in shop")
    return False


def _buy_harvest_card_in_shop(d: "uiauto.Device", count: int = HARVEST_CARD_BUY_COUNT) -> int:
    """Buy up to `count` 菜園豐收卡. Stops early when a purchase doesn't go
    through (card sold out / weekly limit reached).

    Returns the number of cards actually bought (0 if none). The caller sizes
    the plant loop to this — each card only boosts 30 plants, so over-planting
    beyond what was bought just wastes seeds and fertilizer."""
    bought = 0
    for i in range(max(1, count)):
        if _buy_one_harvest_card(d):
            bought += 1
            time.sleep(wait_jitter(TIMING["long"]))
            continue
        if bought == 0:
            return 0
        logger.info(
            f"[harvest_card] 第 {i + 1} 張未購買成立（可能已達上限），停止；共買 {bought} 張"
        )
        break
    if bought:
        logger.info(f"[harvest_card] 共購買豐收卡 x{bought}")
    return bought


def _close_carpark_shop(d: "uiauto.Device") -> None:
    """Close ParkingShopView overlay."""
    page = getattr(d, "_page", None)
    if page is not None:
        try:
            page.evaluate(r"""() => {
              const find = (root, parts) => {
                let n = root;
                for (const p of parts) {
                  if (!n || !n.children) return null;
                  n = n.children.find(c => (c.name||'') === p);
                  if (!n) return null;
                }
                return n;
              };
              const views = ['ParkingShopView'];
              const nv = find(cc.director.getScene(), ['UIRoot','NormalView','ParkingMainView','container']);
              if (nv && nv.children) {
                for (const child of [...nv.children].reverse()) {
                  if (views.includes(child.name) && child.active) {
                    const btn = child.children && child.children.find(c => c.name === 'btnClose');
                    if (btn) { btn.emit('click', btn); return; }
                  }
                }
              }
            }""")
            time.sleep(1.5)
            return
        except Exception:
            pass

    if COORD["carpark_close"] != (0, 0):
        click_with_jitter(d, COORD["carpark_close"][0], COORD["carpark_close"][1], jitter=5)
    else:
        img_tools.click_str_by_server(d, "關閉", wait_timeout=3)
    time.sleep(wait_jitter(TIMING["long"]))


def _navigate_carpark_to_home(d: "uiauto.Device", device_ip: Optional[str] = None) -> None:
    """Close ParkingMainView and return to 主頁面.

    Clears purchase-reward popups (incl. TopView boxTips/獎勵) first so the
    traditional navigate_to_farm that follows starts from a clean main page.
    """
    page = getattr(d, "_page", None)
    if page is not None:
        try:
            from utils.cocos_navigator import CocosNavigator
            nav = CocosNavigator(page)
            nav.dismiss_blocking_popups()
            if nav.goto_main():
                time.sleep(1.0)
                return
        except Exception as e:
            logger.warning(f"[harvest_card] cocos carpark→home failed: {e}")

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))


def _navigate_home_to_farm(
    d: "uiauto.Device",
    device_ip: Optional[str] = None,
    cnn_model=None,
) -> None:
    """Return to the farm via the traditional farm-navigation path.

    Delegates to ``farm_v2.manager.navigate_to_farm`` — the same routine the
    rest of the farm flow uses (cocos fast-path on web_h5, click + CNN
    homeplace-wait on ADB). The harvest-card flow used to do its own blind
    home→farm_entry clicks here, which landed on the wrong page when the
    homeplace was slow to render; reusing the shared path fixes that.
    """
    from farm_v2.manager import navigate_to_farm

    navigate_to_farm(d, cnn_model, device_ip=device_ip)


# ---------------------------------------------------------------------------
# Planting with premium seeds
# ---------------------------------------------------------------------------

def _plant_premium_seed(d: "uiauto.Device") -> bool:
    """Plant 特級種子 on all empty plots via the one-key plant dialog.

    web_h5: pixel-tap btnOneKeyPlant → select 特級種子 by Label text (NOT child
    index — the SeedSelectView ScrollView order is not stable) → confirm btnUse.
    adb: OCR-driven equivalent. Returns False (without planting) if the premium
    seed can't be selected, so we never silently plant a cheaper tier.
    """
    page = _page_of(d)

    if page is not None:
        if not web_farm.tap_onekey(page, "btnOneKeyPlant"):
            logger.warning("[harvest_card] btnOneKeyPlant 不可點（無空地？）")
            return False
        time.sleep(wait_jitter(TIMING["long"]))
        if not web_farm.seed_dialog_open(page):
            logger.warning("[harvest_card] SeedSelectView 未開啟")
            return False
        if not web_farm.select_seed_by_name(page, "特級種子"):
            logger.error("[harvest_card] 找不到特級種子，放棄本輪種植（避免種到低階種子）")
            web_farm.tap_seed_confirm(page)  # close dialog cleanly is not guaranteed; best-effort
            return False
        time.sleep(wait_jitter(TIMING["medium"]))
        web_farm.tap_seed_confirm(page)
        time.sleep(wait_jitter(TIMING["very_long"]))
    else:
        # adb fallback: OCR the one-key plant button, OCR-select 特級, confirm coord.
        if not img_tools.click_str_by_server(d, "一鍵種植", wait_timeout=3):
            click_with_jitter(d, COORD["one_click_plant"][0], COORD["one_click_plant"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))
        if not img_tools.click_str_by_server(d, "特級", wait_timeout=3, shift_y=-20):
            logger.error("[harvest_card] adb 找不到特級種子，放棄本輪種植")
            return False
        time.sleep(wait_jitter(TIMING["medium"]))
        click_with_jitter(
            d, COORD["one_click_plant_confirm"][0], COORD["one_click_plant_confirm"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["very_long"]))

    from tools import click_white
    click_white(d)
    time.sleep(1.0)

    logger.info("[harvest_card] planted with premium seeds")
    return True


# ---------------------------------------------------------------------------
# Fertilize loop
# ---------------------------------------------------------------------------

PUTONG_FERTILIZER = "普通肥料"
GAOCHAN_FERTILIZER = "高產肥料"


def _choose_fertilizer(putong: int, gaochan: int) -> Optional[str]:
    """Pick the fertilizer for this pass. Priority: 普通肥料 (free/cheap) first,
    fall back to 高產肥料 when 普通 is depleted; None when both are empty.

    One 一鍵施肥 confirm matures every immature plot it can afford and can drain a
    whole fertilizer stack in one go (verified: 20 普通 spent in one batch, field
    still not fully mature) — so the caller must loop and re-choose each pass."""
    if putong > 0:
        return PUTONG_FERTILIZER
    if gaochan > 0:
        return GAOCHAN_FERTILIZER
    return None


def _lead_int(s) -> int:
    """Leading integer of a count label like '20/20' or '1393' (0 on failure)."""
    try:
        return int(str(s).split("/")[0])
    except Exception:
        return 0


def _read_fertilizer_counts_web(page) -> tuple:
    """(普通肥料, 高產肥料) current counts from the farm top bar."""
    st = web_farm.read_farm_state(page)
    return _lead_int(st.get("putong")), _lead_int(st.get("gaochan"))


def _claim_free_fertilizer(d: "uiauto.Device") -> bool:
    """Claim free fertilizer (user has ad-free card, so it's instant)."""
    found = img_tools.wait_for_any_text(
        d, ["看廣告", "免費領", "免費"],
        timeout=5, click_if_found=True,
    )
    if not found:
        if COORD["free_fertilizer_btn"] != (0, 0):
            click_with_jitter(
                d, COORD["free_fertilizer_btn"][0], COORD["free_fertilizer_btn"][1], jitter=5,
            )
            found = True

    if found:
        time.sleep(3.0)
        from tools import click_white
        click_white(d)
        time.sleep(1.0)
        logger.info("[harvest_card] claimed free fertilizer (20 packs)")
        return True

    logger.warning("[harvest_card] failed to claim free fertilizer")
    return False


def _fertilize_until_mature(d: "uiauto.Device", cap: int = 8) -> bool:
    """Fertilize every immature plot until none remain (or `cap` passes).

    Real flow (verified live): tap 一鍵施肥 (btnOneKeyGrow) → FertilizeSelectView
    opens → pick a fertilizer type → tap btnUse confirm. One confirm matures all
    plots it can afford, so this loops a few passes, preferring 普通肥料 (claiming
    free packs when out) and only spending 高產肥料 when 普通 is unavailable.
    Returns True when no immature crop remains.
    """
    page = _page_of(d)
    if page is not None:
        return _fertilize_until_mature_web(d, page, cap)
    return _fertilize_until_mature_adb(d, cap)


def _fertilize_until_mature_web(d: "uiauto.Device", page, cap: int) -> bool:
    claims_used = 0
    for rnd in range(cap):
        if not web_farm.onekey_active(page, "btnOneKeyGrow"):
            logger.info(f"[harvest_card] 作物已全部催熟（{rnd} 趟施肥）")
            return True

        putong, gaochan = _read_fertilizer_counts_web(page)
        # 優先免費/普通：普通耗盡時先試免費領，再重讀決定肥料。
        if putong <= 0 and claims_used < FERTILIZER_FREE_CLAIMS:
            if _claim_free_fertilizer(d):
                claims_used += 1
                new_putong, gaochan = _read_fertilizer_counts_web(page)
                if new_putong <= putong:
                    # Claim reported success but 普通 didn't actually restock (daily
                    # free refill on cooldown / used up). Don't keep re-claiming —
                    # fall back to 高產肥料 for the rest of this session.
                    logger.info("[harvest_card] 免費普通肥料未補貨（冷卻中？），改用高產")
                    claims_used = FERTILIZER_FREE_CLAIMS
                putong = new_putong

        kind = _choose_fertilizer(putong, gaochan)
        if kind is None:
            logger.warning("[harvest_card] 肥料用盡（普通+高產皆 0），停止施肥")
            return not web_farm.onekey_active(page, "btnOneKeyGrow")

        if not web_farm.tap_onekey(page, "btnOneKeyGrow"):
            return not web_farm.onekey_active(page, "btnOneKeyGrow")
        time.sleep(wait_jitter(TIMING["long"]))
        if not web_farm.fert_dialog_open(page):
            logger.warning("[harvest_card] FertilizeSelectView 未開啟")
            return False
        if not web_farm.select_fertilizer_by_name(page, kind):
            logger.warning(f"[harvest_card] 選肥料失敗：{kind}")
            return False
        time.sleep(wait_jitter(TIMING["medium"]))
        web_farm.tap_fert_confirm(page)
        logger.info(f"[harvest_card] 施肥第 {rnd + 1} 趟，使用 {kind}")
        time.sleep(wait_jitter(TIMING["farm_wait"]))  # 施肥約需 5s 結算

    logger.warning(f"[harvest_card] 施肥達上限 {cap} 趟仍未全熟")
    return not web_farm.onekey_active(page, "btnOneKeyGrow")


def _fertilize_until_mature_adb(d: "uiauto.Device", cap: int) -> bool:
    """adb fallback (best-effort, OCR-driven): same dialog flow without scene
    reads. Coords are portable (both backends render 540x960)."""
    for rnd in range(cap):
        # 一鍵施肥 text only appears while immature crops exist.
        if not img_tools.click_str_by_server(d, "一鍵施肥", wait_timeout=3):
            logger.info(f"[harvest_card] adb 無施肥按鈕，視為已熟（{rnd} 趟）")
            return True
        time.sleep(wait_jitter(TIMING["long"]))
        picked = img_tools.click_str_by_server(d, PUTONG_FERTILIZER, wait_timeout=2)
        if not picked:
            picked = img_tools.click_str_by_server(d, GAOCHAN_FERTILIZER, wait_timeout=2)
        if not picked:
            logger.warning("[harvest_card] adb 找不到肥料選項")
            return False
        time.sleep(wait_jitter(TIMING["medium"]))
        click_with_jitter(
            d, COORD["fertilize_select_confirm"][0], COORD["fertilize_select_confirm"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["farm_wait"]))
    return True


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

def _harvest_crops(d: "uiauto.Device") -> bool:
    """Harvest matured crops: 采摘 (btnOneKeyPick) then 領取 (btnOneKeyFetch).

    領取 is the step that actually consumes the 豐收 buff (verified live). Returns
    True if either action fired."""
    page = _page_of(d)
    harvested = False

    if page is not None:
        picked = web_farm.tap_onekey(page, "btnOneKeyPick")  # 采摘
        if picked:
            harvested = True
            time.sleep(wait_jitter(TIMING["very_long"]))
        # 領取 collects the picked crops AND consumes the 豐收 buff. It only
        # activates AFTER the 采摘 pickup animation + server round-trip settles,
        # which can take several seconds. Poll on a wall-clock deadline for it to
        # appear (a small consecutive-miss cap loses this race on slow transitions
        # and leaves 領取 unpressed → buff unspent). Once it has fired, keep going
        # until it stays inactive (multiple batches possible). Only wait when we
        # actually picked something / it is already up, so an empty field returns
        # fast instead of stalling the full deadline.
        if picked or web_farm.onekey_active(page, "btnOneKeyFetch"):
            deadline = time.time() + 15.0
            fetched_any = False
            while time.time() < deadline:
                if web_farm.tap_onekey(page, "btnOneKeyFetch"):
                    harvested = True
                    fetched_any = True
                    time.sleep(wait_jitter(TIMING["very_long"]))
                    continue
                if fetched_any:
                    break  # 領取 fired and is now gone → nothing left to collect
                time.sleep(wait_jitter(TIMING["medium"]))  # not active yet — wait
    else:
        for _ in range(6):
            if img_tools.click_str_by_server(d, "采摘", wait_timeout=2):
                harvested = True
                time.sleep(3.0)
            elif img_tools.click_str_by_server(d, "領取", wait_timeout=2):
                harvested = True
                time.sleep(3.0)
            else:
                break

    from tools import click_white
    click_white(d)
    time.sleep(1.0)

    if harvested:
        logger.info("[harvest_card] 收穫完成（采摘 + 領取）")
    else:
        logger.warning("[harvest_card] 沒有可收穫的作物")
    return harvested


def _card_buff_exhausted(d: "uiauto.Device") -> bool:
    """True once the 豐收 buff is no longer active (its 2x quota is used up).

    Judged by the SpecialBuff node's active state — never the SpecialBuff/num
    label, which goes stale after the buff ends. adb has no scene read so this
    returns False there and the loop relies on the cards×cycles cap instead."""
    page = _page_of(d)
    if page is None:
        return False
    return not web_farm.buff_active(page)


# ---------------------------------------------------------------------------
# Field-clearing precondition
# ---------------------------------------------------------------------------

def _ensure_fields_empty(d: "uiauto.Device") -> None:
    """Clear all 6 plots BEFORE buying/activating the harvest card.

    Two reasons the field must be empty at this point:
      1. The harvest card only boosts the next 30 plants (2x yield). Crops the
         打工 worker already planted would otherwise consume that quota, so they
         must be harvested off before the card is bought.
      2. Premium seeds can't be planted on top of existing crops.

    So fertilize any existing crops to maturity, harvest, and claim. Implemented
    as one fertilize→harvest pass, which is a near no-op when the plots are
    already empty (`_fertilize_until_mature` returns "mature" immediately when
    there's nothing to fertilize, and `_harvest_crops` finds no harvest buttons).
    Relies on 打工 having been cancelled upstream — without that, the worker
    would re-plant the plots we just cleared.
    """
    logger.info("[harvest_card] 清空菜田（如有作物先施肥+收成）後再種特級種子")
    _fertilize_until_mature(d)
    _harvest_crops(d)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

# remove-after-ws-farm-verify(2026-06-19): H5 視覺豐收卡流程由 ws_token.farm.run_harvest_card_cycle
# (停打工→施肥→收成→買卡 shop_type11/id1604→種特級種子103→恢復打工,純 WS) 取代。
# 移除前置:5556/5558/5560/7fe98fc6 都接上 ws_token.farm.harvest_card_cycle{enabled:true} 並 live 驗證。
# 必留:ADB 後備(adb-fc65396d 無 WS farm 設定,_*_adb 分支 + check_if_parttime 仍需保留),
# 以及 weekly_card.check_if_parttime(harvest_card.py:31 仍 import 使用,勿連帶刪 weekly_card)。
def run_harvest_card(
    d: "uiauto.Device",
    device_ip: Optional[str] = None,
    cnn_model=None,
) -> bool:
    """Execute the full weekly harvest card flow.

    Returns True on success (all cycles completed).
    """
    logger.info(f"[harvest_card] starting weekly harvest card flow on {device_ip}")

    # Step 1: cancel work FIRST. The 打工 worker auto-plants the plots, so it
    # must be off before we clear the field — otherwise it re-fills the plots we
    # just emptied, and its crops would compete for the harvest card's quota.
    _cancel_work_if_active(d)

    # Step 2: clear the field BEFORE buying the card. The card only boosts the
    # next 30 plants (2x yield); any crops the 打工 worker already planted would
    # consume that quota, so fertilize them to maturity, harvest, and claim to
    # empty all plots before the card is purchased/activated.
    _ensure_fields_empty(d)

    # Step 3: navigate to carpark shop and buy harvest card(s)
    _navigate_farm_to_home(d, device_ip)

    if not _navigate_home_to_carpark(d, device_ip):
        logger.error("[harvest_card] failed to navigate to carpark")
        _navigate_home_to_farm(d, device_ip, cnn_model)
        _enable_work(d)
        return False

    if not _open_carpark_shop(d):
        logger.error("[harvest_card] failed to open carpark shop")
        _navigate_carpark_to_home(d, device_ip)
        _navigate_home_to_farm(d, device_ip, cnn_model)
        _enable_work(d)
        return False

    cards_bought = _buy_harvest_card_in_shop(d)
    if cards_bought == 0:
        logger.error("[harvest_card] failed to buy harvest card")
        _close_carpark_shop(d)
        _navigate_carpark_to_home(d, device_ip)
        _navigate_home_to_farm(d, device_ip, cnn_model)
        _enable_work(d)
        return False

    _close_carpark_shop(d)

    # Step 4: return to farm via the traditional navigation path
    _navigate_carpark_to_home(d, device_ip)
    _navigate_home_to_farm(d, device_ip, cnn_model)

    # Step 5: plant-fertilize-harvest loop, sized to the cards actually bought.
    # Each card boosts only PLANTS_PER_CARD plants (2x yield); at CROPS_PER_CYCLE
    # crops per cycle that's PLANTS_PER_CARD // CROPS_PER_CYCLE cycles per card.
    # Planting beyond that would grow un-boosted crops and waste seeds/fertilizer.
    cycles = cards_bought * (PLANTS_PER_CARD // CROPS_PER_CYCLE)
    logger.info(
        f"[harvest_card] {cards_bought} 張卡 → 種植 {cycles} 輪 "
        f"({cards_bought}×{PLANTS_PER_CARD}÷{CROPS_PER_CYCLE})"
    )

    completed = 0
    failed = False
    for i in range(cycles):
        logger.info(f"[harvest_card] cycle {i + 1}/{cycles}")

        if not _plant_premium_seed(d):
            logger.error(f"[harvest_card] plant failed at cycle {i + 1}")
            failed = True
            break

        if not _fertilize_until_mature(d):
            logger.error(f"[harvest_card] fertilize failed at cycle {i + 1}")
            _harvest_crops(d)
            completed += 1
            failed = True
            break

        if not _harvest_crops(d):
            logger.error(f"[harvest_card] harvest failed at cycle {i + 1}")
            failed = True
            break

        completed += 1

        # Stop the moment the card buff is spent (SpecialBuff inactive). The
        # cards×cycles count is only the upper bound — 打工 or pre-existing crops
        # may have eaten part of the quota, so the buff can run out early. This is
        # a normal, successful finish (not a failure worth retrying).
        if _card_buff_exhausted(d):
            logger.info(f"[harvest_card] 豐收 buff 已耗盡，於第 {i + 1} 輪提前結束")
            break

    # Step 6: re-enable work
    _enable_work(d)

    total_crops = completed * CROPS_PER_CYCLE
    logger.info(
        f"[harvest_card] done: {completed}/{cycles} cycles, "
        f"{total_crops} crops harvested"
    )
    # Success = consumed the whole buff early OR ran every cycle with no op
    # failing. Only a real plant/fertilize/harvest failure returns False so the
    # weekly flow retries next wake (and doesn't needlessly re-buy cards).
    return not failed

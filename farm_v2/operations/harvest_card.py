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
    HARVEST_CARD_CYCLES,
    CROPS_PER_CYCLE,
    FERTILIZER_FREE_CLAIMS,
)
from farm_v2.operations.base import click_with_jitter, wait_jitter, safe_screenshot
from farm_v2.operations.weekly_card import check_if_parttime

logger = logging.getLogger("farm_v2.harvest_card")


# ---------------------------------------------------------------------------
# Work (打工) helpers
# ---------------------------------------------------------------------------

def _cancel_work_if_active(d: "uiauto.Device") -> bool:
    """Open work panel, find and click 取消打工. Returns True if cancelled."""
    # Step 1: click 打工 button to open work detail panel
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
            nav._click_path(COCOS_PATHS["home_tab"])
            time.sleep(1.5)
            nav._click_path(COCOS_PATHS["carpark_node"])
            time.sleep(2.5)
            return True
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


def _buy_harvest_card_in_shop(d: "uiauto.Device") -> bool:
    """Find and buy the harvest card in ParkingShopView.

    The card is at content[1]/ScrollView/content[3] — needs scrolling to be visible.
    Uses cocos emit-click for web_h5, OCR scroll+click for ADB.
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
                logger.info("[harvest_card] harvest card purchased via cocos")
                return True
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
            logger.info("[harvest_card] harvest card purchased via OCR")
            return True
        d.swipe(0.5, 0.7, 0.5, 0.4, 1)
        time.sleep(1.5)

    logger.error("[harvest_card] cannot find harvest card in shop")
    return False


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
    """Close ParkingMainView and return to home."""
    from utils.cocos_navigator import try_cocos_navigate

    result = try_cocos_navigate(d, device_ip, "main")
    if result is True:
        time.sleep(1.0)
        return

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))


def _navigate_home_to_farm(d: "uiauto.Device", device_ip: Optional[str] = None) -> None:
    """From home tab, enter farm."""
    from utils.cocos_navigator import try_cocos_navigate

    result = try_cocos_navigate(d, device_ip, "farm")
    if result is True:
        time.sleep(1.0)
        return

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))
    click_with_jitter(d, COORD["farm_entry"][0], COORD["farm_entry"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["farm_wait"]))


# ---------------------------------------------------------------------------
# Planting with premium seeds
# ---------------------------------------------------------------------------

def _plant_premium_seed(d: "uiauto.Device") -> bool:
    """Open SeedSelectView, pick premium seeds, confirm one-click plant."""
    # Step 1: open SeedSelectView via btnOneKeyPlant on farm
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
                ['UIRoot','NormalView','PlantMainView','OneKeyOprate','btnOneKeyPlant']);
              if (btn) btn.emit('click', btn);
            }""")
        except Exception:
            click_with_jitter(d, COORD["one_click_plant"][0], COORD["one_click_plant"][1], jitter=5)
    else:
        click_with_jitter(d, COORD["one_click_plant"][0], COORD["one_click_plant"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    # Step 2: select premium seed (特級種子) in SeedSelectView
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
              const sv = find(cc.director.getScene(),
                ['UIRoot','NormalView','SeedSelectView','view','ScrollView','view','content']);
              if (!sv) return;
              // item[2] = 特級種子
              const item = sv.children && sv.children[2];
              if (item) {
                const btn = item.children && item.children.find(c => c.name === 'btnSeed');
                if (btn) btn.emit('click', btn);
              }
            }""")
        except Exception:
            click_with_jitter(d, COORD["premium_seed_tab"][0], COORD["premium_seed_tab"][1], jitter=5)
    else:
        click_with_jitter(d, COORD["premium_seed_tab"][0], COORD["premium_seed_tab"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["medium"]))

    # Step 3: confirm one-click plant (btnUse)
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
                ['UIRoot','NormalView','SeedSelectView','bg','btnUse']);
              if (btn) btn.emit('click', btn);
            }""")
        except Exception:
            click_with_jitter(d, COORD["one_click_plant_confirm"][0], COORD["one_click_plant_confirm"][1], jitter=5)
    else:
        click_with_jitter(d, COORD["one_click_plant_confirm"][0], COORD["one_click_plant_confirm"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))

    from tools import click_white
    click_white(d)
    time.sleep(1.0)

    logger.info("[harvest_card] planted with premium seeds")
    return True


# ---------------------------------------------------------------------------
# Fertilize loop
# ---------------------------------------------------------------------------

def _apply_fertilizer(d: "uiauto.Device") -> str:
    """Apply one round of fertilizer.

    Returns:
        "success" - fertilizer applied
        "out" - out of fertilizer (need to claim free)
        "mature" - crops already mature
        "error" - unknown state
    """
    if img_tools.find_and_click(d, "get_all.jpg", threshold=0.6):
        return "mature"

    if not img_tools.find_and_click(d, "fertilize.jpg"):
        found = img_tools.click_str_by_server(d, "施肥", wait_timeout=3)
        if not found:
            return "mature"

    time.sleep(wait_jitter(TIMING["long"]))

    click_with_jitter(d, COORD["fertilize_btn"][0], COORD["fertilize_btn"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    click_with_jitter(d, COORD["fertilize_confirm"][0], COORD["fertilize_confirm"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    out_text = img_tools.wait_for_any_text(
        d, ["看廣告", "免費領", "免費", "不足"],
        timeout=2, click_if_found=False,
    )
    if out_text:
        return "out"

    return "success"


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


def _fertilize_until_mature(d: "uiauto.Device") -> bool:
    """Fertilize all crops until harvestable. Claim free fertilizer when out."""
    claims_used = 0
    max_rounds = 60

    for rnd in range(max_rounds):
        result = _apply_fertilizer(d)

        if result == "mature":
            logger.info(f"[harvest_card] crops mature after {rnd} fertilize rounds")
            return True

        if result == "out":
            if claims_used < FERTILIZER_FREE_CLAIMS:
                if _claim_free_fertilizer(d):
                    claims_used += 1
                    continue
                else:
                    logger.warning("[harvest_card] free fertilizer claim failed")
                    return False
            else:
                logger.warning("[harvest_card] all free fertilizer claims used up")
                return False

        if result == "error":
            logger.warning(f"[harvest_card] fertilize error at round {rnd}")
            time.sleep(1.0)

    logger.warning("[harvest_card] fertilize loop hit safety cap")
    return img_tools.find_and_click(d, "get_all.jpg", threshold=0.6)


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------

def _harvest_crops(d: "uiauto.Device") -> bool:
    """Harvest all mature crops."""
    harvested = False
    for _ in range(5):
        if img_tools.find_and_click(d, "get_all.jpg"):
            time.sleep(3.0)
            harvested = True
        elif img_tools.find_and_click(d, "getting.jpg"):
            time.sleep(5.0)
            harvested = True
        elif img_tools.find_and_click(d, "new_get.jpg", threshold=0.6, x=10, y=100):
            time.sleep(5.0)
            harvested = True
        else:
            break

    from tools import click_white
    click_white(d)
    time.sleep(1.0)

    if harvested:
        logger.info(f"[harvest_card] harvested {CROPS_PER_CYCLE} crops")
    else:
        logger.warning("[harvest_card] no harvest buttons found")
    return harvested


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def run_harvest_card(d: "uiauto.Device", device_ip: Optional[str] = None) -> bool:
    """Execute the full weekly harvest card flow.

    Returns True on success (all cycles completed).
    """
    logger.info(f"[harvest_card] starting weekly harvest card flow on {device_ip}")

    # Step 1: cancel work
    _cancel_work_if_active(d)

    # Step 2: navigate to carpark shop and buy harvest card
    _navigate_farm_to_home(d, device_ip)

    if not _navigate_home_to_carpark(d, device_ip):
        logger.error("[harvest_card] failed to navigate to carpark")
        _navigate_home_to_farm(d, device_ip)
        _enable_work(d)
        return False

    if not _open_carpark_shop(d):
        logger.error("[harvest_card] failed to open carpark shop")
        _navigate_carpark_to_home(d, device_ip)
        _navigate_home_to_farm(d, device_ip)
        _enable_work(d)
        return False

    if not _buy_harvest_card_in_shop(d):
        logger.error("[harvest_card] failed to buy harvest card")
        _close_carpark_shop(d)
        _navigate_carpark_to_home(d, device_ip)
        _navigate_home_to_farm(d, device_ip)
        _enable_work(d)
        return False

    _close_carpark_shop(d)

    # Step 3: navigate back to farm
    _navigate_carpark_to_home(d, device_ip)
    _navigate_home_to_farm(d, device_ip)

    # Step 4: plant-fertilize-harvest loop
    completed = 0
    for i in range(HARVEST_CARD_CYCLES):
        logger.info(f"[harvest_card] cycle {i + 1}/{HARVEST_CARD_CYCLES}")

        if not _plant_premium_seed(d):
            logger.error(f"[harvest_card] plant failed at cycle {i + 1}")
            break

        if not _fertilize_until_mature(d):
            logger.error(f"[harvest_card] fertilize failed at cycle {i + 1}")
            _harvest_crops(d)
            completed += 1
            break

        if not _harvest_crops(d):
            logger.error(f"[harvest_card] harvest failed at cycle {i + 1}")
            break

        completed += 1

    # Step 5: re-enable work
    _enable_work(d)

    total_crops = completed * CROPS_PER_CYCLE
    logger.info(
        f"[harvest_card] done: {completed}/{HARVEST_CARD_CYCLES} cycles, "
        f"{total_crops} crops harvested"
    )
    return completed == HARVEST_CARD_CYCLES

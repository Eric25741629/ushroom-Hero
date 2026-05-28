"""菇菇車位 — ADB (uiautomator2 + OCR) cross-server park flow.

Counterpart to the H5 ``carpark_auto`` module. The game is the same H5 canvas
even on a phone/emulator, rendered at the same **540x960**, so the *tap
coordinates are portable* from H5 — but ADB has NO cocos scene tree, only
screenshot pixels, so every *state read* (is the tier full? which spot is
empty? which car is fresh?) must go through OCR (``img_tools.get_all_text``)
instead of ``page.evaluate``.

Design + rationale: ``docs/protocol/CARPARK_ADB_DESIGN.md``.

────────────────────────────────────────────────────────────────────────────
STATUS: SCAFFOLD — NOT YET CALIBRATED OR LIVE-VERIFIED.
────────────────────────────────────────────────────────────────────────────
The two things ADB needs that this module does NOT yet have hard values for:

1. **Tap coordinates** for the fixed UI buttons (btnSpace, 跨界 tab, 泊銀 tier
   cell, 開始停車). These are portable from H5 and are captured by the existing
   H5 ``carpark_click_recorder`` (``logs/<dev>/carpark_clicks.jsonl``, tagged
   per action). Fill ``_CALIB`` from a real H5 park run, then verify on ADB.
2. **OCR crop regions** for the numbers/labels we read. Calibrate against a
   real ADB screenshot (or the equivalent H5 screenshot — same 540x960).

Until ``_CALIBRATED`` is flipped to True, ``park_one_silver_adb`` / ``reconcile_adb``
REFUSE to issue taps (dry-run) so an un-calibrated build can never mis-tap and
waste resources on the real account. The "is the whole 泊銀車座 full → skip"
read (the user's core ask) is fully implemented; the actual park steps
(find-empty-lot → pick-car → confirm) are honest stubs that return None until
their per-run OCR is built and live-verified.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from utils.carpark_auto import (
    SILVER_LOT_COUNT, is_daytime_window, parse_occupied_total, silver_tier_full,
    target_state,
)

logger = logging.getLogger(__name__)

# Expected SILVER tier total spots (30 public lots × 10). Used as a stricter
# gate than parse_occupied_total's generic %10 check — rejects OCR digit slips
# like 300→30 that would otherwise pass.
SILVER_EXPECTED_TOTAL = SILVER_LOT_COUNT * 10  # 300


# ───────────────────────────────────────────────────────────────────
# CALIBRATION — fill from H5 carpark_click_recorder + an ADB screenshot.
# Coordinates are in 540x960 game space (portable H5↔ADB). All None until
# captured; _CALIBRATED stays False so no taps fire.
# ───────────────────────────────────────────────────────────────────

_CALIBRATED = False  # flip to True ONLY after coords/regions are filled + ADB live-verified

# action tag → (x, y) in 540x960. Source: H5 logs/<dev>/carpark_clicks.jsonl.
_CALIB: dict[str, Optional[Tuple[int, int]]] = {
    "btnSpace": None,        # ParkingMainView/bottom/btnSpace  → opens ParkingSpaceView
    "cross_tab": None,       # ParkingSpaceView 跨界 tab (content/128)
    "silver_tier": None,     # ParkingCrossSpaceView2 泊銀 tier cell btnParkingSpace
    "confirm_park": None,    # ParkingHorseParkManageView 開始停車 (nodeStatus/nodePark)
}

# region name → (x0, y0, x1, y1) crop box in 540x960 for OCR. TODO: calibrate.
_REGION: dict[str, Optional[Tuple[int, int, int, int]]] = {
    # The 泊銀 tier cell's "占用/總數" number (e.g. "299/300"). On H5 this is the
    # tier cell numSpot/num label; on ADB OCR the same screen region.
    "silver_tier_num": None,
}


# ───────────────────────────────────────────────────────────────────
# Low-level ADB primitives
# ───────────────────────────────────────────────────────────────────


def _tap(d: Any, key: str) -> bool:
    """Tap a calibrated UI button by its _CALIB key. No-op (returns False) when
    uncalibrated — the safety gate that stops blind taps."""
    if not _CALIBRATED:
        logger.warning(f"[carpark_adb] tap '{key}' skipped — module not calibrated")
        return False
    xy = _CALIB.get(key)
    if not xy:
        logger.warning(f"[carpark_adb] no calibrated coord for '{key}'")
        return False
    d.click(xy[0], xy[1])
    return True


def _screenshot(d: Any):
    """Grab an OpenCV screenshot (540x960). Returns None on failure."""
    try:
        return d.screenshot(format="opencv")
    except Exception as e:
        logger.debug(f"[carpark_adb] screenshot failed: {e}")
        return None


def _ocr_all(d: Any) -> List[str]:
    """OCR the whole screen → list of text fragments. [] on failure."""
    import img_tools
    img = _screenshot(d)
    if img is None:
        return []
    try:
        return img_tools.get_all_text(img)
    except Exception as e:
        logger.debug(f"[carpark_adb] OCR failed: {e}")
        return []


def _ocr_region(d: Any, region: Tuple[int, int, int, int]) -> List[str]:
    """OCR a cropped region (x0,y0,x1,y1) of the screen. Upscaled 3x to help OCR
    on small glyphs. [] on failure."""
    import cv2
    import img_tools
    img = _screenshot(d)
    if img is None:
        return []
    h, w = img.shape[:2]
    x0, y0, x1, y1 = region
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return []
    crop = img[y0:y1, x0:x1]
    try:
        up = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return img_tools.get_all_text(up)
    except Exception as e:
        logger.debug(f"[carpark_adb] region OCR failed: {e}")
        return []


def _ocr_contains(d: Any, needles: Tuple[str, ...]) -> bool:
    """True if any short needle substring appears in full-screen OCR. Use SHORT
    substrings — OCR drops characters (per the project OCR convention)."""
    texts = _ocr_all(d)
    return any(n in t for t in texts for n in needles)


# ───────────────────────────────────────────────────────────────────
# Navigation (tap + OCR-verify)  —  needs _CALIB coords
# ───────────────────────────────────────────────────────────────────


def _open_cross_tab_adb(d: Any) -> bool:
    """From ParkingMainView, open 車位選擇 then the 跨界 tab. Verifies arrival by
    OCR (tier names appear). Returns True iff the cross space view is showing.

    Assumes the caller already navigated to ParkingMainView (家園 → 車位). That
    navigation reuses the shared main-page/home helpers and is out of scope for
    this skip-check scaffold; wire it from the same nav the H5 path uses.
    """
    import time
    if not _tap(d, "btnSpace"):
        return False
    time.sleep(2.0)
    if not _tap(d, "cross_tab"):
        return False
    time.sleep(2.0)
    # Verify: the cross space view shows the tier list (泊銀/鎏金/曜鑽 …).
    return _ocr_contains(d, ("泊銀", "鎏金", "曜鑽", "車座"))


# ───────────────────────────────────────────────────────────────────
# Core skip-check: is the whole 泊銀車座 full?  (the user's ask)
# ───────────────────────────────────────────────────────────────────


def silver_tier_occupied_total(d: Any) -> Optional[Tuple[int, int]]:
    """OCR the 泊銀 tier "占用/總數" number → (occupied, total).

    Requires the cross space view to be open and ``_REGION['silver_tier_num']``
    calibrated. Returns None when uncalibrated or OCR can't yield a trustworthy
    reading (caller treats None conservatively — see read_silver_tier_full).
    """
    region = _REGION.get("silver_tier_num")
    if region is None:
        logger.warning("[carpark_adb] silver_tier_num region not calibrated")
        return None
    occ_total = parse_occupied_total(_ocr_region(d, region))
    if occ_total is None:
        return None
    occ, tot = occ_total
    # Stricter gate: silver tier total is always 30 lots × 10 = 300. Reject a
    # reading that doesn't match (likely an OCR digit slip).
    if tot != SILVER_EXPECTED_TOTAL:
        logger.info(f"[carpark_adb] silver total {tot} != expected {SILVER_EXPECTED_TOTAL}; "
                    "treating as untrustworthy")
        return None
    return (occ, tot)


def read_silver_tier_full(d: Any) -> Optional[bool]:
    """True if the whole 泊銀車座 is full (no empty), False if it has space,
    None if unreadable. Mirrors the H5 _silver_tier_has_empty verdict (inverse).
    """
    return silver_tier_full(silver_tier_occupied_total(d))


# ───────────────────────────────────────────────────────────────────
# Park flow  —  skip-check wired; actual-park steps are honest stubs
# ───────────────────────────────────────────────────────────────────


def park_one_silver_adb(d: Any) -> Optional[str]:
    """Park one cross car at a 泊銀 lot on ADB. Returns the car name on success,
    None on skip/failure.

    Implemented: open cross tab → read whole-tier occupancy → **skip if full**.
    Stubbed (honest TODO, returns None — never fakes a park): find an empty lot
    + spot by OCR → pick a fresh car → confirm 開始停車 → OCR-verify. Those
    steps need per-run OCR that must be built against a live ADB device.
    """
    if not _CALIBRATED:
        logger.warning("[carpark_adb] park_one_silver_adb: not calibrated — dry-run, no taps")
        return None
    if not _open_cross_tab_adb(d):
        logger.info("[carpark_adb] could not reach 跨界 tier list")
        return None

    full = read_silver_tier_full(d)
    if full is True:
        logger.info("[carpark_adb] 整個泊銀車座已滿，跳過跨服停車")
        return None
    if full is None:
        # Conservative: an unreadable tier number means we don't trust that
        # there's space. Skip this cycle (no churn, no mis-park); retry next.
        logger.info("[carpark_adb] 泊銀占用數無法可靠 OCR，本輪保守跳過")
        return None

    # full is False → there IS space. Below requires live-built per-run OCR.
    logger.warning("[carpark_adb] 泊銀有空位，但 ADB find-empty-lot/pick-car 流程尚未實作 "
                   "(需 live 開發)；本輪不停車")
    return None  # honest stub — do NOT fabricate a successful park


def reconcile_adb(d: Any, cfg: dict, now=None) -> dict:
    """ADB counterpart to carpark_auto.reconcile. Currently only the cross
    top-up path is meaningful, and it short-circuits on the calibration gate.
    Returns a summary dict shaped like the H5 reconcile for the dashboard."""
    actions: List[str] = []
    tgt = target_state(cfg, now)
    summary = {
        "snapshot": {"backend": "adb", "cross_deployed": "ocr-tbd"},
        "target": {"cross_deployed": tgt.cross_count},
        "actions": actions,
        "time_window": "daytime" if is_daytime_window(now) else "nighttime",
    }
    if not _CALIBRATED:
        actions.append("adb carpark 未校準 (dry-run)")
        return summary
    if tgt.cross_count <= 0:
        actions.append("夜間/目標 0，不停跨服")
        return summary
    name = park_one_silver_adb(d)
    actions.append(f"parked cross: {name}" if name else "跳過跨服停車 (滿/無法讀/未實作)")
    return summary

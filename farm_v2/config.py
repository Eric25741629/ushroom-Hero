"""農場自動化 v2 - 配置模組"""

from __future__ import annotations
from typing import Dict, Tuple
import os

SCREEN_CFG = {
    "width": 540,
    "height": 960,
}

COORD = {
    "home": (321, 920),
    "farm_entry": (208, 584),
    "farm_tab": (480, 929),
    "work_button": (479, 207),
    "cancel_work": (252, 707),
    "close": (272, 868),
    "fertilize_btn": (362, 430),
    "fertilize_confirm": (270, 600),
    "getting_btn": (73, 823),
    "shop_entry": (470, 446),
    "shop_buy": (380, 910),
    "buy_confirm_1": (398, 466),
    "buy_confirm_2": (277, 551),
    "shop_close": (380, 926),
    "shop_close_2": (471, 915),
    "shop_scroll_point": (210, 690),
    "plants_tab": (357, 442),
    "plant_confirm": (267, 564),
    "plant_slot_1": (73, 823),
    "plant_slot_2": (73, 823),
    "fertilize": (73, 823),
    "fertilize_confirm_btn": (365, 440),
    "use_fertilizer": (276, 606),
    "get_all": (73, 823),
    "seed_shop_entry": (375, 915),
    "seed_buy_1": (160, 428),
    "seed_select": (335, 465),
    "seed_confirm": (273, 558),
    "seed_buy_btn": (475, 800),
    "seed_confirm_page": (384, 428),
    "plant_check_pixel": (231, 437),
    "plant_check_color": (173, 112, 68),
    # harvest card flow (screen coords verified on 540x960, scale 0.75)
    "carpark_shop_btn": (376, 914),   # ParkingMainView/bottom/btnShop
    "carpark_close": (472, 914),      # ParkingMainView/bottom/btnClose
    # premium_seed_tab is the adb-only fallback; web_h5 selects 特級種子 by Label
    # text via farm_v2.web_farm (the SeedSelectView ScrollView order is not stable).
    "premium_seed_tab": (356, 434),   # SeedSelectView 特級種子 (adb fallback)
    "one_click_plant": (77, 830),     # PlantMainView/OneKeyOprate/btnOneKeyPlant
    "one_click_plant_confirm": (270, 568),  # SeedSelectView/bg/btnUse
    "free_fertilizer_btn": (69, 125), # PlantMainView/top/btnFertilizerGet
    "one_click_fertilize": (334, 830),# PlantMainView/OneKeyOprate/btnOneKeyGrow
    # FertilizeSelectView (一鍵施肥 對話框) — adb fallback coords; web_h5 selects by
    # Label text. 普通肥料=btnFertilizeGet, 高產肥料=btnFertilizeBuy, btnUse=confirm.
    "fertilizer_putong": (201, 431),       # FertilizeSelectView 普通肥料
    "fertilizer_gaochan": (356, 431),      # FertilizeSelectView 高產肥料
    "fertilize_select_confirm": (270, 604),  # FertilizeSelectView btnUse 一鍵施肥
}

TIMING = {
    "short": 0.5,
    "medium": 1.0,
    "long": 2.0,
    "very_long": 3.0,
    "farm_wait": 5,
    "plant_wait": 8,
}

SEED_PRICE = 100
MAX_PLANT_PER_DAY = 2
# 進場節流：距上次農場進場未滿這個時數就略過(滑動視窗)。打工會自動用免費種子
# 持續種+收，所以農場不必每小時進；每 8h 進一次足以保活打工、補種子、收散落。
FARM_VISIT_INTERVAL_HOURS = 8

# 看廣告補初級種子：免廣告卡=按下直接發種子無影片。每日上限 2 次(遊戲端 (N/2))，
# 我們另外持久化當日次數，跨多次 8h 進場也不會超看、看過就不再看。只在 8 點後做。
DAILY_AD_SEED_LIMIT = 2
FARM_AD_SEED_MIN_HOUR = 8
WEEKLY_CARD_DAYS = {1, 3, 5}  # deprecated — kept for rollback

CROPS_PER_CYCLE = 6
FERTILIZER_FREE_CLAIMS = 2
HARVEST_CARD_BUY_COUNT = 3  # 菜園豐收卡每週購買上限（達上限會提前停止）
PLANTS_PER_CARD = 30        # 一張豐收卡加成的株數（2 倍產量）
# 種植輪數依「實際買到的張數」動態計算：bought * (PLANTS_PER_CARD // CROPS_PER_CYCLE)
# 例：3 張 × 30 株 ÷ 6 株/輪 = 15 輪。舊的固定 HARVEST_CARD_CYCLES=15 已移除。

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "farm_templates"
)
if not os.path.exists(TEMPLATE_DIR):
    TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "farm_templates")

"""每日 / 每週戰鬥任務套件。

從 2026-05-19 起 ``new_battle.py`` 由單一檔案拆成此 ``battle`` 套件：
    _helpers.py        — 內部共用（device id 解析、biweekly slot helpers）
    biweekly.py        — 賞金之路 / 大盜來襲
    manager.py         — BattleManager class（每日戰鬥實例）
    cloud.py           — 雲纏天梯 + weekly orchestrators
    weekly_trials.py   — 萬神試煉
    store.py           — 萬神_秘寶閣 每週購買
    special.py         — hell_door / fight_snow_country

對外維持原本的 import 介面，但建議新程式使用 ``from battle import …``。
"""

from ._helpers import (
    _compute_biweekly_slot_key,
    _resolve_device_id,
)
from .biweekly import run_biweekly_bounty_road_single
from .manager import BattleManager
from .cloud import (
    into_cloud,
    friend_help,
    help_friend,
    cloud_fight_pre,
    cloud_fight_pre_help,
    check_if_pass,
    cloud_fighting,
    run_weekly_cloud_pre_single,
    run_saturday_help_single,
    run_weekly_cloud_fighting_single,
)
from .store import (
    _update_store_record_extra,
    buy_god_everyweek,
)
from .weekly_trials import fight_test
from .special import hell_door, fight_snow_country

__all__ = [
    # core class
    "BattleManager",
    # daily / biweekly
    "run_biweekly_bounty_road_single",
    # cloud
    "into_cloud",
    "friend_help",
    "help_friend",
    "cloud_fight_pre",
    "cloud_fight_pre_help",
    "check_if_pass",
    "cloud_fighting",
    "run_weekly_cloud_pre_single",
    "run_saturday_help_single",
    "run_weekly_cloud_fighting_single",
    # store
    "buy_god_everyweek",
    # weekly trials
    "fight_test",
    # special
    "hell_door",
    "fight_snow_country",
    # internals used by tests / advanced callers
    "_compute_biweekly_slot_key",
    "_resolve_device_id",
    "_update_store_record_extra",
]

"""Shop purchase decision logic (extracted from json_manager.StoreDataManager)."""
import logging

# Import the timestamp helpers from json_manager (where they are originally defined)
from json_manager import _ts_same_day, _ts_same_week

logger = logging.getLogger(__name__)


def should_purchase(store_mgr, mode: str, max_purchases: int = 2, max_checks: int = 2) -> bool:
    """Decide whether to purchase in the given mode ('daily' or 'weekly').

    Extracted from StoreDataManager.should_purchase() in json_manager.py.

    Args:
        store_mgr: A StoreDataManager instance (provides _load_states, SECTION_KEYS, timezone).
        mode:      'daily' or 'weekly'.
        max_purchases: max purchases allowed per cycle (default 2).
        max_checks:    max check attempts per cycle (default 2).

    Returns:
        True if a purchase should be attempted, False otherwise.
    """
    if mode not in store_mgr.SECTION_KEYS:
        raise ValueError("mode 必須是 'daily' 或 'weekly'")
    states = store_mgr._load_states()
    state = states[mode]
    if mode == "daily":
        is_new_cycle = not _ts_same_day(state.timestamp, store_mgr.timezone)
        label, unit = "Daily", "天"
    else:
        is_new_cycle = not _ts_same_week(state.timestamp, store_mgr.timezone)
        label, unit = "Weekly", "週"
    within_purchase_limit = state.buy_num < max_purchases
    within_check_limit = state.check_time < max_checks
    result = is_new_cycle or (within_purchase_limit and within_check_limit)
    logger.info(
        "%s 檢查: 新的一%s=%s, 購買次數=%s/%s, 檢查次數=%s/%s, 應該購買=%s",
        label, unit, is_new_cycle,
        state.buy_num, max_purchases,
        state.check_time, max_checks,
        result,
    )
    return result

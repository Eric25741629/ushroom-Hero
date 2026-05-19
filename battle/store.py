"""萬神_秘寶閣 (god treasure store) — 每週購買流程。"""

import datetime
import logging
import time

import img_tools
import BUY
from json_manager import create_store_manager

from ._helpers import _resolve_device_id

logger = logging.getLogger(__name__)


def _update_store_record_extra(manager, title, extra_fields):
    """輔助更新商店記錄，不改動時間戳與 schema。"""
    data = manager.load_data()
    record = data.get(title, {})
    record.update(extra_fields)
    data[title] = record
    manager.save_data(data)


def buy_god_everweek(d) -> bool:
    """每週購買「萬神_秘寶閣」物品：僅在跨週時執行一次（週一為一週起點）。

    回傳 True 表示已成功（或已記錄為已購買），False 表示未購買或略過。
    """
    try:
        serial = None
        try:
            serial = d.adb_device.info.get('serialno')
        except Exception:
            serial = getattr(d, 'serial', None) or getattr(d, 'device_serial', None) or 'unknown'

        store_manager = create_store_manager(serial)

        TPE = datetime.timezone(datetime.timedelta(hours=8))
        today_str = datetime.datetime.now(TPE).strftime("%Y-%m-%d")

        title = "萬神_秘寶閣"
        record = store_manager.get_purchase_record(title) or {}

        last_check_date = record.get("last_check_date")
        try:
            check_times = int(record.get("check_times", 0))
        except Exception:
            check_times = 0
        if last_check_date != today_str:
            check_times = 0
        check_times += 1

        try:
            store_manager.record_purchase(title, {"check_times": check_times, "last_check_date": today_str})
        except Exception:
            try:
                data = store_manager.load_data()
                rec = data.get(title, {})
                rec.update({"check_times": check_times, "last_check_date": today_str})
                data[title] = rec
                store_manager.save_data(data)
            except Exception:
                pass

        try:
            if store_manager.is_purchased_today(title, period="week"):
                print("本週已購買過萬神秘寶閣，跳過")
                return False
        except Exception:
            print("無法判斷是否已於本週購買，跳過以免重複購買")
            return False

        try:
            img_tools.click_str_by_server(d, '秘寶閣', shift_y=-20)
            time.sleep(2)
            want_items = ['神樹精華', '符石還原劑', '靈契之符']
            result = BUY.buy_items(d, want_items)
            time.sleep(1)
            try:
                img_tools.click_str_by_server(d, '秘寶閣', shift_y=844 - 93)
            except Exception:
                pass

            if result:
                try:
                    store_manager.record_purchase(title, {"purchased": True})
                except Exception:
                    try:
                        data = store_manager.load_data()
                        rec = data.get(title, {})
                        rec.update({"last_check_date": today_str, "purchased": True})
                        data[title] = rec
                        store_manager.save_data(data)
                    except Exception:
                        pass
                print("購買完成並已記錄本週購買")
                return True
            else:
                print("未購買到任何指定物品")
                return False

        except Exception as e:
            print(f"嘗試購買時發生錯誤: {e}")
            return False

    except Exception as e:
        print(f"buy_god_everweek 發生不可預期的錯誤: {e}")
        return False


def buy_god_everyweek(d):
    """每週購買：萬神_秘寶閣（以 ISO 週判斷，使用 StoreDataManager 的 week 判斷）"""
    ip = _resolve_device_id(d)
    store_manager = create_store_manager(ip)
    TPE = datetime.timezone(datetime.timedelta(hours=8))
    now_local = datetime.datetime.now(TPE).date()
    year, week, _ = now_local.isocalendar()
    this_week_str = f"{year}-W{week}"

    record = store_manager.get_purchase_record("萬神_秘寶閣") or {}

    last_check_week = record.get("last_check_week")
    check_times = int(record.get("check_times", 0))
    if last_check_week != this_week_str:
        check_times = 0
    check_times += 1
    _update_store_record_extra(
        store_manager,
        "萬神_秘寶閣",
        {"check_times": check_times, "last_check_week": this_week_str},
    )

    if store_manager.is_purchased_today("萬神_秘寶閣", "week"):
        logger.info(f"[{ip}] 本週已購買萬神_秘寶閣，檢查 {check_times} 次，跳過。")
        return False

    logger.info(f"[{ip}] 嘗試執行萬神_秘寶閣每週購買（本週第 {check_times} 次檢查）。")
    try:
        img_tools.click_str_by_server(d, '秘寶閣', shift_y=-20)
        time.sleep(2)
        want_items = ['神樹精華', '符石還原劑', '靈契之符', '覺醒卷軸']
        buy_result = BUY.buy_items(d, want_items)
        buy_num = 0
        if isinstance(buy_result, dict):
            buy_num = len(buy_result.get('bought', []))
        total_count = int(record.get("count", 0)) + buy_num
        store_manager.record_purchase("萬神_秘寶閣", {"count": total_count})
        logger.info(f"[{ip}] 萬神_秘寶閣購買完成，購買數量：{buy_num}，累計: {total_count}，本週檢查 {check_times} 次。")
        return True
    except Exception as e:
        logger.error(f"[{ip}] 萬神_秘寶閣購買流程失敗: {e}")
        return False

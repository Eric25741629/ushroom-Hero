"""特殊副本 — 地獄之門 (hell_door)、雪國危機 (fight_snow_country)。"""

import datetime
import random
import time
import uiautomator2 as u2

import img_tools
from json_manager import create_store_manager

from ._helpers import _resolve_device_id


def hell_door(d, ip):
    # 2026-08-09 使用者要求：取消加速環節（看廣告開 2x 戰鬥加速）。web_h5 已透過
    # battle_speed_scale=4（官方 battleMain.timeScale）實現 4x，無需再看廣告 2x。
    d.click(random.randint(191, 256), random.randint(886, 946))  # 點擊副本
    time.sleep(0.5)
    d.swipe(0.5 - random.random() * 0.2, 0.2, 0.3 + random.random() * 0.2, 0.8, 0.1)
    time.sleep(1 + random.random() * 2)
    d.swipe(0.5 - random.random() * 0.2, 0.2, 0.3 + random.random() * 0.2, 0.8, 0.1)
    time.sleep(0.5)
    d.click(random.randint(391, 495), random.randint(352, 380))  # 入場按鈕
    time.sleep(0.8 + random.random() * 2)
    d.click(random.randint(200, 339), random.randint(764, 799))  # 挑戰按鈕
    start = time.time()
    while time.time() - start < 10 * 60 + 30:
        d.screenshot(format='opencv')[9:39, 208:383]
        if img_tools.click_str_by_server(d, '討伐結束', y_range=(0, 308)):
            print("討伐結束")
            break
    while time.time() - start < 10 * 60 + 30:
        d.screenshot(format='opencv')[9:39, 208:383]
        if img_tools.click_str_by_server(d, '恭喜獲得', y_range=(0, 308)):
            print("恭喜獲得")
            break
    time.sleep(1)
    d.click(521, 17)
    d.click(521, 17)
    time.sleep(0.5)
    d.click(random.randint(191, 256), random.randint(886, 946))  # 再次點擊副本退出
    time.sleep(1)


def fight_snow_country(d: u2.Device, device_id=None, max_success_per_week=2, max_checks_per_week=6):
    """執行 '雪國危機'，每週最多記錄 max_success_per_week 次成功；同時限制每週檢查次數。

    - 以 ISO 年-週 作為週鍵，週換時自動重置計數。
    - 每次呼叫都會把檢查次數 (check_times) +1 並寫回 JSON（避免無限重試）。
    - 只有在成功時 (恭喜獲得) 才把 count +1。
    - 若 count 已達門檻或 check_times 達上限，會提早返回 False。
    """
    if device_id is None:
        device_id = _resolve_device_id(d)
    store_manager = create_store_manager(device_id)
    title = '雪國危機'
    now = datetime.datetime.now()
    year, week, _ = now.isocalendar()
    this_week_key = f"{year}-{week}"
    record = store_manager.get_purchase_record(title) or {}
    record_week = record.get('week')
    if record_week != this_week_key:
        current_count = 0
        check_times = 0
    else:
        current_count = int(record.get('count', 0))
        check_times = int(record.get('check_times', 0))
    if current_count >= max_success_per_week:
        print(f'本週 {title} 成功次數 {current_count} 已達上限 {max_success_per_week}，跳過檢查。')
        return False
    if check_times >= max_checks_per_week:
        print(f'本週 {title} 檢查次數 {check_times} 已達檢查上限 {max_checks_per_week}，跳過。')
        return False
    check_times += 1
    store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key})
    for i in range(2):
        if not img_tools.check_str_in_region(d, '雪國危機', y_range=(716, 831)):
            print(f'未找到 {title} 入口，本次仍計為一次檢查（檢查次數={check_times}）。')
            return False
        img_tools.click_str_by_server(d, '雪國危機', y_range=(716, 831))
        time.sleep(3)
        img_tools.click_str_by_server(d, '入場', y_range=(250, 296))
        time.sleep(2)
        img_tools.click_str_by_server(d, '前往組隊', y_range=(748, 795))
        time.sleep(3)
        img_tools.click_str_by_server(d, '速戰', y_range=(701, 748))
        start = time.time()
        MAX_WAIT = 180
        phase = None
        while time.time() - start < MAX_WAIT:
            if img_tools.click_str_by_server(d, '挑戰成功') and phase is None:
                print("挑戰成功")
            if img_tools.click_str_by_server(d, '恭喜獲得', y_range=(161, 300), shift_y=500) and phase is None:
                print("恭喜獲得")
            if img_tools.click_str_by_server(d, '當前積分', shift_y=100, y_range=(111, 140)) and phase is None:
                print("打boss")
                time.sleep(0.5)
                d.click(268, 388)
            if img_tools.click_str_by_server(d, '組隊挑戰') and phase is None:
                time.sleep(10)
                phase = '等打完'
            if img_tools.click_str_by_server(d, '領取討伐獎勵', y_range=(751, 802)) and phase == '等打完':
                print("領取討伐獎勵")
                time.sleep(2)
                break
            if img_tools.click_str_by_server(d, '失敗', y_range=(161, 300)) and phase == '等打完':
                print("討伐結束")
                phase = False
                time.sleep(2)
                break
        if img_tools.click_str_by_server(d, '領取討伐獎勵', y_range=(751, 802)):
            print("領取討伐獎勵")
            time.sleep(2)
            phase = '打完'
            d.click(270, 891)
            time.sleep(2)
            d.click(364, 555)

        if phase == '打完':
            current_count += 2
            store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key, 'note': 'win'})
            print(f'本週 {title} 成功次數更新為 {current_count} 次（檢查次數={check_times}）。')
            if current_count >= max_success_per_week:
                print(f'已達每週成功門檻 {max_success_per_week}，不再檢查。')
                return True
            return True
        elif img_tools.click_str_by_server(d, '恭喜獲得', y_range=(161, 300), shift_y=500):
            current_count += 1
            store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key, 'note': 'win'})
            print(f'本週 {title} 成功次數更新為 {current_count} 次（檢查次數={check_times}）。')
            if current_count >= max_success_per_week:
                print(f'已達每週成功門檻 {max_success_per_week}，不再檢查。')
                return True
            return True
        else:
            store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key, 'note': 'lose'})
            print(f'本次 {title} 未成功，檢查次數={check_times}。')
            return False
    return False

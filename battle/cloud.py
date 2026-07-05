"""雲纏天梯試煉 — into_cloud / friend_help / cloud_fighting + weekly orchestrators."""

import datetime
import time

import img_tools
from json_manager import return_time, time_recording


def into_cloud(d):
    img_tools.click_str_by_server(d, '副本', y_range=(870, 973))
    time.sleep(2)
    for _ in range(3):
        d.swipe(239, 752, 239, 352, 0.2)
        time.sleep(0.1)
    d.click(239, 752)
    d.swipe(239, 262, 239, 310, 0.2)
    time.sleep(1)
    d.click(239, 752)
    state = False
    if img_tools.click_str_by_server(d, '雲纏天梯試煉', (426 - 149), (410 - 335)):
        print("點擊成功")
        state = True
        if img_tools.click_str_by_server(d, '結算獎勵'):
            print("點擊成功")
    return state


def friend_help(d, name='大車輪'):
    """請求朋友幫助"""
    img_tools.click_str_by_server(d, '戰友設置', y_range=(726, 827))
    time.sleep(2)
    img_tools.click_str_by_server(d, '戰友招募', y_range=(583, 631))
    time.sleep(0.1)
    img_tools.click_str_by_server(d, name)
    time.sleep(2)
    img_tools.click_str_by_server(d, '發送')
    time.sleep(0.1)
    d.click(60, 828)
    time.sleep(0.5)
    d.click(276, 888)
    img_tools.click_str_by_server(d, '關閉')


def help_friend(d):
    """幫助朋友"""
    img_tools.click_str_by_server(d, '助戰設置', y_range=(726, 827))
    time.sleep(2)
    img = d.screenshot(format='opencv')
    img_tools.analyze_skill_via_http(img[607:740, :])
    while img_tools.click_str_by_server(d, '新申請', y_range=(606, 740)):
        time.sleep(1)
        img_tools.click_str_by_server(d, '同意')
        time.sleep(1)
        d.swipe(300, 673, 259, 673, 0.3)
        time.sleep(0.3)
    d.click(276, 888)
    time.sleep(1)
    img_tools.click_str_by_server(d, '關閉')


def cloud_fight_pre(d, ip, name='大車輪'):
    state = into_cloud(d)
    if not state:
        return
    time.sleep(2)
    if 'emulator-5558' not in ip:
        friend_help(d, name)
        time.sleep(2)


def cloud_fight_pre_help(d):
    time.sleep(2)
    help_friend(d)
    time.sleep(2)


def check_if_pass(d):
    img = d.screenshot(format='opencv')
    result = img_tools.analyze_skill_via_http(img)
    if result['success'] != False:
        for text in result['ocr_results']:
            if '已通过最高难度' in text['text']:
                print("已通過最高難度，跳過")
                return True
    return False


def cloud_fighting(d, ip, name='大車輪'):
    state = into_cloud(d)
    if check_if_pass(d) and state:
        d.click(276, 888)
        time.sleep(1)
        img_tools.click_str_by_server(d, '關閉')
        return
    img_tools.click_str_by_server(d, '戰友設置', y_range=(726, 827))
    time.sleep(2)
    img_tools.click_str_by_server(d, '選擇', wait_timeout=5)
    time.sleep(0.2)
    img_tools.click_str_by_server(d, '副本入場', wait_timeout=5)
    time.sleep(2)
    for i in range(5):
        if check_if_pass(d):
            break
        if img_tools.click_str_by_server(d, '入場', wait_timeout=5):
            time.sleep(2)
            MAX_CHALLENGE_WAIT = 300
            while img_tools.click_str_by_server(d, '前往挑戰'):
                time.sleep(2)
                img_tools.click_str_by_server(d, '開始挑戰')
                time.sleep(7)
                start = time.time()
                err = False
                while True:
                    if img_tools.click_str_by_server(d, '挑戰成功'):
                        break
                    if img_tools.click_str_by_server(d, '挑戰失敗'):
                        print("挑戰失敗，重新嘗試")
                        err = True
                        break
                    if time.time() - start > MAX_CHALLENGE_WAIT:
                        print("挑戰等待超過 timeout，放棄這一場")
                        break
                    if img_tools.click_str_by_server(d, '恭喜獲得'):
                        break
                    time.sleep(2)
                if err:
                    d.click(477, 894)
                    break
    d.click(276, 888)
    time.sleep(1)
    img_tools.click_str_by_server(d, '關閉')


def run_weekly_cloud_pre_single(d, ip: str, name='大車輪') -> None:
    """單一 IP 版本的 weekly cloud_pre。週一到週五執行一次，排除 emulator-5558。"""
    if ip == 'emulator-5558':
        print(f"跳過排除的 emulator: {ip}")
        return

    today = datetime.date.today()
    if today.weekday() > 4:
        print("今天不是週一到週五，跳過 weekly cloud_pre 任務")
        return

    try:
        rec = return_time(ip, name="cloud_pre_weekly")
    except Exception:
        rec = None

    already_this_week = False
    if rec and isinstance(rec, dict) and rec.get("timestamp"):
        try:
            last_ts = float(rec.get("timestamp"))
            last_week = datetime.datetime.fromtimestamp(last_ts).isocalendar()[1]
            curr_week = today.isocalendar()[1]
            if last_week == curr_week:
                already_this_week = True
        except Exception:
            already_this_week = False

    if already_this_week:
        print(f"{ip} 本週已執行過 cloud_fight_pre，跳過")
        return

    print(f"準備對 {ip} 執行 cloud_fight_pre 並記錄本週執行")
    try:
        cloud_fight_pre(d, ip, name=name)
        time_recording(ip, name="cloud_pre_weekly")
        print(f"已記錄 {ip} 的 weekly cloud_pre 執行時間")
    except Exception as e:
        print(f"無法對 {ip} 執行 cloud_fight_pre: {e}")


def run_saturday_help_single(d, ip: str) -> None:
    """若 `ip == 'emulator-5558'`，週六執行一次 help_friend(d)。"""
    if ip != 'emulator-5558':
        print(f"run_saturday_help_single: 跳過非目標 emulator {ip}")
        return

    today = datetime.date.today()
    if today.weekday() != 5:
        print("今天不是週六，跳過 saturday help 任務")
        return

    try:
        rec = return_time(ip, name="saturday_help_weekly")
    except Exception:
        rec = None

    already_this_week = False
    if rec and isinstance(rec, dict) and rec.get("timestamp"):
        try:
            last_ts = float(rec.get("timestamp"))
            last_week = datetime.datetime.fromtimestamp(last_ts).isocalendar()[1]
            curr_week = today.isocalendar()[1]
            if last_week == curr_week:
                already_this_week = True
        except Exception:
            already_this_week = False

    if already_this_week:
        print(f"{ip} 本週已執行過 saturday help，跳過")
        return

    print(f"準備在本週週六對 {ip} 執行 help_friend 並記錄本週執行")
    try:
        help_friend(d)
        time_recording(ip, name="saturday_help_weekly")
        print(f"已記錄 {ip} 的 saturday help 執行時間")
    except Exception as e:
        print(f"執行 help_friend 發生錯誤: {e}")


def run_weekly_cloud_fighting_single(d, ip: str) -> None:
    """單一 IP 版本：每週一次 cloud_fighting，任一天手機在線即補打（不再綁死週一）。"""
    # due 判斷（每週一次 + 僅週一保留凌晨3點下限）改由 task_due registry 統一。
    from game_actions.task_due import is_due
    if not is_due("雲端戰鬥", ip):
        print(f"{ip} weekly cloud_fighting 未到執行時間或本週已執行，跳過")
        return

    print(f"準備在本週週一對 {ip} 執行 cloud_fighting 並記錄本週執行")
    try:
        cloud_fighting(d, ip)
        time_recording(ip, name="cloud_fighting_weekly")
        print(f"已記錄 {ip} 的 weekly cloud_fighting 執行時間")
    except Exception as e:
        print(f"執行 cloud_fighting 發生錯誤: {e}")

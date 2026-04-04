import time
import uiautomator2 as u2
import easyocr
import numpy as np
from tools import click_white
import mask
import cv2
import random
import os
from json_manager import time_recording, return_time, create_store_manager, JsonDataManager
import img_tools
from typing import List, Optional, Callable, Dict, Any
import datetime
import BUY
import logging
import json
logger = logging.getLogger(__name__)

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _resolve_device_id(d: Any) -> str:
    """Resolve device id across ADB and Playwright backends."""
    try:
        adb_dev = getattr(d, "adb_device", None)
        if adb_dev is not None:
            info = getattr(adb_dev, "info", {}) or {}
            serial = info.get("serialno") or info.get("serial")
            if serial:
                return str(serial)
    except Exception:
        pass

    for attr in ("device_id", "serial", "device_serial"):
        value = getattr(d, attr, None)
        if value:
            return str(value)

    try:
        info = getattr(d, "device_info", {}) or {}
        serial = info.get("serial") or info.get("serialno")
        if serial:
            return str(serial)
    except Exception:
        pass

    try:
        info = getattr(d, "info", {}) or {}
        serial = info.get("serial") or info.get("serialno")
        if serial:
            return str(serial)
    except Exception:
        pass

    return "unknown"


def _compute_biweekly_slot_key(now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Return a slot key for Sat/Sun 20:00~20:04 (Asia/Taipei), else None."""
    if now is None:
        now = datetime.datetime.now(_TPE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_TPE)
    else:
        now = now.astimezone(_TPE)

    if now.weekday() not in (5, 6):
        return None
    if now.hour != 20 or now.minute > 4:
        return None
    return f"{now.strftime('%Y-%m-%d')}-20"


def _record_biweekly_slot(ip: str, slot_key: str, result: str, step: str, detail: str = "") -> None:
    manager = JsonDataManager(ip)
    manager.record_timestamp(
        "bounty_road_biweekly_slot",
        {
            "slot_key": slot_key,
            "result": result,
            "step": step,
            "detail": detail,
        },
    )


def _safe_click_step(
    d,
    label: str,
    retry: int = 3,
    step_timeout_s: int = 8,
    logger_obj: Optional[logging.Logger] = None,
    **kwargs,
) -> bool:
    lg = logger_obj or logger
    start = time.time()
    for attempt in range(1, retry + 1):
        if time.time() - start > step_timeout_s:
            break
        ok = False
        try:
            ok = bool(img_tools.click_str_by_server(d, label, **kwargs))
        except Exception as exc:
            lg.warning(f"safe_click exception label={label} attempt={attempt}: {exc}")
        if ok:
            return True
        time.sleep(min(2.0, 0.4 * attempt))
    return False


def _recover_to_home(d, logger_obj: Optional[logging.Logger] = None) -> bool:
    lg = logger_obj or logger
    try:
        for _ in range(3):
            d.click(509, 56)
            time.sleep(0.2)
        img_tools.click_str_by_server(d, "關閉")
        img_tools.click_str_by_server(d, "確定")
        d.click(274, 875)
        time.sleep(0.5)
        return True
    except Exception as exc:
        lg.warning(f"recover_to_home failed: {exc}")
        return False



def _append_biweekly_log(ip: str, payload: Dict[str, Any]) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        safe_ip = ip.replace(":", "_").replace(" ", "_")
        path = os.path.join("logs", f"biweekly_{safe_ip}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(f"append biweekly log failed: {exc}")
def run_biweekly_bounty_road_single(
    d,
    ip: str,
    logger_obj: Optional[logging.Logger] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """MVP biweekly instance runner with slot dedupe, guarded steps, safe exit and recovery."""
    lg = logger_obj or logger
    now = datetime.datetime.now(_TPE)
    slot_key = _compute_biweekly_slot_key(now)
    if not slot_key:
        return False

    rec = return_time(ip, name="bounty_road_biweekly_slot") or {}
    if isinstance(rec, dict) and rec.get("slot_key") == slot_key:
        lg.info(f"[{ip}] biweekly slot already executed: {slot_key}")
        return False

    run_id = f"{ip}-{int(time.time())}"
    _record_biweekly_slot(ip, slot_key, "started", "enter", detail=run_id)
    _append_biweekly_log(ip, {"event": "started", "slot_key": slot_key, "run_id": run_id, "ts": datetime.datetime.now(_TPE).isoformat()})

    log_ctx: Dict[str, Any] = {
        "ts": datetime.datetime.now(_TPE).isoformat(),
        "device_id": ip,
        "run_id": run_id,
        "trigger_slot": slot_key,
        "phase": "10",
    }

    try:
        if not _safe_click_step(d, "賞金之路", retry=4, step_timeout_s=12, x_range=(0, 68), logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:賞金之路(入口)")
        if not _safe_click_step(d, "賞金之路", retry=4, step_timeout_s=12, logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:賞金之路")
        if not _safe_click_step(d, "大盜來襲", retry=4, step_timeout_s=12, logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:大盜來襲")
        _safe_click_step(d, "恭喜獲得", retry=2, step_timeout_s=4, logger_obj=lg)

        for _ in range(2):
            d.click(7, 167)
            time.sleep(0.5)

        for label in ("道具收集處", "高級", "10次", "挑戰"):
            if not _safe_click_step(d, label, retry=3, step_timeout_s=10, logger_obj=lg):
                raise RuntimeError(f"STEP_TIMEOUT:{label}")
        if not _safe_click_step(d, "開啟自動戰鬥", retry=3, step_timeout_s=10, x_range=(397, 502), logger_obj=lg):
            raise RuntimeError("STEP_TIMEOUT:開啟自動戰鬥")

        started = time.time()
        max_duration_s = 10 * 60
        idle_cycles = 0
        max_idle_cycles = 18
        while True:
            if should_stop and should_stop():
                lg.info(f"[{ip}] biweekly interrupted by external stop")
                break
            elapsed = time.time() - started
            if elapsed > max_duration_s:
                lg.info(f"[{ip}] biweekly loop exit by max_duration_s={max_duration_s}")
                break
            if idle_cycles >= max_idle_cycles:
                lg.info(f"[{ip}] biweekly loop exit by idle_cycles={idle_cycles}")
                break

            progressed = False
            if img_tools.check_str_by_server(d, "高級"):
                time.sleep(10)
                progressed = True

            for label, shift_x in (("回復", 300), ("減少", 300), ("熄火", 300)):
                _safe_click_step(d, "餵食", retry=2, step_timeout_s=5, logger_obj=lg)
                if _safe_click_step(d, label, retry=2, step_timeout_s=5, shift_x=shift_x, logger_obj=lg):
                    time.sleep(1)
                    d.click(286, 500)
                    d.send_keys(text="999999999", clear=True)
                    d.click(100, 200)
                    time.sleep(0.2)
                    _safe_click_step(d, "使用", retry=2, step_timeout_s=5, logger_obj=lg)
                    progressed = True

            idle_cycles = 0 if progressed else (idle_cycles + 1)

        _record_biweekly_slot(ip, slot_key, "success", "done", detail=run_id)
        _append_biweekly_log(ip, {"event": "success", "slot_key": slot_key, "run_id": run_id, "ts": datetime.datetime.now(_TPE).isoformat()})
        lg.info(json.dumps({**log_ctx, "step": "done", "result": "success"}, ensure_ascii=False))
        return True
    except Exception as exc:
        recovered = _recover_to_home(d, logger_obj=lg)
        _record_biweekly_slot(ip, slot_key, "failed", "recover", detail=f"{run_id}|recovered={recovered}|err={exc}")
        _append_biweekly_log(ip, {"event": "failed", "slot_key": slot_key, "run_id": run_id, "recovered": recovered, "error": str(exc), "ts": datetime.datetime.now(_TPE).isoformat()})
        lg.error(json.dumps({**log_ctx, "step": "recover", "result": "failed", "error_code": "FLOW_EXCEPTION", "error_detail": str(exc), "recovery_result": recovered}, ensure_ascii=False))
        return False

class BattleManager:
    def __init__(self, device: u2.Device, reader: easyocr.Reader,cnn_model=None):
        self.device = device
        self.reader = reader

    def swipe_screen(self, start, end, steps=40, delay=0.01):
        """模擬滑動螢幕"""
        try:
            # 將相對座標轉換為絕對像素座標
            screen_width, screen_height = self.device.window_size()
            print(f"螢幕尺寸: {screen_width}x{screen_height}")
            if start[0] < 1 and start[1] < 1:
                start = (int(start[0] * screen_width),
                         int(start[1] * screen_height))
            if end[0] < 1 and end[1] < 1:
                end = (int(end[0] * screen_width), int(end[1] * screen_height))
            # 開始滑動
            self.device.touch.down(*start)
            for i in range(steps + 1):  # 加 1，確保包含最後一步
                current_x = start[0] + (end[0] - start[0]) * i / steps
                current_y = start[1] + (end[1] - start[1]) * i / steps
                current = (int(current_x), int(current_y))  # 確保是整數
                self.device.touch.move(*current)
                time.sleep(delay/20)
            time.sleep(delay)
            self.device.touch.up(*end)
            return True
        except Exception as e:
            print(f"滑動螢幕失敗: {e}")
            return False

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        img = self.device.screenshot(format='opencv')
        if abs(np.sum(img[234, 189]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[218, 236]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[228, 318]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[236, 363]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[249, 132]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[264, 139]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[329, 154]) - np.sum([194, 219, 227])) < 10 and abs(np.sum(img[361, 370]) - np.sum([193, 218, 226])) < 10 and abs(np.sum(img[337, 451]) - np.sum([44, 155, 111])) < 10:
            self.device.click(509, 56)
            time.sleep(1)
            img = self.device.screenshot(format='opencv')
        return img

    def click_text(self, text: str) -> bool:
        """
        點擊包含指定文字的螢幕區域。
        """
        img = self.capture_screenshot()
        result = self.reader.readtext(img)
        for item in result:
            if text in str(item[1]):
                [x1, _, x3, _] = item[0]
                center = [int((x1[0] + x3[0]) / 2), int((x1[1] + x3[1]) / 2)]
                self.device.click(center[0], center[1])
                return True
        return False

    def find_battle_instance(self, battle_name: str, check: bool = False) -> bool:
        """
        找到指定戰鬥實例的座標，並執行點擊。
        """
        
        img = self.capture_screenshot()
        result = self.reader.readtext(img)
        print(f"OCR結果：{result}")
        find = False
        for item in result:
            if battle_name in item[1]:
                [x1, _, x3, _] = item[0]
                find = True
                break

        if not find:
            print(f"未找到戰鬥實例：{battle_name}")
            return False

        # 計算中心點座標
        center = [int((x1[0] + x3[0]) / 2) + 279,
                  int((x1[1] + x3[1]) / 2) + 70]

        # 如果需要檢查是否可以進入
        if check:
            cropped_img = img[center[1] - 40:center[1] +
                              40, center[0] - 40:center[0] + 30]
            cropped_result = self.reader.readtext(cropped_img, detail=0)
            print(f"檢查結果：{cropped_result}")
            if '入場' not in str(cropped_result) or '場' not in str(cropped_result):
                return False

        # 點擊戰鬥實例
        self.device.click(center[0], center[1])
        time.sleep(3)
        return True

    def handle_battle(self, battle_name: str):
        """
        根據不同的戰鬥實例執行特定邏輯。
        """
        start_time = time.time()
        if battle_name == "突襲神燈小偷" or battle_name == "挑戰冰巢龍穴":
            while time.time() - start_time < 5:
                self.device.click(221+random.randint(0, 5),  702+random.randint(0, 5))
            time.sleep(1)
            self.device.click(267, 812)
        if battle_name == "守衛":
            img = self.capture_screenshot()
            conditions = [
                abs(np.sum(img[749, 51]) - np.sum([196, 226, 237])) < 10,
                abs(np.sum(img[740, 273]) - np.sum([196, 226, 237])) < 10,
                abs(np.sum(img[827, 283]) - np.sum([196, 226, 237])) < 10,
                abs(np.sum(img[693, 483]) - np.sum([197, 227, 238])) < 10,
                abs(np.sum(img[803, 486]) - np.sum([197, 227, 238])) < 10
            ]
            if any(conditions):
                while time.time() - start_time < 5:
                    self.device.click(221,  772)
                time.sleep(1)
                self.click_text("確定")
                time.sleep(1)
                self.device.click(267, 903)
                time.sleep(3)
                self.click_text("確定")
        elif battle_name == "暗黑試煉"  :
            for _ in range(5):
                self.click_text("快速通關")
                time.sleep(3)
                self.click_text("確認")
                time.sleep(3)
                self.device.click(516, 80)
                self.click_text("重置")
                time.sleep(3)
                img = self.capture_screenshot()
                if np.abs(np.sum(img[593, 142]) - np.sum([171, 200, 215])) <= 10 and np.abs(np.sum(img[594, 196]) - np.sum([172, 201, 215])) <= 10 and np.abs(np.sum(img[595, 122]) - np.sum([174, 204, 215])) <= 10 and np.abs(np.sum(img[379, 202]) - np.sum([174, 204, 215])) <= 10 and np.abs(np.sum(img[479, 176]) - np.sum([167, 202, 212])) <= 10 and np.abs(np.sum(img[472, 386]) - np.sum([173, 203, 214])) <= 10:
                    print("還有次數")
                    self.click_text("確定")
                    time.sleep(3)
                elif (np.abs(np.sum(img[554, 120]) - np.sum([57, 64, 197])) <= 10 and np.abs(np.sum(img[557, 225]) - np.sum([58, 65, 198])) <= 10 and np.abs(np.sum(img[570, 169]) - np.sum([61, 65, 200])) <= 10) or (np.abs(np.sum(img[590, 123]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[589, 218]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[606, 208]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[605, 138]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[592, 106]) - np.sum([57, 66, 194])) <= 10):
                    print("沒有次數")
                    self.device.click(509, 56)
                    time.sleep(1)
                    break
            self.device.click(272, 878)
            time.sleep(2)
            self.device.swipe(261+random.randint(0, 5),767,261+random.randint(0, 5),600,0.3)
            time.sleep(0.3)
            self.device.click(261, 578)
            # self.swipe_screen((0.5, 0.8), (0.5, 0.65), delay=0.5)
            time.sleep(2)
        elif battle_name == "探秘焚焰神殿":
            while time.time() - start_time < 5:
                self.device.click(235, 739)
            time.sleep(1)
            self.device.click(267, 812)
        elif battle_name =="神樹試煉":
            img = self.capture_screenshot()[160:254 ,9:99]
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
            if np.sum(mask_img) > 30000 and np.sum(mask_img) < 35000:
                self.device.click(47+random.randint(0, 5), 211+random.randint(0, 5))
                time.sleep(2)
                self.device.click(302+random.randint(0, 5), 583+random.randint(0, 5))
                time.sleep(2)
                self.device.click(509, 56) #空白處
                time.sleep(1)
            self.device.click(509, 56) #空白處
            time.sleep(1)
            self.device.click(487,922)
        else:
            while time.time() - start_time < 5:
                self.device.click(225, 702)
            time.sleep(1)
            self.device.click(267, 812)

    def execute_all_battles(self, check: bool = False,ip=''):
        """
        執行所有戰鬥實例。
        """
        battle_names = ["突襲神燈小偷", "挑戰冰巢龍穴", "守衛", "顛倒時序塔", "探秘焚焰神殿"]
        check_list = [True, True, False, False, True]
        self.device.swipe(0.5+random.uniform(-0.05, 0.05), 0.2+random.uniform(-0.05, 0.05), 0.5, 0.8)
        time.sleep(3)
        record_time = return_time(ip, name="battle_daily_reset")
        print(record_time)
        if record_time is None :
            should_execute = True
        else:
            should_execute = record_time.get("is_next_day", False) 
        print(f"是否執行暗黑試煉/神樹試煉: {should_execute}")
        if should_execute:
            print("今日已執行過暗黑試煉/神樹試煉，跳過執行。")
            # 移除暗黑試煉/神樹試煉
            battle_names = ["突襲神燈小偷", "挑戰冰巢龍穴", "守衛", "顛倒時序塔", "探秘焚焰神殿", "暗黑試煉","神樹試煉"]
            check_list = [True, True, False, False, True, False, False]
        for name, check in zip(battle_names, check_list):
            print(f"開始處理戰鬥實例：{name}")
            if self.find_battle_instance(name, check):
                self.handle_battle(name)
            time.sleep(2)
            self.device.swipe(261+random.randint(0, 5),767,261+random.randint(0, 5),600,0.3)
            time.sleep(0.1)
            self.device.click(261, 578)
            # self.swipe_screen((0.5 + random.uniform(-0.05, 0.05), 0.8), (0.5 , 0.65), delay=0.5)
            if name == "暗黑試煉" :
                for _ in range(3):
                    self.device.swipe(261+random.randint(0, 5),767,261+random.randint(0, 5),600,0.3)
                    time.sleep(0.1)
                    self.device.click(261, 578)
                # self.swipe_screen((0.5 + random.uniform(-0.05, 0.05), 0.8), (0.5 , 0.65), delay=0.5)
                # self.swipe_screen((0.5 + random.uniform(-0.05, 0.05), 0.8), (0.5 , 0.65), delay=0.5)
                # self.swipe_screen((0.5 + random.uniform(-0.05, 0.05), 0.8), (0.5 , 0.65), delay=0.5)

            time.sleep(2)
        if should_execute:
            time_recording(ip, name="battle_daily_reset")
        # 返回主頁
        self.device.click(227, 915)
        time.sleep(1)
def into_cloud(d):
    img_tools.click_str_by_server(d,'副本',y_range=(870,973))
    time.sleep(2)
    for _ in range(3):
        d.swipe(239,752,239,352,0.2)
        time.sleep(0.1)
    d.click(239, 752)
    d.swipe(239,262,239,310,0.2)
    time.sleep(1)
    d.click(239, 752)
    state = False
    if img_tools.click_str_by_server(d,'雲纏天梯試煉',(426-149),(410-335)):
        print("點擊成功")
        state = True
        if img_tools.click_str_by_server(d,'結算獎勵'):
            print("點擊成功")
    return state

def friend_help(d,name='大車輪'):
    '''請求朋友幫助'''
    img_tools.click_str_by_server(d,'戰友設置',y_range=(726,827))
    time.sleep(2)
    img_tools.click_str_by_server(d,'戰友招募',y_range=(583,631))
    time.sleep(0.1)
    img_tools.click_str_by_server(d,name)
    time.sleep(2)
    img_tools.click_str_by_server(d,'發送')
    time.sleep(0.1)
    d.click(60,828)
    time.sleep(0.5)
    d.click(276,888)
    img_tools.click_str_by_server(d,'關閉')
def help_friend(d):
    '''幫助朋友'''
    img_tools.click_str_by_server(d,'助戰設置',y_range=(726,827))
    time.sleep(2)
    check = True
    img = d.screenshot(format='opencv')
    result = img_tools.analyze_skill_via_http(img[607:740,:])
    while img_tools.click_str_by_server(d,'新申請',y_range=(606,740)):
        time.sleep(1)
        img_tools.click_str_by_server(d,'同意')
        time.sleep(1)
        d.swipe(300,673,259,673,0.3)
        time.sleep(0.3)
    d.click(276,888)   
    time.sleep(1)
    img_tools.click_str_by_server(d,'關閉')
def cloud_fight_pre(d,ip,name='大車輪'):
    state = into_cloud(d)
    if not state:
        return
    time.sleep(2)
    if 'emulator-5558' not in ip:
        friend_help(d,name)
        time.sleep(2)
def cloud_fight_pre_help(d):
    time.sleep(2)
    help_friend(d)
    time.sleep(2)
def check_if_pass(d):
    img = d.screenshot(format='opencv')
    result = img_tools.analyze_skill_via_http(img)
    if result['success']!=False:
        for text in result['ocr_results']:
            if '已通过最高难度' in text['text']:
                print("已通過最高難度，跳過")
                return True
    return False

def cloud_fighting(d,ip,name='大車輪'):
    state = into_cloud(d)
    if check_if_pass(d) and state:
        d.click(276,888)   
        time.sleep(1)
        img_tools.click_str_by_server(d,'關閉')
        return
    img_tools.click_str_by_server(d,'戰友設置',y_range=(726,827))
    time.sleep(2)
    img_tools.click_str_by_server(d,'選擇',wait_timeout=5)
    time.sleep(0.2)
    img_tools.click_str_by_server(d,'副本入場',wait_timeout=5)
    time.sleep(2)
    for i in range(5):
        if check_if_pass(d):
            break
        if img_tools.click_str_by_server(d,'入場',wait_timeout=5):
            time.sleep(2)
            MAX_CHALLENGE_WAIT = 300  # 最多等 60 秒，你可自行調整
            while img_tools.click_str_by_server(d, '前往挑戰'):
                time.sleep(2)
                img_tools.click_str_by_server(d, '開始挑戰')
                time.sleep(7)
                start = time.time()
                err = False
                while True:
                    # 若已經看到「挑戰成功」，跳出內層迴圈
                    if img_tools.click_str_by_server(d, '挑戰成功'):
                        break
                    if img_tools.click_str_by_server(d, '挑戰失敗'):
                        print("挑戰失敗，重新嘗試")
                        err =True
                        break
                    # timeout 檢查
                    if time.time() - start > MAX_CHALLENGE_WAIT:
                        print("挑戰等待超過 timeout，放棄這一場")
                        break
                    if img_tools.click_str_by_server(d, '恭喜獲得'):
                        break
                    time.sleep(2)
                if err:
                    d.click(477,894)
                    break
    d.click(276,888)   
    time.sleep(1)
    img_tools.click_str_by_server(d,'關閉')

def run_weekly_cloud_pre_single(d,ip: str,name='大車輪') -> None:
    """針對單一 IP 執行 weekly cloud_pre 的便利函式（多執行緒場景使用）。

    行為與 `run_weekly_cloud_pre` 相同：週一到週五執行一次，排除 `emulator-5558`，並以
    ISO 週做為本週判斷，執行後透過 `time_recording` 記錄。
    """
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
        cloud_fight_pre(d, ip,name=name)
        time_recording(ip, name="cloud_pre_weekly")
        print(f"已記錄 {ip} 的 weekly cloud_pre 執行時間")
    except Exception as e:
        print(f"無法對 {ip} 執行 cloud_fight_pre: {e}")


def buy_god_everweek(d) -> bool:
    """每週購買「萬神_秘寶閣」物品：僅在跨週時執行一次（週一為一週起點）。

    回傳 True 表示已成功（或已記錄為已購買），False 表示未購買或略過。
    """
    try:
        # 取得設備序號作為 store manager 的 key
        serial = None
        try:
            serial = d.adb_device.info.get('serialno')
        except Exception:
            # 若 d 沒有 adb_device 屬性，嘗試其它常見屬性
            serial = getattr(d, 'serial', None) or getattr(d, 'device_serial', None) or 'unknown'

        store_manager = create_store_manager(serial)

        # 時區：台北
        TPE = datetime.timezone(datetime.timedelta(hours=8))
        today_str = datetime.datetime.now(TPE).strftime("%Y-%m-%d")

        title = "萬神_秘寶閣"
        record = store_manager.get_purchase_record(title) or {}

        # 每次呼叫皆更新檢查次數（跨日重置）
        last_check_date = record.get("last_check_date")
        try:
            check_times = int(record.get("check_times", 0))
        except Exception:
            check_times = 0
        if last_check_date != today_str:
            check_times = 0
        check_times += 1

        # 更新檢查次數與最後檢查日（使用 record_purchase 儲存額外欄位）
        try:
            store_manager.record_purchase(title, {"check_times": check_times, "last_check_date": today_str})
        except Exception:
            # 若 record_purchase 不可用，嘗試直接寫入 get/save
            try:
                data = store_manager.load_data()
                rec = data.get(title, {})
                rec.update({"check_times": check_times, "last_check_date": today_str})
                data[title] = rec
                store_manager.save_data(data)
            except Exception:
                pass

        # 使用剛加入的週判斷（period='week'），若本週已購買則跳過
        try:
            if store_manager.is_purchased_today(title, period="week"):
                print("本週已購買過萬神秘寶閣，跳過")
                return False
        except Exception:
            # 若判斷失敗，保守跳過購買
            print("無法判斷是否已於本週購買，跳過以免重複購買")
            return False

        # 點擊秘寶閣並嘗試購買
        try:
            img_tools.click_str_by_server(d, '秘寶閣', shift_y=-20)
            time.sleep(2)
            want_items = ['神樹精華', '符石還原劑', '靈契之符']
            result = BUY.buy_items(d, want_items)
            time.sleep(1)
            # 關閉或返回秘寶閣介面（避免留在特殊頁面）
            try:
                img_tools.click_str_by_server(d, '秘寶閣', shift_y=844-93)
            except Exception:
                pass

            # 若購買成功，記錄本次購買時間（將被 is_purchased_today(period='week') 辨識）
            if result:
                try:
                    store_manager.record_purchase(title, {"purchased": True})
                except Exception:
                    # 若無法使用 record_purchase，回寫基本欄位
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


def run_saturday_help_single(d, ip: str) -> None:
    """若 `ip == 'emulator-5558'`，則在每週六執行一次 `help_friend(d)`，並記錄執行時間。

    - 接受已連線的 `d`（uiautomator2 device）與 `ip`。
    - 以 ISO 週比較來判斷是否已在本週執行過（使用 `return_time` / `time_recording`）。
    """
    # 只處理指定 emulator
    if ip != 'emulator-5558':
        print(f"run_saturday_help_single: 跳過非目標 emulator {ip}")
        return

    today = datetime.date.today()
    # weekday: 0=Mon .. 5=Sat .. 6=Sun
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
    """
    single-IP 版本：接受已連線的 `d`（uiautomator2 device）並在每週日執行一次 `cloud_fighting`，
    以 ISO 週做為本週判斷，執行後用 `time_recording` 記錄。
    """
    today = datetime.date.today()
    if today.weekday() != 6:
        print("今天不是週日，跳過 weekly cloud_fighting 任務")
        return

    try:
        rec = return_time(ip, name="cloud_fighting_weekly")
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
        print(f"{ip} 本週已執行過 cloud_fighting，跳過")
        return

    print(f"準備在本週週日對 {ip} 執行 cloud_fighting 並記錄本週執行")
    try:
        cloud_fighting(d, ip)
        time_recording(ip, name="cloud_fighting_weekly")
        print(f"已記錄 {ip} 的 weekly cloud_fighting 執行時間")
    except Exception as e:
        print(f"執行 cloud_fighting 發生錯誤: {e}")



def fight_test(d):
    img_tools.click_str_by_server(d,'副本')
    time.sleep(2)
    for _ in range(3):
        d.swipe(239,752,239,352,0.2)
    d.click(239, 752)
    time.sleep(2)
    if img_tools.click_str_by_server(d,'萬神試煉',(426-149),(410-335)):
        time.sleep(2)
        for i in range(7):
            img_tools.click_str_by_server(d,'開始')
            time.sleep(1.5+random.uniform(0,0.5))
            img_tools.click_str_by_server(d,'開始')
            time.sleep(1+random.uniform(0,0.5))
            img_tools.click_str_by_server(d,'確定')
            time.sleep(1.5)
            d.click(446,81)
            img_tools.click_str_by_server(d,'開始挑戰')
            time.sleep(7)
            img_tools.click_str_by_server(d,'跳過')
            time.sleep(2)
            d.click(446,81)
            time.sleep(2)
            d.click(490,919)#點擊退出
            time.sleep(1)
            img_tools.click_str_by_server(d,'結束本局',170-339,521-433)
            time.sleep(1)
            img_tools.click_str_by_server(d,'確定')
            time.sleep(2)
            d.click(446,81)
            time.sleep(0.5)
            d.click(274,875)
            time.sleep(1)
        buy_god_everyweek(d)
        #領取獎勵 
        d.click(45,262)
        time.sleep(1)
        img_tools.click_str_by_server(d,'本周積分',shift_y=90)
        #點擊空白處
        time.sleep(0.2)
        for i in range(3):
            d.click(509,56)
        time.sleep(2)
        d.click(490,919)#點擊退出
        time.sleep(1)
        img_tools.click_str_by_server(d,'關閉')
def _update_store_record_extra(manager, title, extra_fields):
    """輔助更新商店記錄，不改動時間戳與 schema。"""
    data = manager.load_data()
    record = data.get(title, {})
    record.update(extra_fields)
    data[title] = record
    manager.save_data(data)
def buy_god_everyweek(d):
    ip = _resolve_device_id(d)
    """每週購買：萬神_秘寶閣（以 ISO 週判斷，使用 StoreDataManager 的 week 判斷）"""
    store_manager = create_store_manager(ip)
    # 現行 ISO 週字串，用於檢查次數記錄（避免跨週累加）
    TPE = datetime.timezone(datetime.timedelta(hours=8))
    now_local = datetime.datetime.now(TPE).date()
    year, week, _ = now_local.isocalendar()
    this_week_str = f"{year}-W{week}"

    record = store_manager.get_purchase_record("萬神_秘寶閣") or {}

    # 每次呼叫都更新檢查次數（跨週重置）
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

    # 使用 store_manager 提供的週判斷
    if store_manager.is_purchased_today("萬神_秘寶閣", "week"):
        logger.info(f"[{ip}] 本週已購買萬神_秘寶閣，檢查 {check_times} 次，跳過。")
        return False

    logger.info(f"[{ip}] 嘗試執行萬神_秘寶閣每週購買（本週第 {check_times} 次檢查）。")
    try:
        # 點擊並購買
        img_tools.click_str_by_server(d, '秘寶閣', shift_y=-20)
        time.sleep(2)
        want_items = ['神樹精華', '符石還原劑', '靈契之符','覺醒卷軸']
        buy_result = BUY.buy_items(d, want_items)
        buy_num = 0
        if isinstance(buy_result, dict):
            buy_num = len(buy_result.get('bought', []))
        # 使用 StoreDataManager.record_purchase 記錄（會寫入 UTC 欄位）
        total_count = int(record.get("count", 0)) + buy_num
        store_manager.record_purchase("萬神_秘寶閣", {"count": total_count})
        logger.info(f"[{ip}] 萬神_秘寶閣購買完成，購買數量：{buy_num}，累計: {total_count}，本週檢查 {check_times} 次。")
        return True
    except Exception as e:
        logger.error(f"[{ip}] 萬神_秘寶閣購買流程失敗: {e}")
        return False
def hell_door(d, ip):
    click_speed =False
    for i in range(10):         
        if 'fc65396d' in ip:
            d.click(random.randint(160, 190),random.randint(145, 179)) #首頁加速的按鈕
        else:
            d.click(178+random.randint(-5, 5),114+random.randint(-5, 5)) #首頁加速的按鈕
        time.sleep(1)
        img = d.screenshot(format='opencv')[239:277,197:340]
        result = img_tools.analyze_skill_via_http(img) #{'combo': '', 'is_unwanted': False, 'normalized_combo': '', 'ocr_results': ['穿越深淵之門'], 'success': True}
        if result.get('success') is False:
            return 
        if result ==None or result.get("success") == False:
            print("未識別到文字，請檢查截圖或服務狀態")
            continue
        if result!=None and  (result.get('ocr_results')[0].get("text",None) == "戰鬥加速"):
            print("找到戰鬥加速")
            click_speed = True
            break
        img = d.screenshot(format='opencv')[421:470,173:374]
        result = img_tools.analyze_skill_via_http(img)
        print(result) #{'combo': '', 'is_unwanted': False, 'normalized_combo': '', 'ocr_results': ['戰鬥加速'], 'success': True}
        if result == None or result.get("success") == False:
            print("未識別到文字，請檢查截圖或服務狀態")
            continue
        if result != None and result.get('ocr_results')[0] == "游荡哥布林":
            print("游荡哥布林")
            d.click(270,685) #點擊領取
            time.sleep(1)
            d.click(525,10) #關閉
    if click_speed:
        d.click(random.randint(196,338),random.randint(485,515)) #觀看廣告的按鈕
        time.sleep(2+random.random()*5)
    d.click(random.randint(191,256),random.randint(886,946)) #點擊副本
    #由上到下滑動
    time.sleep(0.5)
    d.swipe(0.5-random.random()*0.2, 0.2, 0.3+random.random()*0.2, 0.8, 0.1)
    time.sleep(1+random.random()*2)
    d.swipe(0.5-random.random()*0.2, 0.2, 0.3+random.random()*0.2, 0.8, 0.1)
    time.sleep(0.5)
    d.click(random.randint(391,495),random.randint(352,380)) #入場按鈕
    time.sleep(0.8+random.random()*2)
    d.click(random.randint(200,339),random.randint(764,799)) #挑戰按鈕
    start = time.time() 
    err = 0
    # time.sleep(5*60+15)
    while time.time() - start < 10*60+30:
        img = d.screenshot(format='opencv')[9:39,208:383]
        # result = img_tools.analyze_skill_via_http(img)     
        if img_tools.click_str_by_server(d,'討伐結束',y_range=(0,308)) :
            print("討伐結束")
            break
    while time.time() - start < 10*60+30:
        img = d.screenshot(format='opencv')[9:39,208:383]
        # result = img_tools.analyze_skill_via_http(img)     
        if img_tools.click_str_by_server(d,'恭喜獲得',y_range=(0,308)) :
            print("恭喜獲得")
            break    
    # img_tools.click_str_by_server(d,'討伐結束',y_range=(0,308))
    time.sleep(1)
    d.click(521,27)
    d.click(521,27)
    d.click(random.randint(191,256),random.randint(886,946)) #再次點擊副本退出
    time.sleep(1)

def fight_snow_country(d:u2.Device,device_id=None, max_success_per_week=2, max_checks_per_week=6):
    if device_id is None:
        device_id = _resolve_device_id(d)
    """執行 '雪國危機'，每週最多記錄 `max_success_per_week` 次成功；同時限制每週檢查次數 `max_checks_per_week`。
    本函式行為：
    - 以 ISO 年-週 作為週鍵，週換時自動重置計數。
    - 每次呼叫都會把檢查次數 (`check_times`) +1 並寫回 JSON（避免無限重試）。
    - 只有在成功時 (`恭喜獲得`) 才把 `count` +1。
    - 若 `count` 已達門檻或 `check_times` 達上限，會提早返回 False。
    """
    import datetime
    # 使用 StoreDataManager 來存放項目紀錄
    store_manager = create_store_manager(device_id)
    title = '雪國危機'
    # 本週識別（ISO 年-週）
    now = datetime.datetime.now()
    year, week, _ = now.isocalendar()
    this_week_key = f"{year}-{week}"
    # 讀取現有紀錄（可能為 None）
    record = store_manager.get_purchase_record(title) or {}
    record_week = record.get('week')
    if record_week != this_week_key:
        # 非本週紀錄，重置計數
        current_count = 0
        check_times = 0
    else:
        current_count = int(record.get('count', 0))
        check_times = int(record.get('check_times', 0))
    # 先判斷是否已達成功次數門檻
    if current_count >= max_success_per_week:
        print(f'本週 {title} 成功次數 {current_count} 已達上限 {max_success_per_week}，跳過檢查。')
        return False
    # 判斷檢查次數是否已達上限
    if check_times >= max_checks_per_week:
        print(f'本週 {title} 檢查次數 {check_times} 已達檢查上限 {max_checks_per_week}，跳過。')
        return False
    # 每次呼叫視為一次檢查（包含未找到入口的情況）
    check_times += 1
    # 先寫入檢查次數與週資訊（count 不變）
    store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key})
    # 進行最多兩次內部嘗試（沿用原本行為）
    for i in range(2):
        if not img_tools.check_str_in_region(d, '雪國危機', y_range=(716,831)):
            print(f'未找到 {title} 入口，本次仍計為一次檢查（檢查次數={check_times}）。')
            # 已經把檢查次數記錄回去，返回 False
            return False
        # 找到入口，嘗試進入並執行戰鬥流程
        img_tools.click_str_by_server(d,'雪國危機',y_range=(716,831))
        time.sleep(3)
        img_tools.click_str_by_server(d,'入場',y_range=(250,296))
        time.sleep(2)
        img_tools.click_str_by_server(d,'前往組隊',y_range=(748,795))
        time.sleep(3)
        img_tools.click_str_by_server(d,'速戰',y_range=(701,748))
        start = time.time()
        MAX_WAIT = 180  # 最多等 180 秒
        phase = None
        while(time.time() - start < MAX_WAIT):
            if img_tools.click_str_by_server(d, '挑戰成功') and phase is None:
                print("挑戰成功")
            if img_tools.click_str_by_server(d, '恭喜獲得',y_range=(161,300),shift_y=500) and phase is None:
                print("恭喜獲得")
            if img_tools.click_str_by_server(d, '當前積分',shift_y=100,y_range=(111,140)) and phase is None:
                print("打boss")
                time.sleep(0.5)
                d.click(268,388)
            if img_tools.click_str_by_server(d, '組隊挑戰') and phase is None:
                time.sleep(10)
                phase = '等打完'
            if img_tools.click_str_by_server(d, '領取討伐獎勵',y_range=(751,802)) and phase == '等打完':
                print("領取討伐獎勵")
                time.sleep(2)
                break
            if img_tools.click_str_by_server(d, '失敗',y_range=(161,300)) and phase == '等打完':
                print("討伐結束")
                phase = False
                time.sleep(2)
                break
        if img_tools.click_str_by_server(d, '領取討伐獎勵',y_range=(751,802)):
            print("領取討伐獎勵")
            time.sleep(2)
            phase ='打完'
            d.click(270,891)
            time.sleep(2)
            d.click(364,555)

        # 判定本次是否成功：依舊以 '恭喜獲得' 判斷
        if phase == '打完':
            current_count += 2
            store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key, 'note': 'win'})
            print(f'本週 {title} 成功次數更新為 {current_count} 次（檢查次數={check_times}）。')
            # 若達到門檻，結束並回傳 True
            if current_count >= max_success_per_week:
                print(f'已達每週成功門檻 {max_success_per_week}，不再檢查。')
                return True
            return True
        elif img_tools.click_str_by_server(d, '恭喜獲得',y_range=(161,300),shift_y=500):
            current_count += 1
            store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key, 'note': 'win'})
            print(f'本週 {title} 成功次數更新為 {current_count} 次（檢查次數={check_times}）。')
            # 若達到門檻，結束並回傳 True
            if current_count >= max_success_per_week:
                print(f'已達每週成功門檻 {max_success_per_week}，不再檢查。')
                return True
            return True
        else:
            # 失敗：只更新檢查次數與註記，不增加成功次數
            store_manager.record_purchase(title, {'count': current_count, 'check_times': check_times, 'week': this_week_key, 'note': 'lose'})
            print(f'本次 {title} 未成功，檢查次數={check_times}。')
            return False
    return False


if __name__ == "__main__":
    # 初始化設備和OCR讀取器
    device = u2.connect('7fe98fc6')  # 替換為你的設備ID
    # img = device.screenshot(format='opencv')
    # img = img [160:254, 9:99]
    # hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
    # print(np.sum(mask_img))
    # if np.sum(mask_img) > 30000 and np.sum(mask_img) < 35000:
    #     print("紅色區域存在")
    # cv2.imshow("img", mask_img)
    # cv2.waitKey(0)
    reader = easyocr.Reader(['ch_tra', 'en'])
    battle_manager = BattleManager(device, reader)
    # img = battle_manager.capture_screenshot()
    # result = reader.readtext(img)
    # print(f"OCR結果：{result}")
    # battle_manager.handle_battle("神樹試煉")
    # # 執行所有戰鬥實例
    battle_manager.execute_all_battles(ip='7fe98fc6',check=True)
    # battle_manager.swipe_screen((0.5, 0.8), (0.5, 0.65), delay=0.5)




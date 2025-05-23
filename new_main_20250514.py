# new_main_20250514.py

import threading
import torch
import miner.simplecnn
# from oralce_manger import oralce
import easyocr
import point
import uiautomator2 as u2
import time
import numpy as np
import cv2
import mask
from Skill import *
from park import *
from family import Family_manager
import new_battle
import random
from Spin_Wheel import spin_wheel
from Mission import mission
from State import state
from Assistant import assistant
import cnn_model
from miner.Mining import MiningPlanner
from cnn_model import ClassName_cnn_model
global lock
lock = False


class GameBot:

    def __init__(self, ip, easyocr_reader, cnn_model, oracle_cnn_model):
        self.ip = ip
        self.d = u2.connect(ip)
        self.easyocr_reader = easyocr_reader
        self.cnn_model = cnn_model
        self.oracle_cnn_model = oracle_cnn_model
        self.wake_up_time = time.time()

        self._initialize_managers()

    def _initialize_managers(self):
        self.parking_manager = ParkingManager(
            device=self.d, reader=self.easyocr_reader, ip=self.ip, cnn_model=self.cnn_model)
        self.battle_manager = new_battle.BattleManager(
            device=self.d, reader=self.easyocr_reader, cnn_model=self.cnn_model)
        self.wheel_manager = spin_wheel(
            device=self.d, cnn_model=self.cnn_model)
        self.mission_manager = mission(device=self.d, ip=self.ip)
        self.family_manager = Family_manager(
            device=self.d, ip=self.ip, cnn_model=self.cnn_model)
        self.state_manager = state(device=self.d, cnn_model=self.cnn_model)
        self.assistant_manager = assistant(d=self.d, cnn_model=self.cnn_model)
        self.mining_planner = MiningPlanner(
            self.oracle_cnn_model, device='cuda' if torch.cuda.is_available() else 'cpu')  # Added device check

    def _handle_initial_state(self):
        # This method would contain the logic from the start of the original main loop:
        # - Checking game state (e.g., "滑動解除節電模式'") and unlocking
        # - Ensuring the game is running (check_in_game, app_start)
        # - Handling "你的帳號在另一個地方登錄" or "退出遊戲"
        # - Handling "公告"
        # - Handling initial rewards
        # Example snippet:
        current_game_state = self.state_manager.get_state()
        if current_game_state == "滑動解除節電模式'":  # Ensure exact string match
            unlock(self.d)

        if not check_in_game(self.d):
            print(f"{self.ip}: Not in game, attempting to start.")
            self.d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
            time.sleep(20)  # Consider a more robust wait here
            wait_time = time.time()
            while True:
                img_text_ocr = self.easyocr_reader.readtext(
                    self.d.screenshot(format='opencv'), detail=0)
                # Assuming stage_by_str and other helpers are accessible
                # Pass self.d if they need it and are not methods yet
                current_stage_ocr = stage_by_str(self.d, img_text_ocr)
                if current_stage_ocr in ["主頁面", "公告", "放置獎勵", "家族", "離線獎勵"]:
                    break
                time.sleep(1)
                if time.time() - wait_time > 60:  # Timeout for starting game
                    self.d.app_stop("com.mxdzz.tw.and")
                    self.d.app_start(
                        package_name="com.mxdzz.tw.and", use_monkey=True)
                    time.sleep(30)  # Wait after restart
                    wait_time = time.time()  # Reset wait time

        img_screenshot = self.d.screenshot(format='opencv')
        ocr_result = self.easyocr_reader.readtext(img_screenshot, detail=0)

        if "你的帳號在另一個地方登錄" in ocr_result or "退出遊戲" in ocr_result:
            if not os.path.exists("other_login"):
                os.makedirs("other_login")
            cv2.imwrite(
                f"other_login/other_login_{self.ip}_{time.time()}.jpg", img_screenshot)
            click_str("退出遊戲", self.d, self.easyocr_reader)
            time.sleep(5)
            click_str("確認登出", self.d, self.easyocr_reader)
            time.sleep(5)  # Wait before restarting
            self.d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
            time.sleep(30)  # Wait for app to start

        # ... (rest of initial state handling: announcements, rewards)
        if "公告" in ocr_result:
            self.d.click(248, 812)  # Example coordinates
            time.sleep(1)
            # Assuming click_white is a helper that takes d
            click_white(self.d)
            time.sleep(1)

        # Handle rewards based on state_manager or OCR
        if self.state_manager.get_state() == "放置獎勵":
            reward(self.d, self.easyocr_reader)
        else:
            ocr_result_rewards = self.easyocr_reader.readtext(
                self.d.screenshot(format='opencv'), detail=0)
            if any(keyword in ocr_result_rewards for keyword in ["放置獎勵", "離線獎勵"]):
                reward(self.d, self.easyocr_reader)
        time.sleep(3)

    def _perform_main_actions(self):
        # This method would contain the core game actions:
        # - family_manager.go_to_family()
        # - farm()
        # - get_Martial_Soul()
        # - get_skill_and_partner()
        # - assistant_manager.go_to_get_assistant()
        # - parking_manager.check_and_park()
        # - battle_manager.execute_all_battles()
        # - oralce()
        # - wheel_manager.spin()
        # Each action should check the current stage and time conditions as in the original code.
        # Example snippet:
        current_time_details = time.localtime()
        # get_stage might need d, cnn_model, easyocr_reader
        current_stage = get_stage(self.d, self.cnn_model, self.easyocr_reader)

        if current_stage == "主頁面":
            self.d.click(random.randint(261, 271), 369)  # Click chest
            time.sleep(1)
            # reward might need d, easyocr_reader
            reward(self.d, self.easyocr_reader)
            time.sleep(3)

        # Need to re-fetch OCR results if stage_by_str relies on fresh text
        ocr_text_for_family = self.easyocr_reader.readtext(
            self.d.screenshot(format='opencv'), detail=0)
        if stage_by_str(self.d, ocr_text_for_family) == "主頁面" or current_time_details.tm_hour == 23:
            self.family_manager.go_to_family()

        # ... (other actions, ensuring to call get_stage before decisions if needed)
        current_stage = get_stage(self.d, self.cnn_model, self.easyocr_reader)
        if current_stage == "主頁面":
            # farm might need d, ip, cnn_model
            farm(self.d, self.ip, self.cnn_model)

        current_stage = get_stage(self.d, self.cnn_model, self.easyocr_reader)
        if current_stage == "主頁面":
            get_Martial_Soul(self.d)  # get_Martial_Soul might need d
            time.sleep(3)

        # ... (continue for all actions from original main loop)

    def _wait_for_next_cycle(self):
        # This method would contain the logic for stopping the app and sleeping
        self.d.app_stop("com.mxdzz.tw.and")

        last_park_info = return_time(self.ip, name="park")
        last_park_timestamp = 0
        if last_park_info:
            if isinstance(last_park_info, dict) and "timestamp" in last_park_info:
                last_park_timestamp = last_park_info["timestamp"]
            elif isinstance(last_park_info, float):  # Handle old format from return_time
                last_park_timestamp = last_park_info

        while True:
            time.sleep(10)  # Check every 10 seconds
            current_time_details = time.localtime()

            # Reconstruct the complex wake-up condition carefully
            # This is a direct translation and could be simplified or made clearer
            wake_condition_met = False
            if ((current_time_details.tm_min == 0 and current_time_details.tm_hour % 4 == 0) or
                (current_time_details.tm_hour < 9 and current_time_details.tm_min == 0) or
                (is_expired(last_park_timestamp, expired_time=60 * 60 * 3 + 59 * 60) and
                 current_time_details.tm_hour > 8 and
                 (time.time() - self.wake_up_time > 60 * 30)) or
                ((current_time_details.tm_hour == 23 and current_time_details.tm_min == 0) or
                 (current_time_details.tm_hour == 23 and current_time_details.tm_min == 45))):
                wake_condition_met = True

            if wake_condition_met:
                break
        self.wake_up_time = time.time()

    def run(self):
        global lock
        while True:
            # Handle the global lock logic for specific IPs
            if 'emulator-5568' in self.ip:
                lock = True
                # Assuming check_on_line is a global helper or adapted
                # It might need self.cnn_model, self.easyocr_reader, and potentially its own 'd' if it connects internally
                for _ in range(6):  # Try 6 times
                    if check_on_line(self.cnn_model, self.easyocr_reader):
                        break
                    time.sleep(60 * 5)  # 5 minutes
                lock = False

            while self.ip == "emulator-5554" and lock:
                time.sleep(60 * 5)

            # Specific IP-based delays from original main
            if 'emulator-5566' in self.ip or 'emulator-5554' in self.ip:
                time.sleep(60 * 5)
            if 'emulator-5560' in self.ip:
                time.sleep(30 * 1)  # 30 seconds

            self._handle_initial_state()
            self._perform_main_actions()
            self._wait_for_next_cycle()


def main_bot_runner(ip, easyocr_reader, cnn_model, oracle_cnn_model):
    # This function is the target for each thread
    bot = GameBot(ip, easyocr_reader, cnn_model, oracle_cnn_model)
    bot.run()


if __name__ == "__main__":

    # Initialize the EasyOCR reader
    easyocr_reader = easyocr.Reader(
        ['ch_tra', 'en'], gpu=True)  # Use GPU if available
    cnn_model = ClassName_cnn_model()
    # Assuming this is a different model for Oracle
    oracle_cnn_model = ClassName_cnn_model()

    # List of IPs to run the bot on
    ips = ["emulator-5554", "emulator-5560", "emulator-5566", "emulator-5568"]

    # Create and start threads for each IP
    threads = []
    for ip in ips:
        thread = threading.Thread(target=main_bot_runner, args=(
            ip, easyocr_reader, cnn_model, oracle_cnn_model))
        thread.start()
        threads.append(thread)

    # Wait for all threads to finish
    for thread in threads:
        thread.join()

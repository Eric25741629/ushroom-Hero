#守護靈
import uiautomator2 as u2
import time
import random
import os
import sys
# 添加父目錄到系統路徑，以便導入上層模組
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import img_tools
import tools
def get_Guardian_Spirit(d: u2.Device):
    #守護靈紅點檢查
    roi = (472, 590, 507, 528)
    # --- 座標與設定 ---
    # 建議將這些座標移至設定檔或檔案開頭，方便管理
    ROI_MAIN_RED_DOT = (472, 590, 507, 528)
    BTN_GUARDIAN_SPIRIT_MAIN = (525, 595)
    BTN_SOME_INNER_BUTTON = (145, 600) # 需要更明確的命名
    ROI_SUMMON_RED_DOT = (881, 910, 426, 465)
    BTN_SUMMON_TAB = (404, 908)
    BTN_DAILY_TEN = (478, 110)
    BTN_PLUS_TEN = (393, 467)
    BTN_PURCHASE = (218, 542)
    BTN_SUMMON_FIVE = (330, 780)
    BTN_SUMMON_ONE = (149, 780)
    BTN_EXIT_GUARDIAN_SPIRIT = (500, 922)

    def click_with_random_offset(x, y, offset=5):
        """在指定座標附近隨機點擊"""
        d.click(x + random.randint(-offset, offset), y + random.randint(-offset, offset))

    if img_tools.check_red_dot(d, ROI_MAIN_RED_DOT):
        print("偵測到主畫面守護靈紅點，進入處理...")
        click_with_random_offset(*BTN_GUARDIAN_SPIRIT_MAIN)
        time.sleep(2)
        
        click_with_random_offset(*BTN_SOME_INNER_BUTTON, offset=50)
        time.sleep(2)

        if img_tools.check_red_dot(d, ROI_SUMMON_RED_DOT):
            print("偵測到召喚分頁紅點，開始處理...")
            click_with_random_offset(*BTN_SUMMON_TAB, offset=20)
            time.sleep(2)

            # 購買每日召喚次數
            click_with_random_offset(*BTN_DAILY_TEN)
            time.sleep(2)
            d.click(*BTN_PLUS_TEN)  # 點擊+10按鈕，通常位置固定，不需偏移
            time.sleep(2)
            click_with_random_offset(*BTN_PURCHASE)
            time.sleep(2)
            tools.click_white(d) # 點擊空白處關閉購買成功視窗
            time.sleep(2)

            # 執行召喚
            print("開始執行召喚...")
            # for _ in range(4): # 召喚5次 * 4 = 20次
            #     click_with_random_offset(*BTN_SUMMON_FIVE)
            #     time.sleep(1)
            #     tools.click_white(d) # 點擊空白處跳過動畫
            #     time.sleep(0.5)

            for _ in range(2): # 召喚1次 * 2 = 2次
                click_with_random_offset(*BTN_SUMMON_ONE)
                time.sleep(1)
                tools.click_white(d) # 點擊空白處跳過動畫
                time.sleep(0.5)

            # 多次點擊確保關閉所有彈出視窗
            tools.click_white(d)
            tools.click_white(d)
            time.sleep(0.5)

        else:
            print("未偵測到召喚分頁紅點。")            
        click_with_random_offset(*BTN_EXIT_GUARDIAN_SPIRIT)
        time.sleep(2)
        # 退出守護靈介面
        d.click(18,600)     #點擊回到主頁  
        time.sleep(2)  
if __name__ == "__main__":
    import uiautomator2 as u2
    d = u2.connect()
    get_Guardian_Spirit(d)
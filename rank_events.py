import img_tools
import json_manager
import time
import bot_state
import datetime
import logging
import os

logger = logging.getLogger(__name__)

"""週期檢查邏輯已移至 json_manager。"""
def park_spring(d, ip):
    # 1. 取得當前台北時間
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    weekday = now.weekday() # 0=Mon, 1=Tue, 2=Wed, 3=Thu...
    
    # 2. 嚴格日期限制：僅限週二 (1) 或週三 (2)
    if weekday not in [1, 2]:
        # logger.info(f"[{ip}] 非週二或週三，跳過衝刺-發條任務。")
        return

    # 週三 22:00 後活動結算，不執行
    if weekday == 2 and now.hour >= 22:
        logger.info(f"[{ip}] 週三 22:00 後已結算，跳過執行。")
        return

    # 3. 檢查本週是否已經執行過
    # return_time 會回傳一個 dict，其中 is_next_week 為 True 代表本週尚未有紀錄
    record = json_manager.return_time(ip, name='衝刺-發條')
    if record and not record.get('is_next_week', True):
        logger.info(f"[{ip}] 本週已執行過衝刺-發條，不再重複執行。")
        return

    # 4. 判斷是否為衝刺週期週 (每 4 週一次)
    should_run, _ = json_manager.should_execute_cycle(
        ip, '衝刺-發條', cycle_weeks=4, allowed_weekdays=[1, 2]
    )
    
    if should_run:
        logger.info(f"[{ip}] 本週為衝刺-發條執行週，開始執行坐騎強化...")
        bot_state.update_state(ip, task="衝刺活動", step="執行中 (坐騎強化/發條)")
        
        try:
            d.click(230, 650) # 點擊坐騎/停車場入口
            time.sleep(2)
            
            # 點擊 '養' (坐騎培養)
            res_raise = img_tools.click_str_by_server(d, '一鍵', y_range=(720, 773), x_range=(293, 403),wait_timeout=3)
            if not res_raise:
                logger.warning(f"[{ip}] 找不到 '一鍵' 按鈕，截圖分析...")
                d.screenshot(f"debug_mount_fail_{ip}.jpg")
                return # 找不到就退出，避免後續報錯

            time.sleep(1)
            
            # 點擊 '無限時' (選擇發條類型)
            res_unlimited = img_tools.click_str_by_server(d, '無限時', y_range=(433, 459), shift_y=20)
            if not res_unlimited:
                logger.warning(f"[{ip}] 找不到 '無限時' 按鈕，可能已在正確頁面或 UI 改變，嘗試截圖...")
                d.screenshot(f"debug_spring_fail_{ip}.jpg")
                # 這裡不一定 return，因為有時候已經預設選好了，嘗試繼續
            
            time.sleep(1)
            
            # 防呆輸入邏輯
            def safe_send_keys(device, text):
                try:
                    # 嘗試標準輸入 (這裡容易出錯)
                    device.send_keys(text, clear=True)
                except Exception as e:
                    logger.warning(f"[{ip}] 標準輸入失敗: {e}，嘗試備援方案...")
                    # 備援方案：先點擊中心點確保聚焦
                    # 假設輸入框在 (271, 529) 附近
                    device.click(271, 529) 
                    time.sleep(0.5)
                    # 透過 adb shell 直接輸入
                    os.system(f"adb -s {ip} shell input text {text}")

            safe_send_keys(d, "12")
            time.sleep(0.5)
            for i in range(3):
                time.sleep(0.5)
                d.click(271, 556) # 確認輸入
            time.sleep(1)
            d.click(331, 553) # 確認輸入
            
            # 使用發條 (點擊使用按鈕或空白處)
            for i in range(3):
                d.click(455, 145) #關閉彈窗
                time.sleep(0.5)
                
            time.sleep(1)
            d.click(271, 893) # 退出/返回
            time.sleep(2)
            
            # 成功執行後才紀錄時間
            json_manager.time_recording(ip, name='衝刺-發條')
            logger.info(f"[{ip}] 坐騎強化任務完成。")
            
        except Exception as e:
            logger.error(f"[{ip}] 坐騎強化過程發生未預期錯誤: {e}")
            d.screenshot(f"error_mount_{ip}.jpg")

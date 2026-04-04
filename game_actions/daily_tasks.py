import time
from json_manager import return_time, time_recording
from utils.logging_utils import logger
import new_cnn.cnn_model as cnn_model_module
import img_tools

def daily_acceleration(d, ip, Cnn_model=None):
    """
    執行每日加速任務
    
    Args:
        d: uiautomator2 device 物件
        ip: 設備 IP
        Cnn_model: CNN 模型實例 (可選)
    """
    record = return_time(ip, name="daily_acceleration")
    should_execute = False
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_day", False)
    if should_execute:
        logger.info(f"[{ip}] 執行每日加速")
        
        # 1. 進入家園/研究中心頁面
        # 使用帶超時的循環檢查
        success_enter = False
        start_time = time.time()
        attempt = 0
        
        while time.time() - start_time < 30: # 最多等待 30 秒
            d.click(321, 913) # 點擊家園按鈕
            time.sleep(1.5)
            
            if Cnn_model is not None:
                cnn_result = cnn_model_module.predict_image(
                    Cnn_model, d.screenshot(format='pillow'))
                if cnn_result == 'homeplace':
                    success_enter = True
                    break
            else:
                success_enter = True
                break
                
            attempt += 1
            if attempt > 5: break
            time.sleep(1)
            
        if not success_enter:
            logger.warning(f"[{ip}] 無法進入家園頁面，跳過每日加速")
            return

        # 2. 執行研究中心加速
        logger.info(f"[{ip}] 進入研究中心")
        d.click(452, 218) # 點擊研究中心
        time.sleep(1.5)
        
        for i in range(5):
            d.click(168, 814) # 跳過 30 分鐘
            time.sleep(0.8)
            
        # 3. 返回主頁面
        d.click(487, 923) # 點擊返回 (關閉研究中心)
        time.sleep(1.5)
        d.click(321, 919) # 點擊家園返回 (回到主介面)
        time.sleep(1)
        
        time_recording(ip, name="daily_acceleration")
        logger.info(f"[{ip}] 每日加速完成")
    else:
        logger.info("今日已執行過每日加速，跳過")


def click_arena_challenges(d, ip):
    """
    執行競技場每日挑戰任務
    
    Args:
        d: uiautomator2 device 物件
        ip: 設備 IP
    """
    record = return_time(ip, name="arena_challenges")
    should_execute = False
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_day", False)
    
    if should_execute:
        logger.info(f"[{ip}] 執行競技場每日挑戰")
        try:
            # 進入競技場
            img_tools.click_str_by_server(d, '競技場', shift_y=-20, x_range=(0, 160))
            time.sleep(0.5)
            img_tools.click_str_by_server(d, '挑戰', wait_timeout=5, y_range=(789, 855))
            
            # 執行 3 次挑戰
            for i in range(3):
                logger.info(f"[{ip}] 競技場挑戰 {i+1}/3")
                img_tools.click_str_by_server(d, '挑戰', y_range=(592, 674), wait_timeout=5)  # 從下面的挑戰開始點
                
                # 等待戰鬥完成(超過15秒點擊跳過)
                start_time = time.time()
                while True:
                    time.sleep(1)
                    check_str = img_tools.wait_for_any_text(d, ['勝利', '對決', '跳過'], y_range=(100, 800), timeout=3)
                    if check_str == '跳過':
                        time.sleep(1)
                    elif check_str in ['勝利', '對決']:
                        logger.info(f"[{ip}] 挑戰 {i+1} 完成")
                        break
                    
                    # 超時保護(最多等待 60 秒)
                    if time.time() - start_time > 60:
                        logger.warning(f"[{ip}] 挑戰 {i+1} 超時，強制結束")
                        break
                        
                time.sleep(2)
            
            # 刷新對手並返回
            img_tools.click_str_by_server(d, '刷新', y_range=(711, 782), shift_y=60)
            time.sleep(1)
            img_tools.click_str_by_server(d, '記錄', y_range=(831, 865), x_range=(437, 521), shift_y=60, wait_timeout=5)
            time.sleep(1)
            
            # 記錄完成
            time_recording(ip, name="arena_challenges")
            logger.info(f"[{ip}] 競技場每日挑戰完成")
            
        except Exception as e:
            logger.error(f"[{ip}] 競技場挑戰執行失敗: {e}")
    else:
        logger.info(f"[{ip}] 今日已執行過競技場挑戰，跳過")


def claim_daily_free_pack(d, ip):
    """
    領取每日自選禮包免費獎勵
    
    Args:
        d: uiautomator2 device 物件
        ip: 設備 IP
    """
    record = return_time(ip, name="daily_free_pack")
    should_execute = False
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_day", False)
    
    if should_execute:
        logger.info(f"[{ip}] 檢查每日自選禮包")
        try:
            # 1. 點擊自選禮包
            if img_tools.click_str_by_server(d, '自選禮包', wait_timeout=5):
                time.sleep(2)
                # 2. 點擊免費
                if img_tools.click_str_by_server(d, '免費', y_range=(334, 380), wait_timeout=5):
                    time.sleep(1.5)
                    # 3. 點擊恭喜獲得 (關閉獎勵彈窗)
                    img_tools.click_str_by_server(d, '恭喜獲得', wait_timeout=5)
                    time.sleep(1.5)
                    # 4. 點擊特定座標關閉禮包介面
                    d.click(274, 841)
                    logger.info(f"[{ip}] ✓ 成功領取每日自選禮包")
                    time_recording(ip, name="daily_free_pack")
                    return True
                else:
                    logger.info(f"[{ip}] ! 未找到免費獎勵，可能已領取")
                    # 嘗試關閉介面
                    d.click(274, 841)
                    time_recording(ip, name="daily_free_pack")
                    return False
            return False
        except Exception as e:
            logger.error(f"[{ip}] 領取自選禮包時發生錯誤: {e}")
            return False
    else:
        # logger.info(f"[{ip}] 今日已領取過自選禮包，跳過")
        pass

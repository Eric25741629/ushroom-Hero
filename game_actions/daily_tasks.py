import time
from json_manager import time_recording
from utils.logging_utils import logger
import new_cnn.cnn_model as cnn_model_module
import img_tools
from game_actions.navigation import navigate_to_main_page
from game_actions.task_due import is_due

def daily_acceleration(d, ip, Cnn_model=None):
    """
    執行每日加速任務
    
    Args:
        d: uiautomator2 device 物件
        ip: 設備 IP
        Cnn_model: CNN 模型實例 (可選)
    """
    should_execute = is_due("每日加速", ip)
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
            # 失敗時遊戲可能停在未知頁面，先導回主頁面再返回，
            # 否則後續任務會連鎖判為「不在主頁面」而中止整條 pipeline。
            navigate_to_main_page(d, Cnn_model, ip, label="daily_accel")
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
    should_execute = is_due("競技場挑戰", ip)

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

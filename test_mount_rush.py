import uiautomator2 as u2
import rank_events
import logging
import bot_state
from device import get_adb_devices

# 設定簡單的 logger
logging.basicConfig(level=logging.INFO)

def test_mount_rush():
    print("=== 坐騎強化 (衝刺-發條) 獨立測試 ===")
    
    # 1. 取得設備
    devices = get_adb_devices()
    if not devices:
        print("未發現 ADB 設備，請確認模擬器已啟動。")
        return
        
    ip = devices[0] # 測試第一台設備
    print(f"正在連線設備: {ip}")
    
    d = u2.connect(ip)
    
    # 2. 初始化 bot_state (因為 rank_events 會用到)
    bot_state.init_device(ip)
    
    # 3. 執行測試
    print("開始執行 rank_events.park_spring...")
    try:
        # 強制執行 (繞過 json_manager 的週期檢查)
        # 這裡我們稍微 hack 一下，直接呼叫內部的邏輯，或是先清除紀錄
        import json_manager
        json_manager.temporary_reset_cycles = lambda: None # 避免依賴
        
        # 清除紀錄以確保執行
        import os, json
        json_file = f"{ip}.json"
        if os.path.exists(json_file):
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if "衝刺-發條" in data:
                del data["衝刺-發條"]
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
                print("已清除舊紀錄，強制執行。")

        rank_events.park_spring(d, ip)
        print("測試完成。")
        
    except Exception as e:
        print(f"測試失敗: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_mount_rush()

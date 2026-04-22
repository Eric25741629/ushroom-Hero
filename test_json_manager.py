#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試json_manager模組的功能
"""

from json_manager import (
    create_park_manager, 
    create_time_manager, 
    create_store_manager,
    time_recording,
    return_time,
    record_json,
    check_json
)

def test_park_manager():
    """測試停車場管理器"""
    print("=== 測試停車場管理器 ===")
    manager = create_park_manager("adb-fc65396d-4LPqmI (2)._adb-tls-connect._tcp")
    
    # 測試應該購買（初次）
    print(f"初次檢查是否應該購買daily: {manager.should_purchase('daily')}")
    print(f"初次檢查是否應該購買weekly: {manager.should_purchase('weekly')}")
    
    # 記錄購買
    manager.record_purchase('daily', 1)
    manager.record_purchase('weekly', 1)
    
    # 再次檢查
    print(f"記錄後檢查是否應該購買daily: {manager.should_purchase('daily')}")  # 應該還能購買（< 2）
    print(f"記錄後檢查是否應該購買weekly: {manager.should_purchase('weekly')}")  # 應該還能購買（< 2）
    
    # 獲取購買數據
    data = manager.get_buy_data()
    print(f"購買數據: {data}")
    
def test_time_manager():
    """測試時間記錄管理器"""
    print("\n=== 測試時間記錄管理器 ===")
    manager = create_time_manager("test_device")
    
    # 記錄時間
    manager.record_time("park")
    manager.record_time("Martial_Soul")
    
    # 檢查記錄
    park_record = manager.get_time_record("park")
    martial_record = manager.get_time_record("Martial_Soul")
    
    print(f"停車記錄: {park_record}")
    print(f"武魂記錄: {martial_record}")
    
    # 測試是否為同一天
    print(f"停車記錄是同一天: {manager.is_same_day('park')}")
    print(f"武魂記錄是同一天: {manager.is_same_day('Martial_Soul')}")

def test_store_manager():
    """測試商店管理器"""
    print("\n=== 測試商店管理器 ===")
    manager = create_store_manager("test_device")
    
    # 記錄購買
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manager.record_purchase("挖礦", {"last_time": now_str})
    manager.record_purchase("神秘商人", {"count": 5, "last_time": now_str})
    
    # 檢查記錄
    mining_record = manager.get_purchase_record("挖礦")
    shop_record = manager.get_purchase_record("神秘商人")
    
    print(f"挖礦記錄: {mining_record}")
    print(f"神秘商人記錄: {shop_record}")
    
    # 檢查是否今天已購買
    print(f"挖礦今天已購買: {manager.is_purchased_today('挖礦')}")
    print(f"神秘商人今天已購買: {manager.is_purchased_today('神秘商人')}")
    
    # is_purchase_expired 已移除，改用 is_record_expired + return_time


def test_backward_compatibility():
    """測試向後兼容性"""
    print("\n=== 測試向後兼容性 ===")
    
    # 測試舊的函數接口
    time_recording("test_device", "test_action")
    record = return_time("test_device", "test_action")
    print(f"時間記錄: {record}")
    
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record_json("test_device", "test_purchase", {"last_time": now_str})
    purchase_record = check_json("test_device", "test_purchase")
    print(f"購買記錄: {purchase_record}")

def interactive_test():
    """互動式測試"""
    print("JSON 數據管理器互動式測試")
    print("=" * 50)
    
    while True:
        # 獲取用戶輸入的設備 IP/ID
        device_id = input("\n請輸入設備 IP 或 ID (輸入 'quit' 退出): ").strip()
        
        if device_id.lower() in ['quit', 'exit', 'q']:
            print("退出測試程序")
            break
        
        if not device_id:
            print("請輸入有效的設備 ID")
            continue
        
        print(f"\n開始測試設備: {device_id}")
        print("=" * 30)
        
        try:
            # 測試停車場管理器
            print(f"\n=== 停車場管理器測試 (設備: {device_id}) ===")
            park_mgr = create_park_manager(device_id)
            
            # 顯示當前狀態
            daily_ts, daily_num, weekly_ts, weekly_num = park_mgr.get_buy_data()
            print(f"當前狀態:")
            print(f"  Daily - 購買次數: {daily_num}")
            print(f"  Weekly - 購買次數: {weekly_num}")
            
            # 檢查是否需要購買
            should_daily = park_mgr.should_purchase('daily', max_purchases=2)
            should_weekly = park_mgr.should_purchase('weekly', max_purchases=2)
            print(f"  需要每日購買: {should_daily}")
            print(f"  需要每週購買: {should_weekly}")
            
            # 詢問是否要記錄測試購買
            if input("是否要記錄測試購買？(y/n): ").lower() == 'y':
                park_mgr.record_purchase('daily', daily_num + 1, 1)
                park_mgr.record_purchase('weekly', weekly_num + 1, 1)
                print("已記錄測試購買")
            
            # 測試時間記錄管理器
            print(f"\n=== 時間記錄管理器測試 (設備: {device_id}) ===")
            time_mgr = create_time_manager(device_id)
            
            # 記錄測試時間
            time_mgr.record_time("interactive_test")
            print("已記錄 interactive_test 時間")
            
            # 檢查記錄
            record = time_mgr.get_time_record("interactive_test")
            if record:
                print(f"時間記錄: {record}")
                print(f"是否為同一天: {time_mgr.is_same_day('interactive_test')}")
            
            # 測試商店管理器
            print(f"\n=== 商店管理器測試 (設備: {device_id}) ===")
            store_mgr = create_store_manager(device_id)
            
            # 記錄測試購買
            import datetime
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            store_mgr.record_purchase("互動測試商品", {
                "quantity": 1,
                "last_time": now_str,
                "test_mode": True
            })
            
            # 檢查記錄
            purchase_record = store_mgr.get_purchase_record("互動測試商品")
            if purchase_record:
                print(f"購買記錄: {purchase_record}")
                print(f"今天已購買: {store_mgr.is_purchased_today('互動測試商品')}")
            
            print(f"\n設備 {device_id} 測試完成!")
            
        except Exception as e:
            print(f"測試過程中發生錯誤: {e}")
            import traceback
            traceback.print_exc()

def show_menu():
    """顯示選單"""
    print("\n請選擇測試模式:")
    print("1. 預設測試 (使用固定設備ID)")
    print("2. 互動測試 (輸入您的設備IP)")
    print("3. 停車場管理器測試")
    print("4. 時間記錄管理器測試")
    print("5. 商店管理器測試")
    print("6. 向後兼容性測試")
    print("0. 退出")
    return input("請輸入選項 (0-6): ").strip()

def run_specific_test():
    """運行特定測試"""
    while True:
        choice = show_menu()
        
        if choice == '0':
            print("退出程序")
            break
        elif choice == '1':
            print("運行預設測試...")
            test_park_manager()
            test_time_manager()
            test_store_manager()
            test_backward_compatibility()
        elif choice == '2':
            interactive_test()
        elif choice == '3':
            test_park_manager()
        elif choice == '4':
            test_time_manager()
        elif choice == '5':
            test_store_manager()
        elif choice == '6':
            test_backward_compatibility()
        else:
            print("無效選項，請重新選擇")

if __name__ == "__main__":
    print("JSON 數據管理器測試程序")
    print("版本: 2.0")
    print("=" * 50)
    
    # 顯示使用說明
    print("\n使用說明:")
    print("- 您可以選擇預設測試或輸入自己的設備IP進行測試")
    print("- 設備IP格式例如: emulator-5554, 192.168.1.100, adb-fc65396d等")
    print("- 所有測試都會在當前目錄創建對應的JSON文件")
    
    run_specific_test()
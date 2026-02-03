"""
每日贈禮任務模塊
使用 json_manager 管理每日執行狀態
"""

import uiautomator2 as u2
from json_manager import JsonDataManager
from img_tools import click_str_by_server, wait_for_any_text
import time
import random


def buy_gift_for_friend_once(d, ip_str='emulator-5554'):
    """
    單次執行贈禮給好友的邏輯
    
    Args:
        d: uiautomator2 設備對象
        ip_str: 設備IP字串
    
    Returns:
        bool: 是否執行成功
    """
    if ip_str == 'emulator-5556':
        print("設備 emulator-5556 跳過此任務")
        return False
    
    try:
        click_str_by_server(d, '家園', shift_y=-20, y_range=(934, 959))
        time.sleep(0.5)
        click_str_by_server(d, '比格先生', shift_y=-20, wait_timeout=5, y_range=(0, 195))
        time.sleep(0.5)
        click_str_by_server(d, '贈禮', shift_y=-20, wait_timeout=5, y_range=(258, 300))
        time.sleep(0.5)
        click_str_by_server(d, '+10', wait_timeout=5, y_range=(499, 544), 
                          shift_x=random.randint(-5, 5), shift_y=random.randint(-5, 5))
        time.sleep(0.5)
        click_str_by_server(d, '使用', wait_timeout=5, y_range=(635, 690), 
                          shift_x=random.randint(-5, 5), shift_y=random.randint(-5, 5))
        time.sleep(0.5)
        click_str_by_server(d, '使用', wait_timeout=5, y_range=(635, 690), 
                          shift_x=random.randint(-5, 5), shift_y=70)
        
        time.sleep(0.3)
        d.click(156, 260)
        
        click_str_by_server(d, '切磋', wait_timeout=5, y_range=(721, 771))
        time.sleep(3)
        # 等待戰鬥結束
        result = wait_for_any_text(
            d, 
            text_list=['跳過', '勝利', '失敗'],
            timeout=30,
            check_interval=0.5,
            y_range=(167, 771),
            click_if_found=True
        )
        
        if result:
            print(f"戰鬥結果: {result}")
        
        click_str_by_server(d, '舉報', wait_timeout=5, shift_x=60, shift_y=70, y_range=(784, 846))
        time.sleep(0.5)
        # d.click(272,879)
        time.sleep(3)
        
        return True
        
    except Exception as e:
        print(f"執行贈禮任務時發生錯誤: {e}")
        return False


def buy_gift_for_friend_daily(d, ip_str='emulator-5554', times=1):
    """
    每日贈禮任務（自動管理執行狀態）
    
    Args:
        d: uiautomator2 設備對象
        ip_str: 設備IP字串
        times: 每日執行次數（默認10次）
    
    Returns:
        dict: 執行結果統計
            {
                "executed": bool,  # 是否執行了任務
                "reason": str,     # 如果未執行，原因是什麼
                "total_count": int,
                "success_count": int,
                "fail_count": int,
                "last_record": dict  # 上次執行記錄（如果存在）
            }
    """
    # 初始化 JSON 管理器
    manager = JsonDataManager(ip_str)
    task_name = "daily_gift_friend"
    
    # 檢查今天是否已執行過
    if manager.is_same_day(task_name):
        record = manager.get_record(task_name)
        print(f"✓ 今日贈禮任務已完成")
        print(f"  執行時間: {record.get('datetime', 'Unknown')}")
        print(f"  成功次數: {record.get('success_count', 0)}/{record.get('total_count', 0)}")
        return {
            "executed": False,
            "reason": "already_done_today",
            "last_record": record
        }
    
    print(f"開始執行每日贈禮任務（共 {times} 次）...")
    
    success_count = 0
    fail_count = 0
    
    for i in range(times):
        print(f"\n--- 第 {i+1}/{times} 次贈禮 ---")
        
        if buy_gift_for_friend_once(d, ip_str):
            success_count += 1
            print(f"✓ 第 {i+1} 次成功")
        else:
            fail_count += 1
            print(f"✗ 第 {i+1} 次失敗")
        
        # 間隔一下避免操作過快
        if i < times - 1:
            time.sleep(1)
    
    # 記錄執行結果
    manager.record_timestamp(task_name, {
        "status": "completed",
        "total_count": times,
        "success_count": success_count,
        "fail_count": fail_count
    })
    
    print(f"\n{'='*50}")
    print(f"每日贈禮任務完成！")
    print(f"成功: {success_count} 次 | 失敗: {fail_count} 次")
    print(f"{'='*50}")
    
    return {
        "executed": True,
        "total_count": times,
        "success_count": success_count,
        "fail_count": fail_count
    }


def check_task_status(ip_str='emulator-5554'):
    """
    查看任務執行狀態
    
    Args:
        ip_str: 設備IP字串
    
    Returns:
        dict: 任務記錄，如果不存在則返回 None
    """
    manager = JsonDataManager(ip_str, file_suffix="gift_task")
    record = manager.get_record("daily_gift_friend")
    
    if record:
        print("任務執行記錄:")
        print(f"  執行時間: {record.get('datetime', 'Unknown')}")
        print(f"  狀態: {record.get('status', 'Unknown')}")
        print(f"  總次數: {record.get('total_count', 0)}")
        print(f"  成功次數: {record.get('success_count', 0)}")
        print(f"  失敗次數: {record.get('fail_count', 0)}")
        
        if manager.is_same_day("daily_gift_friend"):
            print("\n✓ 今天已執行過此任務")
        else:
            print("\n✗ 今天尚未執行此任務")
    else:
        print("尚無任務執行記錄")
    
    return record


def reset_task(ip_str='emulator-5554'):
    """
    重置任務記錄（用於測試或強制重新執行）
    
    Args:
        ip_str: 設備IP字串
    
    Returns:
        bool: 是否重置成功
    """
    manager = JsonDataManager(ip_str)
    data = manager.load_data()
    
    if "daily_gift_friend" in data:
        del data["daily_gift_friend"]
        manager.save_data(data)
        print("✓ 已刪除任務記錄，可以重新執行")
        return True
    else:
        print("✗ 找不到任務記錄")
        return False


if __name__ == "__main__":
    # 使用示例
    device_id = 'emulator-5554'
    
    # 連接設備
    d = u2.connect(device_id)
    print(f"已連接設備: {device_id}")
    
    # 方法1: 執行每日任務（自動管理）
    result = buy_gift_for_friend_daily(d, device_id, times=1)
    print(f"\n執行結果: {result}")
    
    # 方法2: 查看任務狀態
    # check_task_status(device_id)
    
    # 方法3: 重置任務（用於測試）
    # reset_task(device_id)

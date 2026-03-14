#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
檢查現有JSON數據的工具，不會修改任何現有數據
"""

import json
import os
import datetime
import time
import pytz

def check_device_data(device_id: str):
    """檢查指定設備的所有JSON數據文件"""
    print(f"=== 檢查設備: {device_id} ===\n")
    
    # 檢查不同類型的JSON文件
    file_types = [
        ("", "停車場數據"),
        ("test", "時間記錄數據"), 
        ("test123", "商店購買數據")
    ]
    
    timezone = pytz.timezone('Asia/Taipei')
    current_time = datetime.datetime.now(timezone)
    
    for suffix, description in file_types:
        if suffix:
            filename = f"{device_id}_{suffix}.json"
        else:
            filename = f"{device_id}.json"
            
        print(f"--- {description} ({filename}) ---")
        
        if not os.path.exists(filename):
            print("❌ 文件不存在")
            print()
            continue
            
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            print("✅ 文件存在且格式正確")
            print(f"📁 文件大小: {os.path.getsize(filename)} bytes")
            print(f"📊 數據項目數量: {len(data)}")
            print("🔍 數據內容:")
            
            # 詳細分析每個數據項目
            for key, value in data.items():
                print(f"  • {key}:")
                if isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if 'timestamp' in sub_key and isinstance(sub_value, (int, float)) and sub_value > 0:
                            try:
                                dt = datetime.datetime.fromtimestamp(sub_value, timezone)
                                time_diff = current_time - dt
                                print(f"    - {sub_key}: {sub_value} ({dt.strftime('%Y-%m-%d %H:%M:%S')}, {time_diff.days}天前)")
                            except (OSError, ValueError):
                                print(f"    - {sub_key}: {sub_value} (無效時間戳)")
                        elif 'time' in sub_key and isinstance(sub_value, str):
                            try:
                                dt = datetime.datetime.strptime(sub_value, '%Y-%m-%d %H:%M:%S')
                                time_diff = current_time.replace(tzinfo=None) - dt
                                print(f"    - {sub_key}: {sub_value} ({time_diff.days}天前)")
                            except ValueError:
                                print(f"    - {sub_key}: {sub_value}")
                        else:
                            print(f"    - {sub_key}: {sub_value}")
                elif 'timestamp' in key and isinstance(value, (int, float)) and value > 0:
                    try:
                        dt = datetime.datetime.fromtimestamp(value, timezone)
                        time_diff = current_time - dt
                        print(f"    值: {value} ({dt.strftime('%Y-%m-%d %H:%M:%S')}, {time_diff.days}天前)")
                    except (OSError, ValueError):
                        print(f"    值: {value} (無效時間戳)")
                else:
                    print(f"    值: {value}")
                    
            # 特殊分析
            if suffix == "":  # 停車場數據
                print("\n🚗 停車場數據分析:")
                if 'daily' in data:
                    daily_ts = data['daily'].get('car_market_timestamp', 0)
                    daily_num = data['daily'].get('car_market_buy_num', 0)
                    if daily_ts > 0:
                        try:
                            daily_date = datetime.datetime.fromtimestamp(daily_ts, timezone).date()
                            is_today = daily_date == current_time.date()
                            print(f"  • Daily: 已購買 {daily_num} 次，{'今天' if is_today else '不是今天'}的記錄")
                        except:
                            print(f"  • Daily: 已購買 {daily_num} 次，時間戳無效")
                    else:
                        print(f"  • Daily: 已購買 {daily_num} 次，無時間記錄")
                        
                if 'weekly' in data:
                    weekly_ts = data['weekly'].get('car_market_timestamp', 0)
                    weekly_num = data['weekly'].get('car_market_buy_num', 0)
                    if weekly_ts > 0:
                        try:
                            weekly_date = datetime.datetime.fromtimestamp(weekly_ts, timezone).date()
                            weekly_year, weekly_week, _ = weekly_date.isocalendar()
                            current_year, current_week, _ = current_time.date().isocalendar()
                            is_this_week = (weekly_year, weekly_week) == (current_year, current_week)
                            print(f"  • Weekly: 已購買 {weekly_num} 次，{'本週' if is_this_week else '不是本週'}的記錄")
                        except:
                            print(f"  • Weekly: 已購買 {weekly_num} 次，時間戳無效")
                    else:
                        print(f"  • Weekly: 已購買 {weekly_num} 次，無時間記錄")
                        
            elif suffix == "test123":  # 商店數據
                print("\n🛒 商店購買分析:")
                for item, record in data.items():
                    if isinstance(record, dict) and 'last_time' in record:
                        try:
                            last_dt = datetime.datetime.strptime(record['last_time'], '%Y-%m-%d %H:%M:%S')
                            is_today = last_dt.date() == current_time.replace(tzinfo=None).date()
                            print(f"  • {item}: {'今天已購買' if is_today else '今天未購買'}")
                        except:
                            print(f"  • {item}: 時間格式錯誤")
                            
        except json.JSONDecodeError as e:
            print(f"❌ JSON格式錯誤: {e}")
        except Exception as e:
            print(f"❌ 讀取失敗: {e}")
            
        print()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        device_id = sys.argv[1]
    else:
        device_id = "adb-fc65396d-4LPqmI (2)._adb-tls-connect._tcp"
        
    check_device_data(device_id)
import json
import os
import datetime

# 設定上週的時間點 (2026-02-05)
last_week_dt = datetime.datetime(2026, 2, 5, 12, 0, 0)
last_week_ts = last_week_dt.timestamp()

record_data = {
    "timestamp": last_week_ts,
    "date": last_week_dt.strftime("%Y-%m-%d"),
    "datetime": last_week_dt.strftime("%Y-%m-%d %H:%M:%S")
}

affected_files = [
    "7fe98fc6.json",
    "emulator-5554.json",
    "emulator-5556.json",
    "emulator-5560.json"
]

for filename in affected_files:
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 補回紀錄
            data["衝刺-發條"] = record_data
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            print(f"✓ 成功補回紀錄: {filename}")
        except Exception as e:
            print(f"✗ 處理 {filename} 時發生錯誤: {e}")
    else:
        print(f"! 找不到檔案: {filename}")

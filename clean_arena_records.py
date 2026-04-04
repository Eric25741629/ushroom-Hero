import json
import os
import glob

# 搜尋所有 JSON 檔案
json_files = glob.glob("*.json")
keys_to_remove = ["mushroom_arena_cycle_start", "mushroom_arena_daily"]

for file_path in json_files:
    try:
        # 讀取並解析 JSON
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 檢查並移除指定鍵值
        changed = False
        for key in keys_to_remove:
            if key in data:
                del data[key]
                changed = True
        
        # 如果有變動，寫回檔案
        if changed:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            print(f"✓ Updated: {file_path}")
            
    except Exception as e:
        # 忽略非資料類型的 JSON (例如 manifest.json) 或損壞的檔案
        continue

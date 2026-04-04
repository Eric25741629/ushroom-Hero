import json
import os
import config_manager

CONFIG_FILE = "bot_config.json"

def update():
    print("正在更新 bot_config.json ...")
    
    if not os.path.exists(CONFIG_FILE):
        print("設定檔不存在，跳過更新（下次啟動會自動建立）。")
        return

    # 1. 讀取現有設定
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"讀取失敗: {e}")
        return

    # 2. 檢查結構
    if "global" not in data:
        data["global"] = config_manager.DEFAULT_GLOBAL_CONFIG.copy()
        print("補上 global 區塊")
    
    # 3. 補上 host_settings
    default_hosts = config_manager.DEFAULT_GLOBAL_CONFIG["host_settings"]
    if "host_settings" not in data["global"]:
        data["global"]["host_settings"] = default_hosts
        print("補上 host_settings 區塊")
    else:
        # 合併（如果沒有才加）
        for k, v in default_hosts.items():
            if k not in data["global"]["host_settings"]:
                data["global"]["host_settings"][k] = v
                print(f"補上範例主機: {k}")

    # 4. 寫回
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print("更新完成！")
    except Exception as e:
        print(f"寫入失敗: {e}")

if __name__ == "__main__":
    update()

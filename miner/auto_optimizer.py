import json
import os
import time

CONFIG_FILE = "miner/planner_config.json"

def get_current_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "cost_pickaxe": 1.0,
        "cost_item": 2.99,
        "weight_h": 1.5,
        "weight_chest": 10.0,
        "weight_depth": 5.0
    }

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
    print(f"--- 已更新配置 ---")
    print(json.dumps(config, indent=4))

def optimize(performance_data):
    """
    根據效能數據調整參數的簡單邏輯。
    performance_data: { "avg_cost_per_step": float, "stuck_detected": bool }
    """
    config = get_current_config()
    
    if performance_data.get("stuck_detected"):
        print("偵測到卡死，增加 A* 啟發式權重以加快搜尋，並降低道具成本鼓勵突圍")
        config["weight_h"] += 0.5
        config["cost_item"] = max(1.5, config["cost_item"] - 0.2)
    
    elif performance_data.get("avg_cost_per_step", 0) > 1.2:
        print("平均每步成本過高，調降道具成本權重")
        config["cost_item"] = max(1.0, config["cost_item"] - 0.1)
    
    save_config(config)

if __name__ == "__main__":
    # 範例：模擬一次優化過程
    # 在實際運行中，這可以由另一個 AI 代理或監控腳本調用
    print("AI 自動優化器啟動")
    sample_perf = {
        "avg_cost_per_step": 1.5,
        "stuck_detected": False
    }
    optimize(sample_perf)

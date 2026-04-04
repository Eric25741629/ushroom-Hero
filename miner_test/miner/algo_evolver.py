import os
import json
import requests
import re
import shutil

SMART_PLANNER_PATH = "miner/planning/smart_planner.py"

def read_current_code():
    with open(SMART_PLANNER_PATH, "r", encoding="utf-8") as f:
        return f.read()

def backup_code():
    backup_path = SMART_PLANNER_PATH + ".bak"
    shutil.copy(SMART_PLANNER_PATH, backup_path)
    print(f"📦 已備份原始程式碼至 {backup_path}")

def call_local_model(prompt):
    """
    呼叫本地的小模型。
    這裡預設使用 Ollama 的本地 API (預設 port 11434)。
    如果您使用的是 OpenAI API、LM Studio 或是其他服務，請修改這裡的請求邏輯。
    """
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "llama3",  # 請替換成您本地安裝的模型名稱，例如 "qwen2.5-coder", "llama3.1-8b"
        "prompt": prompt,
        "stream": False
    }
    print("🧠 正在呼叫 AI 模型思考並改寫演算法...")
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("response", "")
    except Exception as e:
        print(f"❌ 呼叫模型失敗 (請確認您的模型 API 是否啟動): {e}")
        return ""

def evolve_algorithm(stats):
    code = read_current_code()
    
    prompt = f"""
你是一個頂尖的演算法工程師。我們正在開發一個自動挖礦遊戲的 AI 腳本。
初始資源為：100 個鎬子、10 個炸彈、10 個鑽頭。
目標：盡可能挖得更深，並收集最多的寶箱。

目前的演算法效能與問題如下：
{json.dumps(stats, indent=2, ensure_ascii=False)}

目前的演算法原始碼如下：
```python
{code}
```

請你修改上述程式碼中的 `SmartPlanner`、`SmartState` 或 `get_heuristic` 邏輯，以解決上述問題並提升效率。
你可以：
1. 重新設計啟發式函數 (Heuristic)。
2. 改變節點擴展 (get_valid_actions) 的條件，讓道具在關鍵時刻才使用。
3. 加入對「岩石迴避」或「連鎖破壞」的計算。

⚠️ 嚴格要求：
請直接回傳完整的、修改後的 Python 程式碼。不要包含任何其他的解釋文字或 Markdown 閒聊，只需回傳程式碼本身，並用 ```python 和 ``` 包裝。
"""

    new_code_text = call_local_model(prompt)
    if not new_code_text:
        return

    # 提取被 ```python 標住的程式碼
    match = re.search(r'```python\s*(.*?)\s*```', new_code_text, re.DOTALL)
    if match:
        new_code = match.group(1)
    else:
        # 如果模型沒有使用 markdown，就直接當作程式碼
        new_code = new_code_text.strip()
        
    # 基本的防呆檢查，確保回傳的是我們的核心類別
    if "class SmartPlanner" in new_code and "class SmartState" in new_code:
        backup_code()
        with open(SMART_PLANNER_PATH, "w", encoding="utf-8") as f:
            f.write(new_code)
        print("✅ 演算法已成功被 AI 改寫並儲存！請重新執行測試。")
    else:
        print("❌ AI 回傳的程式碼格式不正確或不完整，已放棄更新。")
        print("--- AI 回傳內容預覽 ---")
        print(new_code_text[:500] + "...")

if __name__ == "__main__":
    # 這裡可以放入您從 simulator_bridge.py 觀察到的現象
    sample_stats = {
        "resources_given": {"pickaxes": 100, "bombs": 10, "drills": 10},
        "performance_issue": "AI 幾乎不使用炸彈和鑽頭，並且太早把鎬子消耗在堅硬的岩石上。遇到大片岩石時會卡死。",
        "goal": "修改演算法，讓 AI 在遇到大量岩石擋路時主動使用炸彈，並利用鑽頭快速向下打通空氣柱。"
    }
    
    evolve_algorithm(sample_stats)

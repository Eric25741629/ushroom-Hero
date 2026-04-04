# 🍄 菇勇者挖礦 AI 演算法沙盒 (miner_test) 使用指南

本沙盒旨在提供一個**隔離且安全**的環境，讓您利用 **AI 小模型** 自動優化與改寫 A* 搜尋演算法。目標是在有限資源（100 鎬子、10 炸彈、10 鑽頭）下，極大化挖掘深度與寶箱收益。

---

## 📁 沙盒結構
- **`miner/index.html`**：遊戲模擬器（前端），提供可視化的測試環境。
- **`miner/planning/smart_planner.py`**：核心演算法（AI 將直接改寫此檔案）。
- **`miner/core/mechanics.py`**：遊戲規則邏輯（炸彈/鑽頭範圍）。
- **`miner/simulator_bridge.py`**：WebSocket 伺服器，連接 Python 演算法與 HTML 模擬器。
- **`miner/algo_evolver.py`**：自動優化腳本，負責將源碼餵給小模型並自動覆寫。
- **`miner/planner_config.json`**：存儲演算法權重參數（會自動生成）。

---

## 🚀 快速啟動步驟

### 1. 安裝必要套件
在終端機執行：
```powershell
pip install websockets requests
```

### 2. 啟動測試橋接器
這會啟動 WebSocket 伺服器，監聽模擬器的請求：
```powershell
# 請確保在 miner_test 目錄下執行
python -m miner.simulator_bridge
```

### 3. 執行模擬器
使用瀏覽器（Chrome/Edge）開啟 `miner_test/miner/index.html`。
- 點擊右下角的 **「Python AI」** 按鈕。
- 觀察目前的 AI 表現（是否太保守、是否不愛用道具）。

### 4. 調用 AI 自動優化與改寫演算法
如果您對目前的表現不滿意，可以讓 AI 模型直接「重寫」演算法邏輯：
```powershell
python -m miner.algo_evolver
```
> **注意**：預設使用 Ollama 本地 API (`http://localhost:11434`)。請確保您的 Ollama 已啟動，並在 `algo_evolver.py` 中將 `model` 設為您擁有的模型名稱（如 `qwen2.5-coder` 或 `llama3.1-8b`）。

---

## 🛠️ 如何調整 AI 優化的方向？

您可以修改 `miner/algo_evolver.py` 中的 `sample_stats` 字典，這相當於給 AI 的「指令」：

```python
sample_stats = {
    "resources_given": {"pickaxes": 100, "bombs": 10, "drills": 10},
    "performance_issue": "目前的挖掘太慢，且總是避開岩石，導致漏掉深層寶箱。", # 描述問題
    "goal": "請讓演算法更貪婪，當寶箱出現在 3 格範圍內時，優先使用炸彈爆破。" # 設定目標
}
```

---

## 💡 進階技巧
- **備份功能**：每次 AI 改寫程式碼前，系統會自動備份舊版本至 `smart_planner.py.bak`。
- **參數 vs 邏輯**：
  - 如果只想微調數值（如道具價格），修改 `planner_config.json`。
  - 如果想改變 AI 的「思考方式」，請執行 `algo_evolver.py` 讓 AI 重構 Python 程式碼。

---

## ⚠️ 常見問題
- **模擬器連不上 Python**：請確認 `simulator_bridge.py` 是否正在運行，且埠號為 `8765`。
- **AI 改寫失敗**：請確保 AI 模型回傳的是完整的 Python 程式碼區塊（使用 ```python 包裹）。

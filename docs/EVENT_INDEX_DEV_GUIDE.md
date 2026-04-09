# Event Index 開發指南

本文件提供後續開發 `Action Trace -> Event Index -> GUI/LLM` 的統一規格與實作入口。

## 1. 目標

- 追蹤每筆操作的「意義」與「觸發位置」
- 能對應到截圖路徑與觸發行號
- 可被人類 GUI 與 LLM 批量分析共用
- 先降本：避免全函式追蹤造成高噪音與高存儲成本

---

## 2. 目前組件與資料流

1. 事件追蹤器（執行期）
- 檔案：`utils/action_tracker.py`
- 寫入：`logs/action_trace/<device>/events.jsonl`
- 來源：`MonitoredDevice`（`tap/click/swipe/screenshot/xpath_click/app_start/app_stop`）

2. 智能截圖記錄器（執行期）
- 檔案：`utils/smart_screenshot.py`
- 寫入：
  - `logs/error_screenshots/<device>/events.jsonl`
  - `logs/error_screenshots/<device>/annotations.json`
  - 截圖檔 `logs/error_screenshots/<device>/*.jpg`

3. 事件索引器（離線）
- 檔案：`tools/build_event_index.py`
- 讀取：`logs/action_trace/**/events.jsonl` + `logs/error_screenshots/**/events.jsonl`
- 產出：
  - `reports/event_index/event_index_<ts>.jsonl`
  - `reports/event_index/event_index_<ts>.csv`

4. GUI 檢視器（離線/近即時）
- 檔案：`tools/event_index_gui.py`
- 使用最新 `reports/event_index/event_index_*.jsonl`
- 入口：`http://127.0.0.1:5088`

---

## 3. 欄位契約（Contract）

### 3.1 Action Trace 事件（原始）

必要欄位（`logs/action_trace/.../events.jsonl`）：
- `timestamp`
- `device_id`
- `event_type`
- `meaning`
- `caller.file`, `caller.line`, `caller.function`
- `payload`
- `device_context.task`, `device_context.step`, `device_context.status`

補充欄位：
- `actor`
- `source`
- `thread`

### 3.2 Smart Screenshot 事件（原始）

必要欄位（`logs/error_screenshots/.../events.jsonl`）：
- `timestamp`
- `device_id`
- `task`
- `stage`
- `reason`
- `image_path`
- `trigger.file`, `trigger.line`, `trigger.function`

補充欄位：
- `actor`
- `source`
- `extra`

### 3.3 Event Index（統一後）

固定欄位（JSONL 與 CSV 一致）：
- `event_time`
- `device_id`
- `event_type`
- `meaning`
- `caller_file`
- `caller_line`
- `caller_function`
- `task`
- `step`
- `status`
- `actor`
- `source`
- `payload_json`
- `screenshot_path`
- `trigger_file`
- `trigger_line`
- `trigger_function`

---

## 4. 啟動流程

```powershell
# 1) 先產生索引
python tools/build_event_index.py --days 7

# 2) 啟動 GUI
python tools/event_index_gui.py
```

---

## 5. 開發規範（重要）

1. 不要在 V1 做全函式追蹤
- 只追蹤裝置操作層與截圖層
- 高層函式先用 `task/step` 表達語義

2. `meaning` 優先權
- 手動 `trace_meaning` > 自動填值
- 自動填值至少應含：`task/step + 操作資訊(座標/XPath/format)`

3. 索引器不可猜測關聯
- 僅在有明確欄位時填 `screenshot_path`
- 禁止用時間近似去硬配對（避免錯誤因果）

4. 向後相容
- 原始 JSONL 允許缺欄位，索引器需容錯並略過壞行

---

## 6. 後續擴充建議（V2+）

1. 成本分析器
- 每裝置 `screenshot` 速率
- 重複事件偵測（同 caller_line + meaning 的短時間重複）
- 高頻行號排行榜（疑似可降本）

2. 任務邊界事件（可選）
- 在高層任務加 `task_start/task_end`
- 用於耗時與成功率計算

3. GUI 強化
- 點擊表格列直接打開截圖
- 行號點擊可跳原始檔案（若環境支援）
- 加入時間區間滑桿與裝置對照視圖

---

## 7. 常見問題

Q: 為什麼有些舊資料 `meaning` 是空的？  
A: 空值是早期紀錄，尚未啟用自動 meaning；新資料會自動填。

Q: 為什麼 `screenshot` 事件有時沒有 `screenshot_path`？  
A: 一般 `screenshot` 只代表抓圖行為，不一定有落檔；只有 `screenshot_saved` 或 SmartScreenshot 事件才保證有路徑。

Q: GUI 顯示找不到資料？  
A: 先跑 `python tools/build_event_index.py --days 7` 生成索引。


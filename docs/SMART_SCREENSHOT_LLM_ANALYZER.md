# SmartScreenshot + LLMAnalyzer 使用說明

## 功能概覽

- `SmartScreenshotRecorder` 已整合到主流程錯誤截圖路徑。
- `ActionTraceRecorder` 已整合到 `MonitoredDevice`，會追蹤點擊/滑動/截圖/啟停事件。
- 每次觸發錯誤截圖時會同步寫入：
  - 截圖檔：`logs/error_screenshots/<device_id>/*.jpg`
  - 事件檔：`logs/error_screenshots/<device_id>/events.jsonl`
  - 註解檔：`logs/error_screenshots/<device_id>/annotations.json`
- 每次裝置操作也會寫入：
  - 追蹤檔：`logs/action_trace/<device_id>/events.jsonl`

## 已整合位置

- 主流程檔案：`new_main_v2.py`
- 整合函式：
  - `save_error_screenshot(...)`
  - `log_main_page_mismatch(...)`

## 事件資料格式（events.jsonl）

每行一筆 JSON，例如：

```json
{
  "timestamp": "2026-04-08T15:21:30.123456",
  "device_id": "emulator-5554",
  "task": "地獄之門",
  "stage": "活動頁面",
  "reason": "地獄之門到達執行時間但不在主頁面",
  "image_path": "logs/error_screenshots/emulator-5554/20260408_152130_123456_xxx.jpg",
  "trigger": {
    "file": "C:/nas同步_project/菇勇者全自動掛機/new_main_v2.py",
    "line": 432,
    "function": "main",
    "module": "new_main_v2"
  },
  "actor": "MonitoredDevice",
  "source": "SmartScreenshotRecorder.capture"
}
```

## 操作追蹤格式（action_trace/events.jsonl）

每行一筆 JSON，重要欄位：

- `event_type`: `tap` / `click` / `swipe` / `screenshot` / `xpath_click` / `app_start` / `app_stop` / `screenshot_saved`
- `meaning`: 操作意圖（可選）
- `caller.file` / `caller.line` / `caller.function`: 觸發位置
- `actor`: 實際執行裝置類型（例如 `uiautomator2.Device` 或 `PlaywrightGameDevice`）
- `payload`: 座標、xpath、格式、截圖路徑等細節

## 如何標註每次點擊意義（可選）

你可以在呼叫點傳入：

```python
d.click(260, 370, trace_meaning="點擊寶箱領獎")
d.xpath_click('//*[@text="菇勇者傳說"]', trace_meaning="桌面啟動遊戲")
d.screenshot(format="opencv", trace_meaning="任務前盤面檢查")
```

若未傳 `trace_meaning`，系統會自動填入 `task/step + 操作資訊`（例如座標或 XPath），並同時記錄觸發檔案、函式與行號。

## 註解資料格式（annotations.json）

鍵為 `image_path`，值包含：

- `updated_at`
- `task`
- `stage`
- `reason`
- `status`（預設 `auto_captured`）
- `comment`（預設空字串，可人工補充）

## 批量分析指令

```powershell
python tools/llm_batch_analyzer.py --days 7
```

輸出：

- `reports/smart_screenshot/smart_screenshot_report_<timestamp>.json`
- `reports/smart_screenshot/smart_screenshot_report_<timestamp>.md`

## 事件索引表（給 LLM 回顧）

```powershell
python tools/build_event_index.py --days 7
```

輸出：

- `reports/event_index/event_index_<timestamp>.jsonl`
- `reports/event_index/event_index_<timestamp>.csv`

索引欄位包含：

- `event_time`, `device_id`, `event_type`, `meaning`
- `caller_file`, `caller_line`, `caller_function`
- `task`, `step`, `status`
- `screenshot_path`

## GUI 檢視器（精美版）

```powershell
python tools/build_event_index.py --days 7
python tools/event_index_gui.py
```

打開：

- `http://127.0.0.1:5088`

功能：

- 關鍵字 / 裝置 / 事件類型篩選
- KPI 卡片（總數、篩選後、裝置數、類型數）
- 事件分布圖
- 事件明細表（含 line 與 screenshot_path）

## 啟用 LLM 診斷

```powershell
$env:OPENAI_API_KEY="YOUR_KEY"
python tools/llm_batch_analyzer.py --days 7 --use-llm --llm-model gpt-4.1-mini
```

## 建議日常流程

1. 平常直接跑 `python new_main_v2.py`。
2. 讓 SmartScreenshot 自動累積異常樣本與註解骨架。
3. 每週執行一次 `tools/llm_batch_analyzer.py`，查看高頻錯誤與修正建議。

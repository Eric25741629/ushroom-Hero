# OCR／PyTorch 分類器使用追蹤

主程式會將實際發生的重型依賴呼叫寫入既有 action trace：

- `ocr_request`：遠端 OCR endpoint 呼叫。
- `classifier_model_load`：PyTorch 模型載入。
- `classifier_inference`：PyTorch CNN 實際推論。

原始事件位於：

```text
logs/<device>/action_trace/events_YYYYMMDD.jsonl
```

事件只記錄呼叫位置、裝置、任務、endpoint、server、結果數量、狀態與耗時，
不保存 OCR 圖片或辨識文字。

## 快速查看使用位置

```powershell
python tools/summarize_usage_tracking.py --days 7
```

輸出會依「事件類型、分類器元件、呼叫檔案、行號與函式」彙整呼叫次數。
裝置無法從 `Bot-<device>` 執行緒或呼叫堆疊判定時會記為 `unknown`，
但呼叫檔案與函式仍會保留。

## 放進統一事件索引

```powershell
python tools/build_event_index.py --days 7
```

在輸出的 CSV／JSONL 以 `event_type` 篩選：

- `ocr_request`
- `classifier_model_load`
- `classifier_inference`

建議至少收集一個完整的日常週期，再依「零呼叫」或「只剩特定任務呼叫」
決定哪些 OCR／分類器依賴可以移除。

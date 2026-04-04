# RL Logs 路徑統一與輪替說明

更新日期：2026-03-12  
適用專案：`A:\菇勇者全自動掛機`

## 1. 目的

原本專案同時存在兩套 RL log 路徑：

- `miner/rl/rl_logs`
- `miner/rl_logs`

這會造成訓練、回放、清理時資料來源不一致。  
本次調整目標是「只保留單一路徑」，並補上 `events.jsonl` 的 log rotate，避免單檔無限增長。

## 2. 本次變更

### 2.1 路徑統一

統一使用：

- `miner/rl_logs`

不再使用：

- `miner/rl/rl_logs`（已合併後移除）

### 2.2 程式碼修改

- `miner/rl/train_rl.py`
  - `LOG_FILE` 改為指向 `../rl_logs/events.jsonl`
- `miner/rl/replay_from_rl_logs.py`
  - `rl_root` 改為指向 `../rl_logs`
- `miner/rl/rl_recorder.py`
  - 新增 log rotate 機制

### 2.3 Log Rotate 行為

在寫入 `events.jsonl` 前，若檔案超過上限則輪替：

- 目前檔案：`events.jsonl`
- 輪替後檔名：`events.<timestamp>.jsonl`
- 自動保留最近 N 份，舊檔自動清除

環境變數：

- `RL_LOG_ROTATE_MAX_BYTES`：單檔最大大小（bytes），預設 `10 * 1024 * 1024`
- `RL_LOG_ROTATE_KEEP`：保留輪替檔數量，預設 `5`

## 3. 資料夾合併處理結果

已執行合併並確認：

- 舊資料夾 `miner/rl/rl_logs` 已移除
- 新資料夾 `miner/rl_logs` 保留並承接資料
- 事件資料已做追加合併（含 `events.jsonl`）
- 目標端缺少的 `meta.json` 才會補拷貝

## 4. 驗證方式（建議）

1. 檢查資料夾狀態
   - `miner/rl/rl_logs` 不存在
   - `miner/rl_logs` 存在
2. 全域搜尋不得再出現舊路徑字串
   - `rl/rl_logs`
   - `rl\\rl_logs`
3. 啟動訓練/回放流程，確認讀寫集中於 `miner/rl_logs`
4. 人工壓力測試寫入，確認 rotate 檔案會產生且舊檔會淘汰

## 5. 維運建議

- 若近期訓練量大，可先把 `RL_LOG_ROTATE_MAX_BYTES` 調大（例如 20MB~50MB）避免太頻繁輪替。
- 若需要較長歷史，可提高 `RL_LOG_ROTATE_KEEP`（例如 10~20）。
- 若要做跨機分析，建議額外每天壓縮備份 `miner/rl_logs`。

## 6. 注意事項

- `get_stage_with_check` 屬必要檢查邏輯，不建議以快取替代其即時檢查。
- 本次僅處理 RL logs 路徑統一與輪替；其他模組行為未變更。

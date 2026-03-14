# Phase 10 Research: 雙週副本（週六/週日 20:00）MVP 自動化實作研究

## Research Scope
本研究聚焦 Phase 10 MVP：
- 僅在週六/週日 20:00 觸發雙週副本流程。
- 覆蓋進入副本、自動戰鬥啟動、戰鬥補給維持、失敗復原到主畫面。
- 不新增 OCR 訓練或 UI 大改。

## Existing Architecture Alignment
- 目前 roadmap 的長線方向是「穩定性 + 排程 + 可觀測」，Phase 10 MVP 可作為 SCH/STAB 的垂直切片。
- 既有流程中已存在多個 click/OCR 互動點，MVP 應避免重寫主流程，改以 wrapper 與子流程封裝落地。
- 建議以「最小侵入」方式新增 `biweekly_instance` 任務模組，並接入現有主循環/任務入口。

## MVP Implementation Strategy

### 1) Scheduler Trigger Correctness
目標：只在 Sat/Sun 20:00 觸發，且同一時窗不重複執行。

建議實作：
- 使用本地時間（Asia/Taipei）判斷 `weekday in {Sat, Sun}` 且 `hour == 20`。
- 增加 1 個固定長度觸發窗（例如 20:00:00~20:04:59），避免秒級抖動漏觸發。
- 去重鍵（dedupe key）採 `YYYY-WW-Sat-20` / `YYYY-WW-Sun-20` 或 `date+slot`，持久化到 state。
- 只要本時窗已成功「開始執行」，同窗內不再重入。
- 進程重啟後仍依持久化 dedupe 判斷，避免重啟造成重複觸發。

建議資料結構：
- `last_trigger_slot`
- `last_trigger_started_at`
- `last_trigger_result` (started/success/fail/aborted)

### 2) Retry + Timeout Wrappers for Key Clicks
目標：關鍵點擊遇到 OCR 誤判、延遲、UI 未到位時可恢復，避免卡死。

建議抽象：
- `safe_click(label, region=None, retry=3, step_timeout_s=8, backoff_s=[0.5,1,2])`
- `safe_step(name, fn, timeout_s, retry, on_retry_hook=None)`

關鍵步驟（至少）應包裝：
- 賞金之路
- 大盜來襲
- 挑戰
- 開啟自動戰鬥
- 使用（補給）

失敗策略：
- 每次失敗記錄 `step`, `attempt`, `elapsed_ms`, `reason`。
- 達上限後返回可判斷錯誤碼（如 `STEP_TIMEOUT`, `NOT_FOUND`, `CLICK_FAILED`）。
- 上層根據錯誤碼決定「局部重試 / 中止並復原回主畫面」。

### 3) Safe Combat Loop Exit
目標：戰鬥維持邏輯不出現無限 `while True`。

建議退出條件（同時存在）：
- `max_combat_duration_s`（硬上限 watchdog）。
- `max_idle_cycles`（連續 N 次未觀測到有效進展即退出）。
- `external_interrupt`（全域停止/裝置停止旗標）。
- `battle_end_detected`（OCR/畫面特徵判斷戰鬥結束）。

補給策略建議：
- 以固定節拍（例如每 3~5 秒）檢查補給，不要每回圈密集 OCR。
- 補給操作用 `safe_click`，且每回合限制最大補給次數，避免 UI 震盪。

建議狀態流：
- `enter_instance -> prepare_battle -> combat_maintenance -> exit_or_recover`

### 4) Fail-safe Return-to-Home with Logging
目標：任一步驟失敗皆可嘗試回主畫面，並可追溯。

建議復原分層：
- Layer 1: 一般返回序列（關閉彈窗、返回鍵、主頁按鈕檢查）。
- Layer 2: 入口重導（重新進入活動入口，再退出至主畫面）。
- Layer 3: 裝置級恢復（如已存在 reconnect/restart 機制，僅在前兩層失敗後升級）。

必要日誌欄位：
- `ts`, `device_id`, `run_id`, `phase`, `step`, `attempt`
- `error_code`, `error_detail`, `recovery_layer`, `recovery_result`
- `trigger_slot`, `duration_ms`

## Concrete Risks and Mitigation

1. 風險：20:00 時間邊界漏觸發或重複觸發
- Mitigation: 觸發窗 + dedupe key 持久化 + 啟動即標記 started。

2. 風險：OCR 抖動導致關鍵按鈕找不到
- Mitigation: `safe_click` 重試、區域限制、退避等待、失敗碼分流。

3. 風險：戰鬥迴圈卡死拖垮主循環
- Mitigation: duration watchdog + idle cycle 上限 + 外部中斷旗標。

4. 風險：復原流程失敗後停在中間頁面
- Mitigation: 三層復原策略與每層 timeout；最終升級到裝置級恢復。

5. 風險：僅有文字日誌但無法診斷 UI 當下情況
- Mitigation: 於關鍵失敗點可選擇保留截圖路徑（不必每次都截），日誌記錄檔名。

6. 風險：與現有任務爭用裝置控制權
- Mitigation: 進入副本前宣告任務鎖（device task lock），退出/失敗皆釋放；加 finally 保證釋放。

## Proposed File-Level Change Targets (for planning)
- 排程入口（既有 scheduler/loop 檔）: 新增 biweekly slot 判斷與 dedupe。
- 副本流程模組（新檔或既有活動模組）: 實作四段式 state flow。
- click/OCR 工具層: 新增 `safe_click` / `safe_step` wrapper。
- 日誌層: 補齊 run_id、trigger_slot、recovery fields。
- state 持久化層: 存放 dedupe 與最後執行結果。

## Validation Architecture

測試與驗證建議拆成四層：

1. Unit Layer
- 時間判斷函式：覆蓋 Sat/Sun 20:00、非時窗、跨日邊界。
- dedupe 函式：同 slot 不重觸發，不同 slot 可觸發。
- `safe_click` / `safe_step`：重試次數、timeout、錯誤碼映射。

2. Integration Layer
- 模擬 click 成功/失敗序列，驗證 step wrapper 是否正確中止與回傳。
- 戰鬥迴圈在 `max_duration`、`max_idle_cycles`、external stop 下都能退出。
- fail-safe 回主畫面流程在 Layer1 失敗後可進入 Layer2/3。

3. Dry-run / Staging Layer
- 以 mock 裝置或低風險帳號執行 Sat/Sun 20:00 觸發演練。
- 注入故障（按鈕缺失、OCR 偽陰性、網路慢）驗證復原鏈路。

4. Observability Acceptance
- 驗證日誌欄位完整性（run_id, step, attempt, recovery_result）。
- 驗證一次完整成功 run 與至少一次失敗 recovery run 可被追溯。
- 設定最小告警條件：連續 N 次 slot 失敗時提示人工介入。

## Planning Notes for Phase 10
- 建議先以單裝置 MVP 上線，確認 2 個週末執行穩定後，再擴到多裝置。
- Phase 10 的完成定義應包含：
  - 週六/週日 20:00 觸發正確且無重複。
  - 關鍵步驟重試 + timeout 生效。
  - 戰鬥迴圈可在可預期時間內退出。
  - 失敗時可回主畫面且有可追溯日誌。

# Phase 10: 雙週副本（週六/週日 20:00）自動開啟與戰鬥補給穩定化 - Context

**Gathered:** 2026-03-15
**Status:** Ready for planning
**Source:** User constraints from $gsd-plan-phase invocation

<domain>
## Phase Boundary

本階段只處理「雙週副本」最小可行自動化：
- 只在週六/週日晚上 20:00 觸發副本流程
- 只覆蓋進入副本、開啟自動戰鬥、戰鬥中補給/回復、失敗回主畫面的閉環
- 不包含新 OCR 模型訓練、不包含 UI 大改、不處理其他副本類型

</domain>

<decisions>
## Implementation Decisions

### Locked Decisions (Must Have)
- 排程觸發必須正確：僅週六/週日 20:00 啟動，且具備去重機制避免重複觸發。
- 關鍵點擊必須有重試與 timeout：如「賞金之路/大盜來襲/挑戰/開啟自動戰鬥/使用」等步驟失敗時可重試，達上限後中止並記錄。
- 戰鬥迴圈必須可安全退出：不可無限 `while True` 卡死，需有退出條件、最大執行時間與外部中斷檢查。
- 失敗時必須可回主畫面並記錄日誌：回復策略需可觀測，至少記錄觸發時間、步驟、錯誤原因、恢復結果。

### Claude's Discretion
- 重試次數、每步 timeout、整體 watchdog 時間可由實作端提案。
- 補給（餵食/回復/減少/熄火）的操作順序可微調，但須具備失敗保護。
- 日誌格式可沿用現有框架（結構化為佳）。

</decisions>

<specifics>
## Specific Ideas

- 將流程拆成狀態節點：`enter_instance -> prepare_battle -> combat_maintenance -> exit_or_recover`。
- `click_str_by_server` 外包一層 `safe_click(label, retry, timeout, region)`。
- 以 deadline + idle-cycle 次數做迴圈退出，避免 OCR 一直偵測到「高級」而無限延長。
- 回主畫面恢復可設 2 層：UI 返回序列 + 逾時後強制回首頁/重開副本入口。

</specifics>

<deferred>
## Deferred Ideas

- 多副本排程協調
- 依裝置性能動態調整補給頻率
- 進階戰鬥策略切換（非 MVP）

</deferred>

---

*Phase: 10-20-00*
*Context gathered: 2026-03-15 via direct user constraints*

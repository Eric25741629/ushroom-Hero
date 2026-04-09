# Phase 1: MuMu 模擬器管理與卡死自動重啟 - Context

**Gathered:** 2026-03-13
**Status:** Ready for planning
**Source:** User clarification in $gsd-plan-phase 1

<domain>
## Phase Boundary

本 phase 專注在 MuMu 12 模擬器管理與卡死自動恢復：
- 封裝 `control.exe` 核心指令（啟動/關閉/重啟/顯示視窗/隱藏視窗）
- 建立 `emulator-*` 與 MuMu 索引映射
- 自動偵測「模擬器卡死」並執行安全重啟
- 重啟後驗證裝置回線與任務可恢復

不包含：新掛機策略邏輯擴充、打車流程開發。
</domain>

<decisions>
## Implementation Decisions

### MuMu Control Commands (Locked)
- 啟動指定模擬器：`control -v <index> launch`
- 關閉指定模擬器：`control -v <index> shutdown`
- 重啟指定模擬器：`control -v <index> restart`
- 顯示視窗：`control -v <index> show_window`
- 隱藏視窗：`control -v <index> hide_window`

### Device Index Mapping (Locked)
- `emulator-5554 = 0`
- `emulator-5556 = 1`
- 其餘依序遞增（`5558=2`, `5560=3` ...）

### Hang Recovery Intent (Locked)
- 目標是固定在電腦上的 `emulator-*` 實例可自動偵測卡死。
- 偵測到卡死時，需自動重啟對應 MuMu 模擬器並恢復掛機。

### Claude's Discretion
- 卡死判定指標組合（心跳停滯、畫面 hash 不變、ADB 命令超時）
- 重啟節流與保護（最大重啟次數、cooldown）
- 重啟後健康檢查與回復策略
</decisions>

<specifics>
## Specific Ideas

- 建立 `emulator_serial -> mumu_index` 映射工具（可配置、可覆蓋預設）
- 把 MuMu 控制動作包裝成統一 API，供排程器或狀態機呼叫
- 對每台裝置維護 hang watchdog 指標
- 每次 hang/restart 記錄事件與耗時，供 WEB 指標頁面使用
</specifics>

<deferred>
## Deferred Ideas

- MuMu 以外模擬器廠商控制命令適配
- 自動化策略收益優化
</deferred>

---

*Phase: 01-mumu-control*
*Context gathered: 2026-03-13 via user clarification*

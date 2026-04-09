# Phase 1 Research - MuMu 模擬器管理與卡死自動重啟

## Objective
為既有 `emulator-*` 裝置建立 MuMu `control.exe` 指令封裝、卡死偵測、與自動重啟恢復流程，避免掛機中途整台模擬器卡死。

## Current Reality
- 裝置控制主鏈路已存在（ADB/uiautomator2 + 多裝置流程）。
- 卡死恢復目前缺乏「模擬器層級」一致處理。
- `Phase 1` 是目前排程中的首個 phase，需盡量低侵入整合。

## Command Contract (Locked)
- `control -v <index> launch`
- `control -v <index> shutdown`
- `control -v <index> restart`
- `control -v <index> show_window`
- `control -v <index> hide_window`

映射規則：
- `emulator-5554 -> 0`
- `emulator-5556 -> 1`
- 以此類推遞增。

## Recommended Design
1. **MuMuControlAdapter**
   - 負責 `control.exe` path 檢測、命令組裝、timeout/retry、stdout/stderr parse。
2. **EmulatorIndexResolver**
   - 將 `emulator-<port>` 轉為 MuMu index。
   - 預設依 port 推導，允許 config 覆寫。
3. **HangWatchdog**
   - 複合判定卡死：心跳停滯 + 截圖 hash 不變 + 關鍵 adb 指令超時。
4. **RecoveryOrchestrator**
   - 判定卡死後執行 restart。
   - 重啟後做 health check（adb online、首頁可互動、worker 可回主循環）。

## Failure & Safety
- 每裝置設 `max_restarts_per_hour`，避免重啟風暴。
- 重啟失敗時標記 Error 並停止該裝置，不影響其他裝置。
- 所有重啟事件寫入結構化日誌（含 duration、result、reason）。

## Integration Points
- `config/game_config.py` 或 `bot_config.json`：新增 MuMu 控制設定與索引映射覆寫。
- `bot_state.py`：新增 emulator-level health/restart counters。
- 主循環（`new_main_v2.py`）或現有 worker loop：插入 hang watchdog 與 recovery hook。
- Web API（`control_panel_app.py`）：暴露每台裝置重啟次數、最近重啟時間、最後錯誤。

## Validation Architecture
- 單元測試：索引映射、命令組裝、卡死判定器。
- 整合測試：mock control.exe 執行器 + 模擬超時與成功回復。
- 壓力測試：多裝置同時卡死時，驗證互不影響與節流保護。

## Risks
- control.exe 路徑在不同機器不一致。
- 部分卡死型態不會讓 adb offline，需多訊號判定。
- 重啟後遊戲初始化耗時波動，需調整 timeout。

## Practical Recommendation
先交付最小可用閉環：
- `restart` 指令可用
- `emulator->index` 映射正確
- 卡死可檢測與自動重啟
- 重啟結果可觀測
再逐步補上 show/hide 與更精細策略。

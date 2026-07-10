# 在線標示明確化 + 工具頁手動連線 — 設計（2026-07-10）

母 spec：[`2026-07-06-account-session-registry-design.md`](2026-07-06-account-session-registry-design.md)。
本批 = 母 spec **Phase 5 + Phase 7** 的執行，外加三個頁面改手動連線/載入。

## 問題

1. Dashboard「當前在線」來自遊戲好友列表 online flag（`ws_token/online_monitor.py:91-106`，`last_login_ts==0`），任何連線（bot 喚醒、工具借用、坐騎追蹤、上線偵測、真人）都會亮。使用者無法分辨「被借走」vs「真實玩家在線」。`session_registry` 已記錄借用者（owner+label）但未接到 dashboard（Phase 7 未做）；bot 自身喚醒也未登記（Phase 5 未做）。
2. 倉庫（`inventory.html:568`）與工具最佳化（`tools_optimize.html:715`）在 page-load 的 init IIFE 無條件 `connectSession()`，一切頁就用帳號 ticket 登入遊戲 WS（非 protected 帳號有互踢真人風險）。飛寵頁（`fly_pet.html:3060` `initAutoLoad()`）雖走 CDP 不會 ticket 登入，使用者仍要求一致改手動。

## 範圍

### A. Phase 5 — 喚醒路徑登記 SCHEDULER（照母 spec §Phase 5）
- `game_actions/ws_phase.py` 喚醒開頭 `registry.acquire(device, Owner.SCHEDULER, preempt=True)` 取代 `wait_for_dashboard_ws_release`；`_wait_until_human_offline` 保留串在其後；喚醒週期結束 release。
- human_played / protected 硬擋維持（acquire 內建，不得繞過）。
- 風險高（動主迴圈）：**需重啟**；先 dashboard manual-hold 取一台 live 驗證。

### B. Phase 7 — presence 顯示（照母 spec §Phase 7，字樣定案如下）
- `bot_state` 加 owner/presence 鏡射（加法欄位，worker 經既有 state sync 帶到 master）。
- `/api/status`（`control_panel/routes_status.py:458`）注入 `session_registry.peek_all()`，per-device 輸出 `{owner, label, channel, since}`。
- 前端卡片（`dashboard.html:3055-3067`）badge 判定順序（2026-07-10 使用者定案：觀察與執行分開）：
  1. lease.owner == SCHEDULER → 「腳本執行」
  2. lease.owner ∈ {ONLINE_MONITOR, ONLINE_CHECK, MOUNT_TRACKER} → 「在線觀察（{owner 中文}）」；映射：ONLINE_MONITOR=上線偵測、ONLINE_CHECK=上線檢查、MOUNT_TRACKER=坐騎追蹤
  3. lease.owner == TOOL → 「被借走：工具（{label}）」
  4. 無 lease 且 `account_online == true` → 「玩家在線」（醒目色，代表可能是真人）
  5. `account_online == false` → 「當前離線」；`null` → 空白（維持現狀）
- 修正 `dashboard.html:3015` title 與 `:511-523` CSS 註解的錯誤語意（現在宣稱「真人是否在線」）。
- UI 改動走 `dashboard-ui-review`。

### C. 三頁改手動連線/載入
- `inventory.html:568`、`tools_optimize.html:715`：移除 init 內自動 `await connectSession()`（保留 `loadDevices()` 與 `syncConnGating()`）。手動連線鈕/斷線/保活/閒置斷線 UI 均已存在，不需新 UI。
- `fly_pet.html:3060`：移除 `initAutoLoad()` 的自動 doLoad；確認既有手動「載入」入口可用（若無獨立按鈕則補一顆，沿用設計系統）。
- 連線前「在線確認 modal」（母 spec Phase 7 提及）：連線 API 回應若 registry peek 顯示帳號已有他人 lease 或 account_online，前端彈確認再連（沿用 `ws_session.py:139` 既有衝突訊息通道）。

## 不做（YAGNI）

- 不改 online_monitor 的 presence/snapshot 演算法本身（母 spec §7 同）。
- 不做 worker 行程內借用型服務的 lease 上報（僅 SCHEDULER 經 bot_state 鏡射；母 spec §7 同）。
- 不做「真人即時偵測」新機制：「玩家在線」= 在線且無任何已知租約的推定，不宣稱 100%。
- 不新增套件、不加 config-only 開關（手動連線是移除自動行為，非新開關）。

## 驗收

- 測試：registry SCHEDULER acquire/release（喚醒模擬）、`/api/status` presence 欄位、前端字樣各情境（pytest 指定檔案）。
- Live：manual-hold 一台驗證 Phase 5 喚醒搶回 + dashboard badge 四態；三頁開頁不自動連線、按鈕連線正常。
- `dashboard-ui-review` 過設計系統。
- 提醒：Phase 5 / control panel 均**需重啟 `new_main_v2.py`** 生效。

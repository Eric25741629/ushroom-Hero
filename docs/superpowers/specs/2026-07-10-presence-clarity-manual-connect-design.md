# 在線標示明確化 + 工具頁手動連線 — 設計（2026-07-10）

母 spec：[`2026-07-06-account-session-registry-design.md`](2026-07-06-account-session-registry-design.md)。
本批 = 母 spec **Phase 5 + Phase 7** 的執行，外加三個頁面改手動連線/載入。

## 問題

1. Dashboard「當前在線」來自遊戲好友列表 online flag（`ws_token/online_monitor.py:91-106`，`last_login_ts==0`），任何連線（bot 喚醒、工具借用、坐騎追蹤、上線偵測、真人）都會亮。使用者無法分辨「被借走」vs「真實玩家在線」。`session_registry` 已記錄借用者（owner+label）但未接到 dashboard（Phase 7 未做）；bot 自身喚醒也未登記（Phase 5 未做）。
2. 倉庫（`inventory.html:568`）與工具最佳化（`tools_optimize.html:715`）在 page-load 的 init IIFE 無條件 `connectSession()`，一切頁就用帳號 ticket 登入遊戲 WS（非 protected 帳號有互踢真人風險）。飛寵頁（`fly_pet.html:3060` `initAutoLoad()`）雖走 CDP 不會 ticket 登入，使用者仍要求一致改手動。

## 範圍

### A. Phase 5 — 喚醒路徑登記 SCHEDULER（依 2026-07-10 使用者定案修正母 spec）
- `game_actions/ws_phase.py` 新增 `acquire_scheduler_lease(ip, log)` 取代 `wait_for_dashboard_ws_release`（兩個呼叫端：`new_main_v2.py:105`、`runtime_services/ws_runner_service.py:524`）：
  - 背景借用者（上線偵測/上線檢查/坐騎追蹤）→ `preempt=True` 直接搶回（它們 poll `preempted` 讓位）。
  - **dashboard TOOL（人手動操作）→ 等待釋放**（15s poll，同今日行為；使用者 2026-07-10 定案，否決母 spec 的字面 preempt——避免裝飾升級做到一半被踢）。「開啟網頁」請求可中斷等待。
  - `reason=="protected"`（human_played 裝置）→ 不登記 lease 直接放行，維持既有觀察者閘門保護（否則該裝置喚醒會被 registry 硬擋死鎖）。
  - `_wait_until_human_offline` 保留串在其後。
- 釋放：`run_sleep_cycle`（`runtime_services/sleep_service.py:181`）入口直接 `registry.release(ip, Owner.SCHEDULER)`（冪等）；ws_runner 若不經 run_sleep_cycle 則在其入睡點補同一行。
- 風險高（動主迴圈）：**需重啟**；先 dashboard manual-hold 取一台 live 驗證。

### B. Phase 7 — presence 顯示（照母 spec §Phase 7，字樣定案如下）
- `/api/status`（`control_panel/routes_status.py:458`）注入 `session_registry.peek_all()`，per-device 輸出 `lease_owner`/`lease_label`。**不做 bot_state 鏡射**（母 spec 提的鏡射改用既有 `task`/`status` 欄位 fallback 推導，見下；所有 lease 型 owner——TOOL/監控/追蹤——都只在 master 行程存在，worker 的 SCHEDULER 顯示由 fallback 覆蓋）。
- 新增 `GET /api/ws_session/<ip>/precheck`：回 `{lease: {owner,label}|null, account_online: bool|null}`，供工具頁連線前確認。
- 前端卡片（`dashboard.html:3055-3067`）badge 判定順序（2026-07-10 使用者定案：觀察與執行分開）：
  1. lease_owner == scheduler → 「腳本執行」
  2. lease_owner ∈ {online_monitor, online_check, mount_tracker} → 「在線觀察（{owner 中文}）」；映射：online_monitor=上線偵測、online_check=上線檢查、mount_tracker=坐騎追蹤
  3. lease_owner == tool → 「被借走：工具（{label}）」
  4. 無 lease 且裝置醒著（status ∈ {ONLINE, DEGRADED, PAUSED} 且 task ∉ {休眠中, 啟動後休眠} 且 task != 等待真人下線）→ 「腳本執行」（fallback：涵蓋 worker 裝置與 H5 瀏覽器階段，Phase 5 未登記時亦有正確顯示）
  5. `account_online == true` → 「玩家在線」（醒目色，代表可能是真人；純 WS session 不會出現在好友 presence，此態專指 H5/APP live session）
  6. `account_online == false` → 「當前離線」；`null` → 空白（維持現狀）
- 修正 `dashboard.html:3015` title 與 `:511-523` CSS 註解的錯誤語意（現在宣稱「真人是否在線」）。
- UI 改動走 `dashboard-ui-review`。

### C. 三頁改手動連線/載入
- `inventory.html:568`、`tools_optimize.html:715`：移除 init 內自動 `await connectSession()`（保留 `loadDevices()` 與 `syncConnGating()`）。手動連線鈕/斷線/保活/閒置斷線 UI 均已存在，不需新 UI。
- `fly_pet.html:1675` `initAutoLoad()`：瀏覽器已開時不再自動 `doLoad`，改提示「點『載入』讀取資料」（既有「載入」按鈕保留為唯一入口；CDP 不涉 ticket 登入，免確認 modal）。
- 連線前「在線確認 modal」（倉庫/工具最佳化）：`connectSession()` 先打 `/api/ws_session/<ip>/precheck`，有 lease 或 `account_online==true` → 彈確認 modal（沿用兩頁既有 modal manager）說明佔用者與踢線後果，確認才連；precheck 失敗 fail-open 直接連。

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

---
created: 2026-07-30T15:22:24.914Z
title: Route hybrid WS kick to login conflict cooldown
area: runtime
priority: P0
files:
  - ws_token/client.py:224
  - ws_token/online_monitor.py
  - ws_token/runner.py:1878
  - game_actions/ws_phase.py:784
  - new_main_v2.py:96
  - new_main_v2.py:582
  - runtime_services/ws_runner_service.py:497
  - runtime_services/web_session_service.py:153
  - runtime_services/online_check_service.py:100
  - bot_config.json:emulator-5558
---

## Problem

`WSGameClient` 已能用 `cmd 259` 偵測異地登入，`run_device()` 也會在
`RunReport.kicked` 回報被踢。純 WS runner 的裝置 loop 會將此狀態轉成 30 分鐘
`kick_cooldown`，但一般 ADB/web_h5 的 hybrid WS-first 路徑只在 summary 寫
`kicked=True`，沒有轉成 `LoginConflictError`。結果可能繼續開啟 App/瀏覽器或執行
後續流程，未套用專案既有的異地登入避讓策略。

`emulator-5558`（修哥帳號）另有一個更危險的競態：手動登入後，排程在喚醒時雖然
會先做 checker online-check，但監控器剛重連、快照逾時或查不到帳號時會得到
`unknown`，非 `human_played` 路徑在重試數次後 best-effort 放行，仍可能由 5558
自己的 WS/H5 登入把真人 session 踢掉。啟動前需要一次新鮮的最後查詢；對真人保護
帳號，`unknown` 不可視為 offline。

另外，現行 `is_kicked()` 同時涵蓋明確 `cmd 259` 與 reader 非預期斷線。兩者是否都應
使用 30 分鐘冷卻需要明確定義，避免把一般網路斷線誤報為帳號異地登入。

## Solution

讓所有登入入口共用同一個「異地登入冷卻」狀態與閘門：

1. hybrid WS-first 收到明確 `cmd 259` 時立即停止本輪，不再開 H5/ADB pipeline，
   轉交既有 `LoginConflictError` / `StartupLoginConflictError` 與 30 分鐘休眠處理。
2. 純 transport drop 與明確異地登入分流；一般斷線不可誤報成帳號衝突。
3. 冷卻以實際被踢的 device/account 為 key，寫入 `cooldown_until`；WS、H5、offline
   fallback、排程、dashboard 工具、一次性 online-check 與 persistent monitor 在
   冷卻期間都不得重新登入同一帳號。偵測器被踢只冷卻偵測器，不得誤傷 5558。
4. 5558 每次喚醒在 `WS 登入/H5 開頁` 前再做一次 fresh online-check，而不是只讀
   舊快照：`online` 留在休眠、`offline` 才可登入、`unknown/timeout` fail-closed
   並延後重查。設定 5558 為 `human_played: true`，讓真人帳號採無限等待保護策略。
5. 手動開網頁仍可立即接管，但不能繞過冷卻；關閉手動頁面後也要先重新查線再恢復
   自動登入。

完成條件：

- hybrid WS-first 的明確 `cmd 259` 不再繼續開啟 H5/ADB pipeline。
- `new_main_v2.py` 既有 `runtime_login_conflict_30m` 路徑確實被觸發。
- 控制訊號不可被 `_run_ws_phase_for_wake()` 的 generic fallback 吞掉。
- local/master/worker 的 dashboard 狀態與下一次喚醒時間一致。
- 啟動前最後一次 fresh online-check 在 `unknown` 時不會登入 5558。
- 手動登入 5558 後，排程喚醒不會將真人 session 踢下線；確認離線後仍能正常恢復
  自動流程。
- WS、H5、fallback、dashboard tool、online-check、online-monitor 共享 cooldown 閘門，
  並以實際被踢帳號隔離狀態。
- 補上 hybrid kicked、一般 WS task error、transport drop、fresh-check
  fail-closed、cooldown 擋登入、手動接管與冷卻後恢復等分流測試。

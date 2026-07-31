---
created: 2026-07-30T15:22:24.914Z
title: Route hybrid WS kick to login conflict cooldown
area: runtime
priority: P0
files:
  - ws_token/client.py:224
  - ws_token/runner.py:1878
  - game_actions/ws_phase.py:784
  - new_main_v2.py:96
  - new_main_v2.py:582
  - runtime_services/ws_runner_service.py:497
---

## Problem

`WSGameClient` 已能用 `cmd 259` 偵測異地登入，`run_device()` 也會在
`RunReport.kicked` 回報被踢。純 WS runner 的裝置 loop 會將此狀態轉成 30 分鐘
`kick_cooldown`，但一般 ADB/web_h5 的 hybrid WS-first 路徑只在 summary 寫
`kicked=True`，沒有轉成 `LoginConflictError`。結果可能繼續開啟 App/瀏覽器或執行
後續流程，未套用專案既有的異地登入避讓策略。

另外，現行 `is_kicked()` 同時涵蓋明確 `cmd 259` 與 reader 非預期斷線。兩者是否都應
使用 30 分鐘冷卻需要明確定義，避免把一般網路斷線誤報為帳號異地登入。

## Solution

讓 hybrid WS-first 在收到 kicked report 時停止本輪並交給既有 runtime login-conflict
處理，而不是只記錄後繼續。建議保留或擴充 kick 原因，至少區分 explicit `cmd 259`
與 transport drop；明確異地登入走 `LoginConflictError` 和 30 分鐘避讓，純斷線則採
較合適的網路恢復策略。

完成條件：

- hybrid WS-first 的明確 `cmd 259` 不再繼續開啟 H5/ADB pipeline。
- `new_main_v2.py` 既有 `runtime_login_conflict_30m` 路徑確實被觸發。
- 控制訊號不可被 `_run_ws_phase_for_wake()` 的 generic fallback 吞掉。
- local/master/worker 的 dashboard 狀態與下一次喚醒時間一致。
- 補上 hybrid kicked、一般 WS task error、transport drop 的分流測試。

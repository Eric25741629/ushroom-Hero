---
created: 2026-07-31T17:32:58.764Z
title: Diagnose recurring 5560 WS socket closure
area: runtime
priority: P0
files:
  - logs/emulator-5560/main.log:657
  - logs/emulator-5560/main.log:730
  - logs/system/session_registry.log
  - new_main_v2.py:102
  - ws_token/client.py:224
  - ws_token/client.py:354
  - ws_token/runner.py:1881
  - ws_token/online_monitor.py:513
  - device_wrapper.py:891
---

## Problem

`emulator-5560` 的 web_h5 WS-first 階段在 2026-07-31 23:00 與
2026-08-01 01:00 都於挖礦後出現
`WebSocketConnectionClosedException: socket is already closed`。同一輪的開神燈
也失敗，summary 標成 `kicked=True`；接著 H5 又記錄
`web_h5 session unavailable (app_start)` 並重啟瀏覽器，後續 H5 挖礦與開神燈仍能完成。

目前 log 無法區分這是明確 `cmd 259` 異地登入、online monitor / mount tracker
共享 session 造成的關閉、睡眠/喚醒時舊 client 被關閉，還是一般網路 transport drop。
`RunReport.kicked` 又同時涵蓋明確被踢與非預期斷線，導致這次事件不能直接判定為帳號衝突。

## Solution

先在一次完整喚醒週期記錄 WS close code、cmd 259/回呼原因、client 建立與關閉時間、
session_registry owner/lease，以及 online monitor handoff 的時間線，對照 H5 browser
restart 是否同一事件的後續反應。依證據把 explicit login conflict、可重連的
transport drop、已由 sleep service 主動關閉分流；只有明確異地登入才交給既有 30 分鐘
冷卻，其他斷線則採正確的重連或 H5 fallback。

補上三類 close reason 的單元測試與 hybrid WS-first 整合測試，確認明確 kick 不會繼續
開 H5/APP，普通 transport drop 不會誤觸發帳號冷卻。

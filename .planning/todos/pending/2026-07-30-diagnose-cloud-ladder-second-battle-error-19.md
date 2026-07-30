---
created: 2026-07-30T15:22:24.914Z
title: Diagnose cloud ladder second battle error 19
area: automation
files:
  - ws_token/cloud_ladder.py:240
  - ws_token/cloud_ladder.py:290
  - ws_token/runner.py:1780
  - tests/test_ws_token_cloud_ladder.py:1
  - logs/emulator-5556/main.log:32
  - logs/adb-fc65396d-4LPqmI._adb-tls-connect._tcp/main.log:51
---

## Problem

2026-07-30 22:48 重啟後，`emulator-5556` 與手機帳號的雲纏天梯都成功完成第一場，
分別推進到 145/150 與 140/150；緊接著第二場的 `dungeon_battle_result` 都收到
`CMD_ERROR error=19`。WS 連線之後仍可完成其他任務，summary 也是 `kicked=False`，
所以目前證據不支持把 `error=19` 直接視為異地登入。

現有錯誤只記 level 和 error code。缺少 start/result 的 dungeon id、seed、原始回覆、
送出間隔、戰前戰後 HP/戰友狀態、當下 `client.is_kicked()` 與錯誤後 server state，
無法判斷原因是連續作戰節流、戰友/HP 狀態、封包欄位、結算時序或 server 狀態競爭。
現有單元測試只用同步 fake client 驗證連續兩場成功，沒有覆蓋 live 第二場拒絕。

## Solution

先加入有界且可回溯的除錯紀錄，再決定修正策略。至少記錄每場 start/result 的 level、
dungeon id、seed、response cmd、error body decode、耗時、前後 LadderState、成功場數與
kicked 狀態；錯誤後做只讀 `read_state()`，確認 server 是否其實已推進。

需要 live 對照時，優先開啟尚未完成的 `emulator-5556` H5 CDP port `9223`，擷取原生
H5 從第二場開戰到結算的命令順序、payload 與時間間隔。不要先用猜測加入 retry 或
固定 sleep，避免重複結算與誤耗資源。

完成條件：

- 一次失敗 log 足以區分異地登入、transport drop、server reject 與狀態已推進。
- 使用 CDP/WS 實測確認 `error=19` 的觸發條件與正確 H5 協定序列。
- 修正後可從目前關卡一路完成到 `now_level > max_level`，只在完整完成後寫 weekly marker。
- 補上 error 19、錯誤後狀態已推進、明確 kicked 與多場 pacing 的測試。


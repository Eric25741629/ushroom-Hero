---
created: 2026-08-10T13:39:19.026Z
title: Refactor device runtime orchestrator in layers
area: runtime
files:
  - new_main_v2.py:99
  - new_main_v2.py:179
  - new_main_v2.py:392
  - new_main_v2.py:646
  - runtime_services/runtime_fsm.py:1
  - runtime_services/runtime_fsm_shadow.py:1
  - runtime_services/sleep_service.py:147
  - game_actions/daily_pipeline.py:125
  - game_actions/manager_factory.py:21
  - tests/test_runtime_fsm.py:1
  - tests/test_runtime_interrupts.py:249
---

## Problem

`new_main_v2.main()` 目前仍是單一裝置的巨型常駐協調器，從初始化、WS-first、
喚醒與遊戲啟動、daily pipeline、手動接管、例外分類、cleanup 到休眠排程都集中在
同一個函式。backend 判斷、全域 `bot_state`、設定讀取與多種控制訊號交錯，使控制流
難以隔離測試，也讓後續改動容易破壞 WS 必須先於 H5 啟動、web_h5 不得執行
Android-only cleanup、強制休眠優先序及登入衝突 cooldown 等既有契約。

目前的 `runtime_fsm` 明確只是 W13 shadow 試點，只涵蓋四個 lifecycle phase 與五個
event；manual launch、pause、login conflict、startup bypass、phone offline 等 live
分支尚未建模，因此不能直接升為 authoritative。例外也不能用簡單的
`SLEEP_POLICY_MAP[type(e)]` 全部合併，因為各分支在 teardown、cleanup、continue/sleep
行為上有實質差異。

另有已完成抽取後發生的架構漂移：`game_actions.manager_factory._init_runtime_managers`
及其測試仍存在，但 `main()` 又直接建立六個 manager。`DailyContext` 也已經包含 feature
flags，後續應建立單次解析的 runtime config snapshot，而不是再增加平行旗標容器。

## Solution

依低風險到高風險分期執行，且每期都在獨立 worktree／功能分支完成、跑目標測試、
合併回 main 後重跑驗證：

1. 補 behavior-level characterization tests，鎖住 force sleep、manual web launch、
   WS-before-H5、三個 login-conflict 入口、phone offline cleanup、萬神 one-shot；逐步
   降低只檢查 AST 形狀的測試依賴。
2. 恢復 manager factory 接線、抽唯一 runtime shadow telemetry adapter，並新增 immutable
   `DeviceRuntimeConfig.from_config()`，確保一輪執行使用同一份設定快照；不改控制流。
3. 新增結構化 `CycleDirective` 與 exception/outcome classifier。directive 至少描述
   `continue/sleep/exit`、sleep policy/reason、forced wake、force-now、stop-runtime、
   skip-phone-cleanup 與 FSM event。先保留 typed handlers 委派分類器，確認行為等價後
   才評估合併 handler。
4. 建立 `RuntimeSession`，抽出單輪 `_run_wake_cycle(session) -> CycleDirective`；讓
   `main()` 只負責初始化、迴圈編排、套用 directive、sleep 與 finally cleanup。
5. 僅針對真正不同的 lifecycle 行為建立 backend adapter：browser liveness、start client、
   credential refresh、stop runtime。保留 `MonitoredDevice` 作為共同裝置介面，禁止把
   Android-only wake/notification cleanup 套到 web_h5。
6. 擴充 shadow FSM 的可觀察事件與優先序，包含 manual launch、pause、login conflict、
   startup bypass、offline degradation；FSM 只管理 lifecycle phase，休眠與 cleanup
   policy 仍由 directive reducer 管理。
7. shadow mismatch 經測試與 live 觀察穩定為零後，才以 per-device feature flag/canary
   讓 FSM effect executor 逐步接管：先單一 web_h5、再 ADB、最後移除舊分支，並保留
   快速退回 shadow-only 的能力。

不要用 context manager 在區塊離開時自動送出 phase-completed event；階段可能因強制休眠、
手動接管或登入衝突中斷，必須由明確的 cycle outcome 區分完成與中止。

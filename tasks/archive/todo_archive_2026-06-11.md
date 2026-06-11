# todo.md 歸檔 (2026-06-11 整理)

> 從 `tasks/todo.md` 移出的已完成項目全文，含 Review。原文照搬，未刪改。

---

# 手機離線 WS 降級模式 + 純 adb 自動撈 token (2026-06-11 完成)

> 使用者已核准。延續下方「掉線判離線」fix。
> 工作慣例：TDD、subagents 一律 model:"opus"、計畫/進度走本檔。

## 需求
1. **WS 降級模式**：adb(+ws) 直連手機（fc65396d/192.168）連不上時，該輪**不要死等**
   （現在 `_wait_for_phone_connection` 會永遠卡住，整個 thread 不再休眠、WS 也只多跑一輪），
   改成：放棄本輪 ADB 任務 → 照常排程休眠 → 每次喚醒仍先跑純 WS（快取 ticket）→
   手機回 wifi 自動恢復完整 ADB 流程。
2. **純 adb 模式也自動撈 token**：就算 `ws_token.enabled=false`，每次正常喚醒、遊戲
   App 啟動成功後，best-effort 從 logcat 撈 WS ticket 寫進 auth capture 檔 →
   使用者日後切「adb+ws」立刻有可用 token。

## 已確認的事實（調查結果）
- `new_main_v2.py` 主迴圈順序：**WS 階段在 ADB 喚醒之前**（~line 252-258 `_run_ws_phase_for_wake`
  → line 262 `handle_device_wakeup`）→ 所以「喚醒失敗就跳過 ADB 去睡」天然等於降級成純 WS。
- 卡死點：`utils/wake_up_handler.py` `_wait_for_phone_connection`（fc65396d/192.168 分支，
  60s 自癒重試、剛改成會更新狀態+可暫停/強制休眠，但仍是無限等待）。
- 主迴圈 except 鏈（new_main_v2 ~383-426）：已有 ForceSleepRequested / StartupBypassError
  等→各自 sleep_policy。降級可仿照：新例外（如 PhoneUnreachableError）→
  sleep_policy="phone_offline_ws_only"、正常 aligned window 休眠。
- ⚠ 任務後收尾段 new_main_v2 ~428-443（`reset_screen_settings`/`d.info` 重連/`open_notification`/
  `screen_off`，僅 fc65396d/192.168 跑）：手機不在會連線失敗且 `connect_u2_with_retries`
  重試很慢 — 降級該輪要跳過這段。
- Token 撈取既有管線：`tools/adb_token_login.py`（restart app→scrape logcat→寫
  `_auth_capture_<dev>.json`；**--verify 會踢 App session，自動化絕不能帶**；ticket 由
  SDK 冷啟動重簽，bot 喚醒本來就會啟動 App → App 剛啟動後 logcat 直接撈即可，不必額外 restart）。
- web_h5 已有對照模式：startup 成功後呼叫 `utils/ws_ticket_refresh.refresh_from_device(d, ip)`
  （new_main_v2 ~354-356）→ adb 版照這個 hook 點做（startup result=True 之後，best-effort）。
- ws runner 端 token 消費/快取：`ws_token/runner.py` `_ensure_token` / `_is_adb_reachable`
  （adb 不可達會沿用快取 token）。降級模式撐到 ticket 過期為止；過期顯示「WS 登入失敗」，
  手機回家後重撈自動恢復 — 此為已告知使用者的接受行為。

## Plan（已全部完成）
- [x] 讀上述 4 檔，釘 capture 檔格式與可重用函式。(2026-06-11 recon：capture=`auth_state/_auth_capture_<dev>.json` `{"creds":{...}}`；讀 utf-8-sig 寫 utf-8；`_ensure_token` 其實在 `runtime_services/ws_runner_service.py` 非 runner.py；`PhoneUnreachableError` 放 `runtime_services/device_runtime_service.py`；`tools/` 非 package 不能直接 import；sleep_policy 純 logging、行為由 forced_wake_ts 決定)
- [x] A1: `_wait_for_phone_connection(..., max_wait_sec=None)`，喚醒路徑帶
      `PHONE_CONNECT_MAX_WAIT_SEC=180`（>=180s 放棄，3 次 60s 重試）→ raise
      `PhoneUnreachableError`（定義在 `runtime_services/device_runtime_service.py`）。
- [x] A2: new_main_v2 except 鏈接住（`skip_phone_cleanup` flag 跳過收尾段）→
      `sleep_policy="phone_offline_ws_only"` 正常 aligned 休眠；
      state 寫「手機離線，本輪僅執行 WS」。release_wakeup_lock/run_sleep_cycle 照常跑。
- [x] A3: 測試：test_wake_phone_reconnect +5（逾時 raise/自癒不破壞/預設無限相容）；
      test_wake_loop_escape/test_offline_after_disconnect 全綠（24 passed）。
      spec review ✅ + quality review APPROVED（deadline 邊界已改 >=）。
- [x] B1: 新 `utils/adb_token_scrape.py` `refresh_from_adb_device(d, ip, *, auth_dir, timeout_sec=20)`：
      `adb logcat -d`（單次 dump、最多 3 次、20s 上限、utf-8）→ importlib 載入
      `tools/adb_token_login.py` 重用 regex+build_creds → 寫 `_auth_capture_<ip>.json`
      （`_source="adb_logcat_passive"`；不 verify、不 restart、永不 raise、warm start 只 log info）。
- [x] B2: new_main_v2 startup 成功分支加 `elif backend_kind == "adb":`（含手機/模擬器，
      ws_token.enabled 與否都跑）。
- [x] B3: 測試 tests/test_adb_token_scrape.py 5 個（happy/warm-start/timeout/壞回包/覆寫，
      happy path 有過 Creds.from_dict round-trip）；consumer test_ws_token_runner 43 綠。
      spec review ✅ + quality review APPROVED（subprocess 補 encoding="utf-8"）。
- [x] 全部改動照例：focused pytest + py_compile；完成後提醒重啟 master+worker。

## Review (2026-06-11 完成，subagent-driven：每 task 實作+spec 審查+品質審查)

- **改動檔**：`runtime_services/device_runtime_service.py`（+`PhoneUnreachableError`）、
  `utils/wake_up_handler.py`（`_wait_for_phone_connection(max_wait_sec=None)` +
  `PHONE_CONNECT_MAX_WAIT_SEC=180`，deadline 在 try 之外、>= 邊界=精準 3 次 60s 重試）、
  `new_main_v2.py`（except PhoneUnreachableError → `skip_phone_cleanup` 跳過收尾段、
  `sleep_policy="phone_offline_ws_only"` 正常 aligned 休眠；startup 成功分支
  `elif backend_kind=="adb"` 接 `refresh_from_adb_device`）、
  `utils/adb_token_scrape.py`（新，被動 logcat 撈 ticket）、
  `tools/adb_token_login.py`（cross-ref 註解）。
- **測試**：test_adb_token_scrape(5 新) + test_wake_phone_reconnect(+5) +
  test_wake_loop_escape + test_offline_after_disconnect + test_ws_token_runner
  = **72 passed**；py_compile 全綠。
- **行為**：手機離線 → 喚醒前 WS 階段照跑（快取 ticket）→ ADB 連線 180s 放棄 →
  跳過收尾段 → 照常排程休眠；手機回 wifi 下輪自動恢復完整流程。
  純 adb 裝置每次冷啟成功後被動撈 ticket 寫 capture 檔（warm start 撈不到=靜默，
  正常；絕不 --verify / 不踢 session）。
- **⚠ 需重啟生效**：master（infinite）與 worker（desktop_ov0asq4）的 `new_main_v2.py`
  都要重啟（連同前次「掉線 1h 判離線」fix 一起生效）。
- **未做（接受）**：ticket 過期且手機持續離線 → WS 登入失敗顯示於 log，回家自動恢復
  （已告知使用者的接受行為）；passive scrape 尚未在實機 live 驗證（等手機/模擬器下次冷啟）。

---

# 手機掉線 1 小時 → 直接判定離線 (2026-06-11 完成)

**Trigger**: 使用者手機 (worker `desktop_ov0asq4` 上的 `adb-fc65396d-...adb-tls-connect`) 人離開家後
wifi ADB 斷線，master dashboard 永遠卡在「ONLINE / 休眠中 / 強制休眠 / 喚醒中... / 更新 23:22:35」。
要求：掉線超過 1 小時就直接判定離線。

## Root cause（已逐行確認）

1. **worker 斷線後完全停止回報該裝置**：`worker_sync_service.py:82-85` 只回報還在
   `adb devices` 的裝置 → master 永遠停在最後一筆快照（更新時間凍結在斷線那刻）。
2. **master ingest 丟棄 status**：`control_panel_app.report_status:1269` 呼叫 update_state
   時沒傳 `status` → remote 裝置在 master 上永遠是建立時的 ONLINE。
3. **沒有任何 staleness 機制在跑**：`bot_state.sweep_stale_states` 存在但 production 無人呼叫
   （只有測試引用），且其預設參數（20s 標離線、120s 刪 remote）不符本系統（本機睡眠不心跳）。
4. **worker 端 thread 卡死且無聲**：`wake_up_handler.py:301-311` fc65396d/192.168 分支的
   screen-check 是無限 while True 重試（每 60s），不更新 bot_state、不理 dashboard 控制。
5. **scan 服務不標離線**：`device_scan_service` 裝置消失只在 infinite 主機對固定清單標
   「疑似當機」task；非 infinite 主機（worker）什麼都不做；另有 3h dead-device 規則會
   永久封鎖「回連後重啟」（offline_since 不會被清）。
6. dashboard 倒數只看 `next_wake_at` 是否過期（dashboard.html:2364-2380），過期就顯示
   「喚醒中...」，與 status 無關 → 標離線時必須清掉 `next_wake_at`。

## Plan（已全部完成）

- [x] **bot_state.py**
  - `update_state(..., status=None)`：status 直通；轉入 OFFLINE 設 `offline_since` + pop
    `next_wake_at`；離開 OFFLINE pop `offline_since`（與 set_offline 語義一致）。
  - `set_offline` 同時 pop `next_wake_at`。
  - 新增 `set_online(ip, reason)`（OFFLINE→ONLINE + 清 anchor）、`clear_offline_anchor(ip)`
    （保留 OFFLINE 但解除 3h 重啟封鎖）。
  - 新增 `sweep_stale_remote_devices(offline_after_sec=3600)`：只動 remote key
    （`":" in ip and not is_local_device(ip)`），逾時標 OFFLINE + 清 next_wake_at，**不刪 entry**
    （本機裝置睡眠不心跳，絕不能用 staleness 標本機）。
- [x] **control_panel_app.report_status**：把 worker 回報的 `status` 傳入 update_state。
- [x] **bootstrap/api_services.scan_loop**：每輪呼叫 `sweep_stale_remote_devices()`（master 上
  remote key 逾 1h 未回報 → 離線；worker 上無 remote key → no-op）。
- [x] **device_scan_service**：`_apply_adb_absence_rule()` — 以 raw `adb devices`（只含
  `device` 狀態）追蹤 `_adb_last_seen`；缺席 ≥ 3600s 且狀態非 OFFLINE → `set_offline`；
  回到清單時 thread 活著 → `set_online`、死了 → `clear_offline_anchor`（修復 3h 封鎖 bug）。
- [x] **wake_up_handler**：301-311 無限迴圈抽成 `_wait_for_phone_connection`：每輪
  `update_state(task="連線中斷", ...)` + `_honor_dashboard_controls`（暫停/強制休眠可中斷），
  保留 60s 重試自癒（手機回家自動接上）。
- [x] 測試：tests/test_offline_after_disconnect.py、tests/test_device_scan_absence.py、
  擴充 test_worker_routes_integration / test_bootstrap_api_services、wake retry 可見性測試。

## Review (2026-06-11 完成)

- 改動檔：`bot_state.py`（update_state status 直通 / set_offline 清 next_wake_at /
  set_online / clear_offline_anchor / sweep_stale_remote_devices）、
  `control_panel_app.py`（ingest status 直通）、`bootstrap/api_services.py`（scan_loop 接 sweep）、
  `runtime_services/device_scan_service.py`（_apply_adb_absence_rule + 回連恢復）、
  `utils/wake_up_handler.py`（_wait_for_phone_connection：可見+可中斷，保留 60s 自癒）。
- 新測試：test_offline_after_disconnect (12)、test_device_scan_absence (6)、
  test_wake_phone_reconnect (3)；擴充 bootstrap (+1)、worker_routes (+1)。全綠；
  bot_state 既有 5 個套件 50 測全綠。
- 順手修：test_wake_loop_escape / test_wake_phone_reconnect 的 fixture teardown 改成
  「還原 bot_state 模組物件」而非 pop（pop 會讓 lazy import 的 device_wrapper 拿到
  分裂的新 bot_state，跨檔汙染 test_pause_routing）。
- **已知（既有、非本次造成）**：`test_bootstrap_api_services` 與
  `test_pause_routing_and_weblaunch` / `test_worker_routes_integration` 混跑會因
  bootstrap 的 bare `worker_webhook_api` / `control_panel_app` stub 互踩（HEAD 上可重現，
  失敗集合相同）。單檔/常規組合皆綠。
- **需重啟才生效**：master（infinite）與 worker（desktop_ov0asq4）的 `new_main_v2.py`
  都要重啟（sys.modules cache）。

預期行為：手機離家 → 1 小時內維持現狀（寬限）→ 1 小時後 master 卡片轉 OFFLINE
（「Worker 超過 60 分鐘未回報，判定離線」），倒數顯示 `--`；手機回家 → worker 60s 內重連、
恢復回報，master 自動翻回 ONLINE，無需手動。

---

# 解耦跨裝置 online-check (S5b 落地, 2026-06-09 完成)

**Branch**: `feat/ws-token-integration` (worktree `C:\Users\Eric\ws-token-integration`)
**Trigger**: 使用者要 checker 不再寫死 emulator-5554、requester 不限 5558。

## 目標
- request 帶 `target_pid`、不綁特定 checker；任一「在 checker 清單 + 空閒 + 好友含 target」的帳號可服務並回寫 mailbox。
- 向後相容：預設 config `online_check_checkers=["emulator-5554"]` → 現行行為零變化、5558 保護不變。
- checker 的 check_on_line 走 **protocol-only** (無 OCR)。

## Plan / Progress（已全部完成）
- [x] config_manager: `DEFAULT_GLOBAL_CONFIG["online_check_checkers"]=["emulator-5554"]` + `get_online_check_checkers()`。
- [x] bot_state:
  - request payload 帶 `target_pid`；queue 改成單一 pending FIFO (`_online_check_pending`)，pop 時 checker 必須在清單內。
  - `submit_online_check_request(requester_ip, checker_ip=None, *, target_pid=None)` 向後相容。
  - `pop_online_check_request(checker_ip)` / `has_pending_online_check_request(checker_ip)` / `is_online_check_priority_active(checker_ip)`：checker 不在清單內一律回空/False。
  - 新增 `is_online_check_checker(ip)`、`get_online_check_target_pid(req_id)` helper。dedup/event/wait/refresh/signal 全保留。
- [x] web_session_service.process_online_check_requests：`if ip != "emulator-5554"` → `if not is_online_check_checker(ip)`；改走 protocol-only check。
- [x] game_initialization: 新增 `check_on_line_protocol_only(checker_ip, target_pid, threshold_sec)` (跳過 OCR、好友沒 target → busy)。
- [x] wake_up_handler / new_main_v2 / sleep_service / device_runtime_service / startup_sleep：所有寫死 `emulator-5554` 的 checker 分支 → `is_online_check_checker(ip)`；requester 不限 5558。
- [x] 新增 `tests/test_online_check_decoupled.py`。

## Review — S5b online-check 解耦 (2026-06-09)

**改了哪些檔**
- `config_manager.py`：`DEFAULT_GLOBAL_CONFIG["online_check_checkers"]=["emulator-5554"]` + `get_online_check_checkers()`（trim/dedup/fallback）。
- `bot_state.py`：mailbox 改成單一 FIFO `_online_check_pending`（移除 `_online_check_queue_by_checker`）；request payload 帶 `target_pid`/`claimed_by`；`submit_online_check_request(requester, checker_ip=None, *, target_pid=None)`（dedup key 改 requester+target_pid）；`pop`/`has_pending`/`is_priority_active` 全部 gate 在 checker 清單內；新增 `is_online_check_checker`/`get_online_check_target_pid`/`_signal_all_checkers_locked`。
- `runtime_services/web_session_service.py`：`process_online_check_requests` checker gate 改 `is_online_check_checker(ip)` + protocol-only（`_run_checker_protocol_only`，undetermined → fail 讓別人試）；`wait_for_checker_gate_before_start` 帶 target_pid；`initialize_runtime_device` requester gate 改「有 target_pid 且非 checker」。
- `game_initialization.py`：新增 `_prepare_checker_web_api(ip)` + `check_on_line_protocol_only(checker_ip, target_pid, threshold)`（好友列表沒 target → `(None, CHECK_TARGET_NOT_FRIEND)`）。
- `utils/wake_up_handler.py` / `new_main_v2.py` / `runtime_services/sleep_service.py` / `device_runtime_service.py` / `startup_sleep.py`：所有 `ip=="emulator-5554"` checker 分支 → `is_online_check_checker(ip)`；5558 requester 分支 → 「有 target_pid 且非 checker」。
- tests：新增 `tests/test_online_check_decoupled.py`（11 測）；修 `test_bot_state_phase2.py`（cleanup 用 `_online_check_pending`）、`test_online_check_immediate_wake.py`（fake state 補 `is_online_check_checker`）。

**測試**：`test_online_check_decoupled / immediate_wake / bot_state_phase2 / startup_sleep / sleep_service / wake_loop_escape / game_initialization / device_config` = **75 passed**。py_compile 全綠。
（`test_stage_guard.py`/`test_pause_routing_and_weblaunch.py` 失敗為**既有環境** import 問題 `adb_operations.tap_device`/`run_adb`，與本次無關。）

**向後相容怎麼保證**：預設 config 不含 `online_check_checkers` → fallback `["emulator-5554"]`；唯一設 `online_check_target_pid` 的是 5558 → requester gate 仍只對 5558 觸發；checker gate 仍只 5554 服務；dedup/中斷立即喚醒/`skip_online_check_once`/resume-sleep 全保留。回歸測試（immediate_wake 5554 立即喚醒、5560 不誤喚；startup_sleep 5554 提前結束）佐證。

**check_on_line 怎麼變 protocol-only**：checker 不再呼叫舊 `check_on_line`（protocol+OCR cross-verify）。改 `check_on_line_protocol_only`：用 checker 既有 web_h5 session 抓好友列表（0x0f02），用與 `is_player_online` 相同規則判 busy；**完全不跑 OCR**。target 不在好友列表 → 回 None（無法判定）→ caller `fail_online_check_request` 讓 requester retry / 其他 checker 試，**不誤判 offline 放行**。

**checker「空閒」判定**：沿用既有中斷驅動語義（未新增 busy flag）。submit 時 `_signal_all_checkers_locked` 對清單內每個 checker 發 SKIP_SLEEP + refresh；checker 在自己的 sleep/wake gate 或主迴圈頂端輪詢 mailbox，正在跑任務的 checker 跑完當前 iteration 才服務。pop 在 `_global_lock` 內原子，最先到的 checker 認領，其餘看到空 pending。

**對 5558 保護的疑慮**：(1) 多 checker 時若某 checker 好友沒 target → 回 None fail，requester 看到 status!=done 視同 busy 繼續等（保守，不放行）。(2) 預設單 checker 下行為與舊版逐行等價。(3) dedup 改 key requester+target_pid：5558 target 固定 → 仍正確 coalesce。

---

# 降低本機運算量 / GPU (2026-05-31，Phase 1 + 分流完成)

**Branch**: `perf/reduce-gpu-usage`
**Trigger**: 「降低 GPU 使用量 / 降低運算」
**用戶設定**: 目標=**本機**(跑 bot+Chrome+挖礦);取捨=**零風險**;重點=**降低運算要求**(不只 GPU)

## 4 路平行分析結論(都讀碼驗證)

本機 GPU/運算三大源:
1. **Chrome WebGL**(最大本機 GPU)— 5 台 web_h5 有 4 台 headful、**完全無 `--disable-gpu`**,Cocos 最高 ~60fps,最多 5 並行。`web_stop_mode=close_browser` → 已是 bursty 非 24h。
2. **挖礦 CNN**(`miner/models/classifier.py`)— torch cu126 自動上 GPU;模型極小;**每次 classify 跑 42 次 batch=1 forward**(可數學等價批次化)。executor 每挖一格再 re-classify 3~5 次。
3. **OCR**(`ocr_server.py`)— PP-OCRv5_server 吃 GPU,但 **bot 預設連遠端 100.64.0.5**,GPU 負載不在本機 → **本任務排除**。

## Phase 1 — 零風險、可削減本機運算(完成)

- [x] **1.1** 挖礦 CNN classify_board 由 42×(batch=1) 改 **單次 batch=42** forward。`miner/models/classifier.py` + `miner/v2/classifier.py`。TDD 等價測試 `tests/test_classifier_batched.py`(single-forward + 逐格參考等價,v1/v2 各 2 測,4 pass)。v2 順手移除已無用的 `_predict_cell`。
- [x] **1.2** mining + page CNN forward 包 `torch.inference_mode()`。v1/v2 classifier 的批次 forward + `cnn_model.predict_image`。輸出不變(等價測試涵蓋 mining 路徑)。
- [x] **1.3** 啟動 `configure_torch_runtime()` 設 torch intra-op 執行緒上限(`utils/torch_runtime.py`,可由 `bot_config global.compute.torch_num_threads` 覆寫;預設 None=不動,保守)。`tests/test_torch_runtime.py`。
- [~] **1.4** Dashboard 輪詢 visibilityState gate + `check_ocr_server` TTL 快取 — **延後**。只在 dashboard 分頁開啟時才有成本,非 24h 背景負載;低優先。要做再開。
- [~] **1.5** pickaxe OCR 校驗間隔 — **不改**。已從 3 刻意調到 5 並註解;再加寬是「漂移偵測延遲」取捨非純利,且 OCR 走遠端非本機。守零風險不擅動。

## 分流(新增需求 2026-05-31「不要通通擠在一起」)

- [x] **F1 運算層分流(零風險,已做)**:`utils/torch_runtime.InferenceGate` 程序級閘門,序列化共用 CNN 模型的 forward;v1/v2 classify_board 的 forward 以 `inference_slot()` 包住。多裝置同醒時推論排隊而非一起擠爆 GPU。併發上限可由 `global.compute.inference_concurrency` 覆寫(預設 1=序列化)。`tests/test_torch_runtime.py` 驗證序列化與有界併發。
- [x] **F2 排程層分流(已做)**:偶/奇數小時喚醒分流。`calc_aligned_wake_ts` 加可選 `hour_parity`(保留 :00–:20 窗口給深淵之門,只挑偶/奇數小時);`run_sleep_cycle` 讀每裝置 `wake_hour_parity`。預設 None=現狀。深淵之門每天只要一次 → 每 2 小時綽綽有餘。
  - **小時分配(3/3 平分)**:偶=5554/5558/5556(5554+5558 同組保住上線互檢)、奇=5560/7fe98fc6/adb-fc65396d。任一小時只 3 台醒,負載砍半。
  - **分鐘錯開(同組內再分流)**:`calc_aligned_wake_ts` 加 `wake_minute_offset`,窗口內固定分鐘取代隨機。每組 0/5/15 錯開 → 同一小時 3 台分別 :00/:05/:15 啟動,不同時湧入。偶:5554=:00、5558=:05、5556=:15;奇:5560=:00、7fe98fc6=:05、adb=:15。
  - 完整排程表:

    | 裝置 | 小時 | 分鐘 |
    |---|---|---|
    | 5554 | even | :00 |
    | 5558 | even | :05 |
    | 5556 | even | :15 |
    | 5560 | odd | :00 |
    | 7fe98fc6 | odd | :05 |
    | adb-fc65396d | odd | :15 |

  - 測試:`tests/test_sleep_service.py` 新增 9 個(even/odd 落點、:00–:20 不變、parity=None 向後相容、`_parse_hour_parity`、固定分鐘 deterministic/clamp/與 parity 組合、run_sleep_cycle parity+minute integration);**23 pass**。
  - **上線互檢不受 parity 影響(關鍵)**:5558 的互檢是**中斷驅動**,5554 在 `sleep_until_wake_or_interrupt` 每秒輪詢,偵測到 `has_pending_online_check_request('emulator-5554')` 就 `return True` **≤1 秒立即喚醒**(`device_runtime_service.py:133-136`),與 parity/`wake_ts` 無關。先前「延遲 2h」的說法是錯的,已用 `tests/test_online_check_immediate_wake.py`(2 pass)鎖死此保證。
- [x] **F4 啟動層分流(2026-05-31)**:啟動瞬間 6 台不再一起湧入。`runtime_services/startup_sleep.py` 的硬編碼 `STARTUP_SLEEP_SEC_BY_DEVICE`(5554/5556/5560 各 3 分,其餘 0 → 兩團一起醒)改為**由 F2/F3 的 `wake_hour_parity`+`wake_minute_offset` 推導**:`compute_stagger_order` 依 even→odd、組內 :00→:05→:15 排序,`resolve_startup_stagger_sec` = rank × gap;gap 由 `global.compute.startup_stagger_sec` 覆寫(預設 **120s**)。實際排程:5554=0:00 / 5558=2:00 / 5556=4:00 / 5560=6:00 / 7fe98fc6=8:00 / adb=10:00。保留 5554↔5558 互檢與手動「開啟瀏覽器」提前結束。`tests/test_startup_sleep.py` +7(共 12 pass)。
  - **⚠️ 殘留重疊(待用戶決定)**:`utils/wake_up_handler.py:322` 還有舊的「分流延遲」(5554/5556 每次喚醒等 5 分,且與 5558 互檢 mailbox 回返邏輯纏在一起)。會與 F4 的首輪啟動延遲疊加(5556 ≈ 4 分啟動 + 再等 5 分)。未動以免動到跨裝置互檢時序;若要全清需小心保留 5554 的 online-check 提前 return。
- [x] **B1 開神燈 callable 崩錯(2026-05-31,順手)**:`device.close_notification`/`open_notification` 對 web_h5 後端會在 `d(packageNameMatches=...)` 觸發 `'PlaywrightGameDevice' object is not callable`(舊 `hasattr('open_quick_settings')` guard 被 Playwright stub 騙過)。改加 `backend_kind=="web_h5"` 早退(沿用 `device_wrapper.py:195` 既有 idiom)。`tests/test_close_notification.py`(3 pass)。

## Review (after execution)

**已完成(branch `perf/reduce-gpu-usage`):**
- 最大運算削減 = CNN 批次化:每次 `classify_board` 由 **42 次 forward → 1 次**(executor 每挖一格再 re-classify 3~5 次,複利)。SimpleCNN 無 BN/Dropout,eval 下逐格與批次數學等價,TDD 釘住等價性(label 全等、confidence allclose 1e-5)。
- `inference_mode` 取代 `no_grad`(mining)、補上 page CNN(原本無)。
- torch 執行緒上限 + GPU 推論閘門(分流),皆 config 可調,預設保守。
- 測試:新增 8 個全綠(`test_classifier_batched` 4 + `test_torch_runtime` 4);`test_manager_factory` 等 runtime import 鏈不受影響(11 pass)。executor 測試單獨跑全過(合跑的 7 失敗是 `test_mining_item_logic` 既有 sys.modules 污染,與本次無關)。
- **未碰**(守零風險/本機範圍):Chrome `--disable-gpu`/headless(Phase 2,需 live 驗證)、OCR server(遠端非本機)、per-action WS drain(餵神燈封包偵測,不可關)。
- **待用戶決定**:F2 排程層分流強度;Phase 2 是否進行。
- **⚠️ 套用需重啟 bot**(sys.modules 快取)。建議重啟後觀察 log「[System] 分流設定 ...」確認生效。

## 明確不碰(陷阱)

- **per-action WS drain**(`device_wrapper.py` _collect_ws_frames)— 餵養**神燈封包偵測**(0x0504 掉落),production-critical,**不可關**(agent 誤判為純 RE,記憶庫證實封包為主)。
- **OCR server 改 CPU/mobile** — 在遠端 100.64.0.5,非本機目標。

---

# 開神燈 V2 重構 — 修三狀態 + 廢 V1 + 預設全 V2 + OCR 驗 ADB (2026-05-29 完成)

## 根因(live 研究 7fe98fc6 確認)
進燈介面後三種啟動狀態,上一輪沒正確收尾會殘留:
1. 空的 → 正常開燈。2. 一件待處理 → 「當前裝備 vs NEW」強制比較窗(出售/裝備,Escape 關不掉)。
3. 20 件待賣 → 全部出售 grid(20 格 + 紅「全部出售」鈕)。
`navigate_to_lamp`=(447,801)→(281,636)→自動→開始 盲點固定座標:(447,801) 開出 grid/比較窗後,
後續點擊不會清殘留 → 卡住。偵測 OK(`is_lamp_sell_page` live 回 True、OCR 找得到「全部出售」),
壞在流程沒「清殘留→確認→驗證清空」。(447,801) 還被 navigate 與 exit 共用。

## 任務
- [x] P1 `opengold_v2/lamp_startup.py`(分類+清殘留 resolver,stall/上限)+ `tests/test_lamp_startup.py`(13 pass)
- [x] P2 UIController:cocos `lamp_ui_state()`/cocos count(`btnBox/txtNum`)/`click_all_sell_and_verify()`/`close_artifact_view()`/`click_cocos_node()`;OCR `is_comparison_dialog/is_blocking_popup` 留 ADB fallback
- [x] P3 LampService:`_enter_lamp_and_clear`(cocos H5 / OCR ADB)清殘留;run 起手 pre-check 數量;cocos 開燈監控迴圈(count停滯重開);`_finish_clean`+收尾驗證乾淨主頁
- [x] P4 單件比較窗(EquipEditView)→ `process_single_lamp` 走同一套規則
- [x] P5 bot_config 全 `use_opengold_v2=true`;`lamp_scheduler._run_lamp` 一律 V2;`open_the_gold` 加 deprecation;測試更新+隔離修正
- [x] P6 ADB OCR/pixel 偵測器於真實截圖驗證(賣場/比較/主頁+count 全對;live ADB 離線無法跑全程)

## Review (2026-05-29)
**Live 驗證(7fe98fc6, H5):** 狀態3(20件)→ click_all_sell_and_verify 清空(cocos EquipTempBagView True→False);
單件(技爆)→ 規則判定不要→出售;乾淨開燈 736142→735122 = **1020顆/58s ≈ 1批/秒(30s ~25次)**;
神器頁誤入→自動關閉復原;收尾回乾淨主頁。manual hold 已釋放。
**根因修正:** 上一輪殘留(賣場/比較窗)擋住盲點導航 + OCR 偵測太慢餓死迴圈。改 cocos 精確+快速偵測。
**測試:** lamp 全套 51 passed。
**⚠️ 待辦:** 需**重啟 bot** 才套用(sys.modules 快取);ADB 待裝置上線跑一次全程驗證;單件「要的」combo 的
切方案 scheme 導航(process_single_lamp)仍是舊盲點座標,已用「誤入神器頁自動關閉」兜底,未來宜改 cocos。

---

# 專案統整 / 重構 計畫 (2026-05-19，Phase 1 完成；後續展開見 docs/REFACTORING_OPPORTUNITIES.md)

**Date**: 2026-05-19
**Trigger**: `/goal 檢查程式碼複雜度 把需要的融合起來 功能不同的切分開來 統整整個專案`
**Status**: Phase 1 COMPLETE. Phase 2/3/4 已被 2026-05-31 的 `docs/REFACTORING_OPPORTUNITIES.md`
（lead 重新驗證版）取代為新的優先序，後續以該文件為準。

## 0. Audit Findings (read-only)

5 parallel audits ran on park / battle / lamp / god-module / cleanup clusters. Top-level numbers:

| Cluster | Live files | Dead files (verified 0 imports) | God modules to split |
|---|---|---|---|
| park | `park.py`, `new_park.py` | `park_test.py` (657L), `detect_parking_p.py` (86L) | — |
| battle | `new_battle.py`, `fight_car.py` | `battle.py` (69L), `fight_car_task.py` (229L) | `new_battle.py` (1001L) |
| lamp | `Open_gold_paddle_ocr.py` (V1), `opengold_v2/` (V2) | `Open_gold.py` (296L) | `Open_gold_paddle_ocr.py` after V2 migration |
| infra | `new_main_v2.py`, `control_panel_app.py`, `device_wrapper.py`, `json_manager.py` | — | all four |
| repo root | — | 25 sync-conflict files, 7 Untitled-*, 8 tmp/trash dirs, 6 empty source dirs, aborted `refactor/` | — |

Pre-existing context discovered during audit:
- A `REFACTOR_ROADMAP.md` was drafted on **2026-05-16** but only survives as a `*.sync-conflict-*` copy; canonical file is missing. The roadmap there proposes a similar P0–P4 plan (threading locks, dedupe, json_manager split, dead-code purge). **This plan supersedes that draft** — the draft will be folded in as Phase 2.
- An aborted `refactor/` scaffold from **2026-04-24** (mostly empty `__init__.py` + READMEs in `adb_layer/`, `core/`, `game_init/`, `game_modules/`, `utils/`) exists. Not imported by anything. Treated as dead.
- The empty top-level dirs (`core/`, `mission/`, `find_img/`, `reward_get/`, `partner/`, `dataset/`) are the same aborted refactor's scaffolding leaking into the repo root. Also dead.

## Phase 1 — DONE 2026-05-19

Commits on branch `chore/consolidation-2026-05-19`:

| SHA | Phase | Files | Net LOC |
|---|---|---|---|
| `ef5cc8aa` | P1F miner_test sandbox | 24 | −9.6 MB / RL artifacts |
| `6d61ec47` | P1B ws_capture migration + tmp/trash/dated purge | ~24 000 | path moved, captures regenerate |
| `c1f01d8e` | P1E farm_v2 wire-in / farm/ retired | 13 | −281 |
| `f57ea8da` | P1C refactor/ scaffold + core/ + partner/ + mission PNGs | 28 | −370 |
| `9becba70` | P1A+1D dead .py modules + scratch | 14 | −1 600 |
| (no commit) | P1A sync-conflicts at root + tests/ (gitignored) | 68 | — |

**Net**: ~24 200 files removed, ~3 700 LOC of code/scripts deleted, 10 protocol docs preserved at `docs/protocol/` and `docs/game_client_sources/`. Tests held at **392 pass / 8 skip** throughout.

Audit corrections made on the fly:
- `find_img/`, `reward_get/`, `dataset/` originally flagged for delete — kept (live runtime writers).
- 2 sync-conflict files in `tools/` missed by initial sweep — caught in P1B commit.
- `farm_v2/run_farm` renamed to `farm` to match legacy call signature.

### Phase 2 — pending（殘項已併入新清單）
- [ ] device_wrapper.py ~25 remaining silent `pass` blocks → warning log
- [ ] device_wrapper.py 7 bare `except:` → typed catches with log
- [ ] Extract `poll_stage(d, target, timeout)` shared helper
- [ ] `bot_state.clear_offline_devices()` two-stage lock merge (verify still applicable post 4d7d8893)

### Phase 3 — 2/5 done, 3 deferred
- [x] json_manager.py 878L → `json_manager/` package (7 files) — commit `8c12cac0`
- [x] new_battle.py 1093L → `battle/` package (7 files) + shim — commit `9b56f620`
- [~] control_panel_app.py — deferred（展開於 docs/REFACTORING_OPPORTUNITIES.md cx-0）
- [~] device_wrapper.py — deferred（同上 cx-5）
- [~] new_main_v2.py — deferred（同上）

### Phase 4 — Lamp V1 retirement（已部分達成：lamp_scheduler 一律 V2、flag 已移除）
- [ ] V2 prod log 穩定後刪 4 個 V1 函式 + `__main__`、退休 `lian_shan_example.py`

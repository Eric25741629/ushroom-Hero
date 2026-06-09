# WS 覆蓋 roadmap — 6 features recon 全完成 (2026-06-09)

> 分支 `feat/ws-token-integration`(worktree `C:\Users\Eric\ws-token-integration`)。整合主體已建(toggle/解耦online-check/手機loop/紅包神燈/kick建構中)。

## 已做 + 在 runner(裝置設 use_ws_runner 就跑這些)
main_tasks(主畫面領任務)、league_solo(魔法劇場+烈炎山洞寶箱)、guild(家族大廳)、steward(家園管家採購掃蕩)、redpack(紅包,免費常開)、lamp(神燈,`ws_token_open_lamp` gate)。

## recon 完成、待 build(6 個)
| feature | family / 關鍵 cmd | 欄位號 | 風險/備註 |
|---|---|---|---|
| 轉盤金幣 | `ad`22:`ad_wheel_info`5635 / `ad_wheel_spin`5636(空body) | CDP匯出 `ad`(進行中) | 轉即中無領獎步;只能轉**免費**次數(看廣告補 WS 用不到) |
| 掛機/離線獎勵 | `main_chapter`13:`reward_info`3333`{type}` / `claim`3334`{type}` | CDP匯出 `main_chapter`(進行中) | 離線(type2)=登入後 server **push**;在線(type1)主動拉;8h上限;無廣告加倍 |
| 跨界停車 | `car_park`50:`info`12801`{type,master_id,ceng}` / `cross_new_parking_start`12847`{park_id,mount_id,pos}` | CDP匯出 `car_park`(進行中);**space_list 空位欄位待釘** | 只停跨界(`type==3`);新/舊跨界用 checkNewCrossOpen 分;不收車 |
| 深淵之門 | `dungeon`:`battle_start`3591`{type,level}`→`battle_result`3592 或 `sweep`3596 | **已在 DUNGEON** | ⚠ **戰鬥客戶端算 + anti-cheat**(checkCheat 強制判敗、server 可能用 seed 回放);type 待 live(強候選 Coin=2);**優先試掃蕩繞過** |
| 週副本(萬神試煉) | `dungeon` type=**23**:battle_start/result 同上;門票 **gtid 1081**(7張/週) | **已在 DUNGEON** | ⚠ 同上 anti-cheat;優先試掃蕩;Mon-Sat 是 bot 排程非 server gate |
| 農場/打工 | home_farm 12(`info`3077/`plant`3078/`harvest`3081)+ 打工 `worker_setting`18689 + 豐收卡 `shop_buy`6914 | **已在 HOME/WORKER_COMMON/SHOP**(WORKER_COMMON cmd_ids 漏 18689-91 待補) | 打工=server 自動種+收(空 `seed_used_seq_list`=用免費種=不買種);需 config 值 live 取 |

## 待 live 釘的 config / 值(非 proto,看一次回包或 dump config)
深淵 type 值、農場 `seed_id`/`fertilizer_id`/`team_cfg_id`、豐收卡 `shop_type`/`shop_id`、轉盤獎品表。

## 兩大共通風險
1. **副本戰鬥 anti-cheat**(深淵/週副本):不能無腦送 `result=0` 騙勝 → 優先 `dungeon_sweep` 掃蕩,或 live 抓一包真實 `battle_result` 看 server 認不認 client result。
2. **看廣告加倍**(轉盤/掛機):WS 無廣告 SDK → 只拿免費/基礎份。

## kick / 異地登入(建構中)
被踢訊號 = **cmd 259**(0x103,body `{1:20}`)+ 隨後斷線。流程:偵測 → 30 分冷卻 → 下輪 online-check 再查在線 → 離線才恢復。

---

# 解耦跨裝置 online-check (S5b 落地, 2026-06-09)

**Branch**: `feat/ws-token-integration` (worktree `C:\Users\Eric\ws-token-integration`)
**Trigger**: 使用者要 checker 不再寫死 emulator-5554、requester 不限 5558。

## 目標
- request 帶 `target_pid`、不綁特定 checker；任一「在 checker 清單 + 空閒 + 好友含 target」的帳號可服務並回寫 mailbox。
- 向後相容：預設 config `online_check_checkers=["emulator-5554"]` → 現行行為零變化、5558 保護不變。
- checker 的 check_on_line 走 **protocol-only** (無 OCR)。

## Plan / Progress
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

# WS 後端整合進 bot — toggle / 5556 pilot / 5558 / 離線自動跑 / 神燈 (2026-06-09)

**Branch**: `feat/dragon-realm`（ws_token 工作都在這支）
**Trigger**: 使用者要「盡可能通通走 WS 取代 Playwright」降低本機運算;5556 試點、5558 調整、ADB 離線自動跑、WS 開神燈一次 20。
**前置**: ws_token 6 任務 + runner 已 live 驗證（見 `tasks/ws_token_backend_todo.md`）。接入點已由 code-explorer map（下）。

## 需求（使用者指定）
1. 用 WS runner 取代舊 Playwright/ADB pipeline,**per-device toggle 可切回舊邏輯**;盡量走 WS（省運算）。
2. **5556（菜雞）先試點**:排程換成 WS,其餘不變。
3. **5558**:(a) 保留 5554 跨裝置 `check_online`（一定要、不可省）;(b) 關掉自動停車。
4. **ADB 裝置（手機）離線時**:用快取 ticket **每 2 小時**自動跑一次 WS 任務。
5. **WS 開神燈**:`equip_box_open_all` 一次 **num=20**。

## 接入點（explorer 已驗,file:line）
- toggle/backend: `config_manager.py:629`（enum 白名單加 `"ws_token"`）、`:29-65`（`DEFAULT_DEVICE_CONFIG` 加 `use_ws_runner`/`ws_token_spend`）
- scanner 納入: `device_scan_service.py:116-120`（仿 `get_web_backend_devices` 收 ws_token 裝置）
- wake loop branch: `new_main_v2.py:325-336`（`daily_pipeline.run` 前依 backend/`use_ws_runner` branch 成 `run_device`）;純 token 不需 ADB/PW → `:122-160` 跳過 init
- 5558 check_online（**保留勿動**）: `web_session_service.py:89,127-128` / `new_main_v2.py:232-238` / `sleep_service.py:242-248`
- 5558 carpark off: `carpark_scheduler.py:31-34`（已 gate `backend=="web_h5"`+`carpark.enabled`;5558 目前無 carpark block=已 off,顯式設 false 保險）
- ADB 離線偵測: `device_scan_service.py:156-179`（`missing_now`）
- ticket: `ws_token/creds.py:83-116`（`load_creds`/`refresh_creds`→`adb_token_login.py`）

## ⚠ 風險 / 待拍板
- **R1（最大）手機離線自動跑 WS 會踢人**:WS `role_login` 踢同帳號 session。你若正在手機上玩那帳號,每 2h 的 WS 登入會把你踢出;純 WS 路徑**沒有真人在線偵測**能保護。→ 需決定處理方式。
- **R2 5556 沒 ticket、也沒連 adb**:要試點得先讓 5556 上線一次撈 ticket;或先用有 ticket 的 5554 試點。
- **R3 同帳號錯開**:5558 維持 web_h5 + 偶爾 WS 會互踢 → 必須錯開時段（`wake_hour_parity`/`wake_minute_offset`）。

## 分解（Codex task 6.3h stale/死了,改自己分解;隔離件現在做,live-bot 件等使用者過目）

**A. 隔離件（不碰 live bot,可現在做）**
- [~] S0 `ws_token/online_guard.py` 純 WS 在線檢查(好友 `0x0F02` last_login_ts==0 / guild `is_online`,**無 OCR**)+ 測試 — 建構中(subagent)
- [x] S6a 神燈 num=20:`ws_token/lamp.py:268` `batch_num=20` **預設已是 20,已滿足**
- [ ] S6b 神燈接進 runner（可選）
- [ ] guild_members_info(cmd 7440)若走 guild 法需在 `ws_token/guild.py` 補(online_guard 一併處理)

**B. Live-bot 件（動 new_main_v2/scanner/config/5558,依 CLAUDE.md「Working Style」規則先過目再動）**

## 步驟（核准後;複雜→用 subagents 隔離）
- [ ] S1 config_manager: `use_ws_runner` 旗標 + backend enum 白名單 + DEFAULT
- [ ] S2 wake loop branch: backend/`use_ws_runner` → `run_device`（跳過 ADB/PW init）
- [ ] S3 scanner 納入 ws_token 裝置
- [ ] S4 5558: 確認 `check_online` 完整保留 + carpark 顯式關
- [ ] S5 手機帳號「撈 token → 下線 → 純 WS 跑到 token 失效」(使用者 2026-06-09 定案流程)
  1. 啟動 / 手機 adb-reachable 時 → `tools/adb_token_login.py` 撈 fresh token(冷啟 App ~30s,趁手機在身邊時做)。
  2. token 到手 → 手機可下線;之後不碰手機。
  3. WS loop 每 2h:**先經解耦 online-check（S5b）查「大意了沒有閃」89565100511322 是否在線**(idle friend-account 走好友 0x0F02 / online_guard,**無 OCR**;在線→skip 不踢)→ 離線才 `run_device(phone, cached token)`。
  4. **跑到 token 失效**(`run_device` 回 `login_ok=False`)→ 停 WS loop,等手機下次 adb-reachable 再撈新 token。
  - online_guard 已 live 驗證(5554 友列 50 筆、target=大意了沒有閃、is_online False=離線),純 WS 路徑可用。
- [ ] S5b **解耦 online-check（使用者 2026-06-09 追加）**:現有 `check_on_line` 硬編「5554 當 checker」（`web_session_service.py:89` `if ip!="emulator-5554":return`、`wake_up_handler.py:239` `checker_ip='emulator-5554'`）→ 改成**動態選 checker**:requester（任何需要在線保護的裝置，如手機/5558）把「查某玩家是否在線」寫進既有 online-check mailbox（`bot_state.submit_online_check_request`）；**由任何「有空閒 + 好友列表含該玩家」的帳號**被喚醒去查（protocol 好友列表 0x0F02 / guild is_online，**無 OCR**），結果 `complete_online_check_request` 回寫。checker 不再鎖 5554。需保留現有 mailbox/中斷喚醒機制，只改「誰當 checker」的選擇邏輯 + requester 不限 5558。
  - 待確認：哪些帳號互為好友（誰能查 89565100511322）、「有空閒」的判定（非任務中/睡眠中可被喚醒）。
- [ ] S6 `ws_token/lamp.py`: num=20 per batch + 接進 runner
- [ ] S7 5556 pilot live 驗證（撈 ticket → 跑一輪）
- [ ] 改 `new_main_v2`/`device_wrapper` 後**需重啟 bot** 才生效;不破壞既有 6 裝置

---

# 降低本機運算量 / GPU (2026-05-31)

**Branch**: `perf/reduce-gpu-usage`
**Trigger**: 「降低 GPU 使用量 / 降低運算」
**用戶設定**: 目標=**本機**(跑 bot+Chrome+挖礦);取捨=**零風險**;重點=**降低運算要求**(不只 GPU)

## 4 路平行分析結論(都讀碼驗證)

本機 GPU/運算三大源:
1. **Chrome WebGL**(最大本機 GPU)— 5 台 web_h5 有 4 台 headful、**完全無 `--disable-gpu`**,Cocos 最高 ~60fps,最多 5 並行。`web_stop_mode=close_browser` → 已是 bursty 非 24h。
2. **挖礦 CNN**(`miner/models/classifier.py`)— torch cu126 自動上 GPU;模型極小;**每次 classify 跑 42 次 batch=1 forward**(可數學等價批次化)。executor 每挖一格再 re-classify 3~5 次。
3. **OCR**(`ocr_server.py`)— PP-OCRv5_server 吃 GPU,但 **bot 預設連遠端 100.64.0.5**,GPU 負載不在本機 → **本任務排除**。

## Phase 1 — 零風險、可削減本機運算(直接做,測試保證等價)

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

## Phase 2 — 需 live 驗證才安全(列出,等拍板;不在零風險範圍)

- [ ] **2.1** Chrome `--disable-gpu`(SwiftShader)或 4 台 headful 改 headless。**本機最大 GPU 削減**,但需 live 驗證 Cocos 畫面 + OCR 仍正確。`device_wrapper.py:604-621`、`bot_config.json` web_headless。
- [ ] **2.2** Cocos `cc.game.setFrameRate` 限 FPS。直接砍 WebGL draw,但該 Cocos build 是否支援未驗證。
- [ ] **2.3** `get_stage` 3 次 OCR round-trip 併成 1(全螢幕先 OCR、再比對 公告/車位倉庫 子字串)。`game_state/detector.py:114-141`。最大 OCR-call 削減但需驗準確度。
- [ ] **2.4** `cv2.matchTemplate` 前先 2× 縮圖。`img_tools.py:416`、`park.py` 多處。需重調 0.8 門檻。

## 明確不碰(陷阱)

- **per-action WS drain**(`device_wrapper.py` _collect_ws_frames)— 餵養**神燈封包偵測**(0x0504 掉落),production-critical,**不可關**(agent 誤判為純 RE,記憶庫證實封包為主)。
- **OCR server 改 CPU/mobile** — 在遠端 100.64.0.5,非本機目標。

---

# 開神燈 V2 重構 — 修三狀態 + 廢 V1 + 預設全 V2 + OCR 驗 ADB (2026-05-29)

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

# 專案統整 / 重構 計畫

**Date**: 2026-05-19
**Trigger**: `/goal 檢查程式碼複雜度 把需要的融合起來 功能不同的切分開來 統整整個專案`
**Status**: Phase 1 COMPLETE. Phase 2/3/4 pending re-prioritization.

---

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

---

## Phase 0 — Inventory & safety net (no deletions yet)

- [ ] **0.1** Stop the bot if running (rotated logs / atomic writes assume single writer)
- [ ] **0.2** `git status` clean, commit current state on branch `chore/consolidation-2026-05-19`
- [ ] **0.3** Tag baseline `pre-consolidation-2026-05-19` (rescue point)
- [ ] **0.4** Run `pytest` to record green baseline; record count + duration in this file
- [ ] **0.5** Decide on `farm_v2/` and `miner/v2/` (see "Decisions needed" below) before Phase 1

**Estimated impact**: 0 code changes. ~10 min.

---

## Phase 1 — Cleanup-only (low-risk, reversible by git revert)

All items below were verified by audit to have **zero imports** in production code (`new_main_v2.py`, `runtime_services/`, `game_actions/`, `control_panel_app.py`, `device_wrapper.py`).

### 1A. Delete root-level scratch / sync-conflict artifacts
- [x] **1A.1** All `*.sync-conflict-*` files at repo root (25 deleted) + tests/ (43 deleted) — total 68 files
  - REFACTOR_ROADMAP draft folded into Phase 2 of this file before deletion
  - Files were gitignored (`*.sync-conflict-*` rule), so no commit needed — physical cleanup only
  - **Verified**: pytest 392 pass / 8 skip / 15.02s — identical to baseline (was 43 collection errors before)
- [ ] **1A.2** `Untitled-*.py`, `Untitled-*.ipynb` (7 files)
- [ ] **1A.3** `#config set.py` (0 bytes)
- [ ] **1A.4** `.tmp_head_control_panel_app.py` (29KB orphan partial)

### 1B. Delete throwaway directories — DONE (commit 6d61ec47)
- [x] **1B.1-8** All done. Writer in utils/ws_listener.py migrated to
  `logs/_archive/ws_capture/auto/`; argparse defaults in
  build_equipment_cache.py and verify_lamp_via_playwright.py updated;
  10 valuable docs/sources preserved via `git mv` to
  `docs/protocol/` and `docs/game_client_sources/`; ~24 000 files
  removed including tmp_ws_capture/, tmp_crops/, tmp_flow_imgs/,
  tmp_lamp_verify/, tmp_rl_test/, trash/, 2026-01-20 195013/,
  新增資料夾/. Tests: 392 pass / 8 skip.

### 1C. Delete empty / aborted refactor scaffolding — partial DONE (commit f57ea8da)
- [x] **1C.1** `partner/` deleted (empty); `mission/*.png` stale PNGs removed (writer commented out)
- [~] **1C.1 KEEP**: `find_img/`, `reward_get/`, `dataset/` — audit was wrong; these have live runtime writers (img_tools.py:413, reward_manager.py:27, config/paths.py)
- [x] **1C.2** `core/` deleted (8 zero-byte sync-conflict files only)
- [x] **1C.3** `refactor/` deleted (`git rm -r`, 17 files; 2026-04 aborted scaffold, audit confirmed zero imports)
- [x] **1C.4** pytest: 392 pass / 8 skip — same as baseline

### 1D. Delete dead top-level Python files (verified 0 imports) — DONE in commit 9becba70
- [x] **1D.1** `battle.py` (69L) — superseded by `new_battle.BattleManager`
- [x] **1D.2** `fight_car_task.py` (229L) — orphan experimental; `fight_car.py` stays
- [x] **1D.3** `park_test.py` (657L) — **NOT a test**; legacy duplicate of `park.py`
- [x] **1D.4** `detect_parking_p.py` (86L) — orphan blue-P detector
- [x] **1D.5** `Open_gold.py` (296L) — zero callers; legacy `easyocr` reader
- [x] **1A.2-4** also folded into commit 9becba70 (7 Untitled-*, #config set.py, .tmp_head_control_panel_app.py)

**Commit**: `9becba70 chore(cleanup): remove dead scratch files and superseded modules`
**Tests**: 392 pass / 8 skip — identical to baseline

**Estimated impact**: ~3 200 LOC + ~24 000 binary files removed. No behavior change. Bot start/stop should be identical.

---

## Phase 2 — Threading & dedup fixes (mostly DONE per recent commits)

**Already landed** (verified via `git log`):
- ✅ `bot_state.request_force_sleep()` lock fix — commit 6c07ab96
- ✅ `bot_state.check_pause()` TOCTOU fix — commit 4d7d8893
- ✅ `json_manager._atomic_write_json()` — commit a8817e06
- ✅ `push_project` subscription lock — commit 2c707e99
- ✅ `navigate_to_main_page()` shared utility — commit a9fbb149 + delegations in farm (906326b8), farm_v2 (01477b50), miner_action (a85693a4)
- ✅ `should_purchase` extracted to `game_actions.shop_manager` — commit 55492348
- ✅ `DeviceConfig` dataclass — commit 01e1e3b0
- ✅ `device_wrapper` 3 silent excepts logged — commit d9d81236

**Remaining**:
- [ ] **2C.3** Convert 7 bare `except:` in `device_wrapper.py` to `except Exception as e: logger.warning(...)`
- [ ] **2C.4** Replace remaining ~25 silent `pass` blocks in `device_wrapper.py` with warning logs (3 done, ~25 to go)
- [ ] **2B.2** Extract `poll_stage(d, target, timeout)` — replaces 4+ stage-poll copies (not done yet — search for `current_stage ==` polling loops)
- [ ] **2B.3** `clear_offline_devices()`: merge two-stage lock window in `bot_state.py` (verify if still applicable post-4d7d8893)

**Estimated impact**: ~100 LOC delta. Targeted tests required. Bot logs become noisier — acceptable.

---

## Phase 3 — God-module splits (higher risk; one PR per module)

Each split is **rename + move only** — preserve every public symbol via re-exports from the old module path so existing imports keep working. After one stable release cycle, remove the re-exports.

### 3A. `json_manager.py` (732L → 4–5 modules)
- [ ] **3A.1** Extract base `JsonDataManager` + atomic write → `json_manager/base.py`
- [ ] **3A.2** Extract `_ts_same_day/week`, `_parse_recorded_date`, `should_execute_*` → `json_manager/time_tracking.py`
- [ ] **3A.3** Extract `ParkMarketDataManager` → `json_manager/park.py`
- [ ] **3A.4** Extract `FamilyMarketDataManager` → `json_manager/family.py`
- [ ] **3A.5** Extract `StoreDataManager`, `TimeRecordDataManager` → `json_manager/store.py`, `json_manager/time_record.py`
- [ ] **3A.6** Make old `json_manager.py` a thin `from json_manager.* import *` shim
- [ ] **3A.7** Consolidate `should_execute_cycle` and `should_execute_cycle_from_record` into one parameterised function

### 3B. `new_battle.py` (1001L → 4 modules under `battle/`)
- [ ] **3B.1** Extract `BattleManager` class (L231-443) → `battle/manager.py`
- [ ] **3B.2** Extract biweekly bounty road logic (L137-230, slot key helpers) → `battle/biweekly_dungeon.py`
- [ ] **3B.3** Extract weekly cloud + friend-help (L445-790) → `battle/weekly.py`
- [ ] **3B.4** Extract hell_door + snow country (L891+) → `battle/special.py`
- [ ] **3B.5** Make `new_battle.py` a re-export shim (or rename callers to `from battle import …`)
- [ ] **3B.6** Address `BattleManager.capture_screenshot()` (hard-coded 9-pixel colour check) — extract to named constants

### 3C. `control_panel_app.py` (1473L → routes + workers + brokers)
- [ ] **3C.1** Extract all `@app.route` handlers → `control_panel/routes.py`
- [ ] **3C.2** Extract `_run_web_login_worker` (240L, deepest nesting) → `control_panel/web_login_worker.py` with a `WebLoginConfig` dataclass for the 30-param unpack
- [ ] **3C.3** Extract `_run_labeler_once_worker` + `_run_trainer_worker` → `control_panel/subprocess_workers.py`
- [ ] **3C.4** Extract `queue_command` + `_push_to_worker_webhook` + state → `control_panel/device_command_broker.py`
- [ ] **3C.5** Extract `check_ocr_server` → `control_panel/ocr_health.py`
- [ ] **3C.6** Old `control_panel_app.py` becomes a thin `app = Flask(...)` + blueprint registration

### 3D. `device_wrapper.py` (1134L → 4 modules)
- [ ] **3D.1** Extract `PlaywrightContextConfig` + `PlaywrightContextAdapter` (L44-147) → `device/playwright_context.py`
- [ ] **3D.2** Extract `MonitoredDevice` (L148-476) → `device/monitored.py`
- [ ] **3D.3** Extract `PlaywrightGameDevice` (L489-1174) → `device/playwright_game.py`
- [ ] **3D.4** Extract trace/WS frame plumbing → `device/action_tracing.py`
- [ ] **3D.5** Keep `device_wrapper.py` as a re-export shim
- [ ] Note: `_WEB_DEVICE_LOCK` must stay an `RLock` (CLAUDE.md L?? — re-entrant requirement)

### 3E. `new_main_v2.py` (1086L → coordinator + 4 modules)
- [ ] **3E.1** Extract `initialize_runtime_device` + backend selection → `main_loop/device_init.py`
- [ ] **3E.2** Extract sleep cycle logic (L202-397) → `main_loop/sleep_scheduler.py`
- [ ] **3E.3** Extract `_run_daily_tasks` (248L, 20 task blocks) → `main_loop/task_orchestrator.py`; consider a registry/list-of-tasks pattern over the giant if-chain
- [ ] **3E.4** Extract `save_error_screenshot`, `log_main_page_mismatch` → `main_loop/error_logging.py`
- [ ] **3E.5** Reduce `main()` (L820-1119, 300L) to thin coordinator

**Estimated impact**: ~6 000 LOC moved across files. **High** PR review burden — propose one module per PR + run pytest + smoke run after each.

---

## Phase 4 — Lamp V1 retirement (gated on V2 adoption)

- [ ] **4.1** Flip `use_opengold_v2 = true` for the two remaining devices (`use_phone_ocr_lamp_mode` device + `emulator-5560`)
- [ ] **4.2** Port `is_compare=False` path to `opengold_v2.LampService` if missing
- [ ] **4.3** Soak test ≥1 week on V2 across all 6 devices
- [ ] **4.4** Remove V1 branch from `game_actions/lamp_scheduler.py:32-33` and `_run_lamp` in `new_main_v2.py:290-297`
- [ ] **4.5** Delete `Open_gold_paddle_ocr.py` (1239L)
- [ ] **4.6** Update CLAUDE.md OCR section ("Open_gold_paddle_ocr.py 已改用 img_tools 共用 fallback") to reflect retirement

**Gate**: must NOT be started until 4.3 passes.

---

## Decisions made (2026-05-19)

1. ✅ **`farm_v2/`** → wired in, `farm/` retired. Commit `c1f01d8e`. Renamed `run_farm` → `farm` to match call-site signature. Test stub updated. Tests 392 pass / 8 skip.
2. ✅ **`miner/v2/`** → keep (flag-gated experimental).
3. ✅ **`miner_test/`** → delete (research sandbox, not production).
4. ✅ **`tmp_ws_capture/`** → migrate writer to `logs/_archive/ws_capture/` (per `LogPaths`) then `rm -rf tmp_ws_capture/`. Same for any other writers (`utils/ws_listener.py`, `utils/web_game_api.py`, `tools/build_equipment_cache.py`, `device_wrapper.py`).
5. ✅ **Branch**: single PR for Phase 0 + Phase 1 + remaining Phase 2; splits (Phase 3) one PR per module; lamp V2 retirement (Phase 4) separate later.

## Pre-flight findings (2026-05-19 audit)

- Git is dirty with 2 uncommitted intentional changes (web_h5 init interruptible backoff + 5560 V2→V1 revert) — those stay untouched on the cleanup branch.
- Recent `git log` (last 30 commits) shows the user is already 1–2 weeks into this refactor — see Phase 2 "Already landed" list. **My job is to extend that work, not duplicate it.**
- **Infra blocker (out of scope, flag to user)**: Syncthing has been syncing `.git/` itself across machines, producing 1 051 sync-conflict files inside `.git/objects/`. Doesn't break git operation but is the **root cause** of the source-tree sync-conflicts. Recommend adding `.git/**` to Syncthing's per-folder ignore patterns and then `find .git/objects -name '*.sync-conflict-*' -delete`. Worktrees probably have the same issue.

---

## Review (after execution)

_Filled in as phases land. Each phase ends with: what changed, what tests proved it, regressions found._

### Phase 0
- [ ]

### Phase 1 — DONE 2026-05-19

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

### Phase 2 — pending
- [ ] device_wrapper.py ~25 remaining silent `pass` blocks → warning log
- [ ] device_wrapper.py 7 bare `except:` → typed catches with log
- [ ] Extract `poll_stage(d, target, timeout)` shared helper
- [ ] `bot_state.clear_offline_devices()` two-stage lock merge (verify still applicable post 4d7d8893)

### Phase 3 — 2/5 done, 3 deferred
- [x] json_manager.py 878L → `json_manager/` package (7 files) — commit `8c12cac0`
- [x] new_battle.py 1093L → `battle/` package (7 files) + shim — commit `9b56f620`
- [~] control_panel_app.py 1722L — **deferred**. Flask app with 3 worker-thread state dicts (_web_login_state, _labeler_state, _trainer_state) and only 3 indirect tests. Reasonable next step: extract `_run_web_login_worker` (L517-758, 241L) into `control_panel/web_login_worker.py` in its own focused PR, paired with new unit tests for the worker's pause/resume/backup paths. Routes+broker stay in main file until coverage exists.
- [~] device_wrapper.py 1134L — **deferred**. Playwright lifecycle module just touched by Phase 2 (`0da9b9d3`); needs to stay stable while runtime soaks the new logging. Plus `_WEB_DEVICE_LOCK` RLock invariant (CLAUDE.md) means any restructure risks reentrancy bugs. Revisit after a week of green production runs.
- [~] new_main_v2.py 1086L — **deferred**. Splitting collides with the unstaged WIP web_h5-init interruptible backoff at L869. Land that first, then revisit `_run_daily_tasks` (248L) extraction into a task registry.

### Phase 4 — pending
- [ ] Flip use_opengold_v2=true for remaining 2 devices
- [ ] Port phone-OCR + 5560 paths to opengold_v2 if missing
- [ ] Soak-test 1 week
- [ ] Remove V1 branch from lamp_scheduler + new_main_v2
- [ ] Delete Open_gold_paddle_ocr.py (1239L)

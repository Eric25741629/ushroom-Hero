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

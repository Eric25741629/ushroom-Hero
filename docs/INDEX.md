# 菇勇者全自動掛機 — 主導覽 (INDEX)

> 多裝置 H5 遊戲自動化 bot 的程式碼地圖：一頁找到 hooks、可復用工具、子系統、文件與分析筆記。雙後端 `adb` (uiautomator2) / `web_h5` (Playwright)，一裝置一 thread。
> 更新日期：**2026-05-31**（部分 drift 已於 2026-06-21 修正；最新後端架構審計見 [BACKEND_ARCH_AUDIT_2026-06-21.md](BACKEND_ARCH_AUDIT_2026-06-21.md)）

---

## 1. 一句話導覽

掃 ADB / web_h5 裝置 → 每裝置開一條 thread → 喚醒解鎖 → 確認在主頁 → 跑 20 項每日任務流程 → 對齊整點窗休眠。Master 跑中控儀表板與 worker 協調，Worker 回報狀態。辨識走 OCR (多 server fallback) + CNN 分頁，挖礦走 A*/DFS planner。

---

## 2. 快速查找表（我想做 X → 看這裡）

| 我想做… | 看這裡 |
|---|---|
| 改任務流程（順序 / 新增每日任務 / 主頁守衛） | `game_actions/daily_pipeline.py`（單一任務排序真相）+ `game_actions/stage_guard.py`（`_run_at_main_page` / `get_stage_with_check`） |
| 改辨識 / OCR | `img_tools.py`（核心 OCR 管線 + 多 server fallback + circuit breaker）；本地推理權重 `OCR_model/`；訓練/廠商源 `OCR/`（離線）；分析見 [../OPTIMIZE_ocr_system.md](../OPTIMIZE_ocr_system.md) |
| 改挖礦 | `miner/`（orchestrator `miner/mining_service.py`）；planner 預設 **v1**（A* whole-board，`miner/planning/`），可切 v3/v4（v2 已移除 2026-06-05、v5 已移除 2026-06-18；WS 挖礦路徑 2026-07-05 起走 v1 `plan_smart`）；機制真相 `miner/core/mechanics.py`；入口任務 `game_actions/miner_action.py`；分析+礦物出現率校正 [protocol 外的 `../docs/MINING_ALGORITHM_ANALYSIS.md`](MINING_ALGORITHM_ANALYSIS.md) |
| 改神燈（開裝備） | `opengold_v2/`（`lamp_service.py` 的 `LampService` 為唯一 live 實作）；排程 `game_actions/lamp_scheduler.py`；V1 `Open_gold_paddle_ocr.py` 已廢棄 |
| 改農場（打工） | `farm_v2/`（`manager.py` + `operations/`；舊 `states.py` 狀態機 2026-07-05 移除）；H5 變體 `farm_v2/web_farm.py` |
| 改航海（Sea） | web_h5 走 `sea_v2/`（flag `use_sea_v2` 預設 OFF）；adb 走 `Sea.py`；路由在 `game_actions/daily_pipeline.py` 的 `_sea_dispatch` |
| 改車位（Carpark） | web_h5 cocos：`game_actions/carpark_scheduler.py` → `utils/carpark_auto.py` / `utils/carpark_state.py`；ADB 雛形 `utils/carpark_adb.py`；legacy ADB（已停用）`park.py` / `new_park.py` / `fight_car.py` |
| 中控儀表板 | `control_panel_app.py`（Flask :5002，master）；獨立靜態服務 `app.py`（:5000）；推播 `push_project/` |
| 多裝置排程喚醒（分流） | `runtime_services/sleep_service.py`（`calc_aligned_wake_ts` 對齊整點 + 偶/奇數小時 parity 分流）+ `runtime_services/startup_sleep.py`（`compute_stagger_order` 啟動錯峰） |
| 裝置喚醒 / 解鎖 | `utils/wake_up_handler.py`（`handle_device_wakeup`：skip-list / blackout / 5558↔5554 互檢 / 電量螢幕）；ADB 解鎖 `adb_operations.py`（`unlock_screen`） |
| log 路徑 | `utils/log_paths.py`（`LogPaths` 唯一真相，`with_root()` 沙箱）+ `utils/logging_utils.py`（per-device logger 工廠 + SMB-safe rotation） |
| 截圖辨識共用 | `utils/smart_screenshot.py`（`SmartScreenshotRecorder.capture`）+ `utils/screenshot_helpers.py`（`save_error_screenshot` / `log_main_page_mismatch`） |
| 協議反推（WS / protobuf） | `utils/ws_listener.py`（frame 擷取 + 自動落盤）+ `utils/web_game_api.py`（`call_raw` RPC + `_walk_pb` protobuf walker）；schema 文件見 [docs/protocol](#協議反推-docsprotocol) |

---

## 3. Hooks（`.claude/hooks`）

> ⚠️ Hooks 為**本機限定**，`.claude/` 已 gitignore（不進版控）。註冊在 `.claude/settings.json`，皆用 conda `mushroom1` 的 `python.exe` 執行。

| 檔案 | 觸發時機 | 作用 | 註冊位置 |
|---|---|---|---|
| `.claude/hooks/py_check.py` | PostToolUse（matcher: `Edit\|Write\|MultiEdit`） | 對被編輯的 `.py` 做 `py_compile` 語法把關（語法錯誤 exit 2 阻擋），外加非阻塞 ruff 報告（report-only, exit 0）。明確 UTF-8 解碼 stdin / 編碼 stderr 以容忍中文路徑 | `.claude/settings.json` |
| `.claude/hooks/check_pytest.py` | PreToolUse（matcher: `Bash`） | 擋裸 `pytest`（無 file/`::`/path/`-k` target），因為全量 pytest 會 import 真實 device/Playwright/OCR/cv2 依賴而 hang（exit 2）。略過 pip 相關指令、會拆解複合指令 | `.claude/settings.json` |

---

## 4. 可復用工具（`utils/`）

> 用這些而不要重造輪子。完整盤點（30 個模組）含 scratch/deprecated 見子系統地圖與 [../OPTIMIZE_utilities.md](../OPTIMIZE_utilities.md)。

### 4.1 高復用價值（active）

| 名稱 | 路徑 | 用途 |
|---|---|---|
| `setup_logger_for_device` / `logger` (LoggerProxy) | `utils/logging_utils.py` | 標準 per-device logger（`logs/<device>/main.log` + console，SMB-safe rotation）。新 thread 必配 + `set_thread_logger()`；~20 個 production 模組已用 |
| `LogPaths` | `utils/log_paths.py` | 計算任何 log artifact 路徑的唯一正解（勿硬編 `logs/<device>/...`）；`with_root()` 給測試沙箱 |
| `SmartScreenshotRecorder.capture` | `utils/smart_screenshot.py` | 「出事抓圖」標準：寫 JPG（Windows 非 ASCII 路徑安全）+ events.jsonl + annotations.json + action-trace |
| `save_error_screenshot` / `log_main_page_mismatch` | `utils/screenshot_helpers.py` | 上者的薄封裝，附帶 daily flow 慣用的 bot_state/logger 副作用；日常任務優先用這層 |
| `ActionTraceRecorder.log` | `utils/action_tracker.py` | append-only 結構化事件 log（自動 caller-frame 歸因 + 裝置 context）；smart_screenshot 與 ws action mapping 的底層 |
| `pause_guard.bind/check/unbind` | `utils/pause_guard.py` | **強制**：任何直接驅動 Playwright page（繞過 device_wrapper）的任務都要 bind() 進場、每個 click 前 check()、finally unbind()，否則 live-view 手動接管無法中斷 |
| `inference_slot` / `configure_torch_runtime` | `utils/torch_runtime.py` | 共用 CNN forward 必須包 `with inference_slot():` 序列化跨 thread GPU；startup 一次性 cap intra-op threads（分流運算） |
| `ensure_local_model` | `utils/model_sync.py` | NAS/SMB 權重複製到本地 SSD cache（MD5 版本化 + atomic rename）；`torch.load` 前先呼叫 |
| `handle_device_wakeup` | `utils/wake_up_handler.py` | 每裝置喚醒/解鎖/同步：skip-list + blackout-hour gating、5558↔5554 互檢、電量螢幕、啟動錯峰（adb） |
| `WebGameAPI.call_raw` / `is_in_game` | `utils/web_game_api.py` | web_h5 後端 RPC 基元：對 live WS 送任意 game cmd 拿解密 body，外加 WS/game-readiness 判定 |
| `_walk_pb` / `decode_equip_template` | `utils/web_game_api.py` | 最小 protobuf wire walker + 裝備 template 解碼；解析 game WS body 的正典所在（redpack_detector 有重複版，待整併） |
| `WSFrameTracker` | `utils/ws_listener.py` | per-device WS frame 擷取（device_wrapper 為每個 web_h5 裝置實例化）；高價值 cmd body 自動落盤供協議分析 |
| `PageDetector` / `try_detect_main_page_fast` | `utils/page_detector.py` | cocos-first / OCR-fallback 頁面辨識 + 共用 `PageState` enum；新 web_h5 導航/狀態機的建議基石（目前 flag-gated experimental） |
| `MuMuController` / `HangDetector` / `EmulatorRecoveryOrchestrator` | `utils/mumu_control.py` + `utils/emulator_watchdog.py` + `utils/emulator_recovery.py` | ADB 模擬器自癒堆疊：偵測 hang（heartbeat+frozen-frame+adb strikes）→ throttle+restart via MuMuManager.exe → 驗 health。純邏輯、已單元測試 |

### 4.2 中等復用價值（active，多為 web_h5）

| 名稱 | 路徑 | 用途 |
|---|---|---|
| `EquipmentCache` | `utils/equipment_cache.py` | UID→裝備詞條查表，由 0x0504 神燈掉落 frame 被動建立，persist 到 `cache/equipment_cache.json` |
| `lamp_drop_watch` | `utils/lamp_drop_watch.py` | 純邏輯偵測「想要品質」(rarity≥11 永恆) 掉落，取代慢的 count-stall timeout |
| `redpack_detector` | `utils/redpack_detector.py` | 主頁紅包偵測+領取：RedPoint cocos gate → 0x2605 列表 → 0x2603 領取 → 0x0201 錯誤 |
| `try_cocos_navigate` | `utils/cocos_navigator.py` | flag-aware 導航入口（`node.emit('click')` 取代座標點），回 None=fallback / True=完成 / False=已試 fallback（experimental） |
| `car_fight_utils.adjust_wake_time_for_cars` | `utils/car_fight_utils.py` | 讀 car_park.json 戰鬥時間，把喚醒挪離 ±10 分戰鬥窗 |
| carpark 讀取 / 自動化 | `utils/carpark_auto.py`（park/unpark/reconcile，僅 SILVER 跨服完成）、`utils/carpark_state.py`（唯讀 cocos 場景樹）、`utils/carpark_tracker.py`（雛形 diff 偵測踢出） | web_h5 車位子系統；analysis 見 [protocol/CARPARK_AUTOMATION.md](protocol/CARPARK_AUTOMATION.md) |

### 4.3 低 / 棄用 / scratch（避免新依賴）

| 名稱 | 路徑 | 狀態 |
|---|---|---|
| `family_lieyan.claim_lieyan_daily` | `utils/family_lieyan.py` | active（低復用，烈焰山洞每日寶箱） |
| `carpark_adb` / `carpark_click_recorder` | `utils/carpark_adb.py` / `utils/carpark_click_recorder.py` | experimental 雛形 / 點擊錄製工具（taps gated `_CALIBRATED=False`） |
| `ocr_clicker.click_str` | `utils/ocr_clicker.py` | **deprecated** shim（零 importer；全 repo 直接呼叫 `img_tools.click_str_by_server`） |
| `model_loader` / `screen_recovery` / `ui_layouts` | `utils/model_loader.py` / `utils/screen_recovery.py` / `utils/ui_layouts.py` | **scratch**（無 production importer；screen_recovery 用 `print()`、imwrite 被註解） |

---

## 5. 子系統地圖

| 子系統 | 路徑 | 用途 | 狀態 |
|---|---|---|---|
| 挖礦引擎 | `miner/` | screenshot → CNN 分類 → plan → execute；orchestrator `mining_service.py`，入口 `game_actions/miner_action.py` | **runtime** |
| 挖礦 v2 套件 | `miner/v2/` | **plan_v2 演算法已移除 2026-06-05**（真實 board 18.8% 破 0.3s）；保留 `BoardClassifierV2` + `service`/`types`/`visualization` 為 v3/v4 共用 CNN 分類層 | **runtime（共用層，非 planner）** |
| 挖礦 planner v3 | `miner/v3/` | cluster-aware 動作模型（clusters/actions/board）；v4 直接 reuse `v3.actions`；有 230ms wall-clock deadline | **runtime**（可切，v4 基礎層） |
| 挖礦 planner v4 | `miner/v4/` | **現行預設**：bounded 3-step rolling-horizon DFS + branch-and-bound（250ms deadline）；reuse `core.mechanics` + `v3.actions` | **runtime（預設）** `mining_planner_version='v4'` |
| 挖礦 RL | `miner/rl/` | SB3 PPO / replay；logging/訓練用，不在 live 決策路徑 | logging-only |
| 農場 | `farm_v2/` | 打工種/收狀態機（`manager.farm`）；`operations/base.py` 的 `click_with_jitter`/`wait_jitter` 通用 | **runtime**（雙後端：manager.py adb / web_farm.py web_h5） |
| 神燈 | `opengold_v2/` | 8 模組重構，`LampService` 編排 OCR→技能評估→比較/賣/裝；自動偵測連閃裝備 | **runtime（唯一 live 路徑）**；V1 `Open_gold_paddle_ocr.py` 已廢 |
| 航海 | `sea_v2/` | H5-first 確定性導航；`session.py` IO 邊界、`tiles/navigator` 純決策、`tasks.py` pipeline | **experimental**（flag `use_sea_v2` 預設 OFF；adb 階段 C 直接 abort） |
| 任務沙箱 | `task_sandbox/` | 通用任務開發/驗證框架（`TaskSpec` + `Schedule` + `NavTarget` + ActionTrace），首個實作為神燈 | **experimental**（dev/驗證框架，未接主迴圈；live 神燈仍走 lamp_scheduler→LampService） |
| 階段偵測 | `game_state/detector.py` | 螢幕+OCR → 命名階段（主頁面/異地登錄/車位倉庫/家族戰）；全 runtime 共用 | **runtime**（⚠️ `new_stage_check` 用 `if [list]:` 恆為 True，疑似潛在 bug） |
| JSON 狀態持久化 | `json_manager/` | atomic-write 各 manager（park/family/time/store）+ 週期/冷卻排程 predicate（`scheduling.py`） | **runtime** |
| CNN 模型 | `new_cnn/` | `SimpleCNN` 10 類分頁；權重經 `model_sync` 同步 SSD | **runtime**（`stage_cnn.py` 自標「即將廢棄」） |
| OCR 推理權重 | `OCR_model/` | Paddle inference artifacts（含 v2/ det_v2/），img_tools 本地 fallback 用 | **runtime**（二進位/config，無 Python） |
| OCR 廠商源 | `OCR/` | vendored PaddleOCR 全樹 + 訓練 snippet | **deprecated/external**（離線訓練用，不在 import graph） |
| Playwright auth | `find_img/` | per-device 登入 session（cookies/localStorage） | **runtime**（⚠️ `emulator-5558.json` 含 live Google OAuth/帳密；應 gitignore，外洩須輪換） |
| 推播 mini-app | `push_project/` | 獨立 Flask web-push + PWA；中控啟動時 lazy 拉起 | **runtime（獨立）**（⚠️ `server/.env` VAPID 私鑰 + subscriptions.json secret 面） |
| 執行期服務 | `runtime_services/` | lazy-start 服務 + 主迴圈構件（見下節） | **runtime** |
| 戰鬥任務 | `battle/`（`new_battle.py` 為 compat shim） | 每日/每週/雙週副本（hell_door/cloud/biweekly/weekly_trials/store/manager） | **runtime** |
| 中控/協調 | `control_panel_app.py`(149L façade) + `control_panel/`（blueprint:`routes_*` + `shared/`）/ `worker_webhook_api.py` / `runtime_services/worker_sync_service.py` | master 儀表板 + worker 回報/命令 | **runtime**（blueprint 拆分已完成 2026-06-11） |
| 儀表板登入/總後台 | `control_panel/shared/auth.py`（before_request 守門 + 豁免清單 + 可見性 helper）+ `routes_auth.py`（/login /apply /logout）+ `routes_admin.py`（/admin + /api/admin/*）+ `utils/dashboard_settings.py`（gitignored `dashboard_settings.json`） | 全站登入制 + 帳號審核 + 裝置可見性 + host_role 覆寫（重啟生效） | **runtime**（飛寵登入已整併至 /login） |

### orchestration / runtime_services 細部

| 模組 | 職責 |
|---|---|
| `new_main_v2.py` | 主入口 + per-device 主迴圈 `main(ip,...)`；`__main__` 啟動服務+30s ADB scan loop（⚠️ 與 `bootstrap/api_services.py` 重複，後者已抽出但未接線；`main.py` 為空 stub） |
| `runtime_services/device_scan_service.py` | ADB+web_h5 掃描、master/worker 過濾、per-device thread 生死（唯一 spawn 點，`running_threads_lock` 在 `thread_registry.py`） |
| `runtime_services/sleep_service.py` | `calc_aligned_wake_ts`（整點 00–20 窗 + parity 分流 + 每裝置 offset）、`run_sleep_cycle`、5556 雙週副本 19:57 override |
| `runtime_services/startup_sleep.py` | `compute_stagger_order` 啟動錯峰（與 sleep_service 同 parity/offset 語意，parser 有小重複） |
| `runtime_services/device_runtime_service.py` | `ForceSleepRequested`/`WakeLoopInterrupted` 例外型別 + `sleep_until_wake_or_interrupt`（可中斷等待）+ connect 失敗處理 |
| `runtime_services/web_session_service.py` | web_h5 session 生命週期 + 5554/5558 互檢握手 + 手動接管流程 |
| `runtime_services/live_view_bridge.py` | 無頭 Chrome raw-CDP screencast + 輸入轉發（不依賴 Playwright，thread-affinity 安全），中控 `/ws/live_view` 用 |
| `runtime_services/worker_sync_service.py` | worker→master 狀態推送 + 命令拉取（含 CA-verify-skip-hostname TLS adapter 解 wildcard cert） |
| `runtime_services/push_server_service.py` | idempotent 拉起 push_project（`is_tcp_port_open` 通用 port probe） |
| `bot_state.py` | thread-safe 狀態 registry + 跨 thread 信號信箱（pause/force_sleep/skip_sleep/manual_release/web_close/web_launch/online-check）；`is_local_device` 是正確路由 key（勿用 `':' in ip`） |
| `device_wrapper.py`(1359L) | `MonitoredDevice` 後端無關抽象 + `PlaywrightGameDevice`；`_WEB_DEVICE_LOCK` 必為 RLock（close() finally 重入） |
| `config_manager.py` | bot_config.json 載入/持久化（RLock、host override、UTF-8-BOM 容忍、self-heal）；`_to_*/_clamp_*/_enum_str` 為可復用 validator |
| `worker_webhook_api.py` | worker webhook 接收（:5003）+ `apply_remote_commands` 命令分派表（與 control_panel `queue_command` local 分支須同步） |

---

## 6. 文件總覽

### 核心總覽
| 文件 | 一句話 |
|---|---|
| [../README.md](../README.md) | 雙後端摘要 + 入口連結 + quick-start + bot_config.json 指引 |
| [../PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md) | 概念定位（2026-04-10）：腳本驅動多裝置掛機 bot、技術特性、目標 |
| [../CLAUDE.md](../CLAUDE.md) | 專案指引（⚠️ 挖礦/神燈段落已過時，見第 7 節修正） |

### 架構
| 文件 | 一句話 |
|---|---|
| [../README_NEW_ARCHITECTURE.md](../README_NEW_ARCHITECTURE.md) | 重構後技術架構：模組分層、miner core/planning/models/rl 拆解、utils 共用基礎 |
| [../SCRIPT_ARCHITECTURE.md](../SCRIPT_ARCHITECTURE.md) | 概念分層（2026-03-12）：腳本協作專案、入口/裝置/辨識/任務四層 |
| [../README_FLASK_SERVER.md](../README_FLASK_SERVER.md) | push_project Flask 快速啟動（:5000 + /api/car_fight + CORS dev note） |

### 協議反推（`docs/protocol`）
| 文件 | 一句話 |
|---|---|
| [protocol/PAGE_NAVIGATION.md](protocol/PAGE_NAVIGATION.md) | 0x4707 NavigateReq schema；建議 cocos `emit('click')` 而非 raw WS 切頁（2026-05-20） |
| [protocol/MINING_SCHEMA.md](protocol/MINING_SCHEMA.md) | 挖礦 0x0c01 board / 0x0c03 dig / 0x0402 inventory；道具 ID（鎬/鑽/炸）（2026-05-11） |
| [protocol/EQUIPMENT_SCHEMA.md](protocol/EQUIPMENT_SCHEMA.md) | 裝備 0x0504 掉落、預設/方案切換 0x0511/0x032a、清單/明細 cmd（2026-05-11） |
| [protocol/REDPACK_SCHEMA.md](protocol/REDPACK_SCHEMA.md) | 紅包 red.red_* 家族：0x2605 列表 / 0x2603 領取 / 0x0201 錯誤（2026-05-20） |
| [protocol/CARPARK_GUILD_NODES.md](protocol/CARPARK_GUILD_NODES.md) | 車位+家族可點節點/cmd 對照：ParkingMainView/GuildMainView 場景樹（2026-05-20） |
| [protocol/GUILD_TREASURE_LIVE.md](protocol/GUILD_TREASURE_LIVE.md) | 家族驚喜寶箱 CDP 實測：需先移動再拾取、UI 與 guild treasure WS 欄位候選映射（2026-08-05） |
| [protocol/CARPARK_AUTOMATION.md](protocol/CARPARK_AUTOMATION.md) | 車位自動化設計+進度：6/5 車日夜目標、state/auto/tracker 模組拆分（2026-05-20） |
| [protocol/CARPARK_ADB_DESIGN.md](protocol/CARPARK_ADB_DESIGN.md) | ADB OCR 跨服停車設計：H5 場景讀 vs ADB tap+OCR 混合、`_CALIBRATED` dry-run gate（2026-05-25） |
| [protocol/SEA_DAILY.md](protocol/SEA_DAILY.md) | 航海設計+協議：取代 Sea.py 固定滑動，map 在 SeasonMapScene + 靜態 config，node 名=type（2026-05-25） |
| [protocol/MOUNT_SPRINT.md](protocol/MOUNT_SPRINT.md) | 坐騎衝刺：月度週二~四、mountEquipItem 入口 px(221,656) 需真 click（emit 失效）、餵食座標（2026-05-25） |
| [protocol/FAMILY_DUNGEON_ANALYSIS.md](protocol/FAMILY_DUNGEON_ANALYSIS.md) | 家族副本/烈焰山洞/萬神試煉分析藍圖：GuildMapSceneView 走位碰撞進入（2026-05-20） |
| [protocol/LIEYAN_CAVE.md](protocol/LIEYAN_CAVE.md) | 烈焰山洞 dungeon_league_solo：0x0e0e info / 0x0e0f reward / 0x0e10 box，4 個每日寶箱 |
| [protocol/GAME_ASSETS.md](protocol/GAME_ASSETS.md) | 遊戲資源探勘：Cocos 7-bundle map、bundle-firstload-res 藏 config/proto、cc.assetManager 拉法（2026-05-11） |

### 優化分析（`OPTIMIZE_*`）
| 文件 | 一句話 |
|---|---|
| [../OPTIMIZE_main_architecture.md](../OPTIMIZE_main_architecture.md) | 主程式架構（2026-05-27）：巨型 main()、bot_state 拆套件、設備判斷 config 化（Phase 1 清理已做，P0/P1 重構多未做） |
| [../OPTIMIZE_game_automation.md](../OPTIMIZE_game_automation.md) | 任務邏輯：畫面設定層、共用主頁偵測、排程統一、任務契約化、表驅動 battle（多未實作） |
| [../OPTIMIZE_ocr_system.md](../OPTIMIZE_ocr_system.md) | OCR 子系統（2026-05-27）：統一後處理層、訓練資料收集、TensorRT/快取（核心 `ocr_postprocess.py` 仍未建） |
| [../OPTIMIZE_opengold_v2.md](../OPTIMIZE_opengold_v2.md) | 神燈 V2：config 序列化 P0 已修、連閃預設 True 修正、座標 config 化（P1 僅部分） |
| [../OPTIMIZE_utilities.md](../OPTIMIZE_utilities.md) | 工具/基礎設施（2026-05-27）：拆 img_tools/control_panel 上帝模組、scheduling 統一（serve bind/死碼清理已做，拆分未做） |
| [../OPTIMIZE_push_project.md](../OPTIMIZE_push_project.md) | 推播服務（2026-05-27）：debug=True+0.0.0.0 安全、API 鑑權、VAPID/XSS（app.run bind 已修 127.0.0.1，鑑權/XSS 待查） |

### 指南
| 文件 | 一句話 |
|---|---|
| [../AGENTS.md](../AGENTS.md) | GSD 操作手冊：conda 環境、`python new_main_v2.py` 啟動、entrypoint 表、FAQ/測試 |
| [../PROJECT_RUNBOOK.md](../PROJECT_RUNBOOK.md) | 維運原則（2026-03-12）：啟動概念、OCR 主備 server、RL-logs 統一路徑、觀測優先低風險優化序 |
| [../QUICK_START.md](../QUICK_START.md) / [../LIAN_SHAN_EQUIP_GUIDE.md](../LIAN_SHAN_EQUIP_GUIDE.md) / [../LIAN_SHAN_IMPLEMENTATION.md](../LIAN_SHAN_IMPLEMENTATION.md) | 連閃裝備神燈模式：速查 + 規則優先序 + 實作改動清單 |
| [../FIGHT_CAR_GUIDE.md](../FIGHT_CAR_GUIDE.md) | fight_car 跨服搶車位 CLI 格式 + web-UI 整合 |
| [EVENT_INDEX_DEV_GUIDE.md](EVENT_INDEX_DEV_GUIDE.md) | Action Trace → Event Index → GUI/LLM pipeline（action_tracker/smart_screenshot/build_event_index 資料流） |
| [SMART_SCREENSHOT_LLM_ANALYZER.md](SMART_SCREENSHOT_LLM_ANALYZER.md) | SmartScreenshotRecorder + ActionTraceRecorder + LLMAnalyzer 用法 + events/annotations 格式 |
| [WEEKLY_LOG_ANALYSIS.md](WEEKLY_LOG_ANALYSIS.md) | tools/weekly_log_analyzer.py：logs+RL → reports/weekly/*，選配 LLM 診斷、Task Scheduler |
| [../測試使用說明.md](../測試使用說明.md) | JSON data-manager 測試：quick_test.py / test_json_manager.py 帶 device IP 參數 |

### 任務待辦（`tasks/`）
| 文件 | 一句話 |
|---|---|
| [../tasks/todo.md](../tasks/todo.md) | **現行任務**（2026-05-31，branch `perf/reduce-gpu-usage`）：降本地運算/GPU；Phase-1 CNN 批次化+inference_mode+torch threads+wake-parity 錯峰已做 |
| [../tasks/sea_v2_todo.md](../tasks/sea_v2_todo.md) | sea_v2 Stage-A：純邏輯+live probe DONE、33 測試過、flag off、未接 runtime |
| [../tasks/mount_sprint_todo.md](../tasks/mount_sprint_todo.md) | 坐騎衝刺改跨後端 OCR-verified config-driven；座標已驗、qty 7000、4 週週二~四 |
| [../tasks/carpark_adb.md](../tasks/carpark_adb.md) | 從零建 ADB OCR 跨服停車（尚未 live 驗）；calibrated dry-run gate、座標源自 H5 recorder |
| [../tasks/carpark_skip_silver.md](../tasks/carpark_skip_silver.md) | 整層泊銀滿時自動跳過（不再空轉 8 格）；ParkingDataCache.null_space 發現 + S1-S4 |
| [../tasks/mining_ore_ab.md](../tasks/mining_ore_ab.md) | A/B/C sim：量化激進用道具是否每 6 分窗清更多 cluster；mining_sim_eval driver |
| [../todolist.md](../todolist.md) | new_main_v2.py 瘦身計畫：block LOC 表，目標 ~1187→400-500，TDD-first 搬移規則（2026-04-24） |

### 規劃（`.planning` / superpowers）
| 路徑 | 一句話 |
|---|---|
| [../.planning/](../.planning/) | GSD 規劃樹：PROJECT/ROADMAP/REQUIREMENTS/STATE + codebase/(ARCHITECTURE,CONVENTIONS,STACK) + research/ + analysis/ + phases/(01-mumu-control, 04-biweekly-dungeon) + quick/ |
| [superpowers/specs/2026-05-02-task-sandbox-design.md](superpowers/specs/2026-05-02-task-sandbox-design.md) / [superpowers/plans/2026-05-02-task-sandbox-phase-1.md](superpowers/plans/2026-05-02-task-sandbox-phase-1.md) | 任務沙箱設計 spec（已核可）+ Phase-1 plan：通用 navigate→run→observe，零修改包 LampService |
| [superpowers/specs/2026-05-25-carpark-tier-picker-design.md](superpowers/specs/2026-05-25-carpark-tier-picker-design.md) / [superpowers/plans/2026-05-25-carpark-tier-picker.md](superpowers/plans/2026-05-25-carpark-tier-picker.md) | 車位 tier-picker 設計+plan：最小 dashboard.html UI 切 carpark.enabled + 選 silver cross_tier |
| [superpowers/specs/2026-06-03-flypet-grouping-breeding-presets-design.md](superpowers/specs/2026-06-03-flypet-grouping-breeding-presets-design.md) | 飛寵頁種類分組（可收合）+ 配種方案系統：多個命名方案（種類白名單/詞條 AND 白名單），每巢穴選一個；新 `/api/fly_pet_catalog` 讀 configFly.datas/configFly_entry.datas |

### lessons / 觀察
| 文件 | 一句話 |
|---|---|
| [../tasks/lessons.md](../tasks/lessons.md) | 教訓記錄（2026-05-30）：data-driven UI bug 需真實使用者資料、神燈 V2 殘留裝備須照比較規則決策 |
| [../bug.md](../bug.md) | Codex code-review bug 報告（2026-05-15）：2 個跳過的 CRITICAL（dashboard auth / worker webhook trust）+ 19 個 HIGH 以下及修復狀態 |
| [unknown_stage_prompt_list.md](unknown_stage_prompt_list.md) | 觀察 log（2026-04-09）：detector.py 誤判為「未知」的畫面盤點（農場/作物/功能子頁）；無邏輯改動 |
| [../RL_LOGS_UNIFICATION.md](../RL_LOGS_UNIFICATION.md) / [../SCREENSHOT_ASSETS_REQUIRED.md](../SCREENSHOT_ASSETS_REQUIRED.md) | RL-logs 路徑統一（2026-03-12）；截圖/模板/debug 圖為必要資產、live 逐流程檢查、勿刪 |

---

## 7. 探索筆記與分析

- **6 份 OPTIMIZE_\* 報告**（scope 見下，多數 2026-05-27）：
  - [../OPTIMIZE_main_architecture.md](../OPTIMIZE_main_architecture.md) — 主程式：new_main_v2 / bot_state / device_wrapper / config_manager / adb_*；巨型 main()、全域狀態、鎖粒度、後端混雜、硬編設備判斷。
  - [../OPTIMIZE_game_automation.md](../OPTIMIZE_game_automation.md) — 任務邏輯：根目錄腳本 + battle/farm_v2/sea_v2/miner + img_tools/mask/game_state/json_manager。
  - [../OPTIMIZE_ocr_system.md](../OPTIMIZE_ocr_system.md) — OCR：ocr_server.py(:5001)+OCRWorkerPool+PaddleOCR v5、兩套後處理不同步、訓練資料、ROI 硬編。
  - [../OPTIMIZE_opengold_v2.md](../OPTIMIZE_opengold_v2.md) — 神燈：130 行 run()、硬編座標、config 序列化、連閃預設值、缺測試。
  - [../OPTIMIZE_utilities.md](../OPTIMIZE_utilities.md) — 工具/基礎：img_tools 上帝模組、control_panel(:5002)、legacy 像素模組、scheduling 統一、多 HTTP server 生命週期。
  - [../OPTIMIZE_push_project.md](../OPTIMIZE_push_project.md) — 推播：Flask debug/bind 安全、API 鑑權、VAPID 金鑰、PWA/SW、前端 XSS。
- **重構機會清單**：[REFACTORING_OPPORTUNITIES.md](REFACTORING_OPPORTUNITIES.md) — 與本 INDEX 一同產出的新報告，彙整跨子系統重複（`_walk_pb` ×3、分流 parity/offset parser 重複、web profile 路徑解析 ×3、command dispatch ×2、`device` base class ×2、cocos 場景樹 walker 重複、device-id sanitizer ×4、cycle 常數 week_events vs periodic_tasks）、dead-code（oralce_manger/gold_mananer/park.ParkingManager/Assistant/main.py 空 stub/bootstrap.api_services 未接線）、cruft（sync-conflict、committed `__pycache__`、檔名 typo oralce/mananer/manger）與安全面（auth_state/VAPID 外洩、control_panel 無鑑權）。
- **狀態管理重構展開**：[REFACTOR_STATE_MANAGEMENT.md](REFACTOR_STATE_MANAGEMENT.md) — `bot_state` 結構級重構的可執行分階段設計（5 phase；Phase 0–3 已完成、Phase 2 在工作區，Phase 4 可選、Phase 5 YAGNI 暫不做），補上「重構機會清單」line ~320「bot_state 套件多未做」的展開版。
- **協議文件**（`docs/protocol/`）：見第 6 節「協議反推」表，為所有 WS/protobuf 反推與 cocos 節點對照的真相來源。

### ⚠️ 已知 CLAUDE.md 過時點（依本次審計）
- **挖礦**（2026-06-21 更新）：`miner/mining_service.py` 預設 `mining_planner_version='v1'`（A* whole-board；v5 已移除 2026-06-18、v2 已移除 2026-06-05），可切 **v3/v4**；WS 挖礦路徑(`ws_token/mining_adapter`)2026-07-05 起走 **v1** `plan_smart`（smart_planner 有 descent-dig fallback，no_pit 不再卡；sim 3711 vs v4 1649）。完整分析與礦物出現率校正見 `docs/MINING_ALGORITHM_ANALYSIS.md`。
- **神燈**：entry-points 仍列 V1 `Open_gold_paddle_ocr.py`，但 `lamp_scheduler.py` 一律路由 `opengold_v2.LampService`（「一律走 V2」）；舊的 `use_opengold_v2` 切換旗標已於 2026-06-07 從 config schema / 儀表板移除（router 本來就不讀）。
- **潛在 bug**：`game_state/detector.new_stage_check` 用 `if [ ...list... ]:`（非空 list 恆 True），疑似應為 `all()`/`any()`，值得查證。

---

## 8. 閱讀順序建議

1. [../README.md](../README.md) → [../CLAUDE.md](../CLAUDE.md)：定位與規範（留意第 7 節過時點）。
2. [../SCRIPT_ARCHITECTURE.md](../SCRIPT_ARCHITECTURE.md) → [../README_NEW_ARCHITECTURE.md](../README_NEW_ARCHITECTURE.md)：四層分層與重構後結構。
3. 本 INDEX 第 5–6 節：對照子系統地圖與 orchestration 細部，找到要動的模組。
4. 動手前查第 2 節快查表 + 第 4 節可復用工具：先確認有沒有現成 helper（log/截圖/pause_guard/torch_runtime/web_game_api）。
5. 改任務 → 讀對應 [docs/protocol](#協議反推-docsprotocol) schema + 相關 [../OPTIMIZE_*](#優化分析-optimize_) + [REFACTORING_OPPORTUNITIES.md](REFACTORING_OPPORTUNITIES.md)。
6. live 驗證流程 / 雙後端注意事項 → [../AGENTS.md](../AGENTS.md) + `dual-backend-task-dev` / `lamp-debug` / `playwright-lamp-test` skill。
7. 開工前掃 [../tasks/lessons.md](../tasks/lessons.md) 與 [../bug.md](../bug.md)：避免重蹈已知陷阱（尤其 `is_local_device` 路由、神燈殘留決策、cocos UIList stale label）。

# Bug Report — Codex Code Review (2026-05-15)

本 PR 處理 Codex code review 中除 CRITICAL（C1/C2，已協議跳過）外的全部 19 項議題。
分兩輪修復：第一輪 HIGH 子集，第二輪用多併發 agent teams 處理剩餘 + Codex 自我 review。

## CRITICAL（協議跳過）

### C1 · Dashboard 未做身份驗證，綁定 0.0.0.0
**File:** `control_panel_app.py:1651-1706`

**狀態：** 跳過（需獨立評估安全邊界 / API token 設計）。

### C2 · `/api/report_status` 信任 worker 自報的 webhook_url + TLS verify=False
**File:** `control_panel_app.py:1159-1179, 989-995`

**狀態：** 跳過（需設計 worker ID 白名單方案）。

---

## HIGH（全數已修）

### H3 · mushroom_session.json 存在 repo 根目錄
**File:** `.gitignore`

加入 `mushroom_session*.json`。已確認檔案未在 git tracking。

**狀態：** 已修。

### H4 · `control_panel_app.py` 共享全域狀態無鎖保護
**File:** `control_panel_app.py`

新增 `_commands_lock = threading.RLock()`，包覆 `_remote_commands` / `_global_commands` / `_worker_webhook_endpoints` 全部 9 個臨界區（`poll_commands` × 1、`_push_to_worker_webhook` × 1、`_push_remote_command_if_possible` × 2、`refresh_devices` × 1、`reset_flag` × 1、`report_status` × 1、`queue_command` × 1）。HTTP I/O 全部在鎖外執行（snapshot 後釋放鎖再 `requests.post`）。

**狀態：** 已修；Codex review 確認無 deadlock 路徑、無漏鎖。

### H5 · `worker_sync_service.py` 停用 TLS 驗證
**File:** `runtime_services/worker_sync_service.py`

加入 `_should_verify_tls(url)` helper：`http://` / `localhost` / `127.0.0.1` / `::1` 才回傳 False，其他情況回傳 True。把所有 `verify=False` 改為 `verify=_should_verify_tls(master_url)`。註解說明自簽 cert 可透過 `REQUESTS_CA_BUNDLE` 環境變數覆寫。

**狀態：** 已修。

### H6 · `_running_threads` 無鎖訪問
**Files:** `runtime_services/thread_registry.py` (new), `runtime_services/device_scan_service.py`, `new_main_v2.py`

新建 `runtime_services/thread_registry.py` 放共享 `running_threads_lock: threading.RLock`（用獨立模組避開 import cycle）。`new_main_v2.py` 在 `_running_threads` 宣告附近匯入鎖。`device_scan_service.py` 的 `refresh_adb_server` 與 `scan_and_start_devices` 把讀取/刪除/spawn block 包進 `with _running_threads_lock:`。

**狀態：** 已修。

### H7 · `bot_state.py` 鎖層次混亂 — `clear_offline_devices` TOCTOU
**File:** `bot_state.py`

在 per-device lock 內重新驗證 status 仍為 OFFLINE 才刪除。`_states` / `_pause_events` / `_skip_sleep_flags` / `_force_sleep_flags` 在 per-device lock 內刪除；`_manual_release_flags` / `_screenshot_windows` / `_locks` 在 `_global_lock` 內刪除（與這三者在他處的存取一致，Codex review 第二輪指出後修正）。

**狀態：** 已修（含 Codex 後續 review 一致性修正）。

### H8 · `close_all_web_devices()` 跨 thread 操作 Playwright context
**File:** `device_wrapper.py`

加 `owner_thread_id` 檢查：若 caller 不是 owner thread 就跳過硬 close 並警告（避免 Playwright thread-affinity 違規）。Codex review 第二輪指出原版會在 skip 前 evict registry 造成孤兒，已修正為只 evict 已嘗試 close 的 device，被 skip 的 device 留在 registry 由 owner thread 的 finally 自行清理。

**狀態：** 已修（含 Codex 後續 review 修正）。

### H9 · SmartPlanner 未過濾超出 shovel 預算的動作
**File:** `miner/planning/smart_planner.py`

`get_valid_actions` 過濾 `dig_cost > state.shovels` 與 `cost_item > state.shovels` 的動作。Codex review 確認過濾與 `simulate_action` 扣分一致，含邊界 case。

**狀態：** 已修。

---

## MEDIUM（已修）

### M10 · 挖礦迴圈對空計劃無退出機制
**File:** `miner/mining_service.py`

加 `_MAX_EMPTY_PLANS = 3` 常數與 `consecutive_empty_plans` 計數器：空 plan 累加；連續 3 次就 break 並 warning；拿到 non-empty plan 即歸零。

**狀態：** 已修。

### M11 · `config_manager.py` read-modify-write 非原子
**File:** `config_manager.py`

`_config_lock` 從 `Lock` 升為 `RLock`（`update_*` 內部會呼叫已持鎖的 `load/save_config`）。`update_ocr_config` / `update_device_config` 整段 load→modify→save 包進 `with _config_lock:`，避免被中途的 save 蓋掉。

**狀態：** 已修。

### M12 · `_run_web_login_worker` 函數職責過多
**File:** `control_panel_app.py:512-753`

**狀態：** 未修（240 行函式需大幅 refactor，與 H4 已修改的同檔案有 merge 風險，建議獨立 PR）。

### M13 · `miner/planning/planner.py` 主函數過長且重複邏輯
**File:** `miner/planning/planner.py:342-739`

**狀態：** 未修（400 行 + 多個嵌套 helper，純結構性 refactor 風險高，建議獨立 PR；M14 已先把該檔案的 silent except 修了）。

### M14 · Planner 吞掉所有例外
**File:** `miner/planning/planner.py`

把 `except Exception` 縮窄為 `except (IndexError, KeyError, ValueError, TypeError) as e:` 並加 `logger.debug(...)` 記錄被吞的 exception 類型與 unrea 內容。其他類型現會 propagate。

**狀態：** 已修。

### M15 · OCR 失敗與「無文字」無法區分
**Files:** `img_tools.py`, `Open_gold_paddle_ocr.py`

`img_tools.py` 新增 `OCRError` / `OCRServerUnavailable(OCRError)` 例外。`analyze_skill_via_http` / `analyze_stage_via_server` 在所有 server 都失敗時改為 raise；`get_all_text` 不再 swallow `OCRError`。`Open_gold_paddle_ocr.py` 在呼叫處 try/except 並用 `logger.warning("[OCR] pipeline error")` 區分。改完語意：`[]` = 「OCR 正常但無文字」；raise = 「OCR 本身壞了」。

**狀態：** 已修。

### M16 · `algo_evolver.py` requests 無 timeout
**File:** `miner/algo_evolver.py`

加 `timeout=(5, 60)`（connect 5s, read 60s）；分支處理 `Timeout` / `RequestException` / 其他 `Exception`，三類分別 log 並回傳 `None`。

**狀態：** 已修。

---

## LOW

### L17 · sync-conflict 檔案殘留
**狀態：** N/A — `find . -name "*sync-conflict*"` 已無檔案，baseline 已清。

### L18 · miner planner/executor 缺乏整合測試
**File:** `tests/test_miner_planner_executor_integration.py` (new, 4 tests)

不 mock smart_planner / planner / executor，用 3×3 board 驅動 `plan_smart` 後以 `simulate_action` 重放。4 個 case：happy path、H9 預算回歸（`shovels=1` 不超預算）、step-by-step invariant、`_FakeDevice` smoke。`execute_plan_steps` 因 UI/classifier/OCR 依賴過重未直接驅動，改以 `simulate_action`（planner/executor 共享的 source-of-truth）驗證 contract。

**狀態：** 已修。

### L19 · worker route 缺乏路由層級測試
**File:** `tests/test_worker_routes_integration.py` (new, 4 tests)

`control_panel_app.app.test_client()` + `requests.post` monkeypatch。4 個 case：(1) `poll_commands` 空 commands；(2) `report_status` 寫 webhook、`_push_to_worker_webhook` 找得到 endpoint；(3) malicious scheme（`file://` / `javascript:` / `ftp://`）被拒；(4) **H4 回歸**：20 thread × 25 commands 並發 `queue_command`，驗證 500 個 write 全部安全落地（對應 `_commands_lock`）。

**狀態：** 已修。

---

## 整體驗證

- 所有修改檔案 `py_compile` 通過
- 自寫 H7/H9 smoke test：6 個 case 全綠（含 TOCTOU 模擬 + 預算邊界）
- 新增 8 個整合測試（L18 4 個 + L19 4 個）全綠，含 H4 多 thread race 回歸
- `pytest tests/` 整體：~270 pass；剩餘 failure 全為 baseline 既有問題（`test_mining_item_logic.py` / `test_lamp_loop_state.py` ImportError、`test_daily_pipeline.py` AttributeError、`test_smoke_config_api.py` 受 `test_bootstrap_api_services` module stub 污染），stash 我的改動後仍存在

## 改動檔案總覽（17 個）

| 類型 | 檔案 |
|------|------|
| Modified | `.gitignore`, `bot_config.json`, `bot_state.py`, `config_manager.py`, `control_panel_app.py`, `device_wrapper.py`, `img_tools.py`, `Open_gold_paddle_ocr.py`, `miner/algo_evolver.py`, `miner/mining_service.py`, `miner/planning/planner.py`, `miner/planning/smart_planner.py`, `new_main_v2.py`, `runtime_services/device_scan_service.py`, `runtime_services/worker_sync_service.py` |
| New | `bug.md`, `runtime_services/thread_registry.py`, `tests/test_miner_planner_executor_integration.py`, `tests/test_worker_routes_integration.py` |

## 後續

- **C1 / C2 / M12 / M13** 各自獨立 PR：
  - C1/C2 需先設計 API token / worker 白名單機制
  - M12 是 240 行 function 拆分
  - M13 是 400 行 function + nested helper 結構性 refactor

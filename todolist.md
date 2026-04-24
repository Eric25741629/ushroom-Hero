# new_main_v2.py 瘦身計畫

**目標**：把 `new_main_v2.py` 從 1187 行降到 400–500 行，讓 `main()` 的「初始化 → wake 迴圈 → pipeline → sleep」四個階段一眼看清楚。

**現況盤點**（2026-04-24）：

| 區塊 | 行數範圍 | 行數 | 目的地 |
|------|---------|------|--------|
| imports | 1–128 | 128 | 隨依賴搬走而瘦身 |
| 截圖/mismatch helper | 141–178 | ~40 | `utils/screenshot_helpers.py` |
| `stop_runtime_device_for_sleep` | 181–200 | ~20 | `runtime_services/sleep_service.py` |
| `run_sleep_cycle` + `_maybe_resume_sleep` | 203–396 | ~170 | `runtime_services/sleep_service.py` |
| Exception classes | 281–288 | ~8 | `runtime_services/errors.py`（或就近保留） |
| `_run_lamp` + `_run_lamp_if_due` | 291–500 | ~115 | `game_actions/lamp_scheduler.py` |
| `get_stage_with_check` | 302–320 | ~20 | `game_actions/stage_guard.py` |
| `_handle_startup_sleep` | 327–344 | ~18 | `runtime_services/startup_sleep.py` |
| `_run_at_main_page` | 399–420 | ~22 | `game_actions/stage_guard.py`（共用 helper） |
| `_run_weekly_dungeon` / `_run_biweekly_dungeon` | 502–570 | ~70 | `game_actions/dungeon_scheduler.py` |
| **`_run_daily_tasks`**（20 個任務） | **573–819** | **~245** | `game_actions/daily_pipeline.py` |
| `main()` | 821–1106 | ~285 | 留下，但瘦身 |
| `__main__` bootstrap | 1147–1187 | ~40 | 部分→ `bootstrap/api_services.py` |

---

## 執行原則

1. **TDD 優先（本專案硬要求）**：每個 Phase 都要先寫 characterization test（鎖住目前行為），看到紅燈 → 搬遷 → 看到綠燈 → commit。
   - 測試檔放在 `tests/test_<新模組名>.py`（例如 `tests/test_screenshot_helpers.py`）
   - 用 AAA pattern：Arrange（建 mock / stub）→ Act（呼叫函式）→ Assert
   - 每個 Phase 至少鎖 3 條：正常路徑、邊界、錯誤路徑
2. **漸進式搬遷**：保留 `new_main_v2.py` 舊 API（或用 re-export），避免一次斷掉太多連結。
3. **小步原子 commit**：一個 Phase 一個 commit（含 tests + 實作），方便回滾。
4. **不改行為**：純粹搬位置，不修 bug、不加功能、不改命名語意。有疑慮就先註記，不動。
5. **隱性契約先挖出來**：例如 `_run_daily_tasks` 裡 task 4 的 stage 被 task 5/6 複用；task 18 執行後要重取 stage 給 task 19（lamp）。搬之前先寫成 docstring。
6. **commit 策略**：每個 Phase 做完自行 commit（2026-04-24 與使用者確認）。

---

## 優化順序（由低風險 → 高風險）

### Phase 0：安全網
- [x] **0.1** 建立 baseline：跑 `pytest` 記下目前通過/失敗清單 → **190 passed**
- [x] **0.2** 確認沒有其他 Python 檔 import `new_main_v2` 的函式（已查：只有 `fix_prints.py` 字面字串、`runtime_services/__init__.py` 註解）

### Phase 1：截圖/mismatch helper（低風險，熱身） ✅
- [x] **1.1** 新建 `utils/screenshot_helpers.py`（48 行），搬移：
  - ~~`_sanitize_filename_part`~~ — 發現是 dead code（`utils/smart_screenshot.py` 已有一份），直接刪除
  - `save_error_screenshot`
  - `log_main_page_mismatch`
  - `_smart_shot` 單例（搬到新模組內）
- [x] **1.2** `new_main_v2.py` 改為 `from utils.screenshot_helpers import ...`
- [x] **1.3** `pytest` 綠燈（190 passed，與 baseline 一致）
- 📉 new_main_v2.py：1187 → 1146 行（-41）

### Phase 2：神燈排程（中低風險） ✅
- [x] **2.1** 新建 `game_actions/lamp_scheduler.py`（112 行），搬移：
  - `_run_lamp`（LampService v2 / 舊版分流）
  - `_run_lamp_if_due`（phone OCR / 5560 / 一般三種分支）
- [x] **2.2** 先寫 10 條 TDD 測試（紅），再搬 code（綠）
- [x] **2.3** `pytest` 綠燈（206 passed）；task 19 改呼叫 `from game_actions.lamp_scheduler import _run_lamp_if_due`
- 📉 new_main_v2.py：1146 → 1057（-89）
- 🧪 tests：196 → 206（+10 lamp scheduler）
- 💡 TDD 發現：原邏輯「phone-OCR 分支 + general 分支可以同時觸發」— 測試鎖住了這個行為

### Phase 3：stage guard 共用（低風險，為後續鋪路） ✅
- [x] **3.1** 新建 `game_actions/stage_guard.py`（~85 行），搬移 `get_stage_with_check`、`_run_at_main_page`、`LoginConflictError`
- [x] **3.2** 6 條 TDD 測試（red→green）
- [x] **3.3** `reward` 改為 lazy import（避開 `tools → adb_operations` eager chain 在測試被 stub 時炸掉）
- 📉 new_main_v2.py：1057 → 1012（-45）
- 🧪 tests：206 → 212（+6）

### Phase 4：副本排程（中風險） ✅
- [x] **4.1** 新建 `game_actions/dungeon_scheduler.py`（93 行）
- [x] **4.2** 12 條 TDD 測試鎖住時間窗邏輯（週/雙週、週一下午/週日跳過、5556 限定等）
- [x] **4.3** `pytest` 綠燈（224 passed）
- 📉 new_main_v2.py：1012 → 942（-70）
- 🧪 tests：212 → 224（+12）

### Phase 5：sleep 服務（中高風險）
- [ ] **5.1** 新建 `runtime_services/sleep_service.py`，搬移：
  - `calc_aligned_wake_ts`（從 `run_sleep_cycle` 內抽出為 top-level）
  - `run_sleep_cycle`
  - `_maybe_resume_sleep`
  - `stop_runtime_device_for_sleep`
  - `StartupBypassError`
- [ ] **5.2** `calc_aligned_wake_ts` 是純函式 → 補單元測試（邊界：min_sleep_sec=0、跨小時）
- [ ] **5.3** `pytest` 綠燈 commit

### Phase 6：startup sleep helper（低風險）
- [ ] **6.1** 新建 `runtime_services/startup_sleep.py`，搬移：
  - `_handle_startup_sleep`
  - `_STARTUP_SLEEP_SEC_BY_DEVICE`（常數）
  - `_PROCESS_START_TS`（在新模組算）
- [ ] **6.2** `pytest` 綠燈 commit

### Phase 7：日常任務 pipeline（最高風險、最大收益）
- [ ] **7.1** 先寫 `game_actions/daily_pipeline.py` 的骨架，定義 `DailyContext`（把 `d, ip, Cnn_model, clf, rl_recorder, current_time, enable_dungeon_manager, wheel_manager, mission_manager, family_manager` 收進一個 dataclass）
- [ ] **7.2** 把 `_run_daily_tasks` 整塊搬過去；先保留 20 個任務的單一 `run(ctx)` 流程，不改內部結構
- [ ] **7.3** 把 `_DEVICE_SKIP_GUARDIAN` 常數一併搬走
- [ ] **7.4** `new_main_v2.py` 改為呼叫 `daily_pipeline.run(ctx)`
- [ ] **7.5** `pytest` 綠燈 commit
- [ ] **7.6** *（可選，下一迭代再做）* 把 20 個任務拆成 `tasks/*.py` 的 `Task` 物件 + registry — 本次先不動，風險太高

### Phase 8：清掉已經沒用的 top-level import
- [ ] **8.1** 掃描 `new_main_v2.py` 剩下的 imports，刪掉已搬走函式的相依（`new_battle`, `Open_gold_paddle_ocr`, `daily_gift_task`, ...）
- [ ] **8.2** `python -c "import new_main_v2"` 不報錯
- [ ] **8.3** `pytest` 綠燈 commit

### Phase 9：`main()` 內部重組（可選，評估後決定）
- [ ] **9.1** 嘗試把 `main()` 拆成：
  - `_init_runtime_managers(d, ip, ...)` → 回傳 managers dict
  - `_run_wake_cycle_once(ctx)` → 一輪 wake/task/sleep
  - `main(ip, ...)`：只剩外層 while + try/except
- [ ] **9.2** 評估拆完是否真的更易讀；如果不明顯，保持原狀
- [ ] **9.3** `pytest` 綠燈 commit

### Phase 10：API bootstrap 抽離（小收益，可最後做或跳過）
- [ ] **10.1** 把 `__main__` 底下的 Flask / push / worker webhook / worker sync 啟動邏輯抽成 `bootstrap/api_services.py` 的 `start_all(mode)`
- [ ] **10.2** `__main__` 只剩 `start_all(mode); scan_loop()` 兩行
- [ ] **10.3** `pytest` 綠燈 commit

### Phase 11：收尾
- [ ] **11.1** 再跑一次完整 `pytest`
- [ ] **11.2** 比對 `wc -l new_main_v2.py` 前後差距、記在本檔
- [ ] **11.3** 更新 `CLAUDE.md` 的 Entry Points 區塊（如果結構描述過時）

---

## 風險紀錄

| 風險 | 影響 | 緩解 |
|------|------|------|
| `_run_daily_tasks` 有隱性 stage 共用（task 4→5/6、task 18→19） | 搬錯順序→跳任務 | Phase 7 不拆內部結構，整塊搬 |
| `run_sleep_cycle` 被 `main()` 從多個 except 分支呼叫，參數狀態不好追 | 漏傳 `forced_wake_ts`→休眠邏輯錯 | Phase 5 先寫單元測試鎖行為 |
| `_DEVICE_SKIP_GUARDIAN`、`_STARTUP_SLEEP_SEC_BY_DEVICE` 等常數散落 | 搬到新模組後兩邊不同步 | 搬完立刻刪舊定義，不留 alias |
| SMB/NAS 上的 .pyc 抑制設定（`sys.dont_write_bytecode = True`） | 新模組第一次 import 有延遲 | 設定在 new_main_v2.py 頂端已生效，新模組沿用即可 |

---

## 完成狀態

### Baseline（Phase 0 完成，2026-04-24）
- 執行：`pytest tests/ --ignore=tests/test_wake_loop_escape.sync-conflict-*.py`
- 結果：**190 passed, 1 skipped, 1 failed, 3 errors**
- 既有 fail（非本次範圍，須維持不變）：
  - `tests/test_mining_item_logic.py::test_dismiss_mining_overlay_when_ocr_finds_mine_text`（miner 模組 monkeypatch 問題）
  - `tests/test_smoke_config_api.py` 3 個 ERROR（fixture 設置）
- 本次重構須維持的綠燈：**190 passed**；特別關注 `test_wake_loop_escape.py`、`test_biweekly_scheduler.py`

### Phase commits

| Phase | Commit | 行數變化 |
|-------|--------|---------|
| 1 | `a7333b1` | new_main_v2.py 1187 → 1146 (-41) |
| 2 | `31747a1` | new_main_v2.py 1146 → 1057 (-89) |
| 3 | `5398886` | new_main_v2.py 1057 → 1012 (-45) |
| 4 | _(pending)_ | new_main_v2.py 1012 → 942 (-70) |

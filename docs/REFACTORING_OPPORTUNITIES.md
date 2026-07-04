# 重構與優化機會（跨子系統）

> 更新日期：2026-05-31（進度更新 2026-06-04）　|　分支：`perf/reduce-gpu-usage`
>
> 本報告是跨子系統（cross-cutting）的重構/優化待辦清單，**補充而非取代**根目錄既有的 6 份 `OPTIMIZE_*.md`（各自聚焦單一子系統）。

## 安全聲明（請先讀）

- **這是一個 LIVE bot。** 本報告全部為「非破壞性分析」，**沒有任何改動已被套用**。
- 每一項都需要：人工審查 → 改動 → **重啟 `new_main_v2.py`** 後才會生效（`sys.modules` 會快取已載入模組，改了不重啟等於白改）。
- 任何觸及「每裝置主迴圈熱路徑」的改動（CNN 推論、截圖、OCR、`get_stage`、`config_manager.load_config`）標記為 **較高風險**：一個 thread 一台裝置，錯誤會在多裝置同時放大。
- 本清單只列出已由 lead 重新驗證為真（`verdict.is_real=true`）的項目；被排除/存疑的兩項收在文末。

### 已釐清的事實基準（與 stale 文件相反，請以此為準）

1. **挖礦 planner 預設是 v4**（`miner/mining_service.py:425`、`config_manager.py:48`，`mining_planner_version` 可切 v1/v2/v3/v4）。CLAUDE.md 稱「v1 為 current runtime」已過時。
2. **神燈一律走 V2**：`game_actions/lamp_scheduler.py:25` 把所有 lamp run 路由到 `opengold_v2.LampService`；V1 `Open_gold_paddle_ocr.py` 已棄用。`use_opengold_v2` flag router 本來就不讀，**已於 2026-06-07 從 config schema / bot_config.json / 儀表板 checkbox 全面移除**（見下方原死碼清理項，現已執行）。
3. `game_state/detector.py:7` `new_stage_check()` 用 `if [list_of_bools]:`（非空 list 恆為 truthy → 永遠回 True）。但**全 repo 零呼叫者**，屬「含潛在 bug 的死碼」，非 active bug（低風險，刪或修皆可）。
4. 確切有 **4 個被追蹤的 `.pyc`**：`tests/__pycache__/conftest.cpython-*.pyc.NNNNN`（數字後綴繞過 `*.pyc` 規則）。`git rm --cached` 安全。

---

## ✅ 已完成進度（branch `perf/reduce-gpu-usage`，更新 2026-06-04）

> 下列項目已實作完成。列出 commit SHA 者已進版控；標「工作區」者已實作並通過測試／py_compile，但尚未 commit。

### 效率（本分支主題，皆熱路徑）

| 本表# / 內文# | 項目 | 狀態 |
|---|---|---|
| #5（內文18）+ #4（內文19） | `config_manager.load_config` mtime 快取（連帶消除 OCR 每次重讀 config 的 NAS round-trip） | ✅ commit `4d2766e3` |
| #7（內文15） | `park.goto_park` 30s busy-wait 迴圈尾加 `sleep(1.0)` | ✅ commit `4d2766e3` |
| #8（內文20）+ #9（內文14） | 頁面 CNN 包 `inference_slot()`+`inference_mode()` + device-aware（`map_location`/`.to(device)`，勿預設 cuda） | ✅ commit `4d2766e3` |
| #6（內文16） | `oracle()` 截一次推一次、刪未用截圖、`print`→`logger` | ✅ 工作區（未 commit） |
| 內文17（eff-3，未進總表） | `get_stage` 同幀 OCR 3-4 次 → 1 次（保留優先序 公告>車位倉庫>...；bbox x>155 守門；新 `img_tools.get_all_text_with_results`，10 測試含 single-call 守衛） | ✅ 工作區（未 commit） |

### 狀態管理（bot_state）結構重構 — 展開於 [REFACTOR_STATE_MANAGEMENT.md](REFACTOR_STATE_MANAGEMENT.md)

> 對應 `OPTIMIZE_main_architecture.md` 標為「多未做」的 P0/P1「bot_state 套件」結構級重構（本報告先前僅於下文「與現有 OPTIMIZE 關係」段落 line ~320 提及，未列為獨立追蹤項）。現正式納入：

| Phase | 內容 | 狀態 |
|---|---|---|
| 0 | 安全網特徵化測試（清理後歸零／paused 一致性／force_sleep 連鎖） | ✅ commit `a603ab41` |
| 1 | 清理路徑漏 channel bug 修復 + accessor 封死 control_panel 直寫 `_states` + 裸全域加鎖 | ✅ commit `a603ab41` |
| 3 | 4 個 one-shot flag 收斂成 `DeviceSignals`（`Signal` enum + `_signals` registry；舊函式改 shim，call site 零改動；清理變一行 `_signals.pop`） | ✅ commit `a603ab41` |
| 2 | `paused` 改衍生值（local-only，pause Event 為單一真相）+ `refresh_needed` 單一設旗入口 `_set_refresh_needed_locked`（避免重入死鎖）+ 兩層顯性化 | ✅ 工作區（未 commit，`tests/test_bot_state_phase2.py` 7 測試） |
| 4 | 抽出 `web_launch` / `online_check` mailbox 成獨立小模組 | ⬜ 可選，未做（~25 call site／7 模組；建議獨立一輪，勿併入 perf 分支） |
| 5 | 整包包成 `DeviceStateStore` 類 | ⬜ 暫不做（提案 §6 YAGNI 護欄：外洩僅 3 處，Phase 1 補 accessor 後封裝已足） |

---

## 優先級總表

優先級 = f(影響, 工作量, 風險)。安全、高影響、低工作量者排前；高風險拆分排後。

| # | 主題 | 類別 | 影響 | 工作量 | 風險 | 核心建議（一句） |
|---|------|------|------|--------|------|------------------|
| 1 | 已追蹤的 4 個 `tests/__pycache__/*.pyc.NNNNN` | cruft | 低 | XS | 低 | `git rm --cached` 即可，`**/__pycache__/*` 已涵蓋 |
| 2 | 空的 `main.py` 遮蔽真入口 | cruft | 低 | XS | 低 | `git rm main.py` + 移除 `SCRIPT_ARCHITECTURE.md:19` 條目 |
| 3 | `new_stage_check()` 死碼含恆真 bug | cruft/complexity | 低 | XS | 低 | 零呼叫者，刪除或修為 `all(...)` |
| 4 | OCR 每次呼叫重讀 NAS config 2-4 次 | efficiency | 高 | S | 低 | 修 `load_config` mtime 快取即連帶解決（熱路徑） |
| 5 | `load_config` 每次重讀+重寫 NAS 無快取 | efficiency | 高 | M | 低 | 加 mtime 快取於現有 `_config_lock` 內（熱路徑） |
| 6 | `oracle()` 每輪 2x predict + 3 截圖 | efficiency | 中 | S | 低 | 截一次、推一次、`print`→`logger`（熱路徑） |
| 7 | `park.goto_park` 30s busy-wait 無 sleep | efficiency | 高 | S | 低 | 迴圈尾加 `time.sleep(1.0)`（熱路徑） |
| 8 | 頁面 CNN 不過 InferenceGate | efficiency | 中 | S | 中 | `predict_image` 包 `inference_slot()`+`inference_mode()` |
| 9 | 頁面 CNN 無 `map_location`/`.to(device)` | efficiency | 中 | S | 中 | `new_cnn/cnn_model.py` device-aware（勿預設 cuda） |
| 10 | scratch/throwaway 腳本散落 root | cruft | 低 | S | 低 | 移 `tools/`、刪 `fix_prints.py`、修連動 doc/notebook |
| 11 | root `test_*.py` 污染 pytest 收集 | cruft | 中 | S | 中 | 加 `pyproject.toml` `testpaths=["tests"]` |
| 12 | device-id 檔名正規化重複 | reuse | 低 | S | 低 | 2 處改呼叫 `LogPaths.safe_device_id()` |
| 13 | cocos `world_to_pixel` 換算公式 3 處 | reuse | 中 | S | 低 | 改呼叫 `sea_v2.navigator.world_to_pixel`（保 `round`） |
| 14 | per-device `{ip}.json` 繞過 `JsonDataManager` | reuse | 中 | M | 中 | 改走 `JsonDataManager`/`_atomic_write_json`（原子寫） |
| 15 | cocos 場景樹 walk + worldToScreen JS 大量重抄 | dup/complexity | 中 | L | 中 | 抽共用 JS 片段至 `cocos_navigator`/`carpark_js`（熱路徑） |
| 16 | `control_panel_app.py` 2576 行 god-module | complexity | 中 | 高 | 中 | 漸進拆 Flask blueprint，path 不變 |

> 未進總表但仍列於下文的項目：主頁面像素守衛重複（dup-0）、OCR 後處理/詞表三/四份（cx-6）、JSON BOM 讀取不一致（dup-5）、carpark 多個 god-flow（cx-2/4/7）、`_start` 150 行（cx-5）、V1 神燈死碼（cx-3）、Flask 錯誤封套 21x（cx-1）、各類 cruft（cruft-4/5/6）。

> ✅ 已完成標記見上方「已完成進度」：本表 #4/#5/#6/#7/#8/#9 + 內文 #17（get_stage）+ 狀態管理重構 Phase 0–3。

---

## 復用與去重

### 1. 主頁面 9 點像素守衛迴圈逐字複製到 5 處（dup-0）

- **位置**：
  - `device.py:14-45`（`device.capture_screenshot`，max_attempts=10）
  - `Mission.py:46-81`（**自有的** `device` class，非 import 自 device.py；max_attempts=10 + test_image 分支）
  - `park.py:167-180`（`ParkingManager.capture_screenshot`，單行 `while True` 無上限）
  - `battle/manager.py:47-54`（`BattleManager`，單次 dismiss + 一次重截，**無迴圈**）
  - `tools.py:40-50`（`android_devices.capture_screenshot`，單行 `while True` 無上限）
  - 既有等價實作：`opengold_v2/ui_controller.py:48-64`（`_pixel_sum_close`/`_match_pixel_profile`）
- **證據**：同 9 組座標 `[(189,234),(236,218),(318,228),(363,236),(132,249),(139,264),(154,329),(370,361),(451,337)]` → 同 BGR、容差 <10、同 dismiss `click(509,56)`、`sleep(1)`，byte-identical 散在 5 份。
- **建議**：抽純函式 `utils/main_page_guard.is_main_page_with_popup(img) -> bool` + 單一 9 點常數表（鏡像 `opengold_v2/ui_controller._match_pixel_profile`）。**只共用布林 matcher，各呼叫點原有迴圈/重試語意保持不變**。先換 `device.py` 與 `Mission.py`（兩份相同的 max_attempts=10 版）。**勿**讓這些 class 繼承 `device_wrapper.capture_screenshot`（那只是 `screenshot(format="opencv")`，無守衛/dismiss，會靜默丟掉彈窗關閉行為）。三個單行版（park/battle/tools）語意各異（無上限 vs 單次），延後到迴圈語意對齊後再動。
- **consolidation target**：`opengold_v2/ui_controller._match_pixel_profile` 或新 `utils/main_page_guard.py`。
- **工作量/風險/影響**：M / MEDIUM / 高（主頁改版時要同步改 5 處，極易漏改）。

### 2. cocos `worldPosition→viewport` 換算公式 3 處各自實作（dup-1）

- **位置**：
  - 正規來源（純函式，stdlib-only，已被 `farm_v2/web_farm.py:174`、`sea_v2/session.py:226,233` 正確復用）：`sea_v2/navigator.py:29-40` `world_to_pixel`
  - `opengold_v2/ui_controller.py:488-489`（手算 `round(wp["x"]*540.0/wp["dw"])` / `round((wp["dh"]-wp["y"])*960.0/wp["dh"])`）
  - `sea_v2/rewards.py:68`（`_JS_RED_NODES` 內嵌 JS `Math.round(wp.x*540/720),Math.round((1280-wp.y)*960/1280)`）
- **建議**：
  - **STEP 1（瑣碎、先做）**：`ui_controller.click_cocos_node` 改 `fpx,fpy = world_to_pixel(wp["x"],wp["y"],frame=(540,960),design=(wp["dw"],wp["dh"]))`，再 `px,py = round(fpx),round(fpy)`。**`world_to_pixel` 回傳 float，必須保留 `round()`**（否則把 float 餵給 `device.click()`）。`frame` 明確傳 `(540,960)`。無循環 import（navigator 只 import stdlib）。
  - **STEP 2（選做）**：`rewards.py` 的 JS 只回傳 `worldPosition + dw/dh`，換算移回 Python 走 `world_to_pixel`。
  - **勿**動 `tools/*` 與 sync-conflict 檔（雜訊）。
- **consolidation target**：`sea_v2/navigator.world_to_pixel`。
- **工作量/風險/影響**：S / LOW / 中（解析度/縮放調整時兩個 runtime 點擊路徑會偏移；專案對座標飄移敏感）。

### 3. device-id 檔名正規化 `ip.replace(":","_")` 重複（dup-3）

- **位置**：
  - `new_main_v2.py:181`（`os.path.join("miner","rl_logs",ip.replace(":","_"))` — 只換冒號）
  - `game_actions/manager_factory.py:41`（同上 — 只換冒號）
  - `battle/_helpers.py:130`（`ip.replace(":","_").replace(" ","_")` — 已等同 `_safe()`）
  - 正規 normalizer：`utils/log_paths.py:21-32` `_safe()` / `LogPaths.safe_device_id()`
- **建議**：前兩處改 `LogPaths.safe_device_id(ip)`（含冒號 ID 輸出 byte-identical，`tests/test_manager_factory.py:108-116` 仍綠）。**保留** `os.path.join("miner","rl_logs",...)`，只換 normalizer 呼叫。`battle/_helpers.py:130` 已等價，純美化選做。**勿**新增 `LogPaths.rl_logs_dir()`（`LogPaths` 以 `logs/` 為根，`rl_logs` 在 `miner/` 下，會破壞契約）。
- **consolidation target**：`LogPaths.safe_device_id()`。
- **工作量/風險/影響**：S / LOW / 低（真實 ID 不含空白，屬一致性而非 active bug）。

### 4. per-device `{ip}.json` 直接 `open`+`json.load/dump`，繞過 `JsonDataManager`（dup-2）

- **位置**：
  - ~~`new_main_v2.py`（`temporary_reset_cycles()` 手動讀改寫刪 `衝刺-發條`，**非原子**）~~ ✅ 2026-07-05 死碼清理已移除該函式，此點作廢
  - `Mission.py:93-110`（`load_data`）+ `112-134`（`record`）：自寫 default `{'mission_timestamp':0,'mission_num':0}` + JSONDecodeError fallback，與 `json_manager/base.py:157-177` 重疊；`record()` 非原子寫
  - `fight_car.py:488-530`（`flush_logs()`）：**MISGROUPED**——寫固定路徑 `push_project/web/car_fight.json`，非 `{ip}.json`
  - `test_mount_rush.py:37-44`（dev 拋棄腳本，最低優先）
  - 正規來源：`json_manager.JsonDataManager`（含 `_atomic_write_json` temp+os.replace、損毀備份）
- **建議**：
  - ~~`new_main_v2.py`：改 `mgr=JsonDataManager(ip); ...`~~ ✅ 作廢：`temporary_reset_cycles()` 已於 2026-07-05 隨死碼清理移除。
  - `Mission.py`：`load_data/record` 委派檔案 IO 給 `JsonDataManager(self.device_ip)` 但**保留 flat keys**（用 `load_data(default=...)`+`save_data()`，**勿**改用 `record_timestamp()`，否則改 on-disk schema、破壞 dashboard 讀 `mission_timestamp`）。
  - `fight_car.py:529-530`：**勿用 JsonDataManager**（固定路徑），改 `json_manager._atomic_write_json(...)` 取得原子安全。
- **consolidation target**：`json_manager.JsonDataManager` / `_atomic_write_json`。
- **工作量/風險/影響**：M（3 個真實改動）/ MEDIUM / 中（NAS/SMB 上非原子寫中途中斷壞檔）。

### 5. JSON 設定/快取讀取 BOM 處理不一致（utf-8 vs utf-8-sig）（dup-5）

- **位置**：
  - `config_manager.py:293/482`（讀 `bot_config.json` 用 `utf-8-sig` — 正確）；寫回 `:285/:341/:491` 用 `utf-8`（無 BOM — 寫側正確，勿改）
  - **真實 BOM-unsafe 讀**：`json_manager/base.py:166`（`JsonDataManager.load_data` 用 plain `utf-8`，最高流量）、`utils/equipment_cache.py:179`（`read_text(encoding='utf-8')`）
  - `opengold_v2/config.py:248`（`from_file` plain utf-8，**零呼叫者**，僅自我 roundtrip，最低優先）
  - **修正原 finding 誤指**：`sea_v2/map_cache.py:45` 其實已用 `utf-8-sig`；「181 處 json.load」誇大，實際約 22 處。
- **建議**：只改**讀**路徑（寫側 no-BOM 是對的）。加 `utils/json_io.py` `read_json_bom_safe(path, default=None)`（一律 `utf-8-sig`，`FileNotFoundError/JSONDecodeError` 回 default），把 `json_manager/base.py:166` 與 `utils/equipment_cache.py:179` 兩個 runtime 讀點導過去。`utf-8`→`utf-8-sig` 對無 BOM 檔行為不變、對有 BOM 檔更寬容，可安全上 live。
- **consolidation target**：新 `utils/json_io.py`。
- **工作量/風險/影響**：S（建議降級）/ LOW / 中（Windows 編輯器/Syncthing 可能加 BOM 致設定載入間歇失效，符合既有踩雷紀錄；但 blast radius 為 2 個 runtime 讀點，非全專案）。

---

## 複雜度熱點

### 6. `control_panel_app.py` 2576 行 god-module（12 子系統共用一個 Flask app）（cx-0）

- **位置**：`control_panel_app.py:1-2576`。關鍵錨點：worker queue `74-368`、web-login 子系統 `400-790`（`_run_web_login_worker:550-790`）、**mid-file 重 import** `831-835`（`cv2`/`numpy`/`new_cnn.cnn_model`）、**冗餘 re-import** `from flask import ...` `943`、`poll_commands/report_status/queue_command` `1007-1331`、live-view WS `1634-1737`、labeler/trainer routes `1782-1913`、CDP helper `1964-2044`、**~17 個 fly-pet endpoint** `2047-2565`（各內嵌大段 raw JS）。
- **證據**：單檔遠超專案 800 行上限；無任何 blueprint/`create_app`；status poll 等瑣碎請求也付出 cv2/numpy/torch 載入成本。
- **建議（漸進、路徑不變、逐一重啟 smoke-test）**：
  1. `routes/fly_pet.py`（風險最低，先做）：移 17 routes + `_fly_pet_auth`；CDP helper 抽到共用 `routes/_cdp.py`。
  2. `routes/labeler.py`（自含鎖）。3. `routes/web_login.py`（保留 `_web_login_lock`）。4. `routes/worker_sync.py`（**必須 import 同一個 `_commands_lock` 物件，勿重建**，否則 data-race）。5. `routes/live_view.py`（**flask-sock 無 blueprint 註冊路徑**，`@sock.route` 須綁 app 物件於 `create_app()` 內）。6. `routes/control.py`。
  - **獨立小清理（各自一 commit）**：把 `831-835` 重 import 改為 `analyze_stage()` 內 lazy import（最便宜的單點勝利）；刪 `943` 冗餘 import。
- **工作量/風險/影響**：高 / 中 / 中。

### 7. carpark 場景樹 walk + worldToScreen JS 大量內嵌（cx-2，與 dup-6 合併）

> **去重註**：本項合併了 duplication lens 的「cocos 場景樹 DFS walk JS 跨 web 模組重抄」（dup-6）與 complexity lens 的「`carpark_auto.py` 內嵌 walk+worldToScreen 16/10 次」（cx-2）——兩者都是同一個 `find=(root,parts)` walker + worldToScreen 換算的重複。

- **位置（跨模組 walker，dup-6）**：
  - `farm_v2/web_farm.py:40-43,47-48,73-77`、`opengold_v2/ui_controller.py:459-471`、`sea_v2/rewards.py:61-70`
  - `utils/cocos_navigator.py:90-307`（**自身 4 份** `find=(root,parts)` 散在 `_CLICK_JS`/`_VIEW_STATE_JS`/`_DISMISS_TOP_POPUPS_JS`/`_FIND_CLOSE_BTN_JS`）
  - 真正合適的 home 是 `utils/cocos_navigator.py`（已擁有 `COCOS_PATHS`）；**非** `utils/web_game_api.py`（純 WS-RPC/protobuf，原 finding 誤指）
- **位置（carpark 內，cx-2）**：`utils/carpark_auto.py` find walker 16x、worldToScreen 10x，散在 `_click_pool_tier:363`、`_click_silver_lot_by_idx:420`、`_click_empty_spot_in_current_lot:454-512`、`_pick_zero_minute_car_and_park:559`、`claim_warehouse:945-981` 等（原列的 `recall_one_cross` 已於 2026-07-05 死碼清理移除）；`utils/carpark_state.py:283,311`（`_AVAILABLE_CARS_JS`/`_CAR_DETAIL_JS`）已抽成 module-level 常數，是 2-file single-source 問題。
- **證據**：worldToScreen 字面量在每處 byte-identical（`Math.round(r.left + wp.x*r.width/v.width)` / `Math.round(r.top + (v.height-wp.y)*r.height/v.height)`）；這正是 540x960 MEMORY note 警告的可攜座標單一真相風險。
- **建議（additive、逐點 live 驗證、勿大爆改）**：
  1. 新 `utils/carpark_js.py`（或在 `cocos_navigator`）放純字串片段 `_FIND_WALKER`、`_WORLD_TO_SCREEN`，及 `node_world_pos(page, path)` helper（鏡像 `ui_controller._WORLD_POS_JS`，含 `convertToWorldSpaceAR` fallback）。
  2. **先重構 `cocos_navigator` 自身 4 份內部副本**（內部、行為保持、先證明 helper）。
  3. carpark：加 `_node_screen_coords(page, path_parts, ...)`，`path_parts` 以 **JS 陣列參數**傳入（非 f-string 內插，順帶消 `{{}}` 跳脫脆弱性）；逐點遷移、每點先 log 新舊座標 assert 相等再刪內嵌。
  4. 統一 walker 的 `activeInHierarchy` vs `active`（各副本分歧）。**勿**在同一 pass 動 `farm_v2/web_farm`、`sea_v2/rewards`（其 JS 與任務專屬抽取邏輯糾纏，字面 swap 有座標回歸風險）。
- **工作量/風險/影響**：L / MEDIUM / 中（**熱路徑**：餵真實點擊/停車金錢動作，離線難測）。

### 8. carpark `park_one_silver` 重試迴圈內嵌整套 UI 重導，深層巢狀 + 複製貼上回復塊（cx-4）

- **位置**：`utils/carpark_auto.py:657-751`（~95 行）。
- **證據**：`for pass_no in (0,1)` → `for idx in order` → 多 `if`，其中兩個分支**逐字重複**同一 6 行回復序列 `_ensure_parking_main_open → _open_space_view_and_cross_tab → _click_pool_tier`（`725-730` 與 `734-739`）。
- **建議**：抽 `_reenter_silver_detail_list(page, pool_id) -> bool`，兩分支改 `if not _reenter_...: return None`。（選做）把每格評估抽成回傳 enum `FULL/NO_CLUSTER/PARKABLE/UNREADABLE` 讓迴圈扁平化——但會動到 fast-path-skip 與 pass_no/cluster 兩遍語意，live bot 上延後。以 `tests/test_carpark_auto.py` 既有 pinning tests 驗證。
- **工作量/風險/影響**：M / LOW / 中（>4 層巢狀違反專案規則；單點修改回復邏輯）。

### 9. `reconcile()` 在 carpark 混 orchestration + 內嵌 JS + summary dict + try/finally（cx-7）

- **位置**：`utils/carpark_auto.py:1151-1281`（131 行）。
- **建議**：1.（最高價值最低風險）把 nested closure `_build_snapshot_summary`（`1198-1209`，被 `1212`/`1277` 呼叫、無自由變數）提升為 module-level 純函式。2.（選做）抽 `_build_report(...)` 純 dict builder。**勿**抽純 `_reconcile_plan`（cross-park 迴圈 `1237-1251` 每次 `take_snapshot(page)` 重讀 live page，無法純化）；可改 `_apply_cross_parks(...)` 隔離迴圈。**勿**動 `try/finally` 的 `_return_parking_to_main`（`tests/test_carpark_auto.py:189` 覆蓋）。**勿**把 inline btnBack JS（`1180-1184`）耦合進尚未建好的 `_node_screen_coords` helper（屬第 7 項範圍）。
- **工作量/風險/影響**：低-中 / LOW / 低-中。

### 10. `PlaywrightGameDevice._start` 一個 ~150 行方法做 5 件事（cx-5）

- **位置**：`device_wrapper.py:553-704`；重複的 path 正規化在 `control_panel_app.py:418-444`（`_resolve_web_profile_dir`/`_resolve_web_state_file`），呼叫點 `638-645,1572-1574`。
- **建議**：
  1.（低風險高價值）把 `control_panel_app` 那兩個純 helper 提到新 `utils/web_profile_paths.py`，兩邊共用。`_start` 內 `562-575`/`578-589` 改呼叫；**保留** device_wrapper 專屬副作用（`os.makedirs`）於純 helper 之外。**注意保留差異**：control_panel 版有 `os.path.normpath()`、device_wrapper 版沒有——統一採 normpath 形式，並加單測 assert 兩路徑對同一 device_id 一致。
  2.（中風險，動 live launch）抽 `_launch_persistent_context_with_fallback(profile_dir, channel)`，把 nested closures `_clear_chrome_singleton_locks`/`_build_launch_kwargs` 升為 method。**純 code-motion**：勿改 launch kwargs、3-element attempt 順序、15000ms timeout、storage_state 語意。
- **工作量/風險/影響**：中（拆成低+中兩步）/ 低-中 / 中（path drift 致 manual-login 與 runtime profile 解析到不同目錄）。

### 11. OCR/文字正規化詞表與技能解析三/四份重抄（cx-6）

- **位置**：`ocr_server.py:270-391`、`Open_gold_paddle_ocr.py:153-217`+`642-651`、**`opengold_v2/config.py:146-225`（V2 已集中，原 finding 漏掉）**、`utils/web_game_api.py:94-98`（`EQUIP_AFFIX` 數字協議 ID，不同 namespace，**非真重複**）。
- **證據修正**：`PAIR_REWRITE` 在 ocr_server 與 V1 byte-identical；但「server 判 unwanted、client 判 wanted 致 lamp 決策不一致」的 bug **不存在**——server `is_unwanted` 無消費者，兩 lamp 路徑都在本地由 `OCRParser`/`is_unwanted_combo` 重算（V2 讀 `OpenGoldConfig`）。真實危害僅 OCR-misread `REPLACEMENTS` 漂移（維護性），非決策正確性。
- **建議**：`OpenGoldConfig`（`config.py:146-225`）**已是**單一真相，**勿**新建 `affix_tables.py`。唯一有價值且較低風險的改動：讓 `ocr_server.py` 從共用常數模組 import 詞表。V1 `Open_gold_paddle_ocr.py` 屬死碼（見第 14 項），單獨排程移除。`web_game_api.EQUIP_AFFIX` 維持獨立。
- **工作量/風險/影響**：M / LOW（自 medium 降級）/ 中（維護漂移）。

### 12. Flask 路由 21x `try/except → jsonify error 500` 封套 + 5x CDP error→HTTP-code 映射（cx-1）

- **位置**：`control_panel_app.py`：21 次 `return jsonify({"status":"error","message":str(e)}),500`（代表行 `1048`）；CDP `code = 400/502/500` 映射 5 次：`2009`（`_cdp_json_response`，正規）、`2269`/`2341`/`2357`/**`2450`（原 finding locations 漏列的第 4 個 hand-rolled caller `fly_pet_refresh_breed`）**。
- **建議**：
  1.（低風險，先做）抽 `_cdp_action(ip, js)`：4 個 fire-and-forget route（皆丟棄 `result`）改 `return _cdp_action(ip, js)`；共用 `code` 映射抽成 `_cdp_err_code(err)`，與 `2009` 共一來源。
  2.（選做、較高風險）`@json_endpoint` decorator 套 21 處——但會同時改多 endpoint 的 traceback surface，**勿一次掃全部**，需停 bot 分批；**勿**與第 1 步綁同一 commit。
- **工作量/風險/影響**：medium / low / 中（錯誤處理漂移：改一處漏其他 20 處）。

### 13. V1 神燈 `open_the_gold` 等深巢 god-flow（已棄用但 finding 誤判仍 live）（cx-3）

> **重新定性**：原 finding 把這當「live 路徑的複雜度重構」，**前提錯誤**。實際 `lamp_scheduler.py:30` 無條件用 `opengold_v2.LampService`，router 已不讀 flag。此項實為**死碼清理 + 修 stale doc**。
>
> **進度（2026-06-07，已做）**：`use_opengold_v2` flag 已從 config schema (`config_manager.py` bool 清單)、`bot_config.json`（7 台）、儀表板 checkbox + load/save 全面移除；`Open_gold_paddle_ocr.py:1-13` stale banner 與 `tests/test_lamp_scheduler.py` 模組 docstring 已更新。**剩餘待辦**：V2 prod log 確認穩定後刪 4 個 V1 函式 + `__main__`、退休 `lian_shan_example.py`。

- **位置**：`Open_gold_paddle_ocr.py:1080-1153`（`open_the_gold`）/`1155-1199`/`1286-1320`/`1322-1362`；**stale banner** `1-13`（仍寫 `use_opengold_v2=false`/`3/6 裝置`——即誤導來源）；stale test docstring `tests/test_lamp_scheduler.py:9-11`。
- **建議**：（1）**勿**翻任何 flag。（2）修 `Open_gold_paddle_ocr.py:1-13` stale banner。（3）確認 V2 prod log 穩定後，刪 4 個 V1 函式 + `__main__`，更新 stale test docstring，退休 `lian_shan_example.py`。
- **工作量/風險/影響**：低-中 / **LOW**（自 medium 降級；live thread 無 import，只剩自身 `__main__`/未 import 的 `.tmp_codex` 備份/docs/tests）/ 中（移除平行維護負擔）。

---

## 效率（GPU / 截圖 / OCR / 輪詢）

> 這是分支 `perf/reduce-gpu-usage` 的**當前主題**。以下多項是熱路徑，預期效益明確。

### 14. 頁面 CNN 無 `map_location`/`.to(device)`，共用頁面分類器跑 CPU（eff-0）

- **位置（修正：runtime 檔是 `new_cnn/`）**：`new_cnn/cnn_model.py:75-80`（`load_cnn_model`）+ `36-58`（`predict_image`，**連 `inference_mode` 都沒有**）；`new_main_v2.py:39,498`（載一次扇出每 thread）。root `cnn_model.py:74-79` 同缺陷但僅 `Store.py`/`fight_car.py` 用。正確參考：`miner/models/classifier.py:30-49`。
- **建議**：修 `new_cnn/cnn_model.py`：`load_cnn_model` 加 `map_location=device`+`model.to(device)`+stamp device；`predict_image` 包 `torch.inference_mode()` 並 `image.to(next(model.parameters()).device)`。**分支感知：勿預設 cuda**，預設 CPU（或讀 `global.compute`），避免把高頻頁面檢查靜默搬上 GPU 違背分支目標。加 `inference_mode` 是單點最高價值最低風險。**勿**遷頁面 caller 到 `miner.models.classifier`（class 不同，非 drop-in）。
- **預期效益**：明確 device 放置、消除 `new_cnn` 缺 `inference_mode` 建 autograd graph 的浪費。
- **工作量/風險/影響**：S / 中 / 高（模型小，影響評為略高估）。

### 15. `park.goto_park` 30 秒 busy-wait（無 sleep）（eff-1）

- **位置**：`park.py:428-434`（`goto_park`）。
- **證據**：`while (time.time()-start_time)<30:` 內每輪 `self.device.screenshot(format='pillow')` + `cnn_model.predict_image(...)` 無 `sleep`，找不到 `homeplace` 時以 CPU/GPU/ADB 全速跑滿 30 秒；對照 `game_actions/daily_tasks.py:33`(sleep 1.5)、`game_initialization.py:457-472` 都有 sleep。
- **建議**：迴圈尾（not-found 分支後）加 `time.sleep(1.0)`，把幾百次壓到 ~30 次。保留 30s timeout 與 `homeplace` break。**勿**「重用既有截圖」（迴圈前未截圖，無可重用）。
- **預期效益**：單裝置 CPU/截圖/推論 spike 大幅下降，一裝置一 thread 跨裝置放大。
- **工作量/風險/影響**：S / LOW / 高。

### 16. `oracle()` 每輪 2x `predict_image` + 3 截圖（1 個未用）（eff-2）

- **位置**：`game_actions/miner_action.py:28-40`。
- **證據**：`29` `img = d.screenshot(format='opencv')` 從未使用；`33-34` 連兩次 `predict_image(...)` 各自重新截圖。單輪 3 截圖 + 2 forward，1+1 即夠；每次 oracle() 最多 5 輪（每次挖礦前呼叫）。
- **建議**：截一次、呼一次存 `prediction`、`print`→`logger.info`、刪 `29` opencv 截圖。`predict_image` 純函式，安全。
- **預期效益**：此迴圈 CNN forward 與截圖 ~3x 降。
- **工作量/風險/影響**：S / LOW / 中。

### 17. `get_stage` 對同一幀跑 3 次（最多 4 次）OCR HTTP（eff-3）

- **位置**：`game_state/detector.py:114-141`（`120` roi_announcement、`125` roi_parking、`130` full-frame）；`stage_by_str` 公告路徑 `61-97` 另有第 4 次 OCR（`64`）。`navigation.py:96-99` while 迴圈每輪呼叫一次。
- **證據**：full-frame OCR 已含公告與車位倉庫字串（`stage_by_str:25-31,61` 即 key off full text），2 個 ROI pass 是同幀多餘 round-trip。常見導航路徑（往主頁/未知）= 3 次，公告 in full text 再 +1。
- **建議**：full-frame `analyze_skill_via_http(img)` 先跑一次，取 text+bbox；把結果 thread 進 `stage_by_str`（新增 optional 參數，default None）消第 4 次；公告位置守衛統一用 bbox `X>155`（`stage_by_str` 已偏好的規則），**勿**假設 hardcoded ROI 與 bbox 門檻可互換。3-4 次 → 1 次。先以 detector fixture 單測鎖定。
- **預期效益**：每 get_stage OCR round-trip 3-4→1（導航 while 迴圈每輪都付）。
- **工作量/風險/影響**：M / 中（早 return 排序變動需 fixture 測試覆蓋）/ 高。

### 18. `config_manager.load_config` 每次重讀+重寫 NAS、無快取（eff-4）

- **位置**：`config_manager.py:278-358`（無快取，缺 keys 時 `339-342` 重寫整檔 + `348` `_write_backup`）；**`201-210` `_write_backup` 在每次成功 load 且 `devices` 非空時再寫第二個 JSON 到 NAS**（穩態 = 每次 read+write）；`new_main_v2.py:102-118`（thread 起始連呼 3 次）。68 個呼叫點funnel 此處。
- **建議**：在既有 `_config_lock`（RLock，`:15`）內加 process-wide **mtime（`st_mtime_ns`）快取**：命中回 `copy.deepcopy(cached)`（caller 會 `.update()`/`.copy()` 變動，**必須回 copy**）。auto-complete 重寫 + `_write_backup` **只在 cache miss 跑**（這才是消除 per-call NAS 寫的關鍵）。寫入路徑（`420/474/542`）結尾把 cached mtime 設 None 強制重讀。自我重寫後更新 cached mtime（避免迴圈）。parse-failure self-heal 路徑勿快取。
- **預期效益**：每次呼叫從 NAS read+write → 1 次 `stat()`，仍能撿到外部編輯。
- **工作量/風險/影響**：M / 低（RLock 已序列化讀寫）/ 高（**熱路徑**）。

### 19. 每次 OCR 呼叫經 `get_ocr_config` 重讀 NAS config 2-4 次（最熱路徑）（eff-5）

- **位置**：`img_tools.py:271-286`（`_call_ocr_endpoint`：`275` timeout、`279` `_build_ocr_server_priority`、`285` verbose 再 resolve）、`54-64`、`67-97`（`89` 又一次）；底層 `config_manager.load_config`（未快取，含 `_write_backup` NAS 寫）。`miner/core/ocr_utils.py:89,119,132` 每輪呼叫。
- **證據**：非 verbose 每次 OCR resolve config 3 次、verbose 4 次；每次都觸發未快取 `load_config`（NAS read + json.load + auto-complete + backup write）。OCR server list/timeout 執行期幾乎不變。
- **建議**：**直接修 `load_config` mtime 快取（第 18 項）即連帶解決**，零 call-site 改動。**勿**在 `img_tools` 另加 TTL 快取（冗餘且有 staleness 分歧風險）。以 `tests/test_ocr_utils.py` 驗證。
- **預期效益**：每 OCR 請求的 NAS round-trip 完全消除（與第 18 項共同）。
- **工作量/風險/影響**：S / 低 / 高（**最熱路徑**）。

### 20. `InferenceGate` 序列化挖礦 CNN，但頁面分類器繞過它（eff-6）

- **位置（修正：主 runtime 檔是 `new_cnn/`）**：`new_cnn/cnn_model.py:51-56`（**bare `model(image)`，無 `inference_slot` 也無 `inference_mode`**；被 `new_main_v2`、`game_initialization.py:461/481`、`park.py:430`、`daily_tasks.py:36`、`miner_action.py`、`family.py`、`farm_v2/manager.py:69` 用）；root `cnn_model.py:50-53`（有 `inference_mode` 缺 `inference_slot`；`Store.py`/`oralce_manger.py`/`fight_car.py` 用）；`utils/torch_runtime.py:35-71`（正確）；`miner/models/classifier.py:143-145`（已正確 gate）；`new_main_v2.py:486-490`（wiring）。
- **證據**：挖礦 `classify_board` 已包 `with inference_slot(), torch.inference_mode()`，但頁面分類器未 acquire `inference_slot()`，多裝置在同一 :00-:20 醒來窗時頁面 forward 不被 gate 序列化，部分抵銷 InferenceGate 設計。
- **建議**：兩個 cnn_model 都改 `with inference_slot(), torch.inference_mode():`（`new_cnn` 順帶補上缺的 `inference_mode`）。單圖 128x128 SimpleCNN forward 極便宜，序列化延遲可忽略。**勿**為補償而放寬 `set_inference_concurrency`。`py_compile` 兩檔 + 跑 `tests/test_torch_runtime.py`。（與第 14 項可一起改 `new_cnn/cnn_model.py`。）
- **預期效益**：消除多裝置同醒窗的頁面 forward 並發 GPU/CPU spike。
- **工作量/風險/影響**：S / 中 / 中。

---

## 倉庫清理（cruft）

> 共同安全註：以下絕大多數**已被 .gitignore 涵蓋**或**未被追蹤**，commit 風險低；主要成本是 SMB 掃描/磁碟。任何 `git rm --cached`/刪檔/移動都需配套更新指向它的 doc/notebook，且 live-tree 掃描須等 bot 停機。

### scratch 腳本（cruft-0）

- 7 個 git-tracked、無 live import：`test.py`、`quick_test.py`、`dashboard_test.py`、`fix_prints.py`、`benchmark_screenshot.py`、`lian_shan_example.py`、`spin_and_send_gold_single_runner.py`。
- **處置**：
  - **delete**：`fix_prints.py`（一次性 codemod，已執行）。
  - **delete + 修 doc**：`quick_test.py`（import 已不存在的 `core/screenshot_manager.py` 等；同時修 `測試使用說明.md:7-16` 那段誤導指引，含一個它根本不收的 IP CLI arg）。
  - **move → `tools/scratch/`**：`test.py`、`dashboard_test.py`（更新 `OPTIMIZE_utilities.md`/`.planning/codebase/TESTING.md` 連結）。
  - **move → `tools/`**：`benchmark_screenshot.py`。
  - **move → `docs/examples/`**：`lian_shan_example.py`。
  - **勿裸移** `spin_and_send_gold_single_runner.py`：`spin_and_send_gold_single.ipynb` 硬 assert 其路徑，移動須同 commit 改 notebook RUNNER 路徑（或留在 root）。
- 風險：低（皆非 runtime import）；唯一危害是 dangling doc/notebook 連結。

### root `test_*.py` 污染 pytest 收集（cruft-1）

- 7 個 root `test_*.py`，無 root pytest 設定。多數會在收集時連真機/OCR（`test_mount_rush.py` `u2.connect`、`test_minigame_ocr.py` `u2.connect('emulator-5556')`、`test_server_brain.py` 缺 fixture、`test_stage_debug.py` argparse CLI、`test_opengold_v2.py` import-smoke）。
- **處置（首選、單檔一次解決）**：加 root `pyproject.toml` `[tool.pytest.ini_options]` `testpaths=["tests"]`、`python_files=["test_*.py"]`。觸碰零追蹤程式碼、不影響 live bot。
- **修正**：`test_item_placement_guards.py` 是**真單測**（純 `miner.executor` 邏輯，**勿改名**）——後續移進 `tests/`。其餘 5 個 device/debug 腳本拿掉 `test_` 前綴移 `tools/debug/`。
- 風險：中（現有 `check_pytest.py` hook 擋本機 bare pytest，但不防 CI/他環境收集；root 設定才是耐久修法）。

### 已追蹤的 `.pyc`（cruft-3）

- 確切 4 個：`tests/__pycache__/conftest.cpython-310-pytest-9.0.2.pyc.{30276,32684}`、`conftest.cpython-312-pytest-7.4.4.pyc.{197400,228420}`（已 `git ls-files` 確認）。
- **處置**：`git rm --cached` 這 4 個並 commit。**無需**新 gitignore 規則——`.gitignore:30 **/__pycache__/*` 已涵蓋（`git check-ignore --no-index` matched）。**勿**手刪 working-tree 檔、**勿**動 `tests/conftest.py`。
- 風險：低（無程式碼引用，零風險）。

### sync-conflict 累積（~34,976 檔）（cruft-6）

- 90% 集中在**孤兒 worktree 目錄**（非註冊 git worktree，無 `.git`）：`.claude/worktrees/agent-add41fa601a0feb15`、`agent-a4e4ef0d81216ca13`、`agent-a73abce22b7ff9a31`、`.worktrees/refactor-safety-cleanup`、`.worktrees/clean-dedup`（各帶複製的 `miner/dataset` 樹）。其餘在 live tree：`playwright_profile/emulator-5554/Default/Sessions`(1119)、`logs/_archive/ws_capture/auto/emulator-5556`(433)。全部 0 tracked，`.gitignore:25 *.sync-conflict-*` 已防 commit。
- **處置**：
  - **Phase 1（現在安全，~31k 檔）**：孤兒目錄用一般目錄刪除（`Remove-Item -Recurse -Force`）——它們**非**註冊 worktree，`git worktree remove` 會失敗。**勿**動真正註冊的 `miner-reverse-search`/`.worktrees/bugfix`/`.worktrees/feature`。
  - **Phase 2（~3.6k 檔，等 bot 停機）**：live-tree sweep。**勿**在 bot 跑時 `find . -iname '*sync-conflict*' -delete`——衝突檔在 active Playwright profile/log 內，會 profile 損毀/寫競爭。
  - 源頭：把 repo（或至少 `miner/dataset/`、`playwright_profile/`、`logs/`）加進 Syncthing `.stignore`。
- 風險：Phase 1 低；Phase 2 中（gated on bot 停機）。

### 垃圾目錄（cruft-4）

- 未追蹤 scratch 目錄；內容皆已被全域副檔名規則 ignore（`*.jpg/*.png/*.txt/*.pyc/*.sync-conflict-*`），**`git add .` 風險實質為 0**（原 finding 高估）。
- **處置**：
  - **刪空目錄**：`found_matches/`（`img_tools.py:422-426` 會按需重建）、`'2026-01-20 195013'/`。
  - **刪**：`trash/`（38 個 .pyc 的手動傾倒區）。
  - **可選 cosmetic**：在 .gitignore 加 `ocr_fails_new/`、`ocr_errors/`、`ocr_incomplete/`、`found_matches/`、`trash/` 及時間戳 glob `20??-??-?? ??????/`（皆未追蹤，加 ignore 不改 index）。
  - **勿 purge live capture 目錄**：`ocr_fails_new/`(17.7k) 是 active labeler 來源（`config/paths.py:8-9`、`ocr_server.py:19`、`control_panel_app.py:118`、`tools/daily_ocr_fail_labeler.py:427`）；`ocr_errors/`/`ocr_incomplete/`/`found_matches/` 由 runtime 寫。要省磁碟改加保留天數上限，勿盲刪。
- 風險：低（cosmetic/掃描成本層級）。

### 重複頂層腳本（cruft-8 — 反刪除守衛）

- **KEEP**：`park.py`、`new_park.py`、`BUY.py`、`mask.py`、`device.py` 全在 live call graph（`new_main_v2.py:17 from park import *`；`park.py:453` lazy `import new_park` → `new_park.py:244 new_park_way`）。`new_park.py` **非** `park.py` 的 stale 副本，提供 `park.py` 消費的 RGB-sampling helper。
- **處置**：不刪、不重構。唯一真正可刪的是空 `main.py`（下方已棄用路徑段）。
- 風險：低（此項目的在防止未來 agent 誤刪 live code）。

### 已棄用路徑（cruft-2 + cruft-5）

- **空 `main.py`（cruft-2）**：0 bytes，首 commit 起即空、無 import。`git rm main.py` + 移除 `SCRIPT_ARCHITECTURE.md:19` 那條把 main.py 列為入口的 bullet。`OPTIMIZE_*.md` 的歷史引用不動。
- **`miner/rl/rl_logs/`（cruft-5）**：14 個全是 `*.sync-conflict-*` NAS 殘渣（已被 `.gitignore:25` ignore，無 commit 風險），CLAUDE.md 明令勿復活。`Remove-Item -Recurse -Force 'miner/rl/rl_logs'` 安全（0 tracked、無 live `events.jsonl`）。可選在 .gitignore 加 `miner/rl/rl_logs/` 守衛。無需改碼：`RLRecorder.log_dir` 是必填位置參數（`miner/rl/rl_recorder.py:14`），兩 live writer（`new_main_v2.py:181`、`manager_factory.py:41`）已用正規 `miner/rl_logs/`。
- 風險：低 / XS。

---

## 與現有 OPTIMIZE_*.md 的關係

| OPTIMIZE 文件 | 關鍵建議是否仍未實作（still_valid） | 與本報告重疊 | 本報告新增 |
|---------------|--------------------------------------|--------------|------------|
| `OPTIMIZE_game_automation.md` | 大多未實作（HomePageVerifier/screen_profile/任務契約/表驅動 battle/統一 recovery 皆無；Mission.py 仍 5 個 print） | dup-0 主頁守衛、dup-4(已排除)、cx-6 詞表 | 跨檔 capture_screenshot 5 份的精確去重路徑（保留各迴圈語意） |
| `OPTIMIZE_main_architecture.md` | 混合：Phase 1 拼字/重複定義已清；P0/P1 結構級（bot_state 套件、DeviceLifecycle、device_wrapper 拆分、config-driven 裝置判斷）多未做 | cx-5 `_start` 拆分、dup-3 device-id、eff-4 config 快取 | `_start`↔`control_panel` path 正規化去重；config mtime 快取的精確 lock-aware 作法 |
| `OPTIMIZE_ocr_system.md` | 核心「統一後處理層 `ocr_postprocess.py`」未實作（檔不存在） | cx-6 詞表三/四份、eff-5 OCR config 重讀 | 指出 V2 `OpenGoldConfig` 已是現成 single source（勿新建檔）；澄清 server `is_unwanted` 為死碼（無決策漂移 bug） |
| `OPTIMIZE_opengold_v2.md` | 混合：兩個 P0 已修；P1 座標配置化僅部分（`ui_controller` 仍 inline 硬編，與 config 重複真相） | cx-3 V1 死碼、dup-1 world_to_pixel | V1 已非 live（修正 stale banner）；`ui_controller` world_to_pixel 改呼叫正規 helper |
| `OPTIMIZE_push_project.md` | P0 安全至少兩項已修（debug=False、bind 127.0.0.1）；API 鑑權/VAPID log/前端 XSS 多未驗 | （無直接重疊；push_project 為獨立子專案） | 本報告聚焦 bot 本體；push_project 安全項仍以該文件為準 |
| `OPTIMIZE_utilities.md` | 混合：低風險清理（serve bind、img_tools 死碼、mask 死碼）已做；模組拆分（img_tools/、control_panel/）未做 | cx-0 control_panel 拆分、cx-1 Flask 封套、eff-3 get_stage 3xOCR | control_panel blueprint 的精確錨點與 flask-sock/lock 陷阱；get_stage 4 次 OCR 合併（含 stage_by_str 第 4 次） |

---

## 建議執行順序

> 每個 Phase 完成後都需獨立審查 + 重啟 `new_main_v2.py`。Phase 間勿混入同一 commit。

### Phase 0 — 安全速贏（cruft / gitignore / 死碼）

零或近零 runtime 風險，先做：

- `git rm --cached` 4 個 `tests/__pycache__/*.pyc.NNNNN`（#1）。
- `git rm main.py` + 修 `SCRIPT_ARCHITECTURE.md:19`（#2）。
- 處理 `new_stage_check()` 死碼（零呼叫者，刪或修為 `all(...)`）（#3）。
- scratch 腳本移動/刪除 + 配套 doc/notebook（cruft-0）；加 root `pyproject.toml` `testpaths=["tests"]`（cruft-1）。
- 刪孤兒 worktree 目錄（cruft-6 Phase 1，~31k 檔）；刪空目錄/`trash/`（cruft-4）；刪 `miner/rl/rl_logs/`（cruft-5）。
- cosmetic gitignore 補充（cruft-4）。**勿**動 live capture 目錄、**勿** live-tree sync-conflict sweep（等 bot 停機 = cruft-6 Phase 2）。

### Phase 1 — 低風險去重（shared helpers）

- `LogPaths.safe_device_id()` 取代 2 處 device-id 正規化（#12 / dup-3）。
- `world_to_pixel` helper 取代 `ui_controller` 手算（#13 / dup-1 STEP 1）。
- `utils/json_io.read_json_bom_safe` 導向 2 個 runtime 讀點（dup-5）。
- per-device `{ip}.json` 改走 `JsonDataManager`/`_atomic_write_json`（#14 / dup-2，含 Mission 保 flat schema）。
- （可選）主頁像素 matcher 共用（dup-0，先換 device.py/Mission.py）。

### Phase 2 — 效率（GPU / OCR / 截圖）

對齊分支 `perf/reduce-gpu-usage`。**熱路徑，逐項 fixture/單測 + live 驗證**：

> ✅ 本 Phase 已全數實作完成（2026-06-04，見上方「已完成進度」）：config 快取 / park / 頁面 CNN 已 commit `4d2766e3`；`oracle()` / `get_stage` 在工作區待 commit。

- `config_manager.load_config` mtime 快取（#5 / eff-4）→ 連帶解決 OCR config 重讀（#19 / eff-5）。
- `park.goto_park` 加 `sleep`（#7 / eff-1）；`oracle()` 截一次推一次（#6 / eff-2）。
- `get_stage` 合併 OCR 為 1 次（#17 / eff-3，先鎖 fixture 測試）。
- 兩個 `cnn_model` device-aware + `inference_slot()`+`inference_mode()`（#8/#9/#14/#20 = eff-0+eff-6，**勿預設 cuda**）。

### Phase 3 — 高風險拆分

每項各自一輪審查、可考慮停 bot 視窗：

- `control_panel_app.py` blueprint 漸進拆分（cx-0，path 不變、逐 blueprint smoke-test；先 `fly_pet`）；附帶 lazy import / 冗餘 import 清理。
- Flask 封套 + CDP `_cdp_action` 抽取（cx-1，先做 4 個 CDP route，`@json_endpoint` 延後分批）。
- carpark JS walk + worldToScreen 共用化（cx-2/dup-6，**熱路徑**，先重構 `cocos_navigator` 自身，再逐點 carpark）。
- carpark `park_one_silver`/`reconcile`/`_start` 分解（cx-4/cx-7/cx-5，以 pinning tests 守護）。
- V1 神燈死碼移除（cx-3，待 V2 prod log 確認穩定）；OCR 詞表向 `OpenGoldConfig` 收斂（cx-6）。

---

## 已排除 / 存疑

- **OCR 點字 + 重試 + 退避迴圈在多任務手寫（dup-4，is_real=false）**：前提不成立。`time.sleep(min(2.0, 0.4*attempt))` 這套退避簽名全 repo **僅 1 份**（`battle/_helpers.py:107`）；所謂「13 檔叢集」是 grep「同檔同時含 `range()` 與 `click_str_by_server`」拼出的，實際迴圈語意各異（swipe-repeat / 點同座標 N 次 / 任務計數 / family 領取 drain）。唯一可做的無行為改動小事是把 `_safe_click_step` 改公開名，**勿**把異質迴圈統一改寫（live bot 回歸風險）。
- **`tools/_probe_out` / `.gitnexus` 未 ignore（cruft-7，is_real=false）**：中心主張錯誤。`git check-ignore -v tools/_probe_out` 命中 `.gitignore:81 tools/_*`，`.gitnexus` 命中 `:68`，兩者皆已被 ignore 且未追蹤——無 commit 風險，提議的「加 `tools/_probe_out/` 規則」是冗餘。僅剩可選的磁碟清理（非 git/程式碼缺陷）。

# 重構 / 去重 / 復用審計（2026-06-14）

> 補充而非取代 [`REFACTORING_OPPORTUNITIES.md`](REFACTORING_OPPORTUNITIES.md)（下稱 ROP）。本份只記**新驗證**的事實、**ROP 已 drift / 已部分完成**的修正，以及一個**已實作**的安全抽取。每項都標 file:line 證據、風險、是否觸及 live bot 熱路徑。
>
> 安全前提同 ROP：這是 LIVE bot，任何改動需人工審查 → 重啟 `new_main_v2.py` 才生效（`sys.modules` 快取）。本份所有「建議」項皆**尚未套用**，唯一已套用者為下方「本輪已實作」。

---

## 本輪已實作（DONE，TDD，未 commit）

### A. 分流 parity/offset parser 去重（ROP INDEX line 121「parser 有小重複」）

- **重複位置（驗證）**：
  - `runtime_services/sleep_service.py`（改前）`_parse_hour_parity`、`_parse_minute_offset`
  - `runtime_services/startup_sleep.py`（改前）`_parity_rank`、`_offset_rank`
  - 兩者核心解析（even/odd 字串映射、int 有效範圍、`bool` 拒絕）逐字相同；唯一差異是 unset 的 sentinel：sleep_service 回 `None`（= 無約束）、startup_sleep 回排序哨兵（parity→2 排最後、offset→0 排最前）。
- **抽取**：新 `runtime_services/wake_parity.py`（純函式、stdlib-only、不 import 本套件任何模組 → 無循環 import）。
  - `parse_hour_parity(value) -> Optional[int]`、`parse_minute_offset(value) -> Optional[int]` 為標準真相（unset = `None`）。
  - `sleep_service` 改 `_parse_hour_parity = parse_hour_parity`（保留私名，call site `:223-224` 與 `tests/test_sleep_service.py:141-149` 零改動）。
  - `startup_sleep._parity_rank/_offset_rank` 改成呼叫共用 parser 再把 `None` 映射回各自哨兵（`2` / `0`），排序語意 byte-identical。
- **為何選這項**：唯一一處兩個 live-bot 模組逐字共享的純邏輯；兩邊各自有完整 pinning tests（43 個）可當安全網；抽取為純函式、無副作用、無熱路徑語意改動（只在喚醒排程「決定 wake_ts」時各呼叫一次，非每幀）。
- **熱路徑**：否（每睡眠週期 / 每啟動一次）。**風險**：低。
- **驗證**：`tests/test_scheduling_parity.py`（新 10 測，含「兩 consumer 與標準值一致」「startup 哨兵保持」守衛）+ `tests/test_sleep_service.py` + `tests/test_startup_sleep.py` 共 **53 passed**；3 檔 + 測試 `py_compile` OK。
- **note**：`new_main_v2.py:84-90`、`ws_runner_service.py:378-379`、`ws_fallback_service.py:45` 只 import `run_sleep_cycle`/`_handle_startup_sleep` 等，**不**碰這些 parser，故抽取完全內含、無 re-export 破壞面。

---

## 對 ROP 的修正（已 drift / 已部分完成 — 驗證於 2026-06-14）

> 這些是 ROP 撰寫後（其基準 2026-05-31）程式碼已動、使該 finding 的 file:line 或狀態需更新者。**先看這裡再依 ROP 動手，否則會找錯行**。

### M1. `control_panel_app.py` 2576 行 god-module 已拆 blueprint（ROP cx-0 / #16、cx-1 / #12、cx-5 / #10 部分作廢）

- **現況**：CLAUDE.md / git status 顯示 `control_panel_app.py` 已是 thin façade，routes 移入 `control_panel/`（`routes_status/control/config/worker/web_session/live_view/labeler/fly_pet/pages` + `shared/`）。
- **drift 證據**：ROP cx-5 / #10 指 `_resolve_web_profile_dir`/`_resolve_web_state_file` 在 `control_panel_app.py:418-444`；實際現在唯一定義處是 `control_panel/routes_web_session.py`（grep 確認）。
- **影響**：ROP 的 cx-0（god-module 拆分）大致**已完成**；cx-1（21x Flask error 封套 + CDP code 映射）與 cx-5（`_start` ↔ control_panel path 正規化去重）的「control_panel 端」錨點全部失效，需在 `control_panel/` 子模組重新定位後才能評估是否仍有重複。
- **本輪不動**：這些檔案正由其他 worker 編輯（任務約束），僅標記為「ROP 對應項需重新定位」。

### M2. device-id sanitizer 去重（ROP dup-3 / #12）— 原 2 處已修，出現新第 3 處

- **原 finding 已完成**：`new_main_v2.py:236`、`game_actions/manager_factory.py:42` **都已**改用 `LogPaths.safe_device_id(ip)`（grep 確認）。dup-3 的主體已被處理。
- **新驗證的殘留 drift**：`miner/v5/priors_runtime.py:93`
  `str(device).replace(":", "_").replace(" ", "_").replace("/", "_")` — 比 `LogPaths._safe()`（只換 `:` 與空白）多一個 `/` 替換。
  - **不能直接 swap**：`safe_device_id()` 不換 `/`，硬換會改變含 `/` 的 device id 輸出（行為飄移）。
  - **建議（opt-in，低優先）**：若 priors_runtime 的 device 來源確定不含 `/`（emulator-序號 / ADB id 都不含），可改 `LogPaths.safe_device_id(device)`；否則先在 `LogPaths` 加一個明確的 `safe_path_segment()`（換 `:`、空白、`/`）再讓兩邊共用。**不建議**為單一呼叫點改 `LogPaths` 契約。
- **熱路徑**：否（v5 priors 寫檔路徑，每 session 少數次）。**風險**：低。

---

## 新驗證的去重 / 復用機會（ROP 未涵蓋或描述不足）

### N1. `runtime_services` 內 30-min 避讓休眠常數重複硬編

- **現況**：`StartupBypassError`（`sleep_service.py:33`）觸發的 30-min sleep 與 `LoginConflictError` 的 30-min sleep（CLAUDE.md「Login conflicts → 30-min device sleep」）為同一語義冷卻窗。值散在多處硬編（`1800` / `30*60`）而非單一具名常數。
- **建議（opt-in，XS）**：在 `runtime_services/` 放一個 `BYPASS_SLEEP_SEC = 30 * 60` 具名常數供各觸發點 import。**先 grep 確認所有 `1800`/`30*60` 出現點語義一致**（部分可能是別的 timeout），不可盲改。
- **熱路徑**：否。**風險**：低（純常數提取，須先確認語義一致）。

### N2. `battle/_helpers.py` 退避迴圈唯一真相應公開命名（承 ROP「已排除 dup-4」的唯一可做項）

- **現況**：`battle/_helpers.py:87` `_safe_click_step(...)` 是 repo 唯一的「點字 + 退避重試」實作（ROP 已查證 dup-4「13 檔叢集」為假，退避簽名 `min(2.0, 0.4*attempt)` 全 repo 僅此 1 份）。
- **建議（opt-in，XS）**：若要被別模組複用，把 `_safe_click_step` 改公開名（如 `click_with_backoff`）。**勿**把其他任務的異質迴圈統一改寫（ROP 已警告會回歸）。
- **熱路徑**：否（戰鬥任務）。**風險**：低（純改名 + 內部呼叫點同步）。

---

## ROP 既有項的現況快評（仍 valid，未重述細節）

下列 ROP 項經本輪抽樣確認**仍 valid 且 file:line 大致準確**（control_panel 相關除外，見 M1）；不重述，依 ROP 內文執行即可：

| ROP # | 主題 | 仍 valid？ | 備註 |
|---|---|---|---|
| dup-0 / #1（已合併入表外） | 主頁 9 點像素守衛 ×5 | 是 | `device.py` / `Mission.py` / `park.py` / `battle/manager.py` / `tools.py`；先換前兩份 |
| dup-1 / #13 | cocos `world_to_pixel` ×3 | 是 | `ui_controller` 手算改呼叫 `sea_v2.navigator.world_to_pixel`（保 `round`） |
| dup-2 / #14 | per-device `{ip}.json` 繞過 `JsonDataManager` | 是 | Mission 須保 flat schema；fight_car 用 `_atomic_write_json` |
| dup-5 | JSON 讀 BOM 不一致 | 是 | 新 `utils/json_io.read_json_bom_safe`，導 `json_manager/base.py` + `equipment_cache.py` |
| cx-2/dup-6 / #15 | carpark / cocos 場景樹 walk + worldToScreen JS 大量重抄 | 是 | **熱路徑**；先重構 `cocos_navigator` 自身 4 份 |
| cx-3 / #13(神燈) | V1 神燈死碼 | 是 | 待 V2 prod log 確認穩定後刪 4 函式 |
| eff-* / #4–#9,#17–#20 | GPU / OCR / 截圖效率 | 多已完成 | 見 ROP「已完成進度」；剩 `oracle()`/`get_stage` 工作區待 commit |

---

## 命名改進建議（全部 opt-in，**僅建議、本輪不改碼**）

> 使用者授權「命名可以改成更精確」。以下為「現名 → 建議名 + 理由」。改名須同步所有呼叫點 + 測試 + （部分）docs；改名屬跨檔，建議獨立一輪、停 bot 視窗，**勿與功能改動同 commit**。

| 現名 | 位置 | 建議名 | 理由 |
|---|---|---|---|
| `_parse_hour_parity` / `_parse_minute_offset`（shim） | `sleep_service.py` | （保留 shim 或直接呼叫 `wake_parity.parse_*`） | 已抽到 `wake_parity`；長期可移除 shim、call site 直接用公開名 |
| `oralce_manger.py` / `gold_mananer.py` | repo root | `oracle_manager.py` / `gold_manager.py` | 檔名拼字錯（ROP / INDEX 已記）；改名牽動 import，須全域同步 |
| `oracle()` | `game_actions/miner_action.py` | `predict_mining_entry()`（或註明回傳語義） | 「oracle」不表意；它是挖礦入口頁 CNN 判斷 |
| `new_cnn/` / `new_main_v2.py` / `new_park.py` | — | （暫不改） | 「new_」前綴已是事實標準名、改名 blast radius 過大，**不建議** |
| `STARTUP_SLEEP_SEC_BY_DEVICE` | `startup_sleep.py` | `STARTUP_STAGGER_OVERRIDE_SEC` | 它已是「override 表」（預設空、由 config 推導），名稱仍像「主要來源」 |
| `_safe_click_step` | `battle/_helpers.py` | `click_with_backoff` | 若要跨模組複用（見 N2） |
| `compute_stagger_order` | `startup_sleep.py` | （保留） | 名稱已精確，無需改 |

---

## 風險分組總覽（本份新增 / 修正項）

### 低風險（純函式 / 常數 / 改名，有測試或無語義變動）
- **A**（已實作）分流 parser 去重 — DONE, 53 tests green。
- N1 30-min 避讓常數提取（須先確認語義一致）。
- N2 `_safe_click_step` 公開命名。
- M2 priors_runtime sanitizer（須確認無 `/` 才能直接 swap）。
- 命名建議全部（但改名須獨立一輪）。

### 中風險（觸及 live 行為或熱路徑，須 fixture/pinning + live 驗證）
- ROP dup-2（`{ip}.json` 原子寫）、dup-5（BOM 讀）。
- ROP cx-2/dup-6（carpark/cocos JS walk，**熱路徑**）。

### 須等他 worker / 停 bot
- M1 control_panel blueprint 後續（cx-1 / cx-5 重新定位）— **其他 worker 正在編輯，本輪不碰**。
- ROP cx-3 V1 神燈死碼（待 V2 prod log）。

---

## 驗證紀錄（本輪抽取）

```
py_compile: runtime_services/wake_parity.py sleep_service.py startup_sleep.py
            tests/test_scheduling_parity.py  → OK
pytest: tests/test_sleep_service.py tests/test_startup_sleep.py
        tests/test_scheduling_parity.py      → 53 passed
```

行為等價：兩 consumer 的私名 parser / rank 函式輸出與抽取前 byte-identical（由 `test_scheduling_parity.py` 的「一致性 / 哨兵保持」測試守衛 + 既有 43 個 pinning 測試）。swap **保留**（未 revert）。

---

## 第二輪已實作（DONE，2026-06-14，未 commit）

> 安全、非熱路徑子集。每項行為等價、加測試、改動最小。`utils/*`、`miner/v5/priors_runtime.py`、`oralce_manger.py` owner 範圍內；未碰 `config_manager.py` / `game_actions/*` / `new_main_v2.py` / `ws_token/*` / control_panel。

### B. protobuf wire-walker 去重 → `utils/protobuf_walk.py`

- **重複位置（驗證）**：`utils/web_game_api.py` 的 `_read_varint` + `_walk_pb`（tuple 形 `(field, wire, value)`、未知 wire **raise**）與 `utils/redpack_detector.py` 的 `_read_varint` + `_walk_pb`（dict 形 `{"field","wire","value"/"bytes"}`、未知 wire / 截斷 **靜默停止**）為同一 varint reader + field-walk loop 的兩種尾巴。
  - 釐清：`utils/ws_listener.py` **不**用 `_walk_pb`（先前 grep 命中的是其 JS 字串裡的 `wire == 2`，非 Python）。`utils/equipment_cache.py` 是 `web_game_api._walk_pb` 的 consumer（import 進來），非第三份重複。`ws_token/codec.py` 另有一份 `walk`/`_read_varint`，但 ws_token **不在本輪 owner 範圍**，未動（見「deferred」）。
- **抽取**：新 `utils/protobuf_walk.py`（純函式、僅 stdlib `struct`）。
  - `read_varint(buf, off)` — 截斷 / overflow `raise ValueError`。
  - `walk_fields(data)` — **strict**：未知 wire `raise`（給 web_game_api）。
  - `walk_fields_lenient(data)` — **tolerant**：未知 wire / 截斷 len-delim / 截斷 fixed 皆乾淨停止回傳已解析部分（給 redpack）。
  - `web_game_api._read_varint = read_varint`；`_walk_pb` 薄包 `walk_fields`（保留私名 + tuple 形）。移除已不再用到的 `import struct`。
  - `redpack_detector._read_varint = read_varint`；`_walk_pb` 薄包 `walk_fields_lenient` 並把 tuple 轉回該模組歷史的 dict 形（wire 2 → `"bytes"`、其餘 → `"value"`）。
- **行為等價**：兩邊回傳 shape / 錯誤語意（raise vs 靜默停止）逐一保留；redpack 的截斷測試（claim 10 bytes 只給 3 → `[]`）仍綠。
- **熱路徑**：否（WS RPC 回應解析，非每幀）。**風險**：低。
- **驗證**：新 `tests/test_protobuf_walk.py`（24 測，涵蓋 strict/lenient + live redbag entry）+ `tests/test_redpack_detector.py` + `tests/test_equipment_cache.py` → **49 passed, 8 skipped**；4 檔 + 新測試 `py_compile` OK；直接 import 三模組並比對 `_walk_pb` 輸出形狀無誤。

### C. device-id sanitizer 漂移（承 M2）→ `LogPaths.safe_path_segment`

- **問題**：`miner/v5/priors_runtime.py:93` 手寫 `.replace(":","_").replace(" ","_").replace("/","_")`，比 `LogPaths._safe`（只換 `:`、空白）多換 `/`。不能直接換 `safe_device_id`（會丟 `/` 處理，且 `test_runtime_path_sanitises_separators` 斷言 `/` 必須被清掉 → 會 fail）。
- **做法（additive，未改既有契約）**：`utils/log_paths.py` 新增私函式 `_safe_segment`（`_safe` 之上再換 `/`）+ public `LogPaths.safe_path_segment(name)`；`priors_runtime.runtime_path` 改呼叫之。`_safe` / `safe_device_id` 既有行為**零變動**。
- **行為等價**：對 `emulator-5554` / `127.0.0.1:5555` / `a:b/c d` / `adb-fc65396d` 等樣本，新輸出與舊手寫 byte-identical（程式驗證通過）。
- **熱路徑**：否。**風險**：低。
- **驗證**：`tests/test_miner_v5_priors_runtime.py` → **15 passed**（含 `test_runtime_path_sanitises_separators`）；`py_compile` OK。

### D. 檔名改名 `oralce_manger.py` → `oracle_manager.py`（使用者授權）

- **importer 普查**：全 repo `*.py` **零** import 此模組（唯一 `from oralce_manger import oralce` 出現在 `easyocr_calls.log` 的**註解行**，非程式）。閘門（importer ∈ config_manager / new_main_v2 / game_actions）**未觸發** → 安全改名。
- **做法**：`git mv oralce_manger.py oracle_manager.py`（git 認得為 rename）。檔案內容 byte-identical（含內部 `oralce = oracle` 向後相容別名）。importer 更新數 = **0**。
- **驗證**：`oracle_manager.py` `py_compile` OK；`tests/` 無任一檔引用新舊檔名。
- **殘留（未改，超出本輪 code scope）**：docs（`OPTIMIZE_*.md`、本檔 line 89、`docs/INDEX.md`）仍寫舊名 `oralce_manger.py`；`easyocr_calls.log` 註解；`new_main_before20250514.py.tmp_codex` 等 `.tmp` 死檔。這些是文件 / 日誌 / 暫存，非 live import。

---

## Deferred — needs supervised pass（本輪**刻意不動**，高風險 / live 熱路徑 / 需監督審查）

> 以下項目已在審計中標出，但因觸及 live hot path、跨啟動/執行期狀態、或正由其他 worker 編輯，**不在本輪安全子集**。需停 bot + fixture/pinning + live 驗證的獨立一輪。

| 項目 | 位置 | 為何 defer |
|---|---|---|
| `device_wrapper.py` 內部重構 | `device_wrapper.py` | repo 最熱路徑（每幀截圖 / 點擊 / session 生命週期）；任何結構變動需 live 雙後端驗證 + pinning，且 `_WEB_DEVICE_LOCK` 必為 RLock 等隱形約束多。 |
| carpark / cocos JS 座標 + 場景樹 walk 合併（ROP cx-2/dup-6 / #15） | `utils/carpark_*.py` / `cocos_navigator` | **熱路徑**；JS worldToScreen / scene-walk 多份重抄，但每份座標基準有細微差異（viewport / round），盲合併易回歸。先重構 `cocos_navigator` 自身 4 份再外擴。 |
| Flask error-envelope 統一改寫（21×，ROP cx-1） | `control_panel/` 各 blueprint | 21 處 try/except → 統一封套 + CDP code 映射；錨點在 control_panel 重新定位後才能評估，且**該批檔案正由其他 worker 編輯**（本輪約束禁碰）。 |
| `STARTUP_SLEEP_SEC_BY_DEVICE` 常數改名 → `STARTUP_STAGGER_OVERRIDE_SEC` | `runtime_services/startup_sleep.py` | 觸及啟動/執行期排程；`startup_sleep.py` 本 session 已被另一 worker commit，改名牽動 import + config 推導路徑，需停 bot 獨立一輪。 |
| `ws_token/codec.py` 的 `walk`/`_read_varint` 併入 `utils/protobuf_walk.py` | `ws_token/codec.py` | 第三份 protobuf walker，**可**併（其 `walk` = `(field, value)` 形、未知 wire break，可由 `walk_fields_lenient` 適配），但 `ws_token/*` **不在本輪 owner 範圍**。留待 ws_token owner 一輪。 |
| `game_actions/miner_action.py` `oracle()` → `predict_mining_entry()` 改名 | `game_actions/miner_action.py` | 語義改名建議成立，但 `game_actions/*` 由 worker K 持有，本輪禁碰。 |
| `gold_mananer.py` → `gold_manager.py` 改名 | repo root | 同類拼字錯改名；本輪僅授權 `oralce_manger` 一檔，未一併做（需先普查 importer）。 |

### 第二輪驗證紀錄

```
py_compile: utils/protobuf_walk.py utils/web_game_api.py utils/redpack_detector.py
            utils/equipment_cache.py utils/log_paths.py miner/v5/priors_runtime.py
            oracle_manager.py tests/test_protobuf_walk.py            → OK
pytest: tests/test_protobuf_walk.py tests/test_redpack_detector.py
        tests/test_equipment_cache.py tests/test_miner_v5_priors_runtime.py
                                                                     → 64 passed, 8 skipped
```

行為等價佐證：protobuf walker 兩邊 shape/錯誤語意逐一保留（redpack 截斷測試綠）；sanitizer 對多樣本 byte-identical（程式比對）；改名零 importer、檔案內容不變。三項皆**保留**（未 revert）。

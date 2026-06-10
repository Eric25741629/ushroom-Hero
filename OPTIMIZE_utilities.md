# 工具與基礎設施優化建議

> 自動分析日期：2026-05-27
> 分析範圍：utils/、tools/、json_manager/、game_state/、game_actions/、runtime_services/、tests/ 及各獨立工具模組

---

## 一、架構總覽

```
┌──────────────────────────────────────────────────────┐
│                    daily_pipeline.py                  │
│              (20-task per-wake sequencer)             │
├──────────┬──────────┬──────────┬─────────────────────┤
│game_state│game_act. │json_mgr  │ runtime_services    │
│/detector │/scheduler│/base     │ /sleep /web /device │
├──────────┴──────────┴──────────┴─────────────────────┤
│    utils/ (27 modules) + tools/ (75+ scripts)        │
├──────────────────────────────────────────────────────┤
│  img_tools.py (OCR + template matching)              │
│  point.py / mask.py (legacy pixel detection)         │
│  control_panel_app.py (Flask dashboard)              │
│  config_manager.py (JSON config hub)                 │
└──────────────────────────────────────────────────────┘
```

---

## 二、工具函數品質分析

### 2.1 `img_tools.py` — 專案核心 OCR 管線

**現狀**：~500 行，同時承載 OCR circuit-breaker、多 server fallback、圖片編碼、模板匹配、文字點擊、紅點偵測、截圖調用追蹤等職責。

**問題清單**：

| # | 問題 | 嚴重度 | 說明 |
|---|------|--------|------|
| 1 | **上帝模組** | 🔴 高 | OCR 管理、模板匹配、截圖工具、紅點偵測全在同一檔案，違反 SRP |
| 2 | **全域可變狀態** | 🔴 高 | `_OCR_SERVER_FAIL_UNTIL`、`_OCR_PROBE_THREAD`、`_OCR_LAST_SUCCESS_SERVER` 等 module-level dict/thread 無封裝，測試難以隔離 |
| 3 | **`_capture_caller_info` 魔法幀數** | 🟡 中 | 硬編 `f_back.f_back.f_back`，任何包裝層增加都會讓追蹤資訊指向錯誤的呼叫者 |
| 4 | **`check_str_in_region` 的 `print(result)`** | 🟡 中 | 生產碼中殘留除錯 print，污染 stdout |
| 5 | **底部 `class img_tools` 死碼** | 🟢 低 | 註解標註「暫時不可用」，但佔用 import 時間且可能造成名稱混淆 |
| 6 | **重複 import** | 🟢 低 | 檔尾重複 `import numpy as np; import uiautomator2 as u2; import time` |
| 7 | **`find_and_click` 保存截圖被註解** | 🟢 低 | `cv2.imwrite` 被註解但 `os.makedirs` 仍在跑，每次呼叫都做無用的目錄建立 |

**優化建議**：

```python
# 拆分為 4 個模組:
img_tools/
├── __init__.py          # re-export 公開 API
├── ocr_client.py        # OCRServerPool (circuit-breaker + fallback)
├── template_match.py    # find_and_click, non_max_suppression
├── text_clicker.py      # click_str_by_server, check_str_in_region, wait_for_any_text
└── screen_utils.py      # check_red_dot, encode_image, save_stage_debug_image
```

- 將 `_OCR_SERVER_FAIL_UNTIL` 等狀態封裝為 `OCRServerPool` 類別的實例屬性
- `_capture_caller_info` 改用 `logging.StackFilter` 或 caller 傳入 tag 參數
- 移除底部 `class img_tools` 死碼和殘留 print

---

### 2.2 `point.py` — 像素偵測

**現狀**：15 行，9 個硬編座標 + RGB 比對。

**問題**：
- 座標魔法數字無任何註解（哪個 UI 元素？什麼解析度？）
- 回傳字串不統一（`"main_page"` vs 其他模組的 `"主頁面"`）
- 無容差（`==` 精確比對），遊戲 UI 微調即失效

**建議**：
- 加入解析度標註和 UI 元素名稱註解
- 統一回傳值為 `PageState` enum（與 `page_detector.py` 對齊）
- 改用容差比對（如 `np.allclose` with atol=5）
- 評估是否已被 `page_detector.py` 的 cocos fast-path 完全取代，若然則標記 deprecated

---

### 2.3 `mask.py` — HSV 色彩遮罩

**現狀**：module-level numpy array + 兩個無用的條件計算列表。

**問題**：
- `home_land_conditions` 和 `home_page` 在 import 時對全黑圖片做計算，結果永遠是 `False`，純屬死碼
- 無文件說明各遮罩對應的 UI 元素

**建議**：
- 移除死碼條件計算
- 為每個遮罩加入文件說明（對應哪個 UI 元素、在哪個場景使用）
- 考慮合併到 `img_tools/screen_utils.py` 作為常量

---

### 2.4 `tools.py` — 低階裝置操作

**現狀**：包含 `click_white`、`non_max_suppression`、`android_devices` 類別。

**問題**：
- `android_devices.capture_screenshot` 有無限迴圈（`while True`）且無超時，若截圖條件永不滿足會卡死
- 硬編座標 `(509, 56)` 無註解
- `non_max_suppression` 純演算法函數放在裝置操作模組中，職責混淆

**建議**：
- 為 `capture_screenshot` 加入 `max_retries` 或 `timeout` 參數
- 將 `non_max_suppression` 移至 `utils/image_math.py` 或 `img_tools/template_match.py`
- 為硬編座標加入 UI 元素註解

---

### 2.5 `game_state/detector.py` — 頁面狀態判定

**現狀**：三層判定架構（cocos fast-path → OCR keyword → pixel fallback）。

**問題**：
- `stage_by_str` 函數中有一段對公告的 OCR bbox 判定，呼叫了 `img_tools.analyze_skill_via_http`，但 `img_tools` 是在函數底部才 import（`import img_tools`），這是為了避免循環依賴的 workaround，說明模組邊界不清晰
- `new_stage_check` 與 `point.py` 的 `find_by_point` 功能高度重疊
- `get_stage` 函數每次呼叫都做截圖 + OCR，無快取

**建議**：
- 建立明確的依賴方向：`game_state` → `img_tools`，禁止反向
- 移除 `new_stage_check`（已被 `page_detector.py` 取代）
- 對短時間內的重複 `get_stage` 呼叫加入 TTL 快取（如 2 秒內回傳上次結果）

---

### 2.6 `json_manager/` — 持久化層

**現狀**：已拆分為 6 個子模組，架構清晰。

**問題**：
- `scheduling.py` 中有兩套週期判斷邏輯：`should_execute_cycle`（ISO week）和 `_should_execute_cycle`（Monday-anchored），語義不同但簽章相似，容易誤用
- `StoreDataManager._migrate_fill_fields` 在每次讀取時可能觸發寫入（lazy migration），有隱藏 I/O 副作用
- `ParkMarketDataManager.record_purchase` 中的 `print()` 應改用 logger

**建議**：
- 統一週期判斷為一個函數，透過參數切換 ISO week vs Monday-anchored 行為
- Lazy migration 改為顯式 migration 工具或在啟動時批量執行
- 所有 `print()` 替換為 `logger.info()` 或 `logger.debug()`

---

### 2.7 `control_panel_app.py` — Flask 中控台

**現狀**：~1000+ 行單一 Flask app，承載設備管理、OCR 檢查、Web 登入、標註器、訓練器、WebSocket live-view 等。

**問題**：
- **單一檔案過大**：所有路由、背景任務、狀態管理全在同一檔案
- **全域可變狀態過多**：`_remote_commands`、`_global_commands`、`_labeler_state`、`_trainer_state`、`_web_login_state`、`_live_view_sessions` 等 6+ 個全域 dict
- **`check_ocr_server` 與 `img_tools` 的 circuit-breaker 邏輯重複**
- **`import cv2, numpy` 在檔案中間**，說明有循環依賴或 import 時序問題

**建議**：
```
control_panel/
├── __init__.py          # Flask app factory
├── routes/
│   ├── device.py        # /api/device_data, /api/config, /api/pause 等
│   ├── ocr.py           # /api/ocr_config, /api/analyze_stage
│   ├── labeler.py       # /api/labeler/*
│   ├── trainer.py       # /api/trainer/*
│   ├── live_view.py     # /ws/live_view, /api/live_view/*
│   └── web_login.py     # /api/web_login/*
├── services/
│   ├── command_queue.py # _remote_commands + _commands_lock
│   ├── labeler_runner.py
│   ├── trainer_runner.py
│   └── ocr_health.py    # 統一 OCR 健康檢查（取代 img_tools + control_panel 各自的版本）
└── app.py               # create_app() 工廠
```

---

### 2.8 `runtime_services/` — 執行期服務

**現狀**：7 個模組，職責劃分合理。

**問題**：
- `sleep_service.py` 中 `calc_aligned_wake_ts` 的邏輯有 edge case：當 `earliest` 恰好在 `win_end` 時，`random.randint(int(earliest), int(win_end))` 只有一個值
- `device_runtime_service.py` 中 `CONNECT_FAILURE_COUNTS` 是 module-level dict，無清理機制，長期運行會累積
- `thread_registry.py` 只匯出一個 `RLock`，可考慮合併到 `bot_state.py`

**建議**：
- `calc_aligned_wake_ts` 加入單元測試覆蓋 edge case
- `CONNECT_FAILURE_COUNTS` 加入 TTL 清理或在設備離線時主動移除
- 評估 `thread_registry.py` 是否值得獨立存在

---

### 2.9 `game_actions/` — 任務排程器

**現狀**：15 個模組，已從 `new_main_v2.py` 拆分。

**問題**：
- `daily_pipeline.py` 仍是 ~300 行的線性序列，每個 task 的前置檢查（stage guard）邏輯重複
- `periodic_tasks.py` 的 `_run_periodic_cycle` 是泛用框架，但 `lamp_scheduler.py` 有自己獨立的排程邏輯，未統一
- 多處 `time.sleep()` 硬等待，無可配置化

**建議**：
- 將 task 序列改為宣告式配置（list of `TaskSpec` dataclass），由通用 runner 執行
- 統一 `lamp_scheduler` 使用 `_run_periodic_cycle` 框架
- 將硬編 sleep 改為從 config 讀取或使用 `wait_for_condition` 模式

---

### 2.10 `serve.py` — 靜態檔案伺服器

**現狀**：30 行，功能完整但 `run()` 預設綁定 `0.0.0.0`。

**問題**：
- 預設 bind `0.0.0.0` 暴露到外部網路，與 docstring 中「本機測試」的定位矛盾
- 無 HTTPS 支援

**建議**：
- 預設改為 `127.0.0.1`
- 在 README 中明確說明安全注意事項

---

### 2.11 `tools/scratch/dashboard_test.py` — 文字版監控

**現狀**：簡單的 polling + console 清屏。

**問題**：
- 同步阻塞 `time.sleep(2)` 無法接收非同步指令
- 已被 `control_panel_app.py` 的 Web 版完全取代

**建議**：標記為 deprecated 或移至 `tools/legacy/`

---

### 2.12 `update_config.py` — 設定檔遷移

**現狀**：一次性腳本，補上 `global` 和 `host_settings` 欄位。

**問題**：
- 無版本號追蹤，無法判斷是否已執行過
- 無 dry-run 模式

**建議**：
- 加入 `_schema_version` 欄位，啟動時自動偵測是否需要遷移
- 合併到 `config_manager.py` 的 `ensure_schema()` 方法

---

## 三、測試覆蓋率分析

### 3.1 現有測試統計

| 模組 | 測試檔案數 | 覆蓋狀態 |
|------|-----------|---------|
| `json_manager/` | 2 (test_json_manager, test_json_atomic) | ✅ 良好，TDD style |
| `game_actions/` | 10+ (pipeline, scheduler, guard) | ✅ 良好，characterization tests |
| `utils/` | 8+ (screenshot, emulator, page_detector) | 🟡 中等 |
| `runtime_services/` | 3 (sleep, startup, bootstrap) | 🟡 中等 |
| `img_tools.py` | 0 | 🔴 **完全無測試** |
| `point.py` | 0 | 🔴 **完全無測試** |
| `mask.py` | 0 | 🔴 **完全無測試** |
| `tools.py` | 0 | 🔴 **完全無測試** |
| `control_panel_app.py` | 1 (smoke_config_api) | 🔴 嚴重不足 |
| `serve.py` | 0 | 🔴 **完全無測試** |

### 3.2 測試品質觀察

**正面**：
- `conftest.py` 正確設定 `sys.path`
- 測試使用 `tmp_path` 隔離檔案 I/O（hermetic）
- Heavy deps 使用 stub（`opencc`, `paddleocr`, `uiautomator2`）
- Characterization tests 鎖定既有行為，適合重構

**問題**：
- **Stub 分散在每個測試檔案**：`test_daily_pipeline.py`、`test_stage_guard.py` 各自重複定義 20+ 行的 stub，應抽取到 `conftest.py`
- **無 mock server 測試 OCR circuit-breaker**
- **`control_panel_app.py` 無整合測試**：路由、背景任務、WebSocket 全未覆蓋
- **無 property-based 測試**：`_ts_same_day`、`_ts_same_week` 等純函數適合用 Hypothesis

### 3.3 優先補測建議

| 優先度 | 模組 | 測試類型 | 預估工時 |
|--------|------|---------|---------|
| P0 | `img_tools.py` OCR circuit-breaker | Unit + mock server | 2h |
| P0 | `img_tools.py` click_str_by_server | Integration (mock OCR) | 1h |
| P1 | `json_manager/scheduling.py` 統一週期邏輯 | Property-based (Hypothesis) | 1h |
| P1 | `control_panel_app.py` 核心路由 | Flask test client | 2h |
| P2 | `point.py` / `mask.py` | Unit (legacy behavior lock) | 0.5h |
| P2 | `tools.py` android_devices | Unit (mock device) | 0.5h |

---

## 四、服務架構分析

### 4.1 當前架構

```
[control_panel_app.py :5002]  ← Flask + WebSocket
         │
    ┌────┴────┐
    │ API     │ ← REST + WS
    │ Routes  │
    └────┬────┘
         │
    ┌────┴──────────────────────┐
    │ Shared mutable state      │
    │ (6+ global dicts + locks) │
    └────┬──────────────────────┘
         │
    ┌────┴────┐
    │ Threads │ ← labeler, trainer, web_login, flag_reset
    └─────────┘

[ocr_server.py :5001]  ← 獨立 OCR 服務
[push_project/server/app.py :5000]  ← 推送服務
[serve.py :8000]  ← 靜態檔案
```

### 4.2 問題

| # | 問題 | 嚴重度 |
|---|------|--------|
| 1 | **3 個獨立 HTTP server 無統一生命週期管理** | 🔴 高 |
| 2 | **OCR 健康檢查邏輯重複**：`img_tools._ocr_probe_loop` + `control_panel_app.check_ocr_server` + `get_ocr_runtime_status` 三處各自實作 | 🔴 高 |
| 3 | **Flask dev server 用於生產**：`app.run(threaded=True)` 無 WSGI server | 🟡 中 |
| 4 | **背景執行緒無優雅關閉**：labeler/trainer/web_login thread 都是 `daemon=True`，進程結束時直接中斷 | 🟡 中 |
| 5 | **無 health endpoint**：`/api/status` 回傳所有設備狀態但無 liveness/readiness probe | 🟡 中 |

### 4.3 優化建議

#### 短期（不改架構）

1. **統一 OCR 健康檢查**：`img_tools.get_ocr_runtime_status()` 作為 single source of truth，`control_panel_app.check_ocr_server()` 改為呼叫它
2. **加入 `/healthz` 端點**：回傳 `{"ocr": bool, "devices": int, "uptime": float}`
3. **Flask 改用 waitress/gunicorn**：`waitress-serve --listen=0.0.0.0:5002 control_panel_app:app`

#### 中期（輕量重構）

4. **生命週期管理器**：建立 `ServiceManager` 統一啟動/停止 OCR probe thread、labeler、trainer、push server
5. **背景任務改用 `threading.Event`** 做優雅停止，取代 daemon thread
6. **Config 熱載入**：`config_manager` 加入 file watcher（`watchdog` 套件），變更時自動重新載入

#### 長期（架構演進）

7. **考慮改用 FastAPI**：原生 async、自動 OpenAPI doc、WebSocket 支援更好
8. **OCR 服務獨立部署**：circuit-breaker 邏輯收斂到 `OCRClient` 類別，供所有模組共用

---

## 五、技術債務清單

| # | 項目 | 位置 | 影響 | 建議處理方式 |
|---|------|------|------|-------------|
| 1 | 硬編座標無註解 | `point.py`, `tools.py`, `mask.py`, `reward_manager.py` | 遊戲更新即失效 | 全面標註 UI 元素名稱 + 解析度 |
| 2 | `print()` 殘留 | `json_manager/*.py`, `img_tools.py` | 污染 stdout | 替換為 logger |
| 3 | 循環依賴 workaround | `game_state/detector.py` 底部 import `img_tools` | 語義不清 | 重構依賴方向 |
| 4 | Dead code | `point.py`、`mask.py` 的條件計算、`img_tools` 底部 class | 增加認知負擔 | 移除或標記 deprecated |
| 5 | Stub 重複 | `tests/test_daily_pipeline.py` 等 20+ 行 stub | 維護成本 | 抽取到 `tests/conftest.py` |
| 6 | `sync-conflict-*` 檔案 | `tools/` 目錄下多個 sync conflict 檔案 | 混亂 | 清理並加入 `.gitignore` |
| 7 | `_should_execute_cycle` 兩套邏輯 | `json_manager/scheduling.py` | 容易誤用 | 統一為一個函數 |

---

## 六、執行優先序

### Phase 1：安全 & 穩定性（本週）
- [ ] `serve.py` 預設 bind 改為 `127.0.0.1`
- [ ] 移除 `img_tools.py` 殘留的 `print(result)`
- [ ] 移除 `tools/` 下的 `sync-conflict-*` 檔案
- [ ] 為 `tools.py` 的 `capture_screenshot` 加入 timeout

### Phase 2：測試補強（下週）
- [ ] 從 `conftest.py` 抽取共用 stub
- [ ] 補 `img_tools.py` OCR circuit-breaker 單元測試
- [ ] 補 `json_manager/scheduling.py` 的 Hypothesis 測試

### Phase 3：模組拆分（兩週後）
- [ ] `img_tools.py` 拆為 4 個子模組
- [ ] `control_panel_app.py` 拆為 routes + services
- [ ] 統一 OCR 健康檢查邏輯

### Phase 4：架構升級（一個月後）
- [ ] 引入 ServiceManager 生命週期管理
- [ ] Flask → waitress/uvicorn WSGI server
- [ ] Config 熱載入機制

---

## 七、附錄：各模組行數與職責

| 模組 | 行數 | 職責 | 評價 |
|------|------|------|------|
| `img_tools.py` | ~500 | OCR + 模板匹配 + 紅點偵測 | 🔴 需拆分 |
| `control_panel_app.py` | ~1000+ | Web 中控台 | 🔴 需拆分 |
| `json_manager/` | ~600 (6 files) | 持久化 | ✅ 已拆分 |
| `game_actions/` | ~1500 (15 files) | 任務排程 | ✅ 架構良好 |
| `runtime_services/` | ~500 (7 files) | 執行期服務 | ✅ 架構良好 |
| `utils/` | ~3000 (27 files) | 工具集 | 🟡 部分需整理 |
| `tools/` | ~2000 (75+ files) | 一次性腳本/工具 | 🟡 需清理 |
| `tests/` | ~3000 (67 files) | 測試 | 🟡 需補強 |

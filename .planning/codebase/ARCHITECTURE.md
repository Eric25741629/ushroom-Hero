# Architecture

更新日期：2026-03-13  
Repository root: `A:\菇勇者全自動掛機`

## System Pattern
- 此專案採用以 `new_main_v2.py` 為核心、由腳本編排的自動化模式。
- 執行期行為是每台裝置一個長時間存活的控制迴圈，而不是單次 request/response 的應用程式。
- 它結合了本地裝置控制（ADB/uiautomator2）、OCR/vision 推論，以及任務派發。
- 在 `control_panel_app.py` 的 Flask 端點中，存在次要的 control-plane 模式。
- 狀態主要以 JSON + logs 持久化，而非以關聯式服務作為主要真實來源。

## Layers And Modules
- 編排層：`new_main_v2.py`、`game_initialization.py`、`event_manager.py`。
- 裝置/IO 層：`adb_operations.py`、`device.py`、`device_wrapper.py`、`utils/wake_up_handler.py`。
- 感知層：`img_tools.py`、`game_state/detector.py`、`new_cnn/cnn_model.py`、`ocr_server.py`。
- 動作層：`game_actions/daily_tasks.py`、`game_actions/miner_action.py`、`game_actions/reward_manager.py`、`game_actions/periodic_tasks.py`、`game_actions/skill_manager.py`。
- 領域模組：`miner/`、`farm/`、`mission/`、`family/`、`park.py`、`fight_car.py`。
- 狀態/設定層：`bot_state.py`、`config_manager.py`、`json_manager.py`、`bot_config.json`、`emulator-5554.json`（以及其他每台裝置的類似 JSON 檔）。
- 可觀測性/支援：`utils/logging_utils.py`、`logs/`、`miner/rl_logs/`、`docs/INDEX.md`。
- UI/control 端點：`control_panel_app.py`、`app.py`、`serve.py`，以及 `菇勇者.html` 與 `templates/` 等靜態頁面。

## Data Flow
1. 啟動時由 `new_main_v2.py` 載入 models/config，使用 `config_manager.py`、`utils/model_loader.py` 與如 `cnn_model.pth` 的 CNN model 檔案。
2. 裝置連線與就緒檢查透過 `adb_operations.py` 執行，並由 `device_wrapper.py` 封裝。
3. 擷取螢幕截圖（`d.screenshot`），並導向 `img_tools.py`、`game_state/detector.py`、`new_cnn/cnn_model.py` 中的 OCR/CNN 邏輯。
4. 階段分類輸出會驅動動作路由，分派到 `game_actions/` 底下模組與功能模組（`mission`、`farm`、`family`、`miner`）。
5. 採礦分支會呼叫 `miner/mining_service.py`，並進一步使用 `miner/core/`、`miner/planning/`、`miner/models/`。
6. 狀態轉移與暫停/刷新命令會透過 `bot_state.py` 與 `control_panel_app.py` 的 dashboard API 同步。
7. 任務時間戳記/cooldown 由 `json_manager.py` 讀寫到每台裝置的 JSON 紀錄。
8. 執行期與除錯輸出會寫入 `logs/`、`easyocr_calls.log`，以及 `miner/rl_logs/<device>/events.jsonl` 中的 RL traces。

## Entry Points
- 主要執行期入口：`new_main_v2.py`。
- 控制面板 web service：`control_panel_app.py`（`/api/...` 底下的 Flask routes 與 dashboard 頁面）。
- 靜態/本地 API server：`app.py`（Flask）與 `serve.py`（簡易 HTTP + CORS）。
- OCR service process：`ocr_server.py`（獨立 OCR endpoint host）。
- 測試入口區域：`tests/test_smoke_config_api.py` 與像 `test_json_manager.py` 這類模組層級測試腳本。

## Notable Boundaries
- `miner/` 是半獨立子系統，擁有自己的 `core/`、`planning/`、`models/`、`rl/` 與 `scripts/`。
- `game_actions/` 是刻意保持精簡的命令邏輯，依賴 detector/state 輸出。
- `bot_state.py` 作為共享記憶體協調與 worker/master 同步邊界。
- JSON 檔（例如 `bot_config.json` 與 `emulator-5560.json`）實質上是跨腳本的設定/狀態契約。

# Structure

更新日期：2026-03-13  
Repository root: `A:\菇勇者全自動掛機`

## Directory Layout
- `miner/`：採礦子系統，包含 `core/`、`planning/`、`models/`、`rl/`、`scripts/`、`dataset/`、`rl_logs/`。
- `game_actions/`：動作執行模組，例如 `daily_tasks.py`、`periodic_tasks.py`、`reward_manager.py`、`miner_action.py`。
- `game_state/`：階段偵測（`detector.py`），由編排層與動作層使用。
- `utils/`：橫切輔助工具（`logging_utils.py`、`model_loader.py`、`ocr_clicker.py`、`wake_up_handler.py`）。
- `tests/`：pytest 風格測試（例如 `tests/test_smoke_config_api.py`），以及 `tests/__pycache__/` 下的快取 bytecode。
- `docs/`：文件索引與說明（`docs/INDEX.md`）。
- `config/`：執行期腳本使用的設定資產。
- `templates/`：面板/UI 元件的 HTML template 資源。
- `logs/`：執行期 log 輸出。
- `new_cnn/`：CNN model 程式碼與相關推論支援。
- `everyday_mission/`、`farm/`、`family/`、`mission/`、`oracle/`、`partner/`：功能專屬自動化模組/資料。

## Key Root Files
- 主要編排：`new_main_v2.py`。
- Web 控制面板：`control_panel_app.py`。
- 本地 flask/static service：`app.py`。
- 靜態檔案伺服工具：`serve.py`。
- 裝置與 ADB 存取：`device.py`、`device_wrapper.py`、`adb_operations.py`、`adb_devices.py`。
- 狀態/設定：`bot_state.py`、`config_manager.py`、`json_manager.py`、`bot_config.json`。
- OCR/推論：`img_tools.py`、`ocr_server.py`、`cnn_model.py`、`cnn_model.pth`。
- 專案文件：`PROJECT_OVERVIEW.md`、`PROJECT_RUNBOOK.md`、`SCRIPT_ARCHITECTURE.md`。

## Naming Conventions Observed
- 主要 Python module 命名為 `snake_case.py`（例如 `game_initialization.py`、`daily_gift_task.py`）。
- 部分 legacy/class 風格檔名使用 PascalCase 或大寫開頭名稱：`Mission.py`、`Skill.py`、`Store.py`、`BUY.py`。
- 功能資料夾通常使用小寫 snake case（`game_actions/`、`game_state/`、`new_cnn/`、`everyday_mission/`）。
- 裝置狀態檔遵循 `<device-id>.json` 模式，例如 `emulator-5554.json`、`emulator-5560.json`、`7fe98fc6.json`。
- 備份檔會附加 `.backup_<timestamp>`（例如 `emulator-5554.json.backup_1770242849`）。
- Logs 與 artifacts 會使用描述性後綴，例如 `_debug`、`_test`、`_analysis`，且某些 dataset 會出現類似日期/時間前綴。
- Python 快取目錄在根目錄與子模組中一致使用 `__pycache__/`。

## Practical Navigation Rules
- 從 `new_main_v2.py` 開始分析執行期，再沿著 imports 追到 `game_actions/`、`game_state/` 與 `utils/`。
- 將 `miner/` 視為專用 bounded context；在深入 `miner/core/` 或 `miner/planning/` 前，先看 `miner/mining_service.py`。
- 將根目錄一次性腳本（`test_*.py`、`quick_test.py`、`update_config.py`）視為工具而非核心架構。
- 將 `docs/` 與 `PROJECT_*.md` 視為操作脈絡，而非可執行來源。
- 編輯時需嚴格控管範圍，因為根目錄同時包含活躍程式碼與封存實驗（`new_main_before20250514.py`、notebook 檔、除錯圖片）。

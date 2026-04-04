# 儲存庫慣例

## 範圍
- 本文件彙整了在現行 Python 模組中觀察到的模式，特別是 `config_manager.py`、`control_panel_app.py`、`bot_state.py`、`game_initialization.py`、`utils/logging_utils.py` 與 `app.py`。

## 風格
- 語言為 Python，模組大多以扁平的頂層腳本組織，並搭配如 `game_actions/`、`game_state/`、`miner/`、`utils/` 等功能資料夾。
- 匯入通常分組為：標準函式庫、第三方套件、再來是本地模組（範例見 `control_panel_app.py`）。
- 型別提示使用不一致，但在核心狀態/設定程式碼中可見（例如 `bot_state.py` 與 `config_manager.py`）。
- JSON 持久化在 `config_manager.py` 中使用明確的 UTF-8 與易讀輸出（`json.dump(..., ensure_ascii=False, indent=4)`）。
- 對非致命的操作問題，偏好記錄日誌而非直接拋出例外；可見 `utils/logging_utils.py` 與 `bot_state.py` 中大量 `print(...)` 狀態訊息。

## 命名
- 多數 Python 檔案/模組採 snake_case 命名：`config_manager.py`、`game_initialization.py`、`daily_gift_task.py`。
- 函式名稱採 snake_case 且以動作導向：`load_config`、`update_device_config`、`get_all_states`、`set_pause`。
- 設定/狀態模組中的常數採 UPPER_SNAKE_CASE（`CONFIG_FILE`、`DEFAULT_DEVICE_CONFIG`、`OFFLINE_RETENTION_SEC`）。
- 內部共用狀態使用前導底線命名：`_states`、`_locks`、`_worker_queue`、`_cached_models`。
- Flask 應用中的 API 路由一致使用 `/api/...` 前綴（`control_panel_app.py`、`app.py`）。

## 常見模式
- 設定合併/遷移模式：
- 載入已儲存 JSON，從預設值補齊缺失鍵，並在 schema 改變時重寫檔案（`config_manager.py`）。
- 防禦式輸入正規化模式：
- 轉換傳入值、限制數值範圍、強制轉型布林/字串（`config_manager.py` 中的 `update_device_config` 與 `update_ocr_config`）。
- Device-id 正規化模式：
- 針對含有 `:` 的遠端 ID，於設定查找前先切分並保留最後一段（`control_panel_app.py` 路由處理器）。
- 執行緒安全的全域狀態模式：
- 使用每裝置鎖 + 全域鎖 + worker 佇列（`bot_state.py`）。
- 背景維護模式：
- 以 daemon 執行緒執行清理/同步迴圈（`bot_state.py` 中的 `_housekeeper_loop`、`_worker_sync_loop`）。
- 每裝置 logger 模式：
- 在 `utils/logging_utils.py` 建立已清理的日誌檔名與 rotating handlers，輸出到 `logs/`。

## 錯誤處理
- API 端點通常以 `try/except` 包住主體，並在 `control_panel_app.py` 回傳 `{"status": "error", "message": str(e)}` 與 HTTP 500。
- 網路/裝置操作常見重試與後備機制：
- 範例：OCR 健康檢查會迭代多個端點，遇到例外時繼續（`control_panel_app.py` 的 `check_ocr_server`）。
- 在迴圈/服務中常見 fail-soft 行為：
- 捕捉廣泛例外、記錄/列印後繼續或重新初始化（`bot_state.py`、`game_initialization.py`）。
- 設定載入在讀取/解析失敗時回傳安全預設值（`config_manager.py` 的 `load_config`）。
- 對不良使用者輸入，偏好明確驗證與最小/最大值防護，而非直接丟例外（`config_manager.py` 的 `update_device_config`、`update_ocr_config`）。

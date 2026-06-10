# 測試筆記

## 框架與進入點
- 目前儲存庫同時混用正式測試與臨時可執行測試腳本。
- `tests/test_smoke_config_api.py` 使用 `unittest`（`unittest.TestCase`、`setUpClass`、`setUp`、`tearDown`）。
- 多個命名類似測試的檔案其實是由 `if __name__ == "__main__":` 驅動的普通腳本，例如 `test_json_manager.py` 與 `test_server_brain.py`。
- 也可在類腳本模組中看到 assertion 風格的單元測試，例如 `test_item_placement_guards.py`。

## 觀察到的設定
- `requirements.txt` 中的執行期相依套件包含 `Flask` 與 `flask-cors`；其中未明確宣告 pytest 相依。
- 透過 `git ls-files` 過濾 `pytest.ini`、`pyproject.toml`、`tox.ini`、`setup.cfg`、`.coveragerc`、`conftest.py` 時，未發現受追蹤的頂層測試執行器設定。
- `tests/test_smoke_config_api.py` 會從 `control_panel_app.app` 建立 Flask 測試 client。
- smoke 測試會在匯入時期將 stub 注入 `sys.modules`，以 patch 相依（`adb_operations`、`game_state.detector`、`new_cnn.cnn_model`）。

## 結構
- 主要測試目錄：`tests/`。
- 在該目錄觀察到的檔案：`tests/test_smoke_config_api.py` 與 `tests/mock_item_placement_rl_test.py`。
- 其他根目錄層級的類測試檔案：`test_item_placement_guards.py`、`test_json_manager.py`（其餘 device/debug 腳本已移至 `tools/debug/`，dashboard_test 移至 `tools/scratch/`）。
- 在 `miner/scripts/` 下也存在領域特定的測試腳本（例如 `miner/scripts/test_void_logic.py`、`miner/scripts/test_streaming.py`）。
- 在 `OCR/PaddleOCR/tests/` 下存在第三方/供應商測試樹；應與專案自有覆蓋率分開看待。

## Mock 與隔離模式
- 在 `tests/test_smoke_config_api.py` 中積極使用 `types.ModuleType` + `sys.modules.setdefault(...)` 進行模組 stub。
- `tests/test_smoke_config_api.py` 透過 `tempfile.TemporaryDirectory()` 使用暫存檔案系統隔離。
- 測試設定時會替換 `config_manager.CONFIG_FILE` 來重新導向全域設定檔，並在 teardown 還原。
- API 行為是透過 HTTP 層呼叫（`client.post`、`client.get`）驗證，而非直接呼叫函式。

## 目前覆蓋率訊號
- 目前最強的自動化訊號看起來是 `tests/test_smoke_config_api.py` 針對設定端點周邊的 API smoke 行為。
- `bot_state.py`、`game_initialization.py`、`img_tools.py`、`new_main_before20250514.py` 的核心執行路徑（裝置協調、OCR 流程、worker 同步、啟動復原）似乎僅有較輕量的正式 assertion 覆蓋。
- 大量手動/互動式腳本的存在，表示目前探索式測試比重高於可重現的 CI 風格測試套件。
- `logs/` 中既有執行期日誌（例如 `logs/emulator-5554.log`、`logs/emulator-5558.log`）提供操作證據，但不屬於覆蓋率指標。

## 新增測試的實務建議
- 優先在 `tests/` 下新增可重現的測試，搭配隔離的暫存檔與明確相依 stub。
- 將外部相依（ADB、OCR server、network time APIs）在模組邊界進行 mock。
- 透過命名與位置將整合型腳本與單元測試分離，以提升執行器清晰度。
- 若後續採用 pytest，先新增單一儲存庫層級設定，再逐步遷移腳本風格測試。

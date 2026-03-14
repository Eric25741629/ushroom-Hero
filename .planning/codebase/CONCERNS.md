# 程式碼庫疑慮

## 高風險區域
- `new_main_v2.py`：協調器是大型單體，具有深度跨模組耦合、重複的階段檢查，以及多個長時間執行迴圈（`while (1)` / `while True`），提高回歸風險並使故障更難隔離。
- `new_main_v2.py`：裝置特定行為以序列號常值（例如模擬器 ID）硬編碼，造成脆弱邏輯，並在不同裝置間產生隱性行為分歧。
- `new_main_v2.py`：在關鍵執行路徑周圍使用廣泛的 `except Exception` 區塊，可能掩蓋根因，並讓執行緒在部分損壞狀態下持續存活。
- `control_panel_app.py`：API 處理器會修改全域記憶體狀態（`_remote_commands`, `_global_commands`），但缺乏一致的鎖定策略，在並行請求下有競態條件風險。
- `event_manager.py`：佇列輪詢優先化在高優先流量持續時可能讓低優先事件飢餓；目前沒有公平性或老化策略。

## 安全性疑慮
- `serve.py`：`run()` 預設綁定 `0.0.0.0`，且 `CORSRequestHandler` 允許 `Access-Control-Allow-Origin: *`，若用於本機/受信任網路外部會不安全。
- `control_panel_app.py`：作業端點（refresh、遠端命令輪詢、state/report APIs）看不到明確的驗證/授權保護。
- `game_api.py`：事件發送與狀態變更端點接受使用者提供的 payload，驗證有限且沒有授權邊界。
- `ocr_server.py`：已有 IP allowlisting，但目前依賴網路假設（`100.64.0.0/24` + loopback）；缺少 token/簽章檢查以提供縱深防禦。
- `ocr_server.py`：硬編碼模型目錄（`A:\OCR_model\...`）洩漏環境假設並造成部署脆弱性。

## 資料完整性 / 可靠性債務
- `json_manager.py`：持久化寫入使用直接 open/write（`json.dump`），未明確採用原子性暫存檔替換，寫入中斷可能毀損各裝置狀態檔。
- `json_manager.py`：在多個復原/遷移路徑可見例外吞沒（`except Exception: pass`），有靜默資料漂移風險。
- `config_manager.py`：`bot_config.json` 在載入時會自動修復/變更，雖然方便，但可能掩蓋設定綱要問題並靜默改寫操作意圖。
- `device.py`：通知處理中的多個裸 `except:` 分支會抑制操作錯誤，降低可觀測性。
- `adb_operations.py`：`safe_log` 的後備路徑引用 `sys.stderr`，但未匯入 `sys`，使錯誤路徑記錄本身也很脆弱。

## 效能熱點
- `new_main_v2.py`：緊密迴圈內頻繁進行截圖 + OCR/階段偵測，且伴隨大量 `time.sleep` 呼叫，顯示高度輪詢行為，並可能造成不必要的 CPU/裝置負載。
- `ocr_server.py`：OCR 執行由 `ocr_lock` 全域序列化，雖簡化執行緒安全，但在並行請求下可能限制吞吐量。
- `control_panel_app.py`：健康檢查與狀態輪詢模式可能產生重複請求流量；較重路徑未見快取/節流策略。
- `event_manager.py`：`event_history` 僅存在記憶體中（上限 1000），重啟會遺失操作脈絡，事件後分析將不完整。

## 測試覆蓋缺口
- `tests/` 相對於系統複雜度僅有非常有限的自動化覆蓋（`tests/test_smoke_config_api.py`, `tests/mock_item_placement_rl_test.py`）。
- 根目錄層級的類測試檔（例如 `test.py`, `quick_test.py`, `park_test.py`）看似臨時性質，且未明確整合進可重複的 CI 流程。
- `requirements.txt` 僅宣告 Flask/CORS 套件，與執行期大量較重依賴（`torch`, `easyocr`, `paddleocr`, `uiautomator2`）不符，增加環境漂移與「只在我機器可跑」風險。

## 脆弱架構訊號
- 執行期邏輯分散於多個根目錄腳本（`new_main_v2.py`, `ocr_server.py`, `control_panel_app.py`, `game_api.py`），職責重疊且使用全域單例。
- 命名/結構不一致（混用 snake/camel、遺留腳本、backup/tmp 變體）顯示重構債務與不明確的真實來源模組。
- 操作狀態似乎分散於專案根目錄多個 JSON 檔（各裝置 `*.json`），雖然簡單，但難以安全地進行版本化、驗證與遷移。

## 優先建議
- 第一優先：將 `new_main_v2.py` 協調流程拆分為明確的狀態機元件，並集中裝置策略/設定（移除硬編碼序列號條件分支）。
- 第一優先：在 `control_panel_app.py` 與 `game_api.py` 的控制 API 加入驗證與請求簽章。
- 第一優先：在 `json_manager.py` 的持久化路徑與 `config_manager.py` 的設定寫入實作原子性 JSON 寫入（暫存檔 + `os.replace`）。
- 下一步：在 `device.py`、`json_manager.py`、`adb_operations.py` 全面標準化結構化錯誤處理/記錄（移除靜默 `pass`）。
- 下一步：定義真正的依賴鎖定/限制檔，並擴充事件流程、OCR 後備路徑與多裝置並行的自動化測試。

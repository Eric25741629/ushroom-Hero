# 停車嚴格抱團與完整決策日誌設計

## 目標

跨界停車改為嚴格抱團模式。每次首停與到期重停都必須先確認目標車位內已有至少 5 位同服玩家，才允許停入。計數發生在自己的車停入之前，因此門檻 5 不包含自己。鉑銀 1、2、3 永久排除。

同時補齊可稽核的 per-device 日誌，使單看 `logs/<device>/main.log` 就能重建每輪掃描、候選過濾、同服計數、選位與放棄原因。

## 行為規則

1. 抱團門檻固定為 5 位「已在車位內」的同服玩家。
2. 自己尚未停入時進行計數，所以自己的車不列入這 5 位。
3. 鉑銀 1、2、3 不得出現在掃描候選、優先範圍或任何 fallback 候選中。
4. 適用於 10:00 首停、8 小時後重停，以及日間窗口內的任何補停。
5. 掃描期間找不到合格車位時保持空車，不得降級停入非抱團車位。
6. 既有「今日不重複停同一 master_id」規則保留；被今日去重排除的車位要寫入日誌。
7. 銀位、空位、品質合格坐騎與日間窗口等既有安全條件維持不變。

## 候選資料流

1. 讀取 `null_space` 與 `collect_space`。
2. 合併兩個來源並依 `master_id` 去重，避免 `collect_space` 非空時遮蔽 `null_space`。
3. 依序過濾：有空位、鉑銀層級、層級不在 1/2/3、今日未停過。
4. 對所有剩餘候選讀取車位明細並計算同服占用人數。
5. 只保留同服人數至少 5 的候選；仍可使用裝置的 `priority_levels` 決定合格候選間的優先順序。
6. 停入前重新讀取選中車位明細，重新確認空位與同服人數仍至少 5，避免掃描後狀態改變。
7. 若重驗失敗，繼續下一輪掃描；掃描期限到達後回傳明確的 `strict_cluster_not_found`，不執行一般停車 fallback。

## 完整日誌

詳細決策訊息必須透過目前的 per-device logger 寫入 `logs/<device>/main.log`，不能只留在未落檔的 `ws_token.runner` module logger。

每次停車任務至少記錄：

- 任務上下文：window、target、current、need、登入 server_id。
- 生效設定：門檻 5、排除層級 `[1, 2, 3]`、掃描 levels、priority levels、duration、interval。
- 候選來源摘要：`null_space` 數量、`collect_space` 數量、合併去重後數量。
- 過濾摘要：非銀、無空位、排除層級、今日去重各移除多少；排除層級與今日去重需列出 level/master_id。
- 每一輪掃描：round、每個成功讀取候選的 level/master_id/空位數/同服人數，以及讀取失敗的候選與錯誤。
- 本輪判定：哪些候選達到或未達門檻、排序後選中哪一個。
- 停入前重驗：最新同服人數、空位位置與是否仍合格。
- 停車請求結果：level、master_id、pos、mount_id、success/error code。
- 放棄結果：沒有候選、沒有合格坐騎、server_id 缺失、掃描逾時、重驗失敗或嚴格模式拒絕 fallback。
- 任務總結：parked_count、reason、scan_rounds、selected level/master_id/allies、next_repark_ts。

日誌採單行結構化 `key=value` 文字，保留現有時間、等級、檔案與行號格式，方便人工閱讀及 `rg` 搜尋。候選清單每輪一行，避免每個車位各寫一行造成過度碎片化。

## 程式邊界

- `ws_token/carpark_plan.py`：提供嚴格門檻與排除層級的設定清洗；預設值為 5 與 `(1, 2, 3)`。
- `ws_token/carpark.py`：負責合併候選、排除層級、同服計數與候選排序；純邏輯維持可單元測試。
- `ws_token/runner.py`：協調多輪掃描、停入前重驗、禁止 fallback，並產生決策事件。
- `game_actions/ws_phase.py`：把 carpark 決策事件轉送到裝置 logger，確保落在 `main.log` 與 dashboard ring buffer。
- `bot_config.json`：五台啟用停車計畫的裝置同步設為門檻 5、排除 1/2/3；levels 與 priority levels 移除 1/2/3。

## 錯誤處理

- 單一車位明細讀取失敗只排除該候選並記錄錯誤，不中斷整輪。
- 缺少登入 server_id 時不得猜測同服身分，直接不停並記錄原因。
- 停車請求逾時沿用既有保守策略，停止追加停車並記錄「結果不確定」，避免重複停入。
- 日誌 callback 自身失敗不得中斷停車流程；仍保留 module logger 作為次要輸出。

## 測試設計

先寫失敗測試，再實作：

1. 4 位同服玩家不得停；5 位可以停；確認計數資料不包含待停的自己。
2. 鉑銀 1、2、3 即使有 10 位同服與空位，也不得成為候選。
3. `collect_space` 非空但不合格時，仍會掃描 `null_space` 的合格候選。
4. 找不到 5 人抱團時掃描到期並回傳 `strict_cluster_not_found`，且不呼叫一般 fallback 停車。
5. 停入前重驗降到 4 人時不得停入。
6. 首停與日間重停都走同一嚴格規則。
7. carpark progress/decision 訊息會寫入傳入的 per-device logger，而非被當作開神燈進度。
8. 設定清洗會把壞門檻退回 5，並固定排除 1/2/3。

目標驗證只跑停車相關測試與修改檔案的 `py_compile`，不執行整包 pytest。

## 不在本次範圍

- 不實作已停車輛的提前召回或搬家。
- 不修改本服／好友車位的 Playwright 自動化。
- 不新增 dashboard 設定 UI；本次先以既有設定檔與預設值落實固定規則。
- 不因找不到抱團而降低門檻或恢復非抱團 fallback。

# Final V1 挖礦規劃器設計

## 目標

新增獨立的 `final_v1` 挖礦規劃器，在不改動既有 v1/v3/v4 基準的前提下，統一處理可見盤面、WS 已知畫面外地形、礦坑導向與道具選擇，並透過離線 A/B、真實盤面 replay 與 shadow log 驗證是否比現行 v1 更有效率。

`mining_planner_version` 預設維持 `v1`；只有裝置明確選擇 `final_v1` 時才執行新規劃器。

## 已確認的領域規則

- 炸彈與鑽頭的取得機率相同；目前庫存差異是歷史演算法偏用鑽頭造成，不能把當前庫存差直接解讀成鑽頭天然更稀有。
- 炸彈與鑽頭使用相同的基礎道具成本，選擇只由實際覆蓋、收礦、開路與漏礦風險決定。
- 炸彈可以計算已知畫面外收益；鑽頭與鎬子只計算目前畫面內效果。
- 目標是用較低資源成本完成更多礦坑 cluster，不是單看深度、單格收益或某一種道具利用率。
- WS 道具現量以 `0x0402` 的 `9800004/9800001/9800009` item push 為準，不使用 `0x0c01.max_num`。

## 架構

### 獨立 Planner

新增 `miner/final_v1/`，主要入口為：

```python
plan_final_v1(
    board,
    shovels,
    items,
    *,
    visible_rows=7,
    known_pits=None,
    valid_targets=None,
    time_budget_ms=250.0,
)
```

規劃器接受可變高度盤面：

- WS 路徑以 `area_info` 與靜態模板重建約 21 列已知地形，並合併全圖已知未採集礦坑。
- CNN/畫面路徑只提供辨識到的 7×6 盤面。
- `visible_rows` 限定鎬子、鑽頭及第一步的可執行範圍；炸彈模擬可以計入已知畫面外 footprint。
- `valid_targets` 限制第一步必須是 server/CNN 執行器接受的目標；深層狀態只用來估計未來價值。

舊 v1/v3/v4、`pit_directed_next` 與 `prop_combo_for_pits` 保留作基準及 fallback，不刪除、不改寫成 final_v1。

### 搜尋

使用 bounded beam search：

- 搜尋深度在 4–6 步內逐層擴展。
- 每層只保留最高分的一小組非支配狀態。
- 動作先依即時收益與路徑價值排序，再限制 branching。
- 以 `time_budget_ms=250` 為硬上限；逾時回傳目前最佳第一步。
- 每輪只執行第一步，取得新盤面與庫存後重新規劃。

狀態分別維護鎬子、炸彈與鑽頭庫存。道具的評分成本不會扣進鎬子數量；零鎬時只要有合法且有價值的道具動作，仍可規劃使用。

## 評分模型

評分由以下部分組成，順序代表設計優先級：

1. 完成整個礦坑 cluster 的收益。
2. 本輪與搜尋視野內採集的礦坑格收益。
3. 鎬子成本。
4. 炸彈與鑽頭的相同基礎成本。
5. 未採集礦被捲出畫面的懲罰。
6. 半挖 cluster 在 session 結束時未完成的風險。
7. 沒有礦時，以低權重獎勵最低成本的向下推進。

路徑開通可以加分，但權重必須低於直接收礦與完成 cluster。不得保留固定的炸彈優先或鑽頭優先 tie-break；效果完全相同時只使用座標等穩定排序，確保決策可重現。

## WS 資料流

1. `ws_phase` 讀取裝置的 `mining_planner_version` 與 shadow 設定並傳給 WS mining runner。
2. `mining_adapter` 使用 `area_info`、`mine_terrain.terrain_at`、raw blocks、actives 與 `map_pits` 建立已知高盤。
3. `final_v1` 只允許目前 server-valid frontier/air placement 作為第一步。
4. 執行一個 `0x0c03` 動作。
5. 等待目標、影響範圍、baseline 或對應庫存發生可歸因變化。
6. 從 `InventoryTracker` 重新取得鎬子、炸彈與鑽頭真值，包含 consume 與 gain，再進入下一輪規劃。

21 列重建不完整時，先退回 7 列 `final_v1`；若 final_v1 無合法步驟，再使用既有 v1 fallback。WS mining 整體失敗時仍保留現行 CNN/Oracle 降級路徑。

## CNN 資料流

`miner.mining_service` 的 planner dispatch 新增 `final_v1`。CNN 路徑使用 7×6 分類盤面、目前 OCR/WS 庫存與既有 blocked actions。執行器介面維持現有 step contract，不建立第二套點擊格式。

## 設定

### 主規劃器

`mining_planner_version` 接受：

- `v1`
- `v3`
- `v4`
- `final_v1`

全域預設與既有裝置設定均維持 `v1`，不自動切換。

### Shadow 規劃器

新增 `mining_shadow_planner_version`：

- 預設為空字串，完全不增加規劃工作。
- 設為 `final_v1` 時，同一盤面額外計算 final_v1，但實際執行仍由 `mining_planner_version` 決定。
- shadow 失敗或逾時只記錄，不得中斷主流程。

## Telemetry

每輪記錄：

- 主規劃器與 shadow 規劃器名稱。
- 第一動作、目標、來源（planner/fallback）。
- 預估 cluster 收益、pit 收益、鎬成本、炸彈/鑽頭成本、漏礦懲罰與總分。
- 實際執行步驟、是否被合法性過濾、確認方式與拒絕原因。
- 動作前後 authoritative inventory。
- 規劃耗時、搜尋深度、展開節點、是否碰到時間預算。

## 驗證與勝出標準

### TDD 單元與整合測試

- 炸彈與鑽頭基礎成本相同。
- 同效果時不因名稱或庫存偏向任一道具。
- 零鎬但有道具時仍能出合法 item plan。
- 炸彈可計算已知畫面外礦坑，鑽頭與鎬子不計。
- 21 列規劃只能輸出目前可執行的第一步。
- row-0/即將捲出的未收礦受到保護。
- 每步後 tracker 的 consume/gain 會覆蓋本地估計。
- 目標未變且庫存未變時不得誤判成功。
- 7 列資料缺失與 shadow 例外能安全降級。

### 離線 A/B

使用固定 seed、相同起始庫存、相同 50/50 炸彈/鑽頭獎勵，分別比較：

- 7 列：v1、v4、final_v1，隔離純演算法品質。
- 21 列：v1 基準流程與 final_v1，量化 WS 已知視野整合的收益。
- 真實 board replay：檢查合法步驟、空 plan、耗時與既有問題盤。

主要 KPI：

- 完成礦坑格與 cluster 數。
- `pits / shovel`。
- `pits / item`。
- `pits / (shovel + equal_item_weight * items_used)`。
- 漏礦、半挖 cluster、fallback、rejected/no-change。
- 規劃 mean/p95/p99/max。

final_v1 只有在礦坑產出與資源效率都優於 v1、沒有增加 stuck/rejected，且規劃 p99 與 max 都不超過 250 ms 時，才建議裝置切換。程式與設定不會自動切換預設值。

## 非目標

- 不移除或改名既有 planner。
- 不宣稱對未知的無限礦井提供數學全局最優。
- 不根據目前炸彈/鑽頭庫存差推導不同取得機率。
- 不在這個版本引入 RL、POMCP、外部求解服務或新的遊戲協議。
- 不自動修改任何裝置為 `final_v1`。

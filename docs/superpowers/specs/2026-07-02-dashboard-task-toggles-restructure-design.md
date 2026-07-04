# Dashboard 任務開關重構 — 設計

日期：2026-07-02
狀態：待實作（設計已與使用者確認 7 項決策）

## 目標

1. 外層「常用任務開關」升級成「任務開關」，放**每個主要每日任務的總開關**；進階設定浮窗只留細項參數。
2. 「啟用副本」單一勾選拆成 **4 個獨立後端開關**（地獄之門 / 萬神試煉 / 雲端戰鬥 / 雙週賞金）。
3. 補上兩個目前的死開關後端接線（競技場 `enable_arena`、挖礦 `enable_mining`）。
4. 農場、挖礦各用**一個總開關同時控 ADB 與 WS**。
5. 跨服停車加**總開關勾選**；農場買種子/肥料改**獨立勾選**（勾了才顯示數量欄）。

## 使用者確認的決策

- 副本：4 個真正獨立後端開關（含雲端戰鬥）。
- 外層 = 每個任務總開關，裡層 = 只留細項參數。
- 外層範圍：只主要每日任務（下方 8 類），niche 任務總開關留浮窗。
- 死開關：競技場、挖礦兩個都補上後端 gating。
- 農場總開關：一個同時控 ADB(`enable_farm`) + WS(`ws_token.farm`)。
- 跨服停車總開關 + 農場買種子/肥料獨立勾選。

## 後端改動

### config_manager.py — DEFAULT_DEVICE_CONFIG 新增 key（皆預設 True）
- `enable_hellgate`、`enable_wanshen`、`enable_cloud_battle`、`enable_biweekly`
- （`enable_arena` / `enable_mining` 已存在，只是後端沒接；本次補接）
- `DeviceConfig` dataclass 同步加對應欄位。

### new_main_v2.py — 計算 4 個副本 granular flag（含向後相容 fallback）
在目前算 `enable_dungeon_manager` 處（~L135）加算 4 flag，缺值時 fallback：
- `enable_hellgate` ← `enable_hellgate` → `enable_dungeon`(True)
- `enable_wanshen` ← `enable_wanshen` → `enable_dungeon_manager` → `enable_dungeon`
- `enable_cloud_battle` ← 同上鏈
- `enable_biweekly` ← 同上鏈
- 另讀 `enable_arena`、`enable_mining`（預設 True）。

**`enable_dungeon_manager` 保留原樣**，繼續餵給 sleep/ws-fallback 路徑（L185/215/516 不動），避免擴大 blast radius。granular flag 只加到 daily_pipeline 的 `DailyContext`（L409 呼叫點）。

### daily_pipeline.py — DailyContext 加欄位並 gate 各任務
- `DailyContext` 加 6 欄位：`enable_hellgate` / `enable_arena` / `enable_mining` / `enable_wanshen` / `enable_cloud_battle` / `enable_biweekly`（保留現有 `enable_dungeon_manager` 欄位不動）。
- Task 1 地獄之門（~L199）：外包 `if not enable_hellgate: skip`。
- Task 10 競技場（~L315）：`if not enable_arena: skip`。
- Task 11 挖礦/Oracle（~L324）：`if not enable_mining: skip`（與現有 `_ws_skip` 併存）。
- Task 15 萬神試煉：`_run_weekly_dungeon(... enable_wanshen ...)`。
- Task 16 雲端戰鬥（~L421）：`if enable_cloud_battle:`。
- Task 17 雙週：`_run_biweekly_dungeon(... enable_biweekly ...)`。

### dungeon_scheduler.py — 參數改名（純 rename，行為不變）
- `_run_weekly_dungeon(..., enable_dungeon_manager, ...)` → `enable_wanshen`
- `_run_biweekly_dungeon(..., enable_dungeon_manager, ...)` → `enable_biweekly`

## 前端改動（templates/dashboard.html）

### 外層「任務開關」清單（取代「常用任務開關」）

| 外層總開關 | checkbox id | 寫入 config |
|---|---|---|
| 農場 | chkFarm | `enable_farm` + `ws_token.farm` 啟用 |
| 競技場 | chkArena | `enable_arena` |
| 挖礦 | chkMining | `enable_mining` + `ws_token.mining.enabled` |
| 神燈 | chkWsOpenLamp | `ws_token.open_lamp` |
| 看廣告 | chkAdEnabled | `ws_token.ad_rewards.enabled` |
| 跨服停車 | chkCarparkEnabled | `carpark.enabled` |
| 航海 | chkWsSea | `ws_token.sea_season` |
| 副本×4 | chkHellgate / chkWanshen / chkCloudBattle / chkBiweekly | 對應 `enable_*` |

作法：既有 checkbox 的 **id 不變、存檔/載入 JS 不動**，只把 `<label>` 從浮窗頁籤搬到外層清單（DOM 位置無關 getElementById）。新增：4 個副本 checkbox + 跨服停車移出。

- **農場/挖礦一鍵控兩邊**：外層 chkFarm 存檔時同時寫 `enable_farm` 與 `ws_token.farm` 啟用；chkMining 同時寫 `enable_mining` 與 `ws_token.mining.enabled`。載入時任一為真即勾選。
- 進階設定浮窗「農場」「挖礦」「航海」「神燈」「看廣告」「跨服停車」頁籤只留**細項參數**（買種子數/肥料/allow_bomb/座標/tier/JSON…），移除已上移的總開關 label。

### 保留在浮窗（總開關+細項都留）
遺物衝刺、龍骸聖域、大亨、跨服戰、伴侶、加工坊、郵件、競猜、抽卡、秘寶、連線備援。

### 農場買種子/肥料改獨立勾選
`inpFarmBuy407` / `inpFarmBuy408` 目前靠「留空=不買」。改成各配一個啟用 checkbox（`chkFarmBuy407` / `chkFarmBuy408`），勾選才顯示數量欄；`_collectFarmTab` 依勾選決定是否寫入。載入時有值即勾。

## 副本掃蕩頁籤（dungeon tab）
浮窗現有 `dungeon` 頁籤只有 `dungeon_sweeps` JSON（掃蕩），與新的 4 個副本總開關無關，維持在浮窗當細項。

## 測試

- `tests/test_device_config.py`：新 config key 預設值。
- `tests/test_dungeon_scheduler.py`：param rename 後 gate 行為。
- 新增 daily_pipeline gate 測試：`enable_hellgate=False` 跳過地獄之門；`enable_arena/mining=False` 跳過對應任務；4 副本 flag 各自獨立。
- `tests/test_dashboard_template.py`：外層出現 4 副本 + 跨服停車 checkbox；farm buy 獨立勾選 id 存在且唯一。
- 向後相容：舊 config 只有 `enable_dungeon`/`enable_dungeon_manager` 時，4 個新 flag 正確 fallback。

## 分階段實作（每階段獨立 commit）

1. 後端 config key + fallback（config_manager、new_main_v2）+ 測試。
2. daily_pipeline 各任務 gate + dungeon_scheduler rename + 測試。
3. 前端外層清單重排（搬 label、加 4 副本 + 停車、農場/挖礦雙寫）+ 模板測試。
4. 農場買種子/肥料獨立勾選 + collect/load JS。
5. 全套 focused pytest + py_compile；更新過時模板斷言。

## 非目標（YAGNI）
- 不動 sleep/ws-fallback 的 `enable_dungeon_manager` 管線。
- 不把 niche WS 任務總開關移出浮窗。
- 不重寫 ws_token 巢狀 merge 邏輯。

# 莊園 (農場 / PlantMainView) WS 協議 recon

> Live 抓於 5554 H5 (manual-hold, CDP 9230)，帳號 uid 89555436834913「下不維力炸醬麵」，2026-06-14。
> 「莊園」= 遊戲內農場頁 `PlantMainView`（頁面 `txtName="xxx的庄园"` 已確認）。
> cmd id 取自 client 源碼 cmd map（`"family.msg_c2s":id` / `id:"family.msg_s2c"`），c2s/s2c 共用 id；body 用
> `ws_token.codec.walk` 解。已 wired 但 flags off 的純 WS 後端模組：`ws_token/farm.py`。

## 模組 / cmd 速查

| 動作 | family.msg | cmd | 方向 |
|------|-----------|-----|------|
| 農場資訊（地塊/建築） | home.home_farm_info | **3077** (0x0c05) | c2s {role_id} / s2c {…land_list} |
| 種菜 | home.home_farm_plant | **3078** (0x0c06) | {seed_id, land_id} / {code, new_land} |
| 施肥 | home.home_farm_fertilize | **3079** (0x0c07) | {role_id, land_id, fertilizer_id, num} / {code, role_id, new_land} |
| 偷菜（採摘他人） | home.home_farm_pick | **3080** (0x0c08) | {role_id, land_id} / {code, role_id, new_land} |
| 收成 | home.home_farm_harvest | **3081** (0x0c09) | {land_id} / {code, new_land, level, exp, reward_list} |
| 打工設定（開管家） | worker_common.worker_common_farm_worker_setting | **18689** (0x4901) | {team_cfg_id, fertilizer_list[], fertilizer_time_rest, seed_used_seq_list[]} / {worker_info:p_worker} |
| **打工偵測（讀狀態）** | worker_common.worker_common_farm_get_other_role_info | **18690** (0x4902) | {role_id, team_cfg_id[]} / {team_list:p_other_worker[]} |
| 商店資訊 | shop.shop_info | **6913** (0x1b01) | {shop_type} / {shop_type, …} |
| 購買 / 賣出 | shop.shop_buy | **6914** (0x1b02) | {shop_type, shop_id, num} / {shop_id, num} |
| 失敗統一回報 | error.error_info | **0x0201** (513) | {error_code} |

模組換算：cmd = module*256 + N。home=12(0x0c)、shop=27(0x1b)、worker_common=73(0x49)。

---

## 1) 購買 WS（種子 / 肥料） — 已 live 驗證

`shop.shop_buy` (**6914**) c2s `{shop_type#1:uint32, shop_id#2:uint32, num#3:uint32}`，
s2c `{shop_id#1, num#2}`。買到的東西用 **`item_change` (cmd 1030, 0x0406)** 推背包增量、
商店當日已購次數用 **`shop_info` (6913)** 推回（吃階梯價）。失敗走 0x0201。

開/刷新農場商店 = `shop.shop_info` (**6913**) `{shop_type#1=4}`（farm 頁 `btnSeedBuy` 觸發，
開 `PlantMainView/container/PlantShopView`）。

**農場商店 = shop_type 4**（`configMall` 內 shop_type==4 共 2 項）：

| shop_id | 給的物品 | 幣別 | 階梯價 (configMall) |
|---------|---------|------|-----|
| **407** | item **102**（初級種子）×1 | 金幣(幣2) | [0,20,30,50,100,260,260,330,680] |
| **408** | item **111**（高產肥料）×5 | 金幣(幣2) | [0,50,90,140,400,660,660,840,1710] |

> Live 實證（注入 `shop_buy{4,407,1}`）：
> `tx 6914 {1:4,2:407,3:1}` → `rx 1030 {item102 ×1}` → `rx 6914 {407,1}` → `rx 6913 {4,{407,1}}`。
>
> 買初級種子：`shop_buy {shop_type:4, shop_id:407, num:N}`
> 買高產肥料：`shop_buy {shop_type:4, shop_id:408, num:N}`（每次給 5 個）
> ⚠ 階梯價：當日第 N 次購買價格遞增（[0,20,30,…]，index = 當日已買次數）。
> **豐收卡（菜園豐收卡）live-confirmed 2026-06-15**：
> `configMall row 1604` → `_data = [1604, 11, [240006,1], [201,40000], ...]`
> ⇒ **shop_type = 11, shop_id = 1604, item 240006 ×1, 粉鑽 201 × 40000**。
> `ws_token/farm.py` `HARVEST_CARD_SHOP_TYPE=11 / HARVEST_CARD_SHOP_ID=1604` 已更新。

### 買到每日上限「4/4」— num 語意 + shop_info 讀計數（live 驗證）
- **`num` = 單次購買數量**（live：`shop_buy{4,407,3}` → rx 1030 給 item102 **×3**、當日計數 1→4）。不需逐次買。
- **讀當日已購計數** = `shop.shop_info` (6913) `{shop_type#1}` → s2c `{shop_type#1, item#2:repeated{shop_id#1, bought_count#2}}`；
  **未購買的 shop_id 不出現**（預設 0）。live：`{2:{1:407, 2:4}}`。shop_info 每次都可靠回應（不像 home_farm_info 去重）。
- **買到 4/4 正解（尊重 GUI 已買）**：先 `shop_info` 讀現值 → `need = target − current` → `need>0` 才 `shop_buy num=need`；
  `current>=target` 直接跳過（使用者若已在 GUI 買過就不重買，且階梯價不重複付）。
- live 端到端驗證：種子 407 已 4/4 → 跳過；肥料 408 0/4 → 買 4 → 兩者 `{407:4, 408:4}`。
- 實作：`ws_token/farm.py` `read_shop_counts` / `buy_to_daily_target` / `buy_farm_shop`；
  runner `_run_farm` 讀 `farm_config["buy"]=[{shop_id,target}]`。設定寫在 **bot_config.json → 裝置 → `ws_token.farm`**
  （5554 已設 `{"buy":[{407,4},{408,4}]}`）。

---

## 2) 施肥 / 種菜 WS — 已 live 驗證

### 施肥 `home.home_farm_fertilize` (**3079**)
c2s `{role_id#1:uint64, land_id#2:uint32, fertilizer_id#3:uint32, num#4:uint32}`，
s2c `{code#1, role_id#2, new_land#3:p_farm_land}`。**per-land**（無批次 cmd；「一鍵施肥」=
client 端對每塊地各送一次）。

> Live 實證（farm 頁 `OneKeyOprate/btnOneKeyGrow` → `FertilizeSelectView` 選肥料 → `btnUse`）：
> 6 塊地各送 `tx 3079 {1:0, 2:land_id(1..6), 3:111, 4:1}`。
> - **role_id#1 client 傳 0**（伺服器用連線身分），farm.py builder 照送 0 即可。
> - **fertilizer_id 111 = 高產肥料**（= shop 408 給的 item，互相印證）。num=1/塊。
> - s2c：`rx 3079 {1:0(code), 2:89555436834913(role), 3:new_land}`。
>
> 肥料種類（`FertilizeSelectView`）：`btnFertilizeGet`=普通肥料(免費,本帳0庫存)、
> `btnFertilizeBuy`=**高產肥料 id 111**、`btnFertilizeHelp`=友情肥料、`btnFertilizeAd`=看廣告+20。
> 普通/友情肥料 item id 未抓（缺貨/未觸發），可從 config 或日後補。

### 種菜 `home.home_farm_plant` (**3078**)
c2s `{seed_id#1:uint32, land_id#2:uint32}`（seed_id **先**，land_id 後），
s2c `{code#1, new_land#2:p_farm_land}`。per-land。

> Live 實證（施肥使作物成熟 → 管家自動收+補種，抓 `rx 3078` new_land）：
> 種出的 crop `seed_id#3=101`、`cfg_id#4=6011/6012/6013`、`state#5` 2(成熟)→7。
> - **seed_id 101 = 免費種子**（管家用的；farm 頁 `btnSeedGet` 7/16 免費領）。
> - **seed_id 102 = 初級種子**（金幣 shop 407 買的）。
> - `cfg_id` = 長出的隨機作物（`configFarm_greens` 的 6011~6017；第二欄=生長秒數 7200/14400/28800）。
> - crop.state#5：0=空地, 1=生長, 2=成熟可收, 7=已收/枯。成熟另看 `end_time#8 <= serverTime`。

### p_farm_land / p_farm_crop（TYPE schema）
`p_farm_land {id#1, crop#2:p_farm_crop}`（crop 不存在 = 空地）
`p_farm_crop {id#1, role_id#2, seed_id#3, cfg_id#4, state#5, start_time#6, acc_time#7, end_time#8, …}`

`home_farm_info_s2c` (3077) = `{role_id#1, name#2, level#3, exp#4, land_list#5:p_farm_land[],
building_list#6:p_farm_building[], self_stolen_list#7, can_help_battle#8}` — **不含 worker**。
⚠ 伺服器每連線 session 對 3077 大致只答一次（client/server 去重）；farm.py 已註記「重用已讀的 info」。

---

## 3) 打工偵測 WS — 已 live 驗證（重點）

**worker_common(模組73) 沒有「讀自己打工狀態」的專用 cmd**（窮舉 18689~18702，最低 18689 是 setting，
無 info）。`home_farm_info(3077)` 也不帶 worker。加工坊的 `worker_pw_info(18434)` 只回加工坊 worker
(team 6001/6002，含 pw_info#7、無 farm_info#6)，**不含農場 worker**。

**正解（純讀取）**：`worker_common.worker_common_farm_get_other_role_info` (**18690**)
c2s `{role_id#1:uint64 = 自己, team_cfg_id#2:repeated uint32 = [7001]}`，
s2c `{team_list#1:p_other_worker[]}`，
`p_other_worker {role_id#1, team_cfg_id#2, pet_info#3:p_pet, worker_status#4:uint32, fly_pet_list#5:p_fly_pet[]}`。

> Live 實證：`send {role_id:self, team_cfg_id:[7001]}` → `p_other_worker {role_id:self, team_cfg_id:7001,
> worker_status:1}`。**worker_status#4 > 0 = 打工運作中**。
> - **農場打工 team_cfg_id = 7001**（加工坊是 6001/6002；農場固定 7001）。
> - **必須帶 team_cfg_id**：空 list / 省略 → 回空。
> - 雖名為 get_*OTHER*_role_info，帶自己 role_id 即讀自己，且**不變動狀態**（優於重送 18689 setting）。

旁證：施肥觸發作物成熟後，6 塊地瞬間被管家自動收+補種（state 2→7、seed 101 重種、`rx 3078/3081` 推播）
→ 印證打工運作中。管家活動是**事件驅動**（靜止時不持續推播）。

### 開/設定打工 `worker_common_farm_worker_setting` (**18689**)
c2s `{team_cfg_id#1, fertilizer_list#2:repeated uint32, fertilizer_time_rest#3,
seed_used_seq_list#4:repeated p_key_value{k,v}}`，s2c `{worker_info#1:p_worker}`。
`p_worker {team_cfg_id#1, worker_base#2, worker_status#3, auto_feed#4, unlock_slot_num#5,
farm_worker_info#6:p_worker_farm_info, …}`。seed_used_seq 空 = 用免費種子 = 不買種。
（farm.py `start_work` 已實作；team_cfg_id 用 **7001**。）

---

## 對 `ws_token/farm.py` 的修正（本次 recon 補實）

| farm.py 既有 | 狀態 |
|---|---|
| CMD_INFO 3077 / PLANT 3078 / FERTILIZE 3079 / PICK 3080 / HARVEST 3081 | ✅ cmd 正確 |
| build_plant_body {seed_id,land_id} | ✅；seed_id 免費=101 / 買=102 |
| **build_fertilize_body（缺）** | ➕ 本次補：{role_id=0, land_id, fertilizer_id=111, num=1} |
| DEFAULT_SEED_ID = None | → **101**（免費）；買種走 shop 407→item102 |
| DEFAULT_FERTILIZER_ID = None | → **111**（高產肥料） |
| DEFAULT_TEAM_CFG_ID = None | → **7001** |
| CMD_SHOP_BUY 6914 {shop_type,shop_id,num} | ✅；種子 type4/id407、高產肥料 type4/id408 |
| HARVEST_CARD_SHOP_TYPE/ID = None | ✅ **live-confirmed 2026-06-15**：type=11 / id=1604（粉鑽 201×40000） |
| **打工偵測（缺）** | ➕ 本次補：get_other_role_info 18690 讀 worker_status |

---

## 4) 取消打工 / 簡易開始打工 — 已 live 驗證 (2026-06-15, 小寶 7fe98fc6)

**純 WS 方式停用 / 啟用打工同伴**（不需 Playwright 點擊）：

| 動作 | cmd (hex) | module | body |
|------|----------|--------|------|
| 開始打工（簡易）| **18177** (0x4701) | 71 | `{field1: 1001}` = `08 e9 07` |
| 取消打工 | **18178** (0x4702) | 71 | `{field1: 1001}` = `08 e9 07` |

> Live 實證（tools/_farm_full_capture.py, CDP 9226）：
> - startBtn「開始打工」點擊 → `tx cmd=18177 hex=08 e9 07`
> - cancelBtn「取消打工」點擊 → `tx cmd=18178 hex=08 e9 07`
>
> 兩者 body 完全相同；cmd 本身區分開始 vs 取消。
> 解碼：`08` = field 1 varint, `e9 07` = varint 1001（field 1 = 1001）。
>
> 注意：這是 **module 71** 的 cmd，與 `worker_setting` (18689, module 73) **不同**。
> 18177/18178 = 簡易開/關（不改配置）；18689 = 改設定+開始（改同伴/肥料）。

`ws_token/farm.py` 已補：`CMD_WORKER_START=18177`, `CMD_WORKER_CANCEL=18178`,
`FARM_WORK_ID=1001`, `stop_work()`, `start_work_simple()`, `run_harvest_card_cycle()`。

---

## 5) 豐收卡完整流程 `run_harvest_card_cycle`

```
stop_work()                     # cmd 18178 {1:1001}
fertilize_lands()               # cmd 3079 per-land
harvest_ready()                 # cmd 3081 per-land（best-effort，~50% timeout）
buy_to_daily_target(1604, n)    # shop_buy cmd 6914 {shop_type=11, shop_id=1604, num}
plant_empty(seed_id=103)        # cmd 3078 {seed_id=103, land_id}
start_work_simple()             # cmd 18177 {1:1001}
```

特級種子 seed_id = **103**（`configGoods id=103`，live-confirmed）。
實作：`ws_token/farm.py::run_harvest_card_cycle`。
觸發：`ws_token.farm_config["harvest_card_cycle_enabled"] = true`（待接 runner）。

---

## 仍待 live 補
- 普通肥料 / 友情肥料 item id（FertilizeSelectView 缺貨未觸發）。
- 一筆真實 `plant` c2s（本次以管家 replant 的 s2c 反推 seed_id；c2s 欄序已由 schema+cmd map 確認）。

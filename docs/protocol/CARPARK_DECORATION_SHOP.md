# 車友商行 / 裝飾 (Parking Decoration) — Recon Recipe + Offline Groundwork (2026-06-14)

> 狀態：**catalog + 成本模型 live 定案 (2026-06-15) — 見 §9。**
> 屬性曲線、碎片成本、貨幣、限購、顯示 % 換算皆已 live 校準。
> **純 WS read+write 全解並接進 dashboard (§10)：** read=skin_list(12801 type0)
> + shop_info(6913 type11) + 菇車幣(role attr 201)；write=buy(shop_buy 6914
> {11,shop_id,num}) + upgrade(car_park_skin_up 12817 {type:0,skin_id})。live 驗
> 一次真實 buy+upgrade 通過(異世之界 lv1→2,扣 20 萬菇車幣 exact)。
> **重大更正：目前服務端設定封頂 15 星 (不是 20，見 §9.1)。**

本文件依 `docs/protocol/CARPARK_GUILD_NODES.md` §C 的 JS-bundle 法，從
`docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`
反推。所有「cmd 編號 / body 形狀 / catalog 欄位」都標明來源行；標 LIVE-TODO 的
需開窗採樣才算定案。

---

## 0. 重要更正：裝飾 **不是** 走 shop.shop_buy

prior recon 猜「裝飾商店重用 `shop.*` (shop_buy 6914 + 新 shop_type)」。**錯。**
裝飾 (skin/decoration) 是 `car_park.*` 自己的一組 cmd，跟農場豐收卡那種
`ParkingShopView` (走 mall/shop) 是 **不同系統**。bundle line 7503 的
`car_park.*` family 內有專屬的 `car_park_skin_*` cmd。所以：

- **沒有 shop_type 要找。** 買裝飾 = `car_park.car_park_skin_up_c2s`，server 自
  catalog 表讀 `expend` 扣款，不經 mall。
- harvest-card 的 `ParkingShopView` 仍是另一條路 (mall 點卡)；裝飾走
  `ParkingDecorateView`。兩者唯一共通是底部 nav bar。

---

## 1. Cocos 進入路徑 (entry)

主頁面 → 車位 (`ParkingMainView`) → 底部 `bottom/btnSkin` → `ParkingDecorateView`。

```
/UIRoot/NormalView/ParkingMainView/bottom/btnSkin   ← 點此開「裝扮/裝飾」
  → uiMgr.openView("ParkingDecorateView")
/UIRoot/NormalView/ParkingMainView/container/ParkingDecorateView   ← 裝飾介面
```

(來源：`CARPARK_GUILD_NODES.md` §A.3 底部 nav bar；bundle ParkingDecorateView
module lines 7547/7551/7569。)

進車位本身的導航已有現成 dual-backend helper，直接複用，勿重寫：
`farm_v2/operations/harvest_card.py`
`_navigate_home_to_carpark` (cocos `dismiss_blocking_popups` + `goto_main` +
`carpark_node` emit-click，OCR「車位」fallback，再用
`utils.carpark_state.parking_view_is_open` 驗證)。開裝飾面板比照
`_open_carpark_shop` 改點 `bottom/btnSkin` 即可。

---

## 2. 協議 (cmd) — 全部來自 bundle 的 cmd-number 表，已定案編號

cmd = module*256 + N，module 50 (car_park)。bundle 內 `"car_park.<name>":NNN`
明文對照表 (line ~7503 區) 直接給編號，**不需臆測**：

| cmd name (c2s) | 編號 | body (from `netManager.send` 呼叫) | 用途 |
|----|----|----|----|
| `car_park.car_park_info_c2s` | **12801** | `{type#1, master_id#2, ceng#3}` | 讀車位 (含自己, type 待採) |
| `car_park.car_park_skin_up_c2s` | **12817** | `{type, skin_id}` | **買 + 升級裝飾** (server 依 catalog (id,level) 扣 expend) |
| `car_park.car_park_skin_use_c2s` | **12810** | `{type, skin_id, is_use, pos:{x,y}}` | 裝備/卸下裝飾 (不花錢) |
| `car_park.car_park_car_up_c2s` | **12803** | (坐騎升級，非裝飾) | — |

req wrappers (bundle，`IS(k)` = ParkingControl)：

```js
// 買 / 升級 (花 expend)：同一個 cmd，server 看 (id, 當前level) 決定下一級
reqSkinUp = function(type, skin_id){
  netManager.send("car_park.car_park_skin_up_c2s", {type, skin_id}, true)
}
// 裝備 / 卸下 (不花錢)
reqParkSkinUse = function(type, skin_id, is_use, pos){
  // pos 預設 {x:0,y:0}
  netManager.send("car_park.car_park_skin_use_c2s",
                  {type, skin_id, is_use, pos}, true)
}
```

**關鍵推論：買跟升級是同一個 cmd `12817 {type, skin_id}`。** client 不送 level；
server 從你目前的 `skin_lev` 推下一級，讀 `configParking_design(id, level+1)` 的
`expend` 扣款。所以 picker 產出的「buy」與「upgrade」step 在 wire 上都是同一個
`reqSkinUp(type, id)` 呼叫，差別只在執行前的 owned level 與要不要連點數次。

`type` 欄位 = 車位類型 (`IS(D).type`)；本服/跨界/家園共用 ParkingControl，多半
帶當前 ParkingMainView 的 type。**LIVE-TODO：採樣確認 `type` 的實際值** (家園裝飾
應是本服 type；採一筆成功 round-trip 對齊)。

---

## 3. Catalog 資料來源 — `configParking_design` (即 `ConfigParking_design.ts`)

裝飾目錄是 client 端 config 表 `configParking_design`，**不需 WS 拉**，但要從
runtime 讀 (`configParking_design.getDatas()` / `getDataByKeys("id",id,"level",lev)`)。
表以 **(id, level)** 為 key：一個裝飾每個等級一行。欄位 index → 名稱 (bundle
register guid `037d7daDkhNQqHpmJhH3OSI`，逐 getter 抽出)：

| idx | 欄位 | 型別 | picker 用途 |
|----|----|----|----|
| 0 | `id` | int | 裝飾 id |
| 1 | `level` | int | 此行的等級 (0 == 免費預設行) |
| 2 | `position` | enum | 部位 TYPE_DOOR/FLOOR/FANCE/LIGHT/DEC |
| 3 | `expend` | `[[goods_id, amount], ...]` | **cost** = `expend[0][1]`，currency = `expend[0][0]` |
| 4 | `own_attrs` | `[[attr_id, value], ...]` | **benefit** = `own_attrs[0][1]` (擁有即加的屬性) |
| 5 | `effect` | `[[k,v],...]` | 一般戰鬥 effect (非 picker 指標) |
| 6 | `pvp_effect` | `[[k,v],...]` | 跨服 PVP effect |
| 7 | `if_initial` | 0/1 | 1 = 免費初始裝飾 (不可買，picker 跳過) |
| 8 | `pos_occupy` | `[w,h]` | 擺放佔格 |
| 9 | `name` | str | 顯示名 |
| 10..17 | path/show_path/icon.../scaling | str/num | 純美術 |
| 18 | `desc` | str | 說明 |
| 19 | `desc_parm` | list | 說明參數 |
| 20 | `power` | int | 戰力 (可作 benefit 的替代指標，見 §4) |

佐證 (bundle)：
- 升級下一級的成本：`configParking_design.getDataByKeys("id",decID,"level",skin_lev+1)`
  → 讀其 `.expend`；affordability = `getGoodsCountByGoodsGtid(expend[0][0]) >= expend[0][1]`。
- 屬性加成彙總 (`getDecBuff`)：對 `skin_list` 每筆 `getDataByKeys(id, skin_lev)`，
  把 `own_attrs` 的 `[attr,val]` 累加；`refreshBuffInfo` 顯示 `+##1%`，值 = `own_attrs[0][1]/100`
  → **benefit 單位是「屬性點 (百分比*100)」，越大越好**。
- 免費初始：`if_initial==1` 的行 `position` 直接當預設裝飾，買升級時
  `1!=getDataByKeys(id,0).if_initial` 才允許 (見 btnCancel 綁定)。

### 我已擁有什麼 (owned) — `skin_list`

登入/讀車位回包帶 `skin_list`，decorate view 以 `n[a.skin_id]=a.skin_lev` 建
「id→已擁有等級」表。每筆 entry 觀察到的欄位 = `{skin_id, skin_lev, k, v}`
(k/v 是 render 用 attr，如 server_id；`skin_lev` 才是等級)。
**LIVE-TODO：採一筆 `car_park_info_s2c` / 登入回包確認 `skin_list` 的 protobuf
field number 與 `skin_lev` 是否真在每筆 entry** (decorate view 讀得到，但 wire
schema 要對齊 `ws_token/codec.walk` 才能 parse)。

---

## 4. 成本效益指標 (picker 用的定義)

picker (`ws_token/carpark_decoration.py`) 選「**cost per benefit** = cost /
benefit」最低者優先 = 每一點屬性最便宜的買法，正是「最划算」。

- **cost** = catalog 行 `expend[0][1]` (花費 currency 數量)。
- **benefit** = catalog 行 `own_attrs[0][1]` (此等級擁有即加的屬性點)。
- 買 = 從 level 0 (未擁有) 取最低可買等級行；升級 = owned level → level+1，
  benefit 取「該級行的 own_attrs」(目前實作以該級絕對值代表邊際增益，**LIVE-TODO**：
  若 catalog 的 `own_attrs` 是「累計總值」而非「該級增量」，picker 升級的 benefit
  要改成 `own_attrs(level+1) - own_attrs(level)` 的差值；採樣兩級資料即可定)。
- `benefit <= 0` (純美術裝飾) 跳過 (不為了屬性買它)。
- 若改用 **`power` (戰力)** 當 benefit 也成立 (同樣 lowest cost-per-power)，
  catalog 行只要把 `benefit` 餵 power 即可，picker 無需改。採樣後若 own_attrs
  噪音大，可切 power。

為何 greedy-by-ratio 而非完整 knapsack：本任務硬限制是 **嘗試次數**
(max_buys/max_upgrades，使用者「不能過多次」)，不是緊預算；cheapest-per-stat-first
同時最大化「每次嘗試的屬性」與「每塊錢的屬性」。O(n log n)、deterministic、log
可讀，優於不透明 DP。詳見模組 docstring。

---

## 5. Live-recon 步驟 (待白天 10:00-22:00 車友商行開放) — 必做才可接 action

開窗後在 bot 的 Chrome (CDP attach) 上，照 `live-protocol-decoder` /
`cocos-app-analysis` skill：

1. **catalog dump (純 client，最快)**：
   ```js
   // page.evaluate — 不用等任何 WS
   configParking_design.getDatas().map(r => ({
     id:r.id, level:r.level, position:r.position, if_initial:r.if_initial,
     expend:r.expend, own_attrs:r.own_attrs, power:r.power, name:r.name,
   }))
   ```
   存成 `docs/protocol/PARKING_DESIGN_CATALOG.json`。確認：
   - `expend` 是不是單一 currency (`expend[0][0]` 是哪個 goods id；查
     `configGoods.getDataByKey(goods_id).name`)。
   - `own_attrs` 是「該級增量」還是「累計」(看同 id 連兩級的值是否遞增等差)。
   - level 是不是 1-based、0 行是否就是 `if_initial`。
2. **buy/upgrade round-trip 校準** (確認 12817 body field number)：
   - 裝 ws probe，在 `ParkingDecorateView` 點一個可負擔裝飾的「購買/升級」。
   - drain ring buffer：確認 tx cmd == **12817**，decode body 對齊
     `{type#?, skin_id#?}` 的真實 field number (client 變數名 ≠ wire field number，
     **務必採樣**，比照 carpark.py 對 12847 的處理)。
   - 記錄成功 s2c (應回 `car_park_skin_up_s2c` / 推 `skin_list` 更新)，與失敗碼
     (錢不夠 / 已達上限的 0x0201 error_code，比照 carpark 12846 的 code=173 模式)。
3. **owned `skin_list` schema**：採同一筆回包 + 登入回包，對齊 `skin_id` /
   `skin_lev` 的 protobuf field number，寫進 (新的) `ws_token/carpark.py` 風格
   parser (本任務 **不** 改 carpark.py，留給接線 worker)。
4. **type 值**：確認家園裝飾的 `type` (本服?)，與 `reqParkSkinUse` 的 `pos`。

採完更新本文件「LIVE-TODO」段落為定案，再由接線者把 picker 輸出接到
`reqSkinUp(type, id)` 的實際 WS 呼叫。

---

## 6. Bounded-purchase 安全規則 (使用者：「可嘗試購買與升級，不能過多次」)

接 action 時務必遵守 (picker 已在純函式層 enforce 前三條)：

1. **每次喚醒的嘗試上限**：`max_buys` + `max_upgrades` 合計小 (建議 buy<=1、
   upgrade<=2/輪)。picker 的 attempt cap 是硬上限，超過不再選。
2. **預算上限**：`budget` = 願意花的 currency 上限；picker 累計 cost 不超過它。
   接線時 budget 應讀「目前 currency 餘額」與「保留底線」取小。
3. **跳過已滿級 / 純美術**：catalog 無下一級行 → 該 id 不再升 (picker `_next_step`
   回 None)；`benefit<=0` 跳過。
4. **idempotent / 防連點**：每個 step 對應一次 `reqSkinUp`，等 s2c 或 error 再
   送下一個 (序列化，勿並發)；收到「不足 / 上限」error 即停該 id，不重試。
5. **失敗即停，不洗版**：任一 step server 拒 (0x0201) → 記 log、停止本輪後續
   購買 (比照 `_buy_harvest_card_in_shop` 的「未成立就停」)。
6. **窗口/節流**：車友商行白天才開；非開放時 catalog/購買可能無效 → 接線時 gate
   在開窗時段，且每裝置每日 / 每喚醒最多跑一次裝飾規劃 (持久化已跑旗標，避免
   每小時喚醒都嘗試)。

---

## 7. Picker API (已實作，純函式，可離線測)

`ws_token/carpark_decoration.py`：

```python
pick_best_decoration(
    catalog,        # iterable[CatalogRow | dict]  一行一個 (id, level)
    owned,          # {id: level} 或 skin_list-like [{skin_id, skin_lev}]
    budget,         # int 花費上限
    *, max_buys,    # int 本輪最多買幾個新裝飾 (bounded)
    max_upgrades,   # int 本輪最多升幾級 (bounded)
) -> DecorationPlan
```

`CatalogRow{id, level, cost, benefit, cost_goods, max_level}`；dict 行可給
flat `{id,level,cost,benefit}` 或 catalog 原生 `{id,level,expend,own_attrs}`
(自動抽 `expend[0]` / `own_attrs[0]`)。`DecorationPlan{steps, total_cost,
total_benefit, buys, upgrades, skipped_reason}`，`Step{id, kind('buy'|'upgrade'),
from_level, to_level, cost, benefit, cost_per_benefit}`。greedy by 最低
cost-per-benefit，尊重 budget 與兩個 attempt cap，跳過滿級/零效益；空計畫時
`skipped_reason` ∈ {no_catalog, zero_quota, no_candidate, no_budget}。

測試：`python -m pytest tests/test_carpark_decoration.py -q` (13 passed)。

---

## 8. 相關檔案

- `ws_token/carpark_decoration.py` — 本 picker (NEW)。
- `tests/test_carpark_decoration.py` — 13 TDD 測試 (NEW)。
- `ws_token/carpark.py` — 跨界停車純 WS (codec / call_for 風格參考；**未改**)。
- `farm_v2/operations/harvest_card.py` — 車位 shop 導航 dual-backend 範本。
- `docs/protocol/CARPARK_GUILD_NODES.md` §A.3/§C — 車位節點 + JS-bundle 反推法。
- `docs/protocol/CARPARK_AUTOMATION.md` — 車位自動化總設計。
- `docs/protocol/PARKING_DESIGN_CATALOG.json` — live catalog dump (983 行, 2026-06-15)。
- `tools/dump_parking_design.py` / `analyze_parking_design.py` / `scan_decor_inventory.py` — 本次 live 工具。

---

## 9. LIVE 定案 (2026-06-15, 5556 / CDP 9223)

工具：`tools/dump_parking_design.py`（catalog dump）、`analyze_parking_design.py`
（曲線/成本）、`nav_parking_decorate.py` + `read_detail_fields.py` + `read_star_slots.py`
（live UI 校準）、`scan_decor_inventory.py`（持有盤點）。

### 9.1 重大更正：目前封頂 15 星，不是 20

三重證據一致：
1. `configParking_design.getDatas()` = 983 行 / 68 裝飾。標準裝飾 `level` 只到 0..15
   （16 行），活動款 0..10。983 = 54×16 + 9×11 + 1×16 + 4×1，剛好對齊，無缺漏。
2. `getDataByKeys('id',10002,'level',N)` 在 N=16..21 全回 `null`。
3. UI 星級 = 5 顆星，每顆 fill 子節點只有 `one/two/three/dark`（無 `four`）= 5×3 = **15 段**。
   花門當前 lv8 顯示 pip = 2+2+2+1+1 = 8。

→ 「20 星」在 5556 當前載入的服務端設定裡**不存在**。可能：使用者記憶/目測誤差、
   或別台裝置/別服較新設定、或未來改版。**接 action 前需與使用者確認來源。**

### 9.2 屬性加成曲線（標準款 54 個共用同一條；活動 9 款封 lv10）

- 顯示「攻擊/生命/防禦加成 %」= `own_attrs` 原始值 / 100。三圍各自獨立同值。
- 每星「擁有即加」(裝扮擁有效果，對**所有持有**裝飾累加，非僅裝備中)：

| 星 | 每圍加成% | 升此星需碎片 | 累計碎片 | 戰力(power) |
|---|---|---|---|---|
| 1 | 320% | 1 (買=解鎖) | 1 | 20000 |
| 2 | 480% | 1 | 2 | 26500 |
| 3 | 640% | 2 | 4 | 33600 |
| 4 | 800% | 2 | 6 | 41300 |
| 5 | 960% | 3 | 9 | 49600 |
| 6 | 1120% | 3 | 12 | 58500 |
| 7 | 1280% | 4 | 16 | 67900 |
| 8 | 1440% | 4 | 20 | 78000 |
| 9 | 1600% | 5 | 25 | 88700 |
| 10 | 1760% | 5 | 30 | 100000 |
| 11 | 1920% | 10 | 40 | 111900 |
| 12 | 2080% | 10 | 50 | 124400 |
| 13 | 2240% | 20 | 70 | 137500 |
| 14 | 2400% | 20 | 90 | 151200 |
| 15 | 2560% | 30 | 120 | 165500 |

- 邊際：lv1 一次 +320%/圍（最划算，1 碎片）；lv2~15 每星固定 +160%/圍 (+48000 原始三圍合計)。
- live 對齊：花門 lv8=1440%、中式庭院大門 lv6=1120%、卡通大門 lv10=1760%，皆 = 表值。

### 9.3 成本模型（live 校準，**修正離線假設**）

- **每個裝飾吃自己的同名碎片**（花門吃「花門」碎片 goods 60102…），**無共用貨幣**。
  → 離線 picker 把 budget 當單一貨幣是**錯的**；真實 budget 是「每碎片各自庫存/可買量」。
- 升級 lv N→N+1 需碎片 = `expend(row N)`：1,1,2,2,3,3,4,4,5,5,10,10,20,20,30（封頂累計 120）。
  live 對齊：花門 lv8 詳情顯示「0/5」= 持 0、需 5 = expend(row8)=5。✓
- **碎片來源 = 詳情頁「購買」鈕**，花 **菇車幣** 買，單價隨裝飾不同（實測 100k~600k/個），
  且每裝飾有 **限購 X/120 終身上限**。
- `own_attrs` 是**累計總值**（64/64 單調遞增）→ picker 必須 `cumulative_benefit=True`
  （邊際 = own_attrs(target) − own_attrs(from)）。離線預設 False 會算錯。

### 9.4 特殊「裝扮加成」(effect 欄；**固定，不隨星成長**)

`effect = [[coin,?],[exp,?],[spec,?],[protect,?]]`，值/100 = %（protect 為分鐘）：
- coin = 私人車位 菇車幣收益%、exp = 改裝點收益%、spec = 額外奇遇機率%、protect = 坐騎停車保護分鐘。
- 部分裝飾 effect 為空、desc 為「車位戰鬥時 攻/生/防 提高」= 車位 PVP 戰鬥加成（吃 pvp_effect）。
- lv1 == max（不成長），與會成長的「基礎攻/生/防 屬性」是兩套獨立加成。
  範例：花門 +5% 菇車幣、中式庭院大門 +奇遇%、迎春來 +坐騎保護分鐘、萬事興 戰鬥攻+生+防。

### 9.5 持有現況 (5556, 2026-06-15)

- 菇車幣餘額 ≈ **4.15 億**。
- **所有裝飾「現有碎片」皆 0**（要先買碎片才能升）。
- 已持有裝飾多在 **lv6~10**，無一封頂；各裝飾限購已用 **90~108/120**（剩 12~30 可買）。
  例：花門 lv8(100/120,單價20萬)、中式庭院大門 lv6(108/120,30萬)、卡通大門 lv10(90/120,10萬)。

### 9.6 成本效益結論（actionable）

真實指標 = **coin-per-attr = (碎片數 × 該裝飾碎片單價) ÷ 邊際屬性**，由低到高貪婪：
1. **買「未擁有」裝飾到 lv1** 最划算：1 碎片 → +96000 三圍合計（單價/96000 coin/attr）。
2. 低星升級（lv6→7=4 碎片）通常優於高星（lv10→11=10、lv12→13=20、lv14→15=30 碎片）。
3. **但碎片單價差異大**（卡通大門 10 萬 vs 場景款 60 萬），便宜單價的高星可能比貴單價的低星更划算
   → 不能只看星級，必須 coin-per-attr 綜合排序。
4. 約束：每裝飾限購 120 上限、coin 餘額、每喚醒嘗試次數（bounded）。

→ picker 接線需改：cost 餵 `frags×price`(coin)、benefit 餵邊際屬性、`cumulative_benefit=True`、
   加 per-deco 限購 cap。詳見 §6 安全規則。

### 9.7 取得 vs 升級 流程 (live 2026-06-15) — 修正「可自由買任意裝飾」假設

- **升級面板 (車位→底部 btnSkin → `ParkingDecorateView`) 只列「已擁有」裝飾。**
  grid cell 數 = 已擁有數 (例 大門 5 格 = 已有 拱門/花門/中式庭院大門/卡通大門/凱旋門)；
  detail 的 ◀▶ (`nodeShow/btnLeft|btnRight`) 只在已擁有間循環，到尾即 wrap。**未擁有裝飾不在此面板。**
- **取得新裝飾 = 車友商行 (`ParkingMainView/bottom/btnShop`) → `ParkingDecorateSelectView`「装扮自选」**，
  是「(1/3) 限定自選」chooser，只有 `content/btnUse`(選用)、**無「購買」鈕/無標價** → 像活動/登入
  免費三選一，**非任意付費購買**。(本次見到的當期 = 菇菇保安亭/保護時間+20 分。)
- **升級面板的「購買」鈕 = 用菇車幣買「已擁有」裝飾的碎片**(非買新裝飾)，再按「升級」
  (`btnUnlock`, skin_up 0x3211=12817) 消耗碎片升星。`限購 X/120` = 每裝飾終身碎片購買上限。
- 結論：使用者原想的「買未擁有裝飾」**非自由商店行為**；新裝飾要走 装扮自选 限定自選，否則只能
  升級已有裝飾。WS 的 buy(碎片) / skin_up cmd body 仍待「實際升級一次已擁有裝飾」才採得到
  (本次 probe 已裝 `window.__probe_inst`，但未實際花費，故未採到 round-trip body)。
- 可復用 live 工具：`tools/rawcdp.py`(raw 單頁 CDP，繞過 Playwright 多 target attach 卡死)、
  `tools/carpark_rawverify.py`(survey/walk/shop/tree/probe-install/full-buy)。

### 9.8 WS round-trip 定案 (live 採到, 2026-06-15) — **修正 §2 的 12817-as-request**

實測：在 5556 把「中式庭院大門」(id 10003) lv6→7（買 4 碎片 + 升級），WS probe 採到完整 round-trip。

**A. 買碎片 = Mall `shop.shop_buy` `0x1b02`(6914)** — 點升級面板「購買」開 `MallTipsView`
(數量 +/− 對話框, btnAdd/btnMinus/btnAddTen(±5)/btnBuy/EditBox/price)，確認後送：
```
tx 0x1b02(6914) body = {1: shop_type=11, 2: shop_item_id=1705, 3: qty=4}   # 變長 varint
rx 0x0302(770) 貨幣更新 / 0x0402(1026)+0x0406(1030) 道具數更新 / 0x1b02 rx {2:1705,...,qty} / 0x1b01(6913) shop list
```
→ 花菇車幣、得碎片。`shop_type=11`、`shop_item_id` 非 goods id(60103) 而是「商城內項目索引」(此款=1705)，
   接線需先從 0x1b01 shop list 建 decoration→shop_item_id 對照(LIVE-TODO：補各款 item_id)。

**B. 升級 = req `0x3801`(14337) JSON body → resp `0x3211`(12817 car_park_skin_up_s2c)**：
```
tx 0x3801(14337) body = ASCII JSON  {"type":0,"skin_id":10003,...}     # 非 protobuf, 是 JSON 字串!
rx 0x3211(12817) body = {1: code=0(成功), 2:{1: skin_id=10003, 2: skin_lev=7, ...}}
   (錢/碎片不足時 rx 0x0201(513) {1: code=3} = 失敗)
```
→ **§2 的「12817 = c2s 買+升級扣貨幣」是錯的。** 真實：c2s 升級走 **0x3801 JSON `{type, skin_id}`**
   (只消耗已有碎片，不碰貨幣)；**12817 是 s2c 回包**(帶新 skin_lev)；貨幣只在 A 的 shop_buy 花掉。

**C. 限購數字 = 剩餘可買量 (非已買)**：升級前 `限購 108/120`、買 4 碎片後 `104/120`(108−4)。
   即 `X = 120 − 已消耗碎片`；120 剛好 = 0→15 滿級累計碎片，故「限購上限=正好能單款滿級一次」。
   中式庭院大門 lv6 累計 12 碎片 → 120−12=108 ✓；升到 lv7(累計16) → 104 ✓。

**D. 加成增量 live 確認**：lv6 1120% → lv7 1280%（三圍各 +160%），與 §9.2 表值完全一致。

接線者：buy=shop_buy(6914){11, item_id, qty}（先查 item_id 表）；upgrade=send 0x3801 JSON
`{"type":0,"skin_id":id}`；序列化、等 0x3211 code=0 再送下一個、收 0x0201 code≠0 即停。

---

## 10. 純 WS read+write 全解 + dashboard 接線 (live 定案 2026-06-15)

把 dashboard 車位裝飾工具（工具優化類分頁）的 read+write 全換成純 WS，移除慢速
cocos 掃描(~90s) 與 cocos 買升 UI。協議全 live 採到並驗證；工具
`tools/read_carpark_ws.py`（唯讀 read snapshot）。

### 10.1 READ（已擁有 + 等級 + 碎片店 + 菇車幣）

- **skin_list = `car_park.car_park_info` (12801)**，c2s `{type:0, master_id:<我的
  role_id>, ceng:0}`。home/私人車位 = **type 0**（bundle `reqParkingInfo(0,
  GetRoleId())`；先前猜 type 1 是錯的，server 不回 12801）。s2c `skin_list#8`
  repeated `p_car_park_skin {skin_id#1, skin_lev#2, pos#3, x#4, y#5}`。
  `skin_lev==0` = 免費初始款 (if_initial)。
- **item_id/碎片單價/限購上限 在 client config `configMall`，不在 WS 包**。每列
  `_data=[shop_id#0, shop_type#1, [frag_goods,qty]#2, [currency,price]#3, ...,
  cap#8]`；裝飾碎片列 = `_data[1]===11` 且 `_data[2][0]` ∈
  `configParking_design.expend[0][0]`（碎片 goods，如花門 60102 / 中式庭院大門
  60103）。shop_type 11 是混合店，務必用「frag ∈ parking」過濾。場景款(position5,
  29/68) 碎片不在任何 shop（活動取得）→ shop_id null，picker 自動跳過。
- **已買數 = WS `shop.shop_info` (6913)** `{shop_type:11}` → `buy_info#2` repeated
  `p_key_value{k=shop_id, v=已買數}`（未買過的不在列＝0）。限購剩餘 = cap − 已買。
- **菇車幣 = role attribute 201**（goods<1000 ⇒ role 屬性）：`roleModel.GetRoleAttr(201)`
  == `roleInfo[201]`。roleModel = 全域 `IS()` singleton accessor 取出（具
  GetRoleAttr+GetRoleId 者；`roleControl` 只是網路 handler），暫時 wrap `IS` 攔下；
  `GetRoleId()` 同源可當 master_id。role_id fallback = localStorage 出現最多次的
  12+ 位尾碼數字。

### 10.2 WRITE（買碎片 + 升級，逐步）

**修正 §9.8 對升級 cmd 的解讀**：升級 c2s = **`car_park.car_park_skin_up_c2s`
(12817) protobuf `{type:0, skin_id}`**（bundle `btnUnlock → reqSkinUp(type,
decID) → netManager.send("car_park.car_park_skin_up_c2s", {type, skin_id})`）。
§9.8 看到的 `0x3801`(14337) 是 `json_proto.json_proto_c2s` 通用 JSON 封套，
netManager 會自行決定傳輸，邏輯 cmd 仍是 12817；s2c 回 `car_park_skin_up_s2c`
(12817) 或失敗 `0x0201`(513)。

每步（`EXEC_STEP_WS_JS`，args `[shop_id, skin_id, frags, do_upgrade]`）：
1. **買碎片 = `shop.shop_buy_c2s` (6914)** `{shop_type:11, shop_id, num:frags}` →
   等 6914 成功 / 0x0201 失敗（花菇車幣）。
2. **升級 = `car_park.car_park_skin_up_c2s` (12817)** `{type:0, skin_id}` → 等 12817
   成功 / 0x0201 失敗（只耗碎片）。
3. **再讀一次 car_park_info 確認等級真的 +1**（ground-truth，不依賴升級回包形狀）。
   回 `{ok, bought, name, before_level, after_level, err?}`；buy 成立但升級失敗時
   `bought=true` → routes 誠實計入已花菇車幣。

序列化、逐步 stop-on-failure，與舊 cocos executor 同 contract。

### 10.3 Dashboard 接線 + 驗證

- `control_panel/carpark_tools_js.py`：新增 `READ_STATE_WS_JS`（= READ snapshot）
  + `EXEC_STEP_WS_JS`（買升）。`control_panel/routes_tools_optimize.py`：`_read_state`
  改用 WS read、`cat/cell` 全換 `shop_id`、`_exec_step` args `[shop_id, id, frags,
  True]`。gacha 分頁不動。`_READ_TIMEOUT` 90→25（WS read ~3-4s）。
- TDD：`tests/test_carpark_ws_io.py`（payload 結構 + routes shop_id 接線 + exec args）；
  全測 27 passed。
- **live 驗證**：異世之界(30092, shop1759, lv1→2, 1 碎片) 真實 buy+upgrade，菇車幣
  88,643,235 → 88,443,235（−200,000 exact）、等級 1→2、已買 1→2。success path 通過。

### 10.4 純 WS 執行層三個坑 (live 定案 2026-07-05, 7fe98fc6)

1. **skin_up 必須走 `json_proto` 封套**：真實 client 是 `netManager.send("car_park.
   car_park_skin_up_c2s", {type, skin_id}, /*useJson=*/true)` → wire 上送
   **14337 `json_proto_c2s` protobuf `{1: proto_id=12817(int32), 2: msg='{"type":0,
   "skin_id":N}'(string)}`**，回包仍是原生 12817 protobuf。§10.2「直接送 12817
   protobuf」在 server 端行為不定（live 觀察三態：執行+回包 / 執行但不回包 /
   完全忽略），§9.8 的 wire 抓包(0x3801=14337)才是對的。
2. **skin_up 有 server 冷卻（靜默丟棄）**：距上一次 skin_up 太近（~1s 內）的請求被
   直接忽略，**不回 0x0201**；間隔 10s 實測 100% 成功。executor 步間隔 10s
   （`_STEP_GAP_S`），未確認時等 `_COOLDOWN_WAIT_S` 再 re-verify → 才重送一次
   （先 re-verify 防 late-landing 重複升級）。
3. **季節限定碎片會下架**：configMall `_data[9]` 有 `open_time` 販售時間窗
   （cap=30 的活動款全帶窗，如 shop 1739 = 2024-06-28~07-21）；窗外購買回
   **0x0201 code=283**。`ws_token/data/mall_parking_frag.json` 已補 `open_time`
   欄位，`read_state` 窗外把 `limit_remaining` 歸 0（`off_shelf: true`），
   planner 自動跳過 —— 這就是「按執行第一步就無法購買」的原因。

回包一律只當 fast path：買/升級成功與否以 re-read（6913 已買數 / 12801 等級）為準；
0x0201 一律解碼 `error_code`（err 格式 `buy_rejected_code_N` / `upgrade_rejected_code_N`）。

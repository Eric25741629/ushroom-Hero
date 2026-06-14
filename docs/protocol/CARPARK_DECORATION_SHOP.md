# 車友商行 / 裝飾 (Parking Decoration) — Recon Recipe + Offline Groundwork (2026-06-14)

> 狀態：**離線推導完成，live capture 待白天 (台灣 10:00-22:00) 車友商行開放時補。**
> 接 action 前 **必須** 先完成本文件 §5 的 live 採樣 (確認 cmd round-trip body
> field number、catalog 行真實 cost/benefit、currency goods id)。在那之前
> `ws_token/carpark_decoration.py` 只是 **純 picker**，沒有任何 WS 動作被 wire。

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

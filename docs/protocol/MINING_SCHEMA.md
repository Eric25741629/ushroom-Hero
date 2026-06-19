# 菇勇者 挖礦系統協議 Schema

捕獲於 2026-05-11，5554 帳號 live session via CDP attach (port 9230)。
更新於 2026-06-11：補 `0x0402 evt=9800004` 登入道具 snapshot 與 WS runner
挖礦串接行為。

**只記錄 user-confirmed 事實**，未驗證的推測一律省略。

---

## 1. Cmd 總覽

| cmd | 方向 | 用途 |
|---|---|---|
| `0x0c01` | tx 0B → rx ~700B | 礦盤狀態查詢（empty body） |
| `0x0c03` | tx 8B → rx varies | dig / use mining prop RPC |
| `0x0402` | rx-only push | inventory 異動（多種 evt_type） |

---

## 2. Prop IDs（user-confirmed via 用前→用後 配對）

| prop_id | 名稱 | 驗證 |
|---:|---|---|
| **4001** | 鎬子 | 用 1 → push 114→113 ✓ |
| **4002** | 鑽頭 | 用 1 → push 181→180 ✓ |
| **4003** | 炸彈 | 用 1 → push 889→888 ✓ |

---

## 3. `0x0402` evt_type（user-confirmed via item count round-trip）

| evt_type | 用途 | 驗證 |
|---:|---|---|
| **9800004** | 登入/道具 snapshot-update | read-only WS probe: entries include `(4001, 35, ...)` ✓ |
| **9800001** | 道具消耗 | 用 1 鎬 → f3 = 113 ✓ |
| **9800009** | 道具獲得 | 挖礦得礦物 → f3 = 5412819（5412.3K → 5412.8K, +500）✓ |
| 1001006 | 神燈消耗 | 自動開 20 → qty −20 ✓ |
| 5011 | 貨幣異動 | （Equipment session 觀察）|

### evt 共通 sub-message 結構：
```
f1 = item_id
f2 = player_uid（大 varint, 可忽略）
f3 = new_count（使用/獲得後的剩餘數量）★ inventory 真實來源
f4-f7 = metadata（用途未驗證）
```

---

## 4. Item IDs（user-confirmed via on-screen 數值對照）

| item_id | 名稱 | 驗證 |
|---:|---|---|
| **1001** | 神燈 | 自動開一次 −20，OCR 對齊 ✓ |
| **1007** | 礦物（貨幣） | push 5412819, on-screen 5412.3K ✓ |
| 4001 | 鎬子 | （見 §2） |
| 4002 | 鑽頭 | （見 §2） |
| 4003 | 炸彈 | （見 §2） |

⚠ **item_id 4001 在挖礦 namespace 是道具**，但**在 0x0504 神燈詞條 namespace 是「同伴爆擊」**。不同 cmd 命名空間獨立。

---

## 5. `0x0c01` 礦盤狀態 schema

```
tx body: empty
rx body fields (top-level only confirmed presence):
  f1 varint            ← pickaxe_cap（user-confirmed: f1=114, current=112，
                         所以是上限不是 current 數量）
  f2 varint            ← server_ts (常為 0)
  f3 varint            ← round_id (per-floor 累加)
  f4 varint            ← floor_offset / current_depth
  f5 varint REPEATED   ← cell_id list（語意未完全驗證, 見 §7）
  f6 bytes REPEATED    ← floor events {f1=event_id, f2=value}
  f7 bytes REPEATED    ← cell features (見 §6)
```

### Cell ID 編碼（user-confirmed）
```
cell_id = depth * 100 + col
e.g. 11003906 → depth=110039, col=6
```

---

## 6. Cell Feature (`f7` sub-message)

```
f1 varint = cell_id
f2 varint = col (= cell_id % 100)
f3 varint = depth (= cell_id // 100)
f4 varint = terrain enum（user-confirmed, 見下表）
f5 varint = count → **DUG 狀態**（CDP dig 2026-06-20 坐實，見 §6.1）
f6 varint = ?（觀測 = 0）
```

### 6.1 `f5` = count = DUG 狀態（LIVE CDP dig 2026-06-20，小寶，201/202 皆同）

> **這條推翻了 2026-06-15 的「count 語意未驗證 / 新石頭 f5=0」舊註記。**

| count | 意義 | 實測 |
|---:|---|---|
| **0** | **已挖（空氣）** | 對 count==0 的格送 0x0c03：**無回覆、版面不變、不耗鏟**（no-op）。`config_id` 此時只是「原本是什麼地形」的歷史值。 |
| **>0** | **未挖 / live** | 送 0x0c03：有回覆、耗 1 鏟、該格變成 count==0（空氣）、可能捲動 baseline 並 reveal 下方新格。201=土 202=岩 401=活礦。 |

實測：挖 `202/count1` → 回 `{area, baseline+1(捲), 新 reveal block, ...}`、格變 `202/count0`；挖 `201/count0` → 無回覆、不變。

**盤面地形還原規則（ws_token/mining_adapter.board_to_grid）：**
- block count==0 → `empty`（空氣；舊版誤標 solid → 整盤看起來「密集」、planner 亂挖浪費）
- block count>0 → 地形（201 dirt / 202 rock / 401 reachable_pit）
- 在 `actives` 但**無 block feature** → `dirt`（未挖泥土；MINING_SCHEMA §7 + dig 實測）
- 非 active 且無 block feature → `empty`

**WS 本質限制**：0x0c01 **不送**「未挖格」的逐格地形——只有 count>0 的少數格帶 config。所以「無 block」的未挖格其 **土/岩 無法從 0x0c01 區分**，且 unreachable 的未挖實心格（被空洞越過）0x0c01 也看不到（會誤判成空）。

### 6.2 地形真正來源 = 前端 client config `configMine_template`（CDP 讀 2026-06-20）

前端能畫出每格地形，是因為**盤面由 client 端 config table 生成**，不是 WS 逐格下發：

| 前端全域 | 內容 |
|---|---|
| `window.configMine_grid.datas` | cell-type → 屬性/獎勵：`100`=空(air)、`201`=土、`202`=岩、`401`=礦洞；`101/102/103/108`=含礦泥土(item 1007 礦物 50/100/250)；`301-308`=特殊獎勵格(礦物/粉鑽2/鑽頭4002/炸彈4003/紅包卡1012-1013) |
| `window.configMine_template.datas` | `_data=[id, [42 個 cell-type 的 7×6 陣列], weight]`，加權隨機選模板生成盤面(如 1001/1002 weight 10、11-19 weight 1-9)。模板上排多為 100(空)、下排 201/202 |
| `window.configMine_hole_type.datas` | 礦洞形狀：`_data=[id, ?, [[dr,dc,401]...](3x3/2x2/1x1), weight, …獎勵 item list, …]` |

**結論**：盤面是前端用 `configMine_template` 加權隨機生成（同 `tools/mining_sim.html` 的 cluster 邏輯），WS 0x0c01 只追蹤「已挖狀態(count) + 可挖前沿(actives) + 礦洞實例」。要讓**純 WS** 路徑拿到完整未挖地形，需其一：(a) web_h5 裝置經 **CDP 讀前端 runtime 生成的盤面**(cocos model)；(b) port 模板生成邏輯(需 server 端的模板/seed 選擇依據，尚未坐實在哪個 WS 欄位)；(c) `miner` CNN classifier 視覺判讀(截圖→GRID_CFG 裁切 x0=6,y0=227,x1=535,y1=852→7×6)。**但 count==0=空氣 的核心修正(§6.1)已先解掉「已挖格誤判實心→盤面假性密集→亂挖浪費」這個主因。**

### f4 terrain enum（user-confirmed）

| f4 | 名稱 | 驗證 cell |
|---:|---|---|
| **201** | 泥土 | H5/CDP board adapter 2026-06-11 對齊 |
| **202** | 石頭 | H5/CDP board adapter 2026-06-11 對齊，通常需 ≥2 hits |
| **401** | 礦洞 | 11003906 → user 指出右側礦洞對應此 cell ✓ |
| 100 | (未驗證) | 出現 2 次但 user 沒挖過任一個 |

---

## 7. `f5` cells_undug 語意（**未完全解明**）

`f5` 是 repeated cell_id list。原本以為是「未挖通的 solid cells」但實測有矛盾：

- user 已挖 cell 11003901（dirt）但仍在 list 內
- list 大小 (43) 超過 viewport 6×7=42 max
- 跟視覺 board 的 solid blocks 數量對不上（43 cell vs ~10 visible solid）

**確定的事**：
- list 內的 cell_id 都是有效的（可作為 0x0c03 dig 的目標）
- 跨多個 depth（包括已捲出 viewport 的 rows + 視窗下方深處 cells）

**未確定**：
- cell 是否「現在仍是 solid」需從 dig 響應推算（client-side 追蹤）

---

## 8. `0x0c03` Dig / Use Prop RPC（user-confirmed via 配對）

### tx body
```
f1 varint = prop_id   (4001 鎬子 / 4002 鑽頭 / 4003 炸彈)
f2 varint = cell_id   (從 0x0c01 f5 list 拿)
```

4001、4002、4003 都走同一個 `home_mine_use_goods`。自動化預設只允許鎬子；
炸彈與鑽頭必須由設定明確開啟，避免未授權消耗。

### rx body
```
f1 varint            ← round_id
f2 varint            ← floor_offset (新深度)
f3 varint REPEATED   ← newly_revealed cell_ids
f4 bytes (optional)  ← single new floor_event
f5 bytes REPEATED    ← updated cell sub-messages（同 f7 18B shape）
```

### dig 副作用
1. server push `0x0402 evt=9800001 {item_id, new_count}` ✓
2. server push `0x0c21 rx empty body`（用途未明）
3. 若挖到收益（如礦物），push `0x0402 evt=9800009`

---

## 9. 自動化整合範例

```python
from utils.web_game_api import (
    PROP_PICKAXE, PROP_DRILL, PROP_BOMB,
    parse_mine_board, parse_inventory_push,
)

api = WebGameAPI(page)

board = api.fetch_mine_board()
# board['cells'] = cell_id list（可挖目標候選）
# board['features'] = cells with terrain info (f4 enum)
# board['floor_offset'] = current depth
# board['pickaxe_cap'] = 鎬子上限（不是 current）

# 找未進入礦洞
caves = [f for f in board['features']
         if f['terrain'] == 401 and f['raw'].get('f5') == 1]

# Dig
result = api.dig_cell(cell_id=11003903, prop_id=PROP_BOMB)
# result['newly_revealed'] = 新揭露的 cells
```

### Inventory tracking
鎬子（axe）現量 = goods cache 的 gtid `4001` 數量（客端 `getGoodsCountByGoodsGtid(4001)`，
UI 顯示 `現量/maxAxeNum` = 例 `350/118`）。`0x0c01` query 的 `max_num`(f1) 只是 cap
（= maxAxeNum），**不是** current。鎬子會隨時間自動回復（recover）。

> **更正（2026-06-16 fc 實測）**：登入時 **不會可靠地** 推送 `9800004` 道具 snapshot
> （多次純連線只收到 `5004` 或完全沒有 `0x0402`）。鎬子現量只在 **每次挖掘後的 `0x0402`
> 消耗推送 `9800001`**（`items=[(4001, 剩餘量)]`）才會出現。先前「登入 snapshot 帶 4001」
> 的結論是用合成 consume push 驗的，對真實登入流程是 **錯的**。

```python
class InventoryTracker:
    """訂閱 0x0402 push 維護 in-memory item count。"""
    def on_0x0402(self, body):
        parsed = parse_inventory_push(body)
        if parsed.get("evt_type") in (9800004, 9800001, 9800009):
            for item in parsed.get("items", []):
                self.counts[item["item_id"]] = item["new_count"]
```

因此 WS runner **不再** 因「沒看過 4001 現量」而 skip：`mine_until_pickaxe_empty` 先
seed 一個正數讓 planner 出步、挖第一鏟，再用第一個 `9800001` 消耗推送的真實剩餘量續挖到 0
（`ws_token/mining_supervised.py`）。

**挖掘有效目標**：server 只接受 `home_mine_info.actives`（= 可挖前沿 block_id）內、且
**未被清除** 的格子。一個 active 格可挖 iff（無 block entry＝未挖泥土）或（block.count>0＝
活的礦洞/半挖）或（config 202 石頭，石頭新鮮時 count 也是 0）。已收集的礦洞（cfg 401
count 0）仍留在 actives，但再挖是 no-op。v4 planner 會提議非 active／已收集的礦洞，需由
`_select_dig_step` 過濾＋前沿 fallback（2026-06-16 fc 實測）。

---

## Verified by

| 項目 | 驗證方式 |
|---|---|
| Prop 4001/4002/4003 | 各用 1 個 → server push 114→113 / 181→180 / 889→888 |
| f4=201 = 泥土 | H5/CDP board adapter 對齊 |
| f4=202 = 石頭 | H5/CDP board adapter 對齊 |
| f4=401 = 礦洞 | user 指出右側礦洞 → 對應 cell 11003906 |
| f1 = pickaxe_cap (=maxAxeNum) | f1=114，玩家當前 112 → 是 cap 不是 current；fc f1=118 玩家 350 |
| 鎬子現量 = goods gtid 4001 | 客端 `getGoodsCountByGoodsGtid(4001)` UI `350/118`；登入**不**保證推 9800004，現量靠 9800001 消耗推送 |
| evt=9800001 = 挖掘消耗推送 | fc 實測每挖 1 鏟推 `(4001, 剩餘)`：350→349→…→0 |
| item_id 1007 = 礦物 | push 5412819, on-screen 5412.3K |
| evt=9800009 = 道具獲得 | 挖礦得礦物觸發此 evt |

# 菇勇者 挖礦系統協議 Schema

捕獲於 2026-05-11，5554 帳號 live session via CDP attach (port 9230)。

**只記錄 user-confirmed 事實**，未驗證的推測一律省略。

---

## 1. Cmd 總覽

| cmd | 方向 | 用途 |
|---|---|---|
| `0x0c01` | tx 0B → rx ~700B | 礦盤狀態查詢（empty body） |
| `0x0c03` | tx 8B → rx varies | dig 一格 RPC |
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
f5 varint = ?（語意未驗證）
f6 varint = ?（觀測 = 0）
```

### f4 terrain enum（user-confirmed）

| f4 | 名稱 | 驗證 cell |
|---:|---|---|
| **201** | 石頭 | 11003501 → user 挖 → 確認「石頭」✓ 且需 ≥2 hits |
| **202** | 泥土 | 11003901 → user 挖 → 確認「泥土」✓ |
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

## 8. `0x0c03` Dig RPC（user-confirmed via 配對）

### tx body
```
f1 varint = prop_id   (4001 / 4002 / 4003)
f2 varint = cell_id   (從 0x0c01 f5 list 拿)
```

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
**現量唯一可信來源是 `0x0402` push**。0x0c01 query 的 f1 是上限不是 current。

```python
class InventoryTracker:
    """訂閱 0x0402 push 維護 in-memory item count。"""
    def __init__(self):
        self.counts = {}

    def on_0x0402(self, body):
        parsed = parse_inventory_push(body)
        if parsed.get("evt_type") in (9800001, 9800009, 1001006):
            for item in parsed.get("items", []):
                self.counts[item["item_id"]] = item["new_count"]
```

---

## Verified by

| 項目 | 驗證方式 |
|---|---|
| Prop 4001/4002/4003 | 各用 1 個 → server push 114→113 / 181→180 / 889→888 |
| f4=202 = 泥土 | user 挖 cell 11003901 → 顯示「泥土」|
| f4=201 = 石頭 | user 挖 cell 11003501 → 顯示「石頭」|
| f4=401 = 礦洞 | user 指出右側礦洞 → 對應 cell 11003906 |
| f1 = pickaxe_cap | f1=114，玩家當前 112 → 是 cap 不是 current |
| item_id 1007 = 礦物 | push 5412819, on-screen 5412.3K |
| evt=9800009 = 道具獲得 | 挖礦得礦物觸發此 evt |

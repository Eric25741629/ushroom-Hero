# 菇勇者 裝備系統協議完整 Schema

捕獲於 2026-05-11，5554 帳號（player_id `89555436834913`，「下不維力炸醬麵」）live session via CDP attach (port 9230)。

---

## Cmd 總覽

| cmd | 方向 | 大小 | 用途 |
|---|---|---:|---|
| `0x0504` | rx | ~1300B (20 lamps) | 神燈掉落結果（含完整裝備資訊） |
| `0x0509` | tx 4B / rx 160B | small | 神燈開啟 RPC（{count}） |
| `0x0511` | tx **2B** | 2B | **切換 preset (套裝) RPC**（`{preset_id}`）— 只動裝備 |
| `0x032a` | tx **2B** | 2B | **切換 scheme (方案) RPC**（`{scheme_id}`）— 動整個 loadout |
| `0x0510` | rx 146B | 12 slots | preset 名稱列表（已存的 12 個套裝） |
| `0x032c` | rx ~178B | varies | 單個 preset 詳細定義（每個 slot 對應的裝備 UID） |
| `0x0e0a` | tx 0B → rx 152B | varies | 點開裝備 UI 時的當前 preset 詳情查詢 |
| `0x0e0b` | rx 2B | 2B | preset list 小 ack |
| `0x0e04` | rx 74B | varies | preset 切換完成 push |
| `0x0308` | rx 79-140B | varies | 玩家「全身總屬性」push（切換後自動推送） |
| `0x0402` (evt=5004) | rx 26B | small | 裝備異動單筆推送 |

---

## 1. 神燈掉落 0x0504（已寫進 `parse_lamp_drops`）

```
f1 varint            ← round_kind (2/3)
f2 varint            ← flag (0/1)
f3 bytes REPEATED    ← ONE PER LAMP (20 entries on auto-press)
   sf1 = drop_uid (連續遞減的 UID)
   sf2 = template_id (rarity*10M + slot*100K + item_id)
   sf3 = position（裝備等級）
   sf6 = REPEATED {stat_id, value} — base stats（攻/生/防 + 武器專屬攻速）
   sf7 = REPEATED {affix_id, value÷100} — 詞條 (1~2 個)
   sf9 = bonus / 戰力分數
```

**template_id 編碼公式**：
```
template_id = rarity * 10_000_000 + slot * 100_000 + item_id
例如 60315011 = r=6 史詩, slot=03 面飾, item=15011
例如 111020001 = r=11 永恆, slot=10 鞋, item=20001  (兩位數稀有度)
```

---

## 2. 11 個稀有度

| r | 名稱 | bonus 範圍 |
|---|---|---:|
| 1 | 普通 | (未抽到) |
| 2 | 優秀 | (未抽到) |
| 3 | 精良 | (未抽到) |
| 4 | 稀有 | ~83k |
| 5 | 卓越 | ~106k |
| 6 | 史詩 | ~133k |
| 7 | 傳奇 | ~168k |
| 8 | 不朽 | ~210k |
| 9 | 超越 | ~432k |
| 10 | 鎏金 | ~892k |
| 11 | 永恆 | ~1.66M |

---

## 3. 10 個部位（slot）

| slot | 部位 | 範例 |
|---|---|---|
| 01 | 武器 | 星河弩 |
| 02 | 帽子/冠冕 | 冠冕 |
| 03 | 面飾 | 眼罩 / 眼鏡 |
| 04 | 肩 | 肩飾 / 肩章 |
| 05 | 胸 | 密林獵裝 / 上班裝甲 / 外套 |
| 06 | 臂 | 臂甲 / 袖箍 |
| 07 | 手套 | 鯊魚頭（造型像鯊魚的手甲）/ 一般手套 |
| 08 | 腰 | 腰帶 |
| 09 | 護膝 | 護膝 / 護溪 |
| 10 | 鞋 | 戰靴 / 皮鞋 / 靴子 |

---

## 4. Base stats (sf6, stat_id)

| ID | 屬性 | format | 適用 |
|---|---|---|---|
| 1001 | 攻擊 | 整數 | 全部 |
| 1002 | 生命 | 整數 | 全部 |
| 1003 | 攻速 | ÷10000（小數，例 13000 → 1.3） | **只有武器** |
| 1024 | 防禦 | 整數 | 全部 |

---

## 5. 詞條 (sf7, affix_id) — 全部值 ÷100 = 顯示 %

| ID | 詞條 | 系列 |
|---|---|---|
| 1004 | 爆擊 | 主動 |
| 1008 | 閃避 | 主動 |
| 1012 | 回復 | 主動 |
| 1016 | 連擊 | 主動 |
| 1017 | 反擊 | 主動 |
| 1023 | 擊暈 | 主動 |
| 1037 | 技能爆擊 | 技能 |
| 4001 | 同伴爆擊 | 同伴 |
| 4005 | 同伴連擊 | 同伴 |

⚠ **跨 cmd 命名空間衝突警告**：
- `4001` 在 `0x0c03 tx`（挖礦 dig）= **鎬子 item_id**
- `4001` 在 `0x0504 sf7`（神燈詞條）= **同伴爆擊 affix_id**
- 不同 cmd 的命名空間獨立，不要混用。

---

## 6. Preset 切換系統

### 6.1 切換的 RPC：`0x0511 tx`

```
body = bytes([0x08, preset_id])    # 2 bytes total
       └─ field 1 wire 0 (varint), value = preset_id (1-12)
```

**Python 用法**：
```python
api.call_raw(0x0511, bytes([0x08, preset_id]))
# 等價於在 UI 上點該 preset 按鈕
```

server 立即執行切換，並推送：
1. `0x0308` rx — 玩家全身總屬性
2. `0x032c` rx — 新 preset 的完整定義
3. `0x0510` rx — preset 列表（active 索引可能更新）

### 6.2 Preset 列表：`0x0510` rx (146B)

```
f1 varint = active_preset_id
f2 bytes REPEATED — 12 slots:
   sf1 = preset_index (1-12)
   sf2 = name (utf-8 string，可能空)
```

**5554 帳號實測（2026-05-11）的 preset 列表**：
```
1.  連爆       ← 預設啟用
2.  反爆
3.  技暈眩
4.  回爆
5.  連閃
6.  同伴連爆
7.  反回
8.  技回
9.  閃回
10. 暈眩回
11. (空)
12. (空)
```

### 6.3 單個 Preset 詳細定義：`0x032c` rx (~178B)

```
f1 bytes:
  sf1 varint = active_preset_id
  sf2 utf-8 string = preset_name（例如「推圖」）
  sf3 bytes REPEATED — 每個槽位的 (key, value)：
    {f1 = 1~14}  → 各部位的裝備配置（slot index → item_uid 或 count）
    {f1 = 101~106} → ??? (戰力 / 副屬性?)
        值 18446744073709551615 = 0xFFFFFFFFFFFFFFFF (= MAX_UINT64) 是「未設定」哨值
```

### 6.4 點開「方案」UI：`0x0e0a` tx 0B → rx 152B

```
tx: empty body (純查詢 ping)
rx: 當前已啟用 preset 的詳細內容
    f1.f1 = active_preset_id
    f1.f4 = 戰力總分
    f1.f5 REPEATED { slot, item_uid }  — 各部位實際裝備的 UID
    f1.f6 = 額外資訊（uid 字串等）
```

### 6.5 切換完成推送：`0x0e04` rx (74B)

切換後 server 廣播給 client（與 0x032c / 0x0510 一起）。

---

## 7. 方案系統 `0x032a` (scheme switch)

「方案」(scheme) 比「套裝」(preset, 第 6 節) 更上層 — 方案綁定一組「裝備 preset + 技能配置 + 同伴隊伍 + 寵物/裝飾」。切方案會 broadcast 整個角色配置 reload，所以 server 一次推 30+ 個 rx cmd。

### 7.1 切換的 RPC：`0x032a` tx

```
body = bytes([0x08, scheme_id])     # 2 bytes total
       └─ field 1 wire 0 (varint), value = scheme_id (1..N)
```

**Python 用法**：
```python
api.call_raw(0x032a, bytes([0x08, scheme_id]))
# 等價於在 UI 上點該方案按鈕
```

server 立即執行切換並廣播全套後續推送（驗證於 5554 4 次切換，每次 30+ rx）：

| 子系統 | rx cmd | 內容 |
|---|---|---|
| 裝備 preset | `0x0510` (146B) | active preset_id（方案綁的裝備 preset） |
| 裝備清單 | `0x4504` (289B), `0x4202` x2 | 詞條彙總 |
| 技能 | `0x0802` (45-61B), `0x080b` (2B) | 技能槽 active idx |
| 同伴 | `0x0706` (37B), `0x070b` (2B) | 同伴隊伍 active idx |
| 寵物/裝飾 | `0x1101` (665B), `0x1107` (2B) | UID 列表 |
| 玩家總屬性 | `0x0308` x6~8 | 多個 sub-namespace 全部重算 |
| ack | `0x032a` (2B) | echo tx body |

### 7.2 方案 vs 套裝 的差異

| | 切套裝 `0x0511` | 切方案 `0x032a` |
|---|---|---|
| 動裝備 | ✓ | ✓ |
| 動技能 | ✗ | ✓ |
| 動同伴 | ✗ | ✓ |
| 動寵物 | ✗ | ✓ |
| 廣播 rx 量 | 3-4 個 | 30+ 個 |
| 用途 | 純裝備換 | 場景整套切換 |

### 7.3 5554 帳號實測方案列表（2026-05-11）

| scheme_id | 方案名 | 綁的裝備 preset |
|---:|---|---|
| 1 | 推圖 | #1 連爆 |
| 3 | 打戰士 | #5 連閃 |
| 4 | 暈回 | #5 連閃（共用） |
| 5 | 魚 | #10 暈眩回 |

scheme_id → preset_id 對應從切換後 `0x0510 f1` (active preset) 讀出，每個方案綁不同 preset。方案總數上限未驗證；2 號未測（介面顯示為「反爆」推測值）。

### 7.4 自動化用例

```python
SCHEME_BY_TASK = {
    "mining":    1,  # 推圖
    "boss":      3,  # 打戰士
    "fishing":   5,  # 魚
    "endurance": 4,  # 暈回
}

def auto_switch_scheme(api, task_name):
    sid = SCHEME_BY_TASK.get(task_name)
    if sid is not None:
        api.call_raw(0x032a, bytes([0x08, sid]))
```

切完 server 會自動把所有屬性 push，client 不用主動查。

---

## 8. 玩家總屬性 push：`0x0308` rx

切換 preset 後立刻推送，內容是**全身彙總**屬性：

```
f1 REPEATED {stat_id, value}:
  1001 (攻擊)    = 7,501,859,858    ← 整數
  1002 (生命)    = 505,389,534,694  ← 整數
  1004 (爆擊)    = 8793             ← 87.93%（÷100）
  1008 (閃避)    = 15250            ← 152.50%
  1012 (回復)    = 16               ← 0.16%
  1016 (連擊)    = 10278            ← 102.78%
  1023 (擊暈)    = 411              ← 4.11%
  1024 (防禦)    = 709,698,136

f2 = 子訊息（可能含戰力細節 / cooldown / etc.）
```

stat_id / divisor 規則跟單件裝備一致，方便 client 直接複用 render 邏輯。

---

## 9. 神燈消耗 0x0402 evt=1001006

切換 preset 跟神燈「消耗」是兩回事，但 0x0402 也常出現。神燈專用的 evt：

```
0x0402 rx evt=1001006:
  f1 = evt_type
  f2 sub: { item_id=1001 (神燈), uid, qty=當前剩餘, ... }

每按一次「自動」(20 把神燈) → qty -= 20
```

---

## 10. 不同 0x0402 evt_type 對照

| evt_type | 用途 |
|---|---|
| 5004 | 裝備異動單筆推送 |
| 5011 | 貨幣異動 |
| 9700002 | 完整素材庫存快照 |
| 9700003 | 部分素材異動 |
| 9800001 | 單一道具消耗（鎬子用一次 → 4001=N） |
| 9800004 | 單一道具事件 |
| 1001006 | 神燈消耗（item 1001, qty -= 20） |

---

## 11. 自動化建議

### 自動切 preset 對應場景

```python
PRESET_BY_TASK = {
    "mining": 1,    # 連爆（高爆擊輸出）
    "dungeon": 1,
    "boss": 7,      # 反回（防禦回復混合）
    "lamp_open": 1, # 神燈不影響掉落，隨意
}

def auto_switch_for_task(api, task_name):
    pid = PRESET_BY_TASK.get(task_name, 1)
    api.call_raw(0x0511, bytes([0x08, pid]))
```

### 取代 OCR 讀詞條

`Open_gold_paddle_ocr.py` 整支可丟，改用：
```python
from utils.web_game_api import parse_lamp_drops
result = parse_lamp_drops(latest_0x0504_body)
for lamp in result['lamps']:
    # rarity / slot / 詞條 全在 lamp dict 裡
    ...
```

### 取代「查當前裝備總屬性」OCR

監聽最近的 `0x0308` rx body，直接拿全身總屬性。或主動 `call_raw(0x0e0a, b'')` 拉當前 preset 的詳細內容。

---

## 12. 還沒解的 cmd（可能後續探索）

- `0x0c01-0x0c11`：挖礦 namespace（0x0c03 dig 已解，其餘是同伴/任務/事件 push）
- `0x4202` 57KB：login bulk state sync（內容未拆，但跟裝備無關）
- `0x4707-0x4708`：task / 任務系統
- `0x0e0b` 2B：preset 子事件 ack（未深入）

---

## Verified by

- 神燈 schema：跨 7+ user-confirmed lamps 對應 (template_id, base, 詞條, position)
- preset (套裝) 切換：`tx 0x0511 body=08 02` ↔ user said "我切到反爆 (preset 2)"，回切 `body=08 01` ↔ "切回連爆 (preset 1)"
- scheme (方案) 切換：user 在 UI 依序點 打戰士→魚→暈回→推圖，hook 抓到對應 4 個 `tx 0x032a` body `08 03 / 08 05 / 08 04 / 08 01`，且每次後續 `0x0510 f1` 推送的 active preset 跟使用者描述一致（連閃/暈眩回/連閃/連爆）
- 11 個稀有度：全部出現過（除了 1-3 普通/優秀/精良 因為太低品質會被 auto-sell 不展示）
- 10 個部位：全部出現過

Schema 漂移風險：遊戲改版可能會新增 stat_id / affix_id / slot / rarity。重要的 invariant 是**結構**（template_id 編碼公式、sf6 vs sf7 區分、÷100 divisor），這些短期內不太會變。

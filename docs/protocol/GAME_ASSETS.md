# 菇勇者 Game Assets 探索

發現於 2026-05-11，user 提供的 asset URL 觸發。CDN 上有兩個 critical bundles 含完整 game schema + data table。

---

## 1. 取得方式

### Asset Bundle 結構（Cocos Creator 3.8.75）

`cc.assetManager.bundles._map` 列出 7 個 bundle：

| bundle | base | 用途 |
|---|---|---|
| `internal` | `assets/internal/` | Cocos 引擎內建 effects/materials |
| `resources` | `assets/resources/` | 字型 |
| `main` | `assets/main/` | render pipeline |
| `bundle-LoadingView` | `assets/bundle-LoadingView/` | loading 畫面 |
| `script` | `assets/script/` | 遊戲邏輯（minified, 0 visible paths） |
| **`bundle-firstload-res`** | `assets/bundle-firstload-res/` | **`config/datas` + `config/proto` ← 兩大寶** |
| `bundle-res` | `assets/bundle-res/` | 36673 個資源（icon、audio、animation 等） |

### 程式抓法
```python
# 從已 attach 的 page (port 9230) 透過 cc.assetManager 取
result = page.evaluate("""
async () => {
  const bundle = cc.assetManager.bundles._map['bundle-firstload-res'];
  return await new Promise(r => {
    bundle.load('config/datas', (err, asset) => {
      // asset._buffer 是 ArrayBuffer (config/datas 才有 buffer)
      // asset.json 是物件 (config/proto)
      r({...});
    });
  });
}
""")
```

抓下來放在 `tmp_ws_capture/game_assets/`：
- `config_datas.bin`（7.1 MB）
- `proto_schema.json`（1.2 MB）

---

## 2. `config/datas` — 遊戲資料 table（7.1 MB binary）

### 格式

每個 table 用 `\x00<name_len><name>` 標記開頭：

```
[Header: 00 05 03 65]
[Table 1]
  00 0b "Achievement"
  00 00 06 0b a7 05  <table_data...>
[Table 2]
  00 09 "Equipment"
  00 00 c2 3c a7 05  <table_data...>
...
```

`a7 05` 是每個 table data block 開頭的 magic bytes（用途未明，可能 schema version + format flag）。

### 已知 83 個 tables

關鍵 entries（與 bot 邏輯相關）：

| Table | 用途 | offset |
|---|---|---:|
| **`Equipment`** | 裝備定義（config_id → name/affix range） | 2,691,125 |
| **`Skill`** | 技能 | 6,376,966 |
| **`Effect`** | 詞條/效果定義 | 2,679,194 |
| **`Attribute`** | 基礎屬性定義 | 148,889 |
| **`Goods`** | 道具/物品 | 2,917,999 |
| **`Achievement`** | 成就 | 4 |

其他全部 tables（按字母）：

```
Achievement, Angel, Appads, Appearance, Appid, Artifact, Attribute,
Badge, Battlepass, Bingo, Breakbricks, Breaklevel, Bullet,
Color, Complaint, Condition,
Dialogue,
Effect, Emoji, Equipment, ErrorInfo,
Familiybrawl, Favorability,
Gamecentre, Gameid, Goods, Guide,
Housekeeper,
Illustrated, Inspire,
Language, Level,
Mainicon, Marry, Merge, Monopoly, Monster, Mount,
NewFuncOpen, Notice,
Output,
Petlevel, Petrace, Privilege,
Ranktype, Relic, Reservation, Robot,
Scene, Science, Setting, Sevenlogin, Share,
Skill, Skilleffcet, Sound, Spirit,
Title, Towerdefence, Towerlevel, Towermonster, Turntable,
UnitModel, UnitType,
Wartoken, Welfare, Workshop
```

### 解碼方式（**未實作**）

每個 table 後續格式（`a7 05` 之後）目前**未驗證**。可能用 protobuf 編碼，schema 對應於 `config/proto` 內某個 type — 但 table-name → proto-type 對應未知。

未來方向：
1. 對比同一 record 的 binary bytes 跟 protobuf schema 試解
2. 找 game source 的 reader 函式

---

## 3. `config/proto` — 完整 protobuf schema（1.2 MB JSON）

### 取得形式
Cocos `JsonAsset.json` 屬性，已是解析好的 JS object。protobufjs reflection 格式：
```json
{
  "nested": {
    "namespace_name": {
      "nested": {
        "message_name": {
          "fields": {
            "field_name": {"rule": "required|repeated|optional", "type": "...", "id": N}
          }
        }
      }
    }
  }
}
```

### 規模
- **3181 個 nested entries**
- **240 個 proto class** 在 runtime `netManager._protoClass` 註冊（s2c + 部分 c2s）
- 命名規則：`<namespace>.<msg>_c2s` (client→server) / `<namespace>.<msg>_s2c` (server→client)

### 與 cmd 對應的關鍵 schemas（user-confirmed）

#### `type.p_equip` — 裝備記錄

```protobuf
message p_equip {
  required uint64 equip_id   = 1;  // 裝備 UID
  required uint32 config_id  = 2;  // template_id (rarity*1e7 + slot*1e5 + item_id)
  required uint32 equip_lv   = 3;  // 等級
  required uint32 location   = 4;  // slot 0-9
  required uint32 tab        = 5;  // preset tab 1-12
  repeated p_key_value base_attr = 6;  // 基礎屬性 (1001 攻 / 1002 血 / 1024 防 / 1003 攻速)
  repeated p_key_value rand_attr = 7;  // 詞條 (1004 爆 / 1008 閃 / 1016 連 / etc.)
  required uint32 refine_lv  = 8;  // 強化等級
  required uint32 power      = 9;  // 戰力
}
```

#### `equip.equip_info_c2s` / `equip.equip_info_s2c` — 完整裝備清單查詢

```protobuf
message equip_info_c2s {}  // empty body

message equip_info_s2c {
  repeated p_equip equip_list = 1;
}
```

⚠ **未送出過**：cmd_id 對應未找到（game source 被 minified，runtime 無 name→cmd 直接表）。

#### 其他 equipment 相關 messages

```
equip.equip_wear_c2s          // 裝備穿上
equip.equip_change_s2c        // 裝備變更通知
equip.equip_box_*_*_*         // 裝備寶箱
equip.equip_book_list_*_*     // 裝備圖鑑
equip.equip_figure_list_*_*   // 裝備外觀
equip.equip_select_*_*        // 選擇裝備
equip.equip_tab_info_*_*      // 標籤頁
equip.equip_choose_tab_c2s    // 切換標籤
equip.equip_change_tab_name_*_*  // 重命名 tab
equip.equip_box_skin_*_*      // 寶箱外觀
equip.equip_change_box_skin_*_*
equip.equip_refine_info_s2c   // 強化資訊
ship.ship_equip_info_*_*      // 船舶裝備（不同子系統）
```

#### 其他探索過的 namespaces（user-confirmed cmd 範圍）

```
mine.* / act_mining.*  ← 挖礦（含 p_mine_block, p_mine_reward, p_mine_hole, p_mine_cell）
preset / strategy.*    ← 套裝（0x0511）
strategy_s.* / scheme.* ← 方案（0x032a）
```

---

## 4. cmd_id ↔ message-name 對應（**未解明**）

`netManager._protoClass` 用 string keys（`equip.equip_info_s2c`），但 `sendMessage(cmd_id, body)` 用 integer cmd_id。中間轉換**沒在 runtime 暴露**。

已知對應（從 user-driven actions confirmed）：

| cmd_id | 對應 message（推測） | user-confirmed |
|---:|---|---|
| 0x0c01 | `mine.mine_info_*` | ✓ 礦盤狀態 |
| 0x0c03 | `mine.mine_dig_*` | ✓ 挖一格 |
| 0x0509 | `gold_lamp.lamp_open_c2s` | ✓ 神燈開啟 |
| 0x0504 | `gold_lamp.lamp_drop_s2c` | ✓ 神燈掉落 |
| 0x0510 | `preset.preset_list_s2c` | ✓ |
| 0x0511 | `preset.preset_switch_c2s` | ✓ |
| 0x032a | `scheme.scheme_switch_c2s` | ✓ |
| 0x0402 | `inventory.inventory_change_s2c` | ✓ |
| 0x4202 | `mount.mount_inventory_s2c` | ✓ 飛寵清單 |
| 0x4504 | `preset.preset_detail_s2c` | ✓ |

未驗證：`equip.equip_info_c2s` cmd_id 候選 = 0x45xx 範圍（preset/equipment family 鄰近 0x4501-0x4504）。可由 trial-and-error 找出，但需 user 同意才能 send。

---

## 5. 未來可用方向

### A. proto-aware parser
用 `protobufjs` (Node) 或 `protobuf` (Python) 載入 schema，每個 capture body 用對應 proto-class decode → 結構化欄位（不需手刻 `_walk_pb`）。

### B. config_id → 中文名映射
拆 `Equipment` table（offset 2691125）→ 建 `config_id → name` 對照。Lamp drop 顯示「永恆 武器 連爆」中文化。

### C. cmd_id 找尋
對 `equip_info_c2s`（empty body, 只在 0x45xx 區間）逐一試送 0x4502/0x4503/0x4505/0x4506 等，從響應 cmd_id 配 schema。

---

## 6. Files

| 路徑 | 用途 |
|---|---|
| `tmp_ws_capture/game_assets/config_datas.bin` | 7.1 MB raw game data |
| `tmp_ws_capture/game_assets/proto_schema.json` | 1.2 MB protobuf reflection JSON |
| `tmp_ws_capture/game_assets/protoclass_keys.txt` | runtime 註冊的 240 個 proto-class 名字 |

---

## Verified by

- asset bundle URL: user 提供 `https://mushroomh5.acenetgame.com/assets/bundle-res/native/93/...`，循此找到 bundle 結構
- `config/datas` 7124268 bytes 含字串 "Achievement"、"Equipment"、"Skill" 等 83 個 table 名（binary search confirmed）
- `config/proto` JSON 含 `equip.equip_info_s2c` + `type.p_equip` schema（與 0x0504 lamp drop sf6/sf7 結構一致）
- 240 個 proto class 透過 `Object.keys(netManager._protoClass)` runtime 列舉確認
- protobuf cmd_id 對應 user-confirmed cmds（0x0c01/0x0c03/0x0504/0x0509/0x0510/0x0511/0x032a/0x0402/0x4202/0x4504）的 message namespace 推測 — **rough guesses, not 1:1 確認**

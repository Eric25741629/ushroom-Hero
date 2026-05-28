# 菇菇車位 + 家族 — Clickable Nodes & Cmd Map (2026-05-20)

掃描方法：`tools/scan_view_clickables.py` 走訪 Cocos scene tree + game JS bundle
(`docs/game_client_sources/index.966f5.js`) 比對 `findChild("…")` 跟對應 click
handler。

兩條獨立資料來源 cross-check：

| 來源 | 用途 |
|------|------|
| Live scene tree (CDP attach) | 知道「現在 active 的節點長什麼樣」 |
| JS bundle source | 知道「按下去送什麼 cmd / 開什麼子 view」 |

兩邊都要過才算解出來。

---

## A. 菇菇車位 (ParkingMainView)

### A.1 view 結構

Root: `/UIRoot/NormalView/ParkingMainView`

Top-level children:

```
ParkingMainView
├── scrollMiddleShow      ← 主畫面建築 (4 大車位 / 10 次級 / 4 跨服)
│   └── view/content/
│       ├── buildingRoot   (普通車位: door / 4×building / btnSetting / btnManage)
│       ├── buildingRoot1  (跨服車位: 10×building)
│       ├── buildingRoot2  (本服車位: 4×building)
│       └── workUnit       (打工人員)
├── top                   ← 上方資源條 + 跨界車位 list + 規則
├── btnWareHourse         ← 倉庫 (右上)
├── nodeCross             ← 跨服戰功能列 (舊版？)
├── nodeCross2            ← 跨服戰 v2 (有 buff 三組)
├── nodeServer            ← 本服戰
├── container             ← (sub-view 容器)
├── bottom                ← 底部 nav bar (報表/裝扮/找位/商店/返回/聊天)
├── flyRoot               ← 動畫
└── resItem               ← (掉落資源)
```

### A.2 cmd family — `car_park.*`

從 JS bundle (line 7503) 抽出。共 78 個雙向 cmd（含跨服戰相關）。核心：

| cmd name | 方向 | 用途 |
|----------|------|------|
| `car_park.car_park_info_*` | 雙 | 取得 car park 完整 state |
| `car_park.car_park_car_info_*` | 雙 | 單一坐騎詳細 |
| `car_park.car_park_car_up_*` | 雙 | 升級坐騎 |
| **`car_park.car_park_parking_start_*`** | 雙 | **派車去停 (派坐騎)** |
| **`car_park.car_park_parking_stop_*`** | 雙 | **停止單台車** |
| **`car_park.car_park_parking_stop_all_c2s`** | tx | **一鍵收車** (body: `{car_list: [int]}`) |
| `car_park.car_park_collect_*` | 雙 | 自動收車 toggle (body: `{role_id, code}` code=0/1) |
| `car_park.car_park_bag_rewards_*` | 雙 | 取得倉庫待領清單 |
| **`car_park.car_park_collect_all_bag_rewards_*`** | 雙 | **一鍵領倉庫** |
| `car_park.car_park_combat_*` | 雙 | 戰鬥（搶佔） |
| `car_park.car_park_rename_*` | 雙 | 車位改名 |
| `car_park.car_park_search_*` | 雙 | 搜尋玩家車位 |
| `car_park.car_park_protect_*` | 雙 | 保護罩 |
| `car_park.car_park_skin_use_*` | 雙 | 換皮膚 |
| `car_park.cross_car_park_*` | 雙 | 跨服車位（一整組 ~30 cmds） |
| `car_park.server_car_*` | 雙 | 本服戰車隊系統 |

完整 cmd 列表：搜 `"car_park\."` in `docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js:7503`。

`mount.*` cmd family 在 line 6659（坐騎升級/天賦/技能/收集 7 個）。

### A.3 clickable nodes 完整對照表

掃描結果：57 hits（active=A, inactive=.）。逐一比對 JS bundle 解出綁定。

#### 主建築 (`scrollMiddleShow/view/content/`)

| Cocos 子 path | 觸發 | 說明 |
|---|---|---|
| `buildingRoot/door` | open `ParkingRenameView` (僅 owner) | 大門 = 車位改名 |
| `buildingRoot/btnSetting` | open `ParkingTaxSettingView` (僅 owner) | 管理費設置 |
| `buildingRoot/btnManage` | open `ParkingHorseManageView` (僅 owner) | 坐騎改裝 |
| `buildingRoot/building1..4` | (內部) 點建築進詳情 | 4 個普通車位 — 顯示倒數計時器 |
| `buildingRoot1/building1..10` | (內部) | 10 個跨服車位（目前都 00:00:00） |
| `buildingRoot2/building1..4` | (內部) | 4 個本服車位 |
| `workUnit/teamMember` | (內部) | 打工人員頭像 |

#### 上方控制列 (`top/`)

| Cocos 子 path | 觸發 | 說明 |
|---|---|---|
| `top/scrollHorse/btnReturn` | open `ParkingOneKeyReturnView` → confirm → **`car_park.car_park_parking_stop_all_c2s` body=`{car_list:[…]}`** | **一鍵收車** |
| `top/scrollHorse/view/content/0..4` | (內部) | 5 個跨界車位列表項 |
| `top/btnOpen` | 切換 list 可見性 (純 client) | 展開車位 list |
| `top/btnClose` | 切換 list 可見性 (純 client) | 收回車位 list |
| `top/btnRule` | open `ParkingRuleTips` | 規則 popup |
| `top/work` | open `FarmPlantView`(WorkTeamParking) | 打工 |
| `top/nodeCollect` | `reqParkCollect(toggle, master_id)` → **`car_park.car_park_collect_c2s` body=`{role_id, code:0/1}`** | 自動收車 toggle |
| `top/SpecialBuff` | (display) | buff 顯示 |
| `top/tips/mask` | (display) | tips mask |
| `top/resRoot/resItem` ×2 | (display) | 資源條 |

#### 倉庫 / 跨服戰功能 (右側)

| Cocos 子 path | 觸發 | 說明 |
|---|---|---|
| `btnWareHourse` | open `ParkingWareHouseView` | **倉庫**（內部可一鍵領 `car_park_collect_all_bag_rewards_c2s`） |
| `nodeCross/btnRob` | open `ParkingCrossRobEnemyInfoView` | 搶佔 (舊版) |
| `nodeCross/btnBattle` | open `ParkingCrossReportView` | 戰況 (舊版) |
| `nodeCross/btnDef` | open `ParkingCrossRobInfoView` | 駐守 (舊版) |
| `nodeCross/btnReport` | open `ParkingCrossRecordView` | 日誌 (舊版) |
| `nodeCross2/btnReport2` | open `ParkingCrossRecordView` | 日誌 (新版) |
| `nodeCross2/btnSwitch` | open `ParkingCrossPresetPlanEditView` | 行裝切換 (跨服) |
| `nodeCross2/buff/btnGroup1..3` | open `CommonBuffPopTipsView` | buff 詳情 popup (純 UI) |
| `nodeServer/btnSwitch` | open `ParkingCrossPresetPlanEditView` | 行裝切換 (本服) |

#### 底部 nav bar (`bottom/`)

底部按鈕都是 toggle 模式：第一次按 = 開對應 view，再按 = 關。

| Cocos 子 path | 開啟的子 view |
|---|---|
| `bottom/btnReport` | `ParkingLogView` (日誌) |
| `bottom/btnSkin` | `ParkingDecorateView` (裝扮) |
| `bottom/btnSpace` | `ParkingSpaceView` (找車位) |
| `bottom/btnShop` | `ParkingShopView` (商店) |
| `bottom/btnClose` | (回主頁) |
| `bottom/btnBack` | (回家園) |
| `bottom/btnChatRoot` | `ChatView` (聊天) |

### A.4 自動化建議

優先級最高的自動操作：

1. **一鍵收車**：點 `top/scrollHorse/btnReturn` → 等 `ParkingOneKeyReturnView` 開 → 點 `btnEnsure` → server 收 `car_park_parking_stop_all_c2s`
2. **一鍵領倉庫**：點 `btnWareHourse` → 等 `ParkingWareHouseView` → 點裡面的「一鍵領取」 → server 收 `car_park_collect_all_bag_rewards_c2s`
3. **自動收車 toggle**：點 `top/nodeCollect` 一次（若目前 off）→ server 收 `car_park_collect_c2s` body=`{role_id, code:1}`

或者跳過 UI，直接送 cmd：

```python
def park_stop_all(page, car_list: list[int]):
    # body = {car_list: [...]} — varint repeated
    pass

def park_collect_toggle(page, role_id: int, on: bool):
    # body = {role_id, code: 1 if on else 0}
    pass
```

但要先 capture 一次成功的 round-trip 來校準 protobuf field number — 不要憑 JS 變數名臆測。

---

## B. 家族 (GuildMainView)

### B.1 view 結構

Root: `/UIRoot/NormalView/MainView/container/GuildMainView/GuildMainView`

Top-level children:

```
GuildMainView (inner prefab)
├── load                    ← 載入動畫 (普通 inactive)
├── load1                   ← 載入動畫 (inactive)
└── GuildMapSceneView       ← 主畫面 (地圖場景)
    ├── btnChatRoot
    ├── Infobg
    │   ├── btnInfo-001     ← 家族資訊按鈕
    │   └── simp
    │       ├── toggle      ← **隱藏其他人 toggle**
    │       └── btnOpen     ← 展開/收回 info 列
    └── riceParty           ← 家族宴會 (現在 inactive 因為沒在辦)
```

注意：GuildMainView 是「家族地圖場景」式介面，玩家在地圖上看到別家族成員的 model
都跑在 `BattleRoot/Unit/*` 下，**不是 click target**（沒 click listener，純視覺）。
真正的點擊都集中在 GuildMapSceneView 的 UI 層。

### B.2 cmd family — `guild.*`

從 JS bundle (line 6439 / 7835，`GuildControl.ts` module) 抽出。`send_29_N` /
`update_29_N` 命名暗示 cmd_group=29 (0x1D)，但實際 cmd 編號要 capture 才能確定。

| send 函式 | cmd name (c2s) | 用途 |
|-----------|----------------|------|
| `send_29_1(guild_id)` | `guild.guild_info_c2s` | 取得家族資訊 |
| `send_29_2(name)` | `guild.guild_create_c2s` | 建立家族 |
| `send_29_3(type, key, page)` | `guild.guild_search_c2s` | 搜尋家族 |
| `send_29_4(guild_id)` | `guild.guild_join_c2s` | 加入 |
| `send_29_5()` | `guild.guild_quick_join_c2s` | 快速加入 |
| `send_29_6(k, v, s, l)` | `guild.guild_setting_c2s` | 設定 (k=key, v=value) |
| `send_29_7(guild_id)` | `guild.guild_apply_c2s` | 申請列表（會長看） |
| `send_29_8(uid, type)` | `guild.guild_approve_c2s` | 同意/拒絕申請 (type=1/2) |
| `send_29_9(role_id)` | `guild.guild_kick_out_c2s` | 踢人 |
| `send_29_11()` | `guild.guild_quit_c2s` | 退出 |
| `send_29_12()` | `guild.guild_dissolve_c2s` | 解散 |
| `send_29_13(role_id, career)` | `guild.guild_change_career_c2s` | 改職位 |
| `send_29_16(guild_id)` | `guild.guild_members_info_c2s` | 成員資訊 |
| `send_29_17()` | `guild.guild_donate_c2s` | 捐獻 |
| `send_29_18()` | `guild.guild_apply_list_c2s` | 我申請過的列表 |
| `send_29_20(rank_type, page, gid?)` | `guild.guild_rank_info_c2s` | 排名 |
| `send_29_21(rank_type, ?)` | `guild.guild_rank_my_info_c2s` | 我的排名 |
| `send_29_22()` | `guild.guild_question_c2s` | 問答 |
| **`send_29_24(pos, break)`** | **`guild.guild_area_enter_c2s`** | **進地圖（登入/重連都會送）** |
| `send_29_25()` | `guild.guild_area_exit_c2s` | 離開地圖 |
| `send_29_26(pos_list)` | `guild.guild_area_move_c2s` | 在地圖移動 |
| `send_29_28(type)` | `guild.guild_help_info_c2s` | 求助列表 |
| `send_29_29(type, sub_type)` | `guild.guild_help_ask_c2s` | 發求助 |
| `send_29_30(help_id)` | `guild.guild_help_c2s` | 幫人 |
| `send_29_32()` | `guild.guild_help_status_c2s` | 求助狀態 |
| `send_29_33()` | `guild.guild_dice_start_c2s` | 骰子開始 |
| `send_29_34()` | `guild.guild_dice_point_c2s` | 骰子點數 |
| `send_29_36(box)` | `guild.guild_treasure_open_c2s` | 開寶箱 (body: `{n, round}`) |
| `send_29_38(log_id)` | `guild.guild_log_c2s` | 日誌 |
| `send_29_39(now_page_id)` | `guild.guild_get_message_notice_list_c2s` | 訊息通知列表 |

s2c handlers（broadcasts）：

| handler | cmd | 用途 |
|---------|-----|------|
| `update_29_15` | `guild.guild_members_change_s2c` | 成員變動推播 |
| `update_29_19` | `guild.guild_resource_change_s2c` | 資源變動 |
| `update_29_27` | `guild.guild_area_broadcast_s2c` | 玩家在地圖上的動作 (add/move/break/del) |
| `update_29_40` | `guild.guild_new_message_notice_s2c` | 新訊息 |
| `update_29_41` | `guild.world_schedule_update_s2c` | 世界排程更新 |

完整：搜 `"guild\.guild_"` in JS bundle line 6439, line 7835。

### B.3 「隱藏其他人」按鈕 — 純 localStorage flag

**Cocos path**: `/UIRoot/NormalView/MainView/container/GuildMainView/GuildMainView/GuildMapSceneView/Infobg/simp/toggle`

**Component**: `cc.Toggle`（不是 Button）

**綁定** (JS bundle line 6531)：

```js
this.view.addComponentCallbackListener(i.node, Toggle.EventType.TOGGLE, function() {
  if (i.isChecked != getShowOthers()) {
    setShowOthers(i.isChecked ? 1 : 0);
    normalEvent.emit(GUILD_HIDE_OTHERS);
  }
});
```

**狀態源** (GuildDataCache, line 6443)：

```js
this.isShowOthers = (1 == storage.get("GUILD_SHOW_OTHERS"));
// 若沒設定過，預設打開
if (!storage.get("GUILD_SHOW_OTHERS")) {
  setShowOthers(1);
  this.isShowOthers = true;
}

setShowOthers(v) {
  storage.set("GUILD_SHOW_OTHERS", v);   // ← localStorage
  this.isShowOthers = (1 == v);
}
```

**重點**：
- **沒有任何 WS 流量**。完全是 client-side 切換。
- localStorage key: `GUILD_SHOW_OTHERS` (值 `"1"` = show, `"0"` 或 unset = hide)
- 切換後 emit `GUILD_HIDE_OTHERS` event，view 收到後重 render 過濾掉其他玩家 model。

**自動化方法**：

選項 A — 模擬 Toggle UI：

```python
def set_hide_others(page, hide: bool):
    js = """
    ([hide]) => {
      const find = (root, parts) => { let n = root; for (const p of parts) { if (!n || !n.children) return null; n = n.children.find(c => (c.name||'')===p); if (!n) return null; } return n; };
      const toggle = find(cc.director.getScene(),
        ['UIRoot','NormalView','MainView','container','GuildMainView','GuildMainView','GuildMapSceneView','Infobg','simp','toggle']);
      if (!toggle) return 'no_toggle';
      const comp = toggle.getComponent('cc.Toggle');
      if (!comp) return 'no_comp';
      comp.isChecked = !hide;  // checked = show others
      comp.node.emit('toggle', comp);
      return {hide, isChecked: comp.isChecked};
    }"""
    return page.evaluate(js, [hide])
```

選項 B — 直接寫 localStorage（更穩，跳過 UI emit）：

```python
def set_hide_others_via_storage(page, hide: bool):
    page.evaluate(
        "([v]) => localStorage.setItem('GUILD_SHOW_OTHERS', v)",
        ["0" if hide else "1"]
    )
    # 注意：這需要重 enter 家族 area 才生效（view 不會自動重 render）
```

實務上推薦選項 A — 點 toggle 一次，UI 會自己重 render。

### B.4 主畫面 5 個 clickable nodes

| Cocos 子 path | 用途 |
|---|---|
| `GuildMapSceneView/btnChatRoot` | open `ChatView` |
| `GuildMapSceneView/Infobg/btnInfo-001` | open `GuildView` (家族總覽：成員/職位/設定/捐獻/排名/...) |
| `GuildMapSceneView/Infobg/simp/toggle` | **隱藏其他人 toggle**（純 client） |
| `GuildMapSceneView/Infobg/simp/btnOpen` | 展開/收回資訊列（純 client） |
| `GuildMapSceneView/riceParty` | 家族宴會（active 時 open `RiceParty*View`） |

進階 sub-view（從 `GuildView` 主視窗點進去的）會在打開後另外掃。

### B.5 全 NormalView 同時 scan 的差異

在 NormalView root scan 時會額外撈到 `MainView/top/systemTop/*` 跟 `subRoots/*`
的全域 UI（共 79 hits），這些不是家族專屬 — 是任何 tab 都有的全局頂部/側邊列。
列出僅供參考（已記錄在 `PAGE_NAVIGATION.md`）。

家族專屬的全局 hint：

| Cocos 子 path | 用途 |
|---|---|
| `MainView/top/systemTop/btnRoot/btnSeason` | 賽季按鈕（會切到家族戰季） |
| `MainView/top/systemTop/btnRoot/btnPvp` | 競技場 |
| `MainView/subRoots/btnGuildAct` | **家族活動入口**（當有家族活動時 active） |

---

## C. 用 JS bundle 分析的方法（給之後 reverse 用）

`docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`
是整個 game client 編譯後的 bundle。所有 cocos view 的 binding 都在裡面。

### C.1 找一個 view 的 module

```bash
grep -n "_virtual/<ViewName>" docs/game_client_sources/index.966f5.js
```

通常 view 自己佔一行 (`System.register(...)`)，跟它的 sub-view / control 各佔
不同行。`awk 'NR==<lineno>'` 把該行抽出來。

### C.2 找 button binding

binding pattern：

```js
var x = this.findChild("<cocos/path>");
this.addComponentCallbackListener(x, Button.EventType.CLICK, function() { ... });
```

`<cocos/path>` 就是該 button 在 view 內的相對路徑，跟 `tools/scan_view_clickables.py`
掃出來的路徑一一對應。

handler body 內容大致幾種：

| pattern | 意義 |
|---------|------|
| `uiMgr.openView("XxxView", ...)` | 開子 view |
| `uiMgr.close("XxxView")` | 關 view |
| `IS(C).send_NN_M(args)` | 送 cmd（找 C 是哪個 Control，就知道是哪個 cmd family） |
| `IS(C).reqXxx(args)` | 同上（明確命名版） |
| `localStorage.set / storage.set` | 純 client flag |
| `normalEvent.emit(...)` | 純 client event |

### C.3 找 cmd name

兩種寫法都會出現在 source：

```js
netManager.send("car_park.car_park_parking_stop_all_c2s", body)
netManager.addEventListener("car_park.car_park_info_s2c", handler, this)
```

搜尋 `"<prefix>\."` (例如 `"car_park\."`、`"guild\."`、`"mount\."`) 就能拿到
該系統的完整 cmd table。

### C.4 找實際 cmd 編號

JS source 用的是 string name，但 wire 上跑的是 cmd_id (int)。要拿到 mapping：

1. 在 page 上裝 [[live-protocol-decoder]] 的 ws probe
2. 點該 button
3. drain ring buffer，看哪個 cmd 號剛被送
4. cross-reference: `cmd_id ↔ string_name`

或者搜 game source 裡的 cmd table 註冊處（通常是 `protobuf.lookupType` + `setSendCmd`
等 pattern），但通常不如直接觀察 tx 流量快。

---

## D. 還待做

| 項目 | 狀態 |
|------|------|
| capture `car_park_parking_stop_all_c2s` body 校準 protobuf field | TODO（要等到有東西可收的 timing） |
| capture `guild_area_enter_c2s` 確認 pos 結構 | TODO |
| 從 `GuildView`（家族總覽 popup）的二級畫面掃節點 | TODO（要點 btnInfo-001 進去再掃） |
| 從 `ParkingWareHouseView` 掃節點 | TODO |
| 把以上整合到 `bot_state` / 自動任務 | TODO（先不接，避免 5554 上線就送 stop_all） |

## E. 相關文件

- `docs/protocol/PAGE_NAVIGATION.md` — 整個頁面導航體系（家園 tab / 家族 tab / 主頁 fast-path）
- `docs/protocol/REDPACK_SCHEMA.md` — 紅包協議（同樣用本文件描述的 JS-bundle 法解出）
- `.claude/skills/cocos-app-analysis/SKILL.md` — methodology
- `tools/scan_view_clickables.py` — 本次新增的 scanner，給之後掃其他 view 用
- 原始 scan dump: `tools/_probe_out/parking_clickables.json` (57 hits) / `tools/_probe_out/guild_clickables.json` (5 hits) / `tools/_probe_out/guild_normalview.json` (79 hits incl. global)

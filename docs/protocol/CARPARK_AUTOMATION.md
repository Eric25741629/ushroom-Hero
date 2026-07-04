# 菇菇車位自動化 — 設計 + 進度紀錄 (2026-05-20)

## 目標 (user goal, set 2026-05-20 via /goal)

```
台灣 10:00–21:59:  總共 6 台車，1 跨界 + 5 一般
台灣 22:00–09:59:  總共 5 台車，0 跨界 (全部一般)
5554:              跨界限定 泊銀車座 (SILVER)
用戶可配置:        lot tier / 靠前(高獎勵) vs 靠後(低獎勵多空位) / 抱團 (≥5/9 同 lot 有額外獎勵)
追蹤需求:          所有本服 lot 槽位狀態，偵測「被打掉」事件 (空了 OR 換人) — 用 NAME 追蹤強者
```

## 架構

```
utils/
├── carpark_state.py    Read-only state inspection (no clicks)
├── carpark_auto.py     Actions (clicks): park / unpark / reconcile
└── carpark_tracker.py  Snapshot diff for kick-out detection
```

中央邏輯：

```
_run_carpark_check_if_due(d, ip)        # in new_main_v2.py
  → reconcile(page, cfg)                # in carpark_auto.py
       → take_snapshot()                # state.py
       → for each gap: park_one_silver  # auto.py — fully implemented
                       park_one_normal  # TODO
                       recall_excess    # TODO
```

接入點 `new_main_v2._run_daily_tasks` Task 0.5，gated by：

1. `experimental_cocos_navigation: true`
2. `backend == web_h5`
3. live Playwright `_page`
4. device cfg `carpark.enabled: true`

→ 目前只 emulator-5554 滿足。

## 配置 schema (bot_config.json → device → carpark)

```json
{
  "enabled": true,
  "cross_tier": "silver",            // 5554 必須 silver (user policy)
  "cross_lot_preference": "back",    // "front" / "back"
  "cluster": true,                   // 抱團: 優先 ≥5 占用的 lot
  "daytime_total": 6,
  "daytime_cross": 1,
  "nighttime_total": 5,
  "nighttime_cross": 0
}
```

## 跨界停車 UI flow (已 reverse 完成，validated 2026-05-20)

```
ParkingMainView
  bottom/btnSpace  →  ParkingSpaceView
    tab content/128 (跨界車位)  →  ParkingCrossSpaceView2 (level picker)
      cell content/<N>/btnParkingSpace  (N=0..4 對應 5 個 tier)
        cell name "0"=奇星 (id=5,Server), "1"=曜鑽 (id=1,DIAMOND),
        "2"=鎏金 (id=2,GOLD), "3"=泊銀 (id=3,SILVER), "4"=灰銅 (id=4,Bronze)
      → emit PARKING_CROSS_SHOW_PUBLIC_SPOT
      → root/item.active = true (detail list 開出)
    root/item/ScrollView/view/content/<N>/btnParkingSpace  (N=0..29)
      → call scrollList.scrollTo(N, 0) 先 render
      → click → send car_park.car_park_info_c2s {type:3, master_id, ceng}
      → server response (~2-3KB) → ParkingMainView re-renders
                                    scrollMiddleShow/.../buildingRoot1 顯示 10 spots
  scrollMiddleShow/view/content/buildingRoot1/building<N>  (N=1..10)
    → click empty (use nodeName.active to detect!)
    → opens ParkingHorseParkManageView (car picker)
  ParkingHorseParkManageView
    root/ScrollView/view/content/<N>  → click to select car
    root/nodeStatus/nodePark "開始停車"  → send parking_start cmd
```

## 各介面/UIList 重點

| Element | 重要規則 |
|---------|---------|
| `scrollMiddleShow/buildingRoot1` | 一個 lot 的 10 spots。**UIList 虛擬化但同時 cell 重用** — `playerName.string` 是 stale。用 `nodeName.active` 判斷 |
| 跨界 level cell (content/0..4) | 4 個顯示但 _datas 有 5 個。cell name → pool_id mapping in `POOL_TYPE_TO_ID` |
| 跨界 detail cell (content/0..29) | 30 個鉑銀 lots。需 `scrollList.scrollTo(N, 0)` render 目標 idx |
| `top/scrollHorse` | 我自己 deployed cars list。每 cell 有 txtHp/txtTime/txtTime-001/txtLimit (**遞迴 grab** — non-direct children) |
| `ParkingHorseParkManageView/root/ScrollView/content/<N>` | 我的 7 台車 picker。`_datas[N]` 只有 mount_id；要點 cell 才看得到 `nodeChange/txtParkTime` |

## 關鍵 cmd schemas

| cmd | id | body | 用途 |
|-----|----|------|------|
| `car_park_info_c2s` | 0x3801 | `{type, master_id, ceng}` | 進入/查詢 lot info |
| `car_park_info_s2c` | (same?) | `{type, master_id, master_name, space_list, ...}` | 回 lot 詳細 |
| `car_park_parking_start_c2s` | 0x322f? | `{type, master_id, mount_id, pos, is_protect, is_replace}` | 派車去停 |
| `car_park_parking_stop_c2s` | (TBD) | `{mount_id}` | 收回單台車 |
| `car_park_parking_stop_all_c2s` | (TBD) | `{car_list:[ids]}` | 一鍵收車 (ParkingOneKeyReturnView 觸發) |
| `car_park_search_c2s` | (TBD) | `{type, park_name}` | 搜車位（含本服公開） |

**Important**: send 一定要透過 `netManager.send("name", obj)`，不要 raw `sock.sendMessage(cmd_id, bytes)` — protobuf wrapper 加 metadata。raw bytes server 回 0x0201 error_code=2 (confirmed)。

## 純 WS 跨界停車 (LIVE-verified 小寶 2026-06-11, `ws_token/carpark.py`)

純 WS 已完整閉環停車成功 (cmd 0x322f=12847 SUCCESS)。關鍵協議事實:

- **取可停 lot 列表 = `car_park_search`(12808) `{type:4}`** (NEW 跨服流程, client `reqParkSearch(crossSpaceList=4,"")`)，回 `null_space:[p_car_park_null]`。**不是** `cross_car_park_preview`(12830) — 那是舊流程，NEW 帳號 (`checkNewCrossOpen()==true`) preview 回空。
- `p_car_park_null {park_type#1, master_id#2, null_num#3, ceng#7}`；`null_num`=空格數，`>0` 才可停。`master_id` 形如 `1001001065`，`ceng == master_id % 1000`。
- **lot 容量 10、pos 是 1-based (1..10)**。`car_park_info`(12801, type=3, master_id, ceng) 回的 `space_list` **只列已占用格** (空格不出現)；空 pos = `{1..10}` 減占用集 (live: lot 1001001045 占 {1,2,3,4,6,8,10} → null_num=3 = 缺 5,7,9)。
- 停車 = `cross_car_park_new_parking_start`(12847) `{park_id#1=master_id, pos#2, mount_id#3}`；舊流程 12832 `{id#1, mount_id#2, pos#3}` 欄位順序相反。
- 閘門: `checkNewCrossOpen()` 需帳齡 ≥ `park_cross_creat_limit`(8天)；非開放時段 search 回空列表 → no-op，不會炸。
- 開關: `ws_token.runner --carpark-auto` 或 device config `ws_token_carpark_auto`；smoke `python -m ws_token.carpark_smoke --device 7fe98fc6 --search` (dry) / `--auto-park` (真停)。只停不收。

## 純 WS 日/夜窗口跨界停車 plan（手機fc，2026-06-13）

`ws_token/carpark_plan.py` + `runner._run_carpark` plan 路徑（config
`ws_token.carpark_plan`，dashboard 方案 adb+ws / 離線 WS fallback 都會走到）：

- 配額：day（08:00-20:00）cross=1、night cross=0；窗口內持續補停（state 記
  `ws_state/<device>.json` → `carpark_plan.{day,night}.{date,cross}`，0 台停入
  不寫 state 下次喚醒重試；跨午夜窗口邏輯日=窗口起始日）。
- **泊銀=跨界的 pool（id=3）**，同一套協議。search(12808 type=4) 一次回**全部
  檔次**的 lot（LIVE 2026-06-13 小寶：68 筆，ceng 1..68 連續，master_id
  `1001001NNN`，ceng==master_id%1000）。泊銀 lot = **ceng 5..34 = 鉑銀1..30**
  （configCross_parking_lot dump 2026-05-20）。`null_space` 欄位：5=skin_plus
  6=ext 8=reward_buff（皆 p_key_value）。
- 選 lot：`auto_select_and_park_many` 限定泊銀 ceng 範圍，優先
  `silver_levels`（預設 [9,10] → ceng 13/14），滿了退其他泊銀 lot（ceng 升冪）。
- 本服（search type=2，master_id=1467，ceng 1..5）/好友（type=1，回 role 列表）
  車位**不自動化**——遊戲內建（使用者 2026-06-13 指示）。type=3/5 search 不回應。
- **跨界窗口 = 台灣 10:00-22:00**（夜間只有本服可停）→ 預設 day window
  ["10:00","22:00"] cross=1、night cross=0。一人只能停 1 台跨界。
- **抱團 = 同服**（使用者 2026-06-13 三次指示）：登入回包 `role_login_s2c`
  欄位 #3 = **server_id**（LIVE 小寶=1467，正好等於本服車位 master_id）。
  排序 =（鉑銀9/10 優先群, 同服占用數降冪, null_num 升冪, ceng）；同服判定
  掃占用格 `info_list#6`/`ext#8` kv 值 == server_id（`count_same_server`；
  attr 欄位 id **待開窗採樣驗證** → `tools/carpark_cluster_probe.py`，kv 全
  無匹配時自動退回占用數排序，安全降級）。預讀候選 lot 上限 8（搶位要快）；
  runner TASK_ORDER 把 carpark 移到登入後第一個任務。
- **收益領取（純 WS）**：倉庫清單 `car_park_bag_rewards` **12845**(0x322d)、
  一鍵領 `car_park_collect_all_bag_rewards` **12846**(0x322e)，c2s 皆空 body；
  沒東西可領時 12846 回 0x0201 error_code=**173**（LIVE 小寶 2026-06-13）。
  plan 路徑每輪喚醒先領收益再停車（與窗口無關）。12844 無回應、12843 回 173。

## Stale-label trap

Cocos UIList 重用 cell node 顯示不同 data。**Label.string 不會自動清空**。
判斷 spot 狀態必須用 sub-node 的 `active`，不能依賴 string。詳見 memory `feedback-cocos-uilist-stale-labels`。

## 實作狀態 (final 2026-05-20)

| 功能 | 狀態 |
|------|------|
| Time gate (台灣 10am-22pm) | ✅ `is_daytime_window()` |
| Target state 解析 | ✅ `target_state(cfg)` |
| Snapshot 自己 deployed | ✅ `take_snapshot()` 走 top/scrollHorse |
| Universal occupancy indicator | ✅ `upgrade.active` works for cross + own + foreign (vs `nodeName.active` which only fires for foreign) |
| 找空 lot+spot | ✅ `_click_silver_lot_by_idx` + `_click_empty_spot_in_current_lot` |
| 選 0 分鐘車 | ✅ `_pick_zero_minute_car_and_park()` |
| Park one silver | ✅ `park_one_silver(prefer_back, cluster)` 完整實作 + live test 成功 |
| **Recall one cross car** | ✅ `recall_one_cross(slot_idx)` 走 ParkingGainView→btnStart→btnEnsure |
| **Recall N cross cars** | ✅ `recall_n_cross(n)` 排序 prefer at_limit + 100% HP first |
| **Park one normal (home)** | ✅ `park_one_normal` 點 bottom/btnBack → home buildingRoot → 空 spot → 選車 (現實: 5554 home 4/4 滿) |
| Reconcile (full cycle) | ✅ 起始清理 popup + recall excess cross + try normal w/ diagnostic |
| Tracker snapshot | ⚠ 結構完成，cache parse 邏輯需 real capture 驗證 |
| Tracker diff (kick-out) | ✅ `diff_scans()` — 處理「空了」+「換人」兩種事件 |
| Tracker persistence | ✅ JSONL append to `logs/<device>/carpark_snapshots.jsonl` |
| CLI tool | ✅ `tools/carpark_check.py` (status/park-silver/scan-silver/reconcile) |
| 整合 new_main_v2 task loop | ✅ `_run_carpark_check_if_due` Task 0.5 |
| Unit tests | ✅ 31 個 (state + auto) |

## 範圍 (per user clarifications 2026-05-20 16:20–16:30)

User 多次澄清最後的 scope:

> 我現在沒有自動化的就是跨界停車  無需處理一鍵收車
> 只有左下角的倉庫要領 這個沒有自動化

→ Bot 負責**兩件事**：
1. **Cross silver 部署** (top-up only — 若日間 cross_count < 1，部署 1 台)
2. **倉庫領取** — 每 cycle check `btnWareHourse/RedPoint`，有紅點就點 + 點 領取

其他全部委派外部系統：
- ❌ Recall (含一鍵收車) — 外部系統處理
- ❌ 一般 5 台 (好友家，每好友 1 台) — 外部系統已自動化
- ❌ Home park 部署 — 外部系統已自動化

`utils/carpark_auto.reconcile()`:
1. `claim_warehouse(page)` — 點 btnWareHourse + 點 領取 (cmd 0x322e)
2. 若 cross_count < target → 呼叫 `park_one_silver(prefer_back, cluster)`
3. 若 cross_count ≥ target → 只 log，不 recall
4. Normal/home — 只 log，不 deploy

`park_one_normal` / `recall_one_cross` / `recall_n_cross` 函數仍保留為 CLI utility / debug tools。

## 5554 行為 (live validated 2026-05-20 16:00)

- **Cross deployment**: ✅ 完全自動化
  - Day target=1 (SILVER 限定)、Night target=0
  - Recall:每 cycle 約收 1 台 (click 偶爾要 retry，spacing 2s 收斂)
  - Park: 找有 ≥5/9 抱團 + 後排 (高 lot_id 低獎勵) 的 SILVER lot
- **「5 一般」** (home park `buildingRoot`):
  - Home capacity = **4 spots** (per ParkingMainView 結構)
  - 目標 5 normal **超過 home capacity**，bot best-effort 填滿到 4
  - 在 5554 已觀察到 3/4 home occupied，bot 嘗試補 1 但 server 拒絕（picker 顯示所有 7 cars，但都已部署 — 不能重複）
- **Snapshot account**:
  - `take_snapshot` 同時讀 scrollHorse (cross) + buildingRoot (home)
  - Reconcile verify 每次 normal deploy 後 home_occupied 真的增加，否則 abort + log
- 配置 `cross_lot_preference="back"` + `cluster=true` 會優先選 鉑銀 25-30 內滿 ≥5 人的 lot

## 不可達目標說明

User goal「1 跨界 + 5 一般 = 6 cars」對 5554 不完全可達，因為：
1. Home park (buildingRoot) 容量只有 4
2. 我目前的車庫 7 台車已全部部署 (4 cross + 3 home + 0 idle)，超過 target 6
3. Picker 不過濾已部署 — server 在送 parking_start 時才拒

bot 最佳趨近狀態 (一旦收斂)：
- **1 cross silver + 4 home = 5 cars** (target 6，缺 1 因 home capacity 上限)
- OR: **2 cross silver + 4 home = 6 cars** (剛好 target 但 cross 多 1)

Reconcile 每 cycle 趨近，最終穩定。

## Tracker (kick-out 偵測)

- `utils/carpark_tracker.py` 結構完成
- `LotScan.spots[i].occupied = upgrade.active` (universal indicator)
- `diff_scans(prev, curr)` 偵測「空了」+「換人」(role_id 不同)
- Persistence: JSONL append
- ⚠ ParkingDataCache.space_list raw field schema 需 live capture 驗證

## Live test 結果 (2026-05-20 15:30-15:53)

```
初始: 5 cross deployed
park_one_silver → 6 cross (新車進 鉑銀30 building1)
recall_one_cross x5 → 1 cross
reconcile() → snapshot=1cross target=1cross "對齊"，normal skip
```

## 後續可選

1. **本服 deployment**: 若 user 想真的部署 5 一般，需要：
   - 釐清 5554 為何 本服列表 empty (帳號限制 vs 需先 reqParkSearch seed?)
   - 反推 ParkingPublicSpaceView 點 cell → enter lot 流程 (cmd type=2?)
   
2. **Tracker live capture**: 跑一次 `scan_all_silver_lots(page)` capture 30 個 lot snapshots，看 ParkingDataCache.space_list 的真實 field schema (role_id / role_name / mount_id / end_time)。

3. **Dashboard 整合**: 把 carpark snapshot + reconcile 日誌串進 control panel UI。

## Test

```bash
# 純 logic test (no Cocos)
pytest tests/test_carpark_state.py tests/test_carpark_auto.py

# Live test (要 5554 跑著)
python tools/carpark_check.py --action status
python tools/carpark_check.py --action park-silver --back
python tools/carpark_check.py --action scan-silver --back
```

## 相關文件

- `docs/protocol/CARPARK_GUILD_NODES.md` — 車位介面節點掃描原始紀錄
- `docs/protocol/REDPACK_SCHEMA.md` — 紅包協議 (同 game 同方法解出)
- Memory: `feedback-cocos-uilist-stale-labels` — UIList 虛擬化陷阱
- Memory: `reference-carpark-guild-nodes` — 車位 + 家族節點 ref

# 龍骸聖域自動化設計 (Dragon Realm Automation)

- 日期：2026-06-04
- 狀態：設計已批准，待寫實作計畫 (writing-plans)
- 後端：H5 (web_h5) 為主；ADB 後端日後再補
- 整合：接進 `daily_pipeline`，由 `dragon_realm_enabled` flag 閘控，預設 **off**
- 時間閘：活動每天 **10:00 才開**（同 sea 的時間閘）；scheduler 在 10:00 前不嘗試

## 1. 背景與目標

龍骸聖域是遊戲內的探索活動。客戶端內部代號 **`dragon_realm`** / **`ActivityLhsy`**（龍骸聖域拼音縮寫），整個玩法走 WebSocket 具名 RPC（`netManager.send("dragon_realm.*_c2s", {...})`）。

玩家進入後預設體力 30/30，按「探索」消耗體力，隨機觸發三種事件：**怪物 / 遺跡 / 陷阱**。打倒怪物有機率掉落進入下一層的鑰匙，最多 3 層。

**自動化目標**：

1. 第 1 層持續探索，直到背包鑰匙 ≥「進第 2 層所需」→ 進入第 2 層。
2. 第 2 層持續探索，直到背包鑰匙 ≥「進第 3 層所需」(即可進第 3 層的條件達成) → **停下來，絕不進第 3 層**。
3. 任一層體力用完（不足以下一次探索）→ 停（**不使用體力道具**）。

**行為策略（使用者確認）**：

- 陷阱：**只按求助，絕不掙扎**（與客戶端預設不同；客戶端 action 6 會用體力掙扎，我們關掉）。
- 怪物打不死(IsChallenge)：按求助讓隊友協助。
- 協助：**主動協助隊友 + 領取自己的協助獎勵**。
- 假設帳號**已在固定隊伍**（不碰組隊邏輯）。

## 2. 客戶端決策樹 (source of truth)

逆向自 `docs/game_client_sources/...index.966f5.js` 的 `ActivityLhsyDataCache.autoExploreHandler()`。下方為原始邏輯 + 我們的覆寫標記。

### 2.1 解出的列舉 (已確認)

**EventType (事件型別)**：

| 值 | 名稱 | 我方處理 |
|----|------|----------|
| 1 | PVE | 怪物：可打 `choice(1)` 前進；IsChallenge 未求助 → `choice(3)` 求助 |
| 2 | PVP | 同 PVE |
| 3 | BOX | 寶箱：有鑰匙 `choice(1)`，否則 `choice(2)` 繞路 |
| 4 | TRAP | 陷阱：IsChallenge → **一律 `choice(3)` 求助**（永不掙扎 choice(1)） |
| 5 | BUFF | `choice(1)` 直接前進 |
| 6 | CAVE | `choice(1)` 直接前進（遺跡類，「直接點就好」） |

**EventDataKey (當前事件 `data` 陣列的 k)**：PveHp=1, TrapTime=2, BackKillTime=3, IsChallenge=4, RoleId=5, MaxHp=6, IsAskHelp=7, Ceng=9

**ActivityLhsyKey (config `configDragon_map_kv` 的 key，靜態設定)**：

| key | 名稱 | 用途 |
|-----|------|------|
| 7 | ENTER_TIER_TWO_REQUIRE | `[key_gtid, count]` 進第 2 層所需鑰匙 |
| 8 | ENTER_TIER_THREE_REQUIRE | `[key_gtid, count]` 進第 3 層所需鑰匙（我們達成即停） |
| 15 | CHEST_KEY | 寶箱鑰匙對照 `[..,gtid,event_id]` |
| 16 | BACK_KILL | 怪物「再次擊殺」冷卻秒數 |
| 17 | STAMINA_TIER | 每層探索體力消耗陣列 (`[ceng-1]`) |
| 26 | STAMINA_ITEM | 體力道具 gtid（我們**不用**） |
| 32 | BACK_TRAP | 陷阱掙扎冷卻（我們不掙扎，僅參考） |

### 2.2 決策樹（每次收到 `dragon_realm_info_s2c` 後執行一次）

依序判斷，命中即送出對應 action 並結束本輪（等下一個 s2c）：

1. **再次擊殺（列表）**：eventList 中我方的 PVE/PVP 事件，若 `BackKillTime + BACK_KILL - serverTime <= 0`（冷卻過）→ `event_choice(1, <list_entry.id>)` + 刷 `help_event_list`。注意：client 傳的 uid 是 **列表項目的 `id`**（`i.id`），非事件設定的 `event_id`（`i.event_id`）；兩者區別待 live 驗證。
2. **協助隊友**：`info.help_hp > 0` 且 eventList 有「非我方」的事件 → `provide_help(role_id, event_id)`。
3. **進入下一層**（覆寫）：
   - ceng==1 且 `bag_count(TIER_TWO_REQUIRE[0]) >= TIER_TWO_REQUIRE[1]` → `enter_ceng(2)`。
   - ceng==2 且 `bag_count(TIER_THREE_REQUIRE[0]) >= TIER_THREE_REQUIRE[1]` → **STOP**（客戶端原為 `enter_ceng(3)`，我們改成停）。
4. **當前事件**：
   - `info.event_id == 0`（無事件）：體力夠 → `start_explore`；體力不足 → **STOP（不用道具）**。
   - 有事件，依 EventType 走 2.1 表格：
     - PVE/PVP：`IsChallenge` 且未 `IsAskHelp` → `choice(3)` 求助；非 challenge（可打）→ `choice(1)` 前進；已求助但冷卻過 → `choice(1, event_uid)` 再次擊殺。
     - TRAP：`IsChallenge` → `choice(3)` 求助（**永不 choice(1)**）；非 challenge → `choice(1)` 前進。
     - BOX：有鑰匙 `choice(1)`，否則 `choice(2)` 繞路。
     - BUFF/CAVE：`choice(1)`。
5. **領獎**：每輪檢查 `help_event_list`/`receive_help_event`，有可領 → `receive_help_event(event_id)`。

### 2.3 體力判斷 (HpEnough)

`need = STAMINA_TIER[ceng-1]`；`hp = info.getHp()`。若 `hp < need` → **STOP**（我們不走客戶端的「用體力道具」分支）。

### 2.4 choice 對照

- `choice(1)` = 前進 / 擊殺 / 掙扎（依事件型別）
- `choice(2)` = 繞路（寶箱無鑰匙）
- `choice(3)` = 求助

## 3. 架構

新增 `dragon_realm/` package，對齊 `sea_v2/` / `farm_v2/` 的分層風格。

| 檔案 | 職責 | 依賴 |
|------|------|------|
| `dragon_realm/client.py` | H5 RPC 橋接：以 `page.evaluate` 封裝 (a) 送具名 c2s、(b) 註冊一次 listener 把最新 `dragon_realm_info_s2c` / `help_event_list_s2c` 存到 `window.__dr_state`，(c) 讀回最新 state + 背包數量 + config KV。讀取 config 用 cocos config table（參考 configFly 列舉法）。 | Playwright page |
| `dragon_realm/state.py` | `@dataclass(frozen=True) DragonState`：`ceng, event_id, event_uid, event_type, help_hp, hp, is_challenge, is_ask_help, back_kill_time, event_list[], stamina_need, server_time`。純解析 raw dict → dataclass。 | 無（純 Python） |
| `dragon_realm/planner.py` | 純函式 `decide(state, config, prefs) -> Action`。完整 port 2.2 決策樹 + 我方覆寫。`Action` 為 frozen dataclass（`kind` ∈ explore/choice/enter_ceng/provide_help/receive_help/stop，附參數）。**無任何 IO**。 | `state.py` |
| `dragon_realm/service.py` | 迴圈協調：`read state → decide → act → 等下一個 s2c`。含 wall-clock 預算、dead-loop 偵測（同一 (event_id,action) 連續 N 次 → 中止）、`pause_guard` 綁定、錯誤截圖。 | client + planner + utils |
| `game_actions/dragon_realm_scheduler.py` | `run_dragon_realm_if_due(ip, d)`，flag 閘控 + 每日冷卻（`json_manager`）。在 `daily_pipeline.run()` 內呼叫。 | service + config + json_manager |

### 3.1 為何選方案 A（client 具名 RPC）

`WebGameAPI.call_raw` 走「數字 cmd_id + 手刻 protobuf」，需 live capture 解碼 10+ 個 dragon_realm 訊息。方案 A 改用 `page.evaluate` 直接呼叫 client 已註冊的 `netManager.send("dragon_realm.*_c2s", obj)`，重用 client 自身的 protobuf 序列化與 s2c 解析，**不需解 protobuf**。決策邏輯在 Python 重寫（純函式、可單測），狀態與 IO 留在 client.py。

## 4. 資料流

```
service loop:
  state = client.read_state()          # page.evaluate 讀 window.__dr_state
  action = planner.decide(state, cfg)  # 純函式
  if action.kind == "stop": break
  client.dispatch(action)              # page.evaluate netManager.send(...)
  client.wait_for_next_update(prev_ts) # 輪詢 window.__dr_state 時間戳前進
```

- **狀態推進**：每個 c2s 後 client 會回 s2c 並更新 info；listener 寫入 `window.__dr_state` 並蓋上 client 端時間戳。service 輪詢時間戳變化推進，避免 race。
- **config**：`TIER_TWO_REQUIRE / TIER_THREE_REQUIRE / STAMINA_TIER / BACK_KILL / CHEST_KEY` 在進場時讀一次（靜態），快取於 service。

## 5. 錯誤處理

- RPC 逾時 / session 離線：`save_error_screenshot` + 中止本輪（**不重試風暴**）。
- 非主頁 / 活動未開啟（ActivityState != Open）：直接跳過本輪，不視為錯誤。
- 卡住（求助後無人回應且冷卻未到）：靠 **wall-clock 預算** 與 **dead-loop 偵測** 收尾，不無限等待。
- 全程綁 `pause_guard`，live-view 手動接管可立即中斷。

## 6. 測試 (TDD，先寫 fail)

| 層 | 測什麼 | 如何 |
|----|--------|------|
| `state.py` | raw dict → DragonState 解析（各欄位、缺欄位 default） | 純 fixture，不碰 Playwright |
| `planner.py` | 決策樹每分支 + 我方覆寫：①陷阱 IsChallenge 只回 choice(3) 永不 choice(1) ②ceng==2 達 TIER_THREE 回 stop 不 enter_ceng ③體力不足回 stop ④協助隊友 ⑤寶箱有/無鑰匙 ⑥再次擊殺冷卻 | 純 fixture，AAA，一分支一測 |
| `service.py` | 迴圈推進、stop 終止、dead-loop 中止、逾時中止 | fake client（無 Playwright） |
| live | H5 真帳號跑一輪到第 2 層達標 | manual-hold 獨佔一台（勿挑正在跑的裝置） |

測試命名描述行為（`test_trap_challenge_always_asks_help_never_struggles`）。

## 7. 待 live 驗證的未知 (writing-plans / 實作期解決)

1. `dragon_realm_info_s2c` 確切欄位名與巢狀結構（`info.ceng / event_id / event_uid / help_hp`、當前事件 `data` 陣列、`eventList` 元素 shape）。
2. `getValue(EventDataKey)` 如何從 `data` 陣列取值（k/v 配對，已知 key 數字）。
3. `page.evaluate` 取得 `netManager` 與 `ActivityLhsyDataCache` 單例的途徑（參考 cocos-app-analysis / web_game_api 既有手法）。
4. `ActivityType.ActivityLhsy` 的 activity id 與進場導航（開活動頁 → 進龍骸聖域）。
5. config 表 `configDragon_map_kv` 的 cocos 讀取法（參考 configFly 列舉）。
6. ADB 後端流程（v2 範圍外，先記 backlog）。
7. **`event_list` 是否包含「當前 active 事件」**：planner 的「再次擊殺(列表)」路徑優先於當前事件處理；若 `event_list` 會含當前的挑戰事件且其 `back_kill_time <= server_time - BACK_KILL`，該路徑可能蓋過陷阱/怪物的 ASK_HELP 覆寫（送出 `choice(1)` 前進而非求助）。我方為**忠實 port 客戶端**（客戶端同樣無 `back_kill_time>0` 守衛），故不臆測修改；須於 live recon 確認 `event_list` 是否排除 active 事件，並在 Task 8 驗證陷阱/怪物挑戰確實只送 ASK_HELP。

## 8. 範圍外 (YAGNI)

- ADB 後端（之後再補）。
- 組隊 / 快速加入 / 隊伍管理（假設已在隊伍）。
- 第 3 層探索、體力道具自動使用、付費 auto 解鎖。
- PVP 影片 / 排行榜 / 寶箱商店。

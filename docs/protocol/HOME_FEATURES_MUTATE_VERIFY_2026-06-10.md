# ws_token 家園功能批次 — MUTATE LIVE 驗證 + 修復報告 (2026-06-10)

> 延續 `HOME_FEATURES_RECON.md` / `MARRY_RECON.md` 的 read 驗證。本份是**三模組 mutate 路徑的 live 驗證 + 過程修掉的 bug**。
> 程式在 worktree `C:\Users\Eric\ws-token-home`(branch `feat/ws-token-home`),**未 merge**。
> 對應交接:`tasks/ws_token_home_todo.md`、memory `project_ws_token_home`。

## 驗證條件

- 帳號(使用者授權「資源可再生、直接驗證、至少跑兩次」):
  - 小寶 `7fe98fc6`(web_h5,CDP 9226)、`emulator-5554`、`emulator-5556`、`emulator-5560`
  - 四台 WS 登入全 `code=0`、`kicked=False`(分兩組各 2 帳號跑、帳號不重疊,避免同帳號異地互踢)
  - ADB 無裝置連線 → 無法 refresh ADB ticket;但既有 ticket 仍有效(WS 直連伺服器)
- 方法:
  - 整合探針 `tools/_verify_home_live.py`(單帳號單次登入跑 spirit/couple/workshop,flag 控 mutate)
  - CDP 讀小寶 live client config:`Get-Content x.js -Raw | python tools/_auth_capture_probe.py 9226`(read-only 不踢)
  - grep 遊戲源碼 `docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js` 取權威 cmd/欄位/食譜/禮物清單
- 測試(全部親自跑,綠):`runner 34 / couple 24 / workshop 29 / spirit 17 / main_tasks 25`

## 結果總表

| 模組 | 功能 | 結果 |
|------|------|------|
| 守護靈 | 免費抽 `draw_all_free` | ✅ 可用(5554 真抽 2 次) |
| 守護靈 | 買招喚貨幣 `buy_summon_currency` | ⚠ 路徑存疑,**未執行花費**(item 800003 不存在) |
| 伴侶 | 送禮 奶茶 1106 / 玫瑰 1614 | ✅ 可用,**修 bug FLOWER 1031→1614** |
| 伴侶 | 戒指錘鍊 `ring_levup(type=1)` / `forge_until_empty` | ✅ 可用(四帳號;空石 graceful 停) |
| 伴侶 | **默契考驗** | ✅ **解開誤標 + 實作 + LIVE 真領到** |
| 加工坊 | `read_info` / `dining_hall` | ✅ 四帳號解析正確 |
| 加工坊 | `workshop_id` 語意 / `switch_recipe` | ✅ **修 bug(=configWorkshop.id,非 team_cfg_id)**;choose 成功路徑受帳號狀態阻擋 |

---

## 守護靈 spirit

### 免費抽 ✅
- `emulator-5554` `free_times=2` → `draw_all_free` 真抽 2 次,`rewards={81003:1, 81004:1}`。
- `count>1` 會 0x0201 code 2 → 模組對每池逐次 `count=1` 單抽(已正確)。
- 其餘三台今日 `free_times=0`(已抽完)。

### 買招喚貨幣 ⚠(未執行)
- `buy_summon_currency` 用 `shop_buy 6914 {shop_type, shop_id, num}`,但假設的 item **`800003` 在整個 client 源碼 + `configMall`(1502 筆掃描)都不存在** → handoff 假設錯誤。
- 源碼 `pay_mall_tab:[[0,"召喚"],...]` 顯示真正召喚走「**召喚**」付費商城(鑽石 gacha),非 generic `shop_buy`。
- **不用瞎掰 shop_id 觸發花費**;已於 `spirit.py::buy_summon_currency` docstring 標 UNCONFIRMED 警告。

---

## 伴侶 couple

### 送禮 ✅ + 修 bug(FLOWER 1031 → 1614)
- 奶茶 `1106` ✅(5554 + 小寶 ok);玫瑰 `1614` ✅(小寶 + 5560 ok)。
- **原 `couple.FLOWER = 1031` 是錯的**:送 1031 一律回 `0x0201 code 2`(請求的參數不合法,**非** code 3 物品不足 → 不是沒庫存,是 id 錯)。
- 源碼 `MarrySendFlowerView` 定義 `var m=[1106,1614]` → 合法贈禮只有 **奶茶 1106 / 玫瑰 1614**;1031/春日鮮花 1118 都不是。**已改 `FLOWER` 1031→1614**。
- `code 3 物品不足` = 帳號沒該禮物庫存(5556 奶茶+玫瑰、5560 奶茶),graceful,正確。

### 戒指錘鍊 ✅
- `ring_levup(type=1)` 四帳號全 `ok=True`(5554 `old_lev 215→216`、消耗真愛之石 1114)→ **type=1 確認正確**。
- `forge_ring_until_empty` 在 5556 實證:石頭耗盡時回 `0x0201 code 3`,loop graceful 停(`stopped_reason=error_code=3`)。

### 🔑 默契考驗 — 解開誤標 + 實作 + LIVE 真領到
原 recon 把「默契考驗」標成 `favor_reward_fetch`,**是錯的**。逐層查證:

1. `favor_reward_fetch`(15142)在遊戲裡**只被 `MarryIntimacyView` 呼叫**,用 `favorcfgs[t].level`,而 `favorcfgs` **只在未婚(level_group<3)建立** → 已婚帳號一律 `0x0201 code 2`(graceful,不 crash)。**這是未婚好友好感里程碑,不是默契考驗**。
2. `marry_mark_info`(15131)= **結婚週年倒數**(唯讀 `{start_time, next_reward_time}`,`next-start≈364 天`,到期自動發),非每週、無領取 cmd。已加 `read_mark_info`。
3. CDP 查 langId:**「默契考驗」= 6603 / 4220**,且 `25400 = 「結婚後可進行默契考驗」`;對應 in-game **`MarryFavorWeekTaskView`(好感週任務)**,該 view import `TaskControl/TaskDataCache` → **走任務系統**。
4. TaskType enum:**`Marry = 6`**。任務領取 = `task_commit{type#1, task_id#2}`(2562)。

**結論:默契考驗(週領)= `TaskType.Marry`(type 6)好感週任務,用 `task_commit{type:6, task_id}` 領。**

**LIVE 領取成功(≥2 帳號)**:

```
小寶 7fe98fc6: type6(Marry) total=7 claimable=3: [(5,36),(2,100),(7,3)]
  commit type=6 task_id=5 -> OK
  commit type=6 task_id=2 -> OK
  commit type=6 task_id=7 -> OK
  re-read: claimable=0   ← 真的領到

emulator-5554: type6(Marry) total=7 claimable=2: [(2,105),(7,3)]
  commit type=6 task_id=2 -> OK
  commit type=6 task_id=7 -> OK
  re-read: claimable=0
```

**實作**:
- `main_tasks.py`:加 `TYPE_MARRY = 6`(+ 補齊 TaskType enum 常量)、`claim_marry_tasks()`(由共用 `_claim_tasks_of_type` 委派,`claim_daily_tasks` 同源)、+ 2 個 unit test。
- `runner.py`:`_run_main_tasks` 加 `claim_marry_tasks`(每日 orchestrator 自動領,免費)。
- `couple.py` docstring:三條路徑(Marry 週任務 / 未婚 favor_reward / 結婚週年 marry_mark)釐清。

---

## 加工坊 workshop

### workshop_id 語意確認 + 修 bug
- CDP 讀 `configWorkshop`(3 列)+ 源碼 `send_worker_pw_choose_food_c2s(food_list, workshop_id)` + `configWorkshop.getDataByKey(workShopId).team_id`:

| configWorkshop.id (=wire workshop_id) | team_id (=p_worker.team_cfg_id) | name |
|------|------|------|
| **1** | 6001 | 手動加工(manual,不吃 team choose_food) |
| **2** | 6002 | 小隊加工 |
| **3** | 6003 | 小隊加工 |

- **`workshop_id` = `configWorkshop.id`(1/2/3),不是 `team_cfg_id`(6001/6002)** → 原 `switch_recipe` 誤把 team_cfg_id 當 workshop_id = bug(live 傳 6001/1/0 全 `code 2`)。
- **已修**:`TEAM_TO_WORKSHOP_ID = {6001:1, 6002:2, 6003:3}` + `team_cfg_id_to_workshop_id()` + `Workshop.workshop_id` property;`switch_recipe` 改吃 `team_cfg_id` 內部轉成 wire id。29 unit test(含 cancel/choose body wire 斷言)綠。
- 食譜在 `configFood`(非 configGoods):`8001 脆脆餅乾 approach[[6017,2]]`、`8003 活力精華 approach[]`、`8005 精英拼盤 approach[[6019,2],[6020,2],[6021,2]]`;`choose_food` 需 approach 材料。`food_list` 是單一 `{k,v}`(模組 `pb_msg(1,kv)` 正確)。

### live choose_food 成功路徑受阻
- 四帳號唯一 idle 是**手動加工(id 1)**,而手動加工不吃 `worker_pw_choose_food`(live:id1 + 無材料的 8003 仍 code 2/3);小隊加工(id 2)全 `running`。
- 依使用者明令「別碰 running 生產」→ 無 idle 小隊加工可驗成功路徑。映射正確性已由 unit test 覆蓋,待有 idle 小隊加工時補 live。

---

## 已改檔(worktree,未 merge)

| 檔 | 變更 |
|----|------|
| `ws_token/couple.py` | `FLOWER 1031→1614`;默契三路徑釐清;`read_mark_info`/`MarkInfo`(by Group A) |
| `ws_token/couple_smoke.py` | 標籤 鮮花→玫瑰 |
| `ws_token/spirit.py` | `buy_summon_currency` 800003 警告 |
| `ws_token/workshop.py` | `TEAM_TO_WORKSHOP_ID` + `team_cfg_id_to_workshop_id()` + `Workshop.workshop_id` + `switch_recipe` 修正 |
| `ws_token/main_tasks.py` | `TYPE_MARRY=6` + `claim_marry_tasks` + `_claim_tasks_of_type` 重構 |
| `ws_token/runner.py` | `_run_main_tasks` 接 `claim_marry_tasks` |
| `tests/test_ws_token_{couple,workshop,main_tasks,runner}.py` | 對應測試 |
| `tools/_verify_home_live.py`、`tools/_a_*.py`、`tools/_cdp_*.js` | 探針(gitignored) |

## 仍待(使用者審)

1. merge worktree → 接 `new_main_v2` 排程。
2. 招喚貨幣付費抽:需另解「召喚」pay-mall 的 `shop_type`/`shop_id`。
3. 加工坊 `choose_food` 成功路徑:待有 idle 小隊加工(或同意動 running)時補 live。

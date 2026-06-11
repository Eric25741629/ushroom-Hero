# ws_token 家園功能批次 — 狀態 / 交接 (2026-06-09)

> 這份是 **家園功能批次** 的權威交接。延續主後端交接 `tasks/ws_token_backend_todo.md`。
> recon 全文在 worktree:`docs/protocol/HOME_FEATURES_RECON.md` + `docs/protocol/MARRY_RECON.md`。
> memory:`project_ws_token_home`(英文)。

## ⚠ 程式在哪 / 下個 session 怎麼接
- **全部在 worktree `C:\Users\Eric\ws-token-home`(branch `feat/ws-token-home`,off feat/ws-token-integration)。**
- 主 checkout 在 `feat/dragon-realm`,**沒有**這些 commit。動 ws_token 家園功能一律進那個 worktree。
- **未 merge、未接 runner / new_main_v2、未重啟 bot。** production 沒受影響。
- 創 worktree 後 `auth_state/*.json`(gitignored)要從主 checkout 複製一份過去才能 live 跑;
  `tools/_login_poc.py` 同理(client 回歸測要)。

## ✅ 三模組全建好 + read 路徑全 live 驗(小寶 7fe98fc6,TDD,各自 commit)

| 功能 | 模組 | 關鍵 cmd (module*256+N) | 測 | live 驗 |
|------|------|------|----|------|
| **守護靈**(每日免費抽 2 次 + 買招喚貨幣) | `ws_token/spirit.py` | draw_info **19743** / draw **19744**(module 77) | 17 | ✅ **真抽 2 次免費**(rewards 80002/81004) |
| **加工坊**(小隊加工) | `ws_token/workshop.py` | info **18434** / choose_food **18435** / cancel **18438** / dining_hall **18441**(module 72) | 25 | ✅ 讀:2 工坊(6001 idle/6002 running)+ dining 食物 |
| **比格先生=伴侶** | `ws_token/couple.py` | give_flower **15140** / reward_fetch **15142** / ring_levup **15135** / status 15105 / favor_info 15139(module 59 marry) | 21 | ✅ 讀伴侶+50好感+戒指 lv224;**真送 1 奶茶 ok(369)** |

commit(feat/ws-token-home,新到舊):d33c6b2f lessons / 6df120ac couple 369 / e9a0849a couple page / a6c8e2a6 couple / 1145fb11 marry recon / 17807500 workshop switch / 07db2a3e spirit 單抽 / 9790099c workshop / 518c45a1 spirit / 8250c1c1 recon+schemas。

### 各功能細節
**守護靈**:`spirit_draw_info`→`p_spirit_draw{draw_id,free_times}`(小寶 draw_id=1 free_times=2)。
**免費抽是單抽**(count>1 回 0x0201 code 2 參數不合法)→ `draw_all_free` 對每池抽 free_times 次 count=1。
買招喚貨幣=`shop_buy` 6914(招喚貨幣 item=800003,shop_id 待 CDP 讀 configMall;花費 gated)。

**加工坊**(使用者教的機制):小隊加工一次做一種、**做到材料歸零**;換配方**必先按取消再選**(`switch_recipe`=cancel_work→讀 dining_hall 取數量→choose_food)。配方 food id:**脆脆餅乾=8001 / 精英拼盤=8005**(configGoods)。
choose_food body={food_list:p_key_value{k=food_id,v=數量}, workshop_id}。

**比格先生=伴侶**(使用者澄清「比格先生其實是伴侶系統」):
- 贈禮:`favor_give_flower{friend_id,flower_id,num}` 送 **奶茶=1106 / 鮮花=1031** 給伴侶(99.99% 第一個)。friend_id=`favor_friend_info` 的 `friend_list[0].role_id`(=lover_id)。
- 默契考驗:`favor_reward_fetch{friend_id,favor_lv}`,**每週日領一次**。可領的 favor_lv 從 `favor_reward_info{friend_id}`(15141,c2s 需 friend_id)讀。
- 戒指錘鍊:`marry_ring_levup{type}`,**消耗真愛之石=1114**,`forge_ring_until_empty` loop 到 0x0201(物品不足)即停。

## 🔑 兩個本批新發現(已寫 lessons + memory,務必記住)
1. **0x0201 不是純 error channel,也帶『成功通知碼』**:`favor_give_flower` 成功回 **0x0201 code 369 = 贈送成功**(不是錯誤!)。所以 `call_for(cmd,0x0201)` 收到 0x0201 **要先解 code 再判成敗**,維護 `OK_NOTICE_CODES`(目前 {369})。「失敗走 0x0201」仍對,但「0x0201 一律失敗」是錯的。
2. **CDP fake-cnet 法離線抓 cmd 號**(H5 WS 斷線也能用):暫換 `netManager._cnet={state:2,sendMessage:(cmd,b)=>cap(cmd)}` + 給 `netManager._protoClass[name]` 塞 encode 不丟的 dummy,然後 `netManager.send('<family>.<msg>_c2s',{})` → cap 到的就是 cmd。驗證 home_mine_info=3073。`protoRoot.toJSON().nested`=82 family。工具 `tools/_cdp_cmds.js`。
3. **c2s body 別假設空**:`favor_friend_info_c2s` 需 `{page#1}`(required),空送→無回應 timeout。建 read 前先 dump `_c2s` schema。

## error 碼(configErrorInfo,CDP 解,權威)
2=請求的參數不合法 / 3=物品不足 / 90=冷卻時間未到 / 159=次數不足 / 173=活動已結束 / **369=贈送成功(success notice)**。
解法:`Get-Content x.js | python tools/_auth_capture_probe.py 9226`(UTF-8 要 `$env:PYTHONIOENCODING='utf-8'`),
`configErrorInfo.getDataByKey(code)._data[1]`=langId → `GetStrFromConfig(langId)`。

## ▶ 下一個 session 從這裡開始(優先序)
1. **mutate live 驗(挑安全的)**:守護靈買貨幣(需 shop_id)、伴侶送花(奶茶已驗 ok;**鮮花 1031 回 code 2** = 小寶可能沒鮮花或 id 待確認)、默契考驗領取(週日)、戒指錘鍊(`type` 值待確認,預設 1)。
   **加工坊 switch_recipe 會擾動小寶正在跑的生產(6002 running)+ workshop_id 語意未確認 → 別亂跑,要先確認 workshop_id**。
2. **比格先生「切磋」cmd 未找**:marry family 裡沒明顯的 spar/切磋。切磋增親密度,但 cmd 待 recon(可能在 arena/pvp/friend,或要抓真實封包)。
3. **補 live-confirm 值(CDP 讀 client)**:招喚貨幣 shop_id(configMall)、戒指 ring_levup `type`、加工坊 workshop_id(=team_cfg_id? slot?)、可領的 favor_lv。
4. **接 runner**:把 spirit/workshop/couple 接進 `ws_token/runner.py`(免費的:守護靈免費抽、伴侶送花/默契考驗/戒指錘鍊;加工坊 switch 要 config workshop_id)。沿用 0x0201 容錯。
5. **merge + 排程**留使用者審。

## CDP 探針工具(gitignored,主 checkout tools/)
`_cdp_cmds.js`(fake-cnet 抓 cmd)、`_cdp_err173.js`/`_cdp_lang.js`(error 解碼)、`_cdp_marrycmds.js`/`_cdp_marryc2s.js`(marry)、`_cdp_food.js`(food id)、`_cdp_proto.js`/`_cdp_protoid.js`(family/schema)、`_save_home_schemas.py`(存 schema)。跑法見上方 error 碼段。

---

## ✅ MUTATE LIVE 驗證 + 修復 (2026-06-10, 四帳號 7fe98fc6/5554/5556/5560 全 code=0 互不踢)

> 使用者授權「資源可再生、直接驗證、至少跑兩次」。用整合探針 `tools/_verify_home_live.py`(單帳號單次登入跑三模組)+ CDP 讀小寶 9226 client config + grep 遊戲源碼 `docs/game_client_sources/...index.966f5.js` 取權威值。全部測試 **runner 34 + couple 24 + workshop 29 + spirit 17 + main_tasks 25 綠**(均親自跑)。程式仍在 worktree,未 merge。

### 守護靈 spirit
- **免費抽 draw_all_free ✅ 可用**:5554 真抽 2 次(rewards 81003/81004);count>1 單抽機制正確。其餘三台今日 free_times 已 0。
- **買招喚貨幣 buy_summon_currency ⚠ 路徑存疑、未執行**:item **800003 在整個 client 源碼 + configMall(1502 筆)都不存在** → handoff 假設錯誤。真正召喚走「召喚」付費商城(`pay_mall_tab [[0,"召喚"]]`),非 generic shop_buy 6914。已在 `spirit.py` docstring 標警告,**不用瞎掰 shop_id 觸發花費**。

### 伴侶 couple
- **送禮 ✅ 可用 + 修 BUG**:奶茶 1106 ✓(5554+小寶);**玫瑰 1614 ✓(小寶+5560)**。**`FLOWER` 原本 = 1031 是錯的**(送 1031 一律 0x0201 code 2);源碼 `MarrySendFlowerView var m=[1106,1614]` = 合法禮物只有奶茶/玫瑰。**已改 `couple.FLOWER` 1031→1614**。code 3 = 物品不足(帳號沒庫存),graceful。
- **戒指錘鍊 ✅ 可用**:`ring_levup(type=1)` 四帳號全 ok(5554 lev 215→216,消耗真愛之石);`forge_ring_until_empty` 在 5556 實證石頭耗盡時 graceful 停(code 3)。type=1 確認正確。
- **🔑 默契考驗 = 已解決 + 實作 + LIVE 領取成功**:原 recon 把它誤標成 `favor_reward_fetch`。真相(CDP 查 langId 6603/4220「默契考驗」+ 源碼 `MarryFavorWeekTaskView`):**「默契考驗(週領)」= TaskType.Marry(type 6) 好感週任務,走任務系統,用 `task_commit{type:6, task_id}` 領取**(不是 couple 模組)。
  - **LIVE 領取成功**:小寶 3 個可領 Marry 任務(5/2/7)、5554 2 個(2/7),`task_commit{type:6}` 全 **OK**,re-read claimable=0。
  - **新增 `main_tasks.claim_marry_tasks` + `TYPE_MARRY=6` + 接進 `runner._run_main_tasks`**(每日 orchestrator 自動領,免費)。+ 2 個 unit test。
  - `couple.favor_reward_fetch`(15142)是**未婚**好友好感里程碑(MarryIntimacyView 用 `favorcfgs[].level`,只 level_group<3 建立);已婚帳號回 code 2 **graceful 不 crash**(使用者「至少不會錯」達成)。`marry_mark_info`(15131)是**結婚週年倒數**(~364 天,唯讀自動發),已加 `read_mark_info`。三者已在 `couple.py` docstring 釐清。

### 加工坊 workshop
- **workshop_id 語意確認 + 修 BUG**:CDP+源碼權威 → **`workshop_id` = `configWorkshop.id`(1=手動加工/team6001、2=小隊加工/team6002、3=team6003),不是 `team_cfg_id`(6001/6002)**。原 `switch_recipe` 誤把 team_cfg_id 當 workshop_id = bug → 三個值(6001/1/0)live 全 code 2。**已加 `TEAM_TO_WORKSHOP_ID{6001:1,6002:2,6003:3}` + `team_cfg_id_to_workshop_id()` + `Workshop.workshop_id` property,`switch_recipe` 改吃 team_cfg_id 內部轉換**。
- recipes 在 `configFood`(非 configGoods):8001 脆脆餅乾 approach[[6017,2]]、8003 活力精華 approach[]、8005 精英拼盤 approach[[6019,2],[6020,2],[6021,2]];choose_food 需 approach 材料。手動加工(id1)不吃 choose_food(live code 2/3)。
- **live choose_food 成功路徑受阻**:四帳號唯一 idle 是手動加工(id1),小隊加工(id2)全 running;依使用者明令「別碰 running 生產」→ 無 idle 小隊加工可驗成功路徑。映射正確性由 29 unit test(cancel/choose body wire 斷言)覆蓋。

### 已改檔(worktree feat/ws-token-home,未 merge)
`ws_token/couple.py`、`couple_smoke.py`、`spirit.py`、`workshop.py`、`main_tasks.py`、`runner.py` + tests `test_ws_token_couple.py`、`test_ws_token_workshop.py`、`test_ws_token_main_tasks.py`、`test_ws_token_runner.py`。探針 `tools/_verify_home_live.py`、`_a_*.py`、`_cdp_*.js`(gitignored)。

### 仍待(留使用者審)
- merge worktree → 排程接 new_main_v2。
- 招喚貨幣付費路徑(若要付費抽)需另解「召喚」pay-mall shop_type/shop_id。
- 加工坊 choose_food 成功路徑待有 idle 小隊加工時補驗(或使用者同意動 running)。

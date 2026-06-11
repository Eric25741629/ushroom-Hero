# Finish Summary Log

## 2026-06-09 19:14:16  ·  session 77af77f8

全部驗證通過：JSON 合法、Stop 已接上、既有 py_check / check_pytest 未被破壞。

我把這個新 hook 記進既有的 hooks 設定記憶（避免未來把自動產生的 finish.md 誤當使用者內容或誤刪）。先讀再更新。

---

## 2026-06-09 19:45:29  ·  session 853290f1

Worktree clean, all committed. Live mutate verification done across all 4 accounts.

## Live mutate 驗證結果(你出門期間,四台都用了)

四台都能登入 (code=0)。真的去「領/種/轉/掃」,不只讀 — 抓到並修了 **3 個 crash bug**:

### ✅ 確認可用
- **idle_reward 完全可用**:小寶 claim online{1}(cmd 0x0d06)+ offline{2}(登入 push)**兩個都 success=True**。掛機/離線獎勵這條完整跑通。
- **turntable 可 spin**:小寶實得 slot 5。但有**spin 間冷卻**(`cd` 欄位),下次 spin 回 0x0201。
- **farm plant 可用**:小寶 plant 5 格各記 error code,不再 crash(planted=0 是因為沒備用 seed 102,正常)。

### 🐛 修掉的 3 個 bug(live 才抓得到)
1. **turntable spin** 冷卻時 `call(只等5636)` → timeout 整個 task crash → 改 `call_for(5636,0x0201)` 優雅停(74e3ad23)
2. **farm plant/harvest/work** 被拒時回 0x0201 → 舊 `call` crash → 改 `_farm_action`/call_for 逐格記 code(935a8839)
3. **`home_farm_info`(3077)一個 session 只答一次** → 第二次 read 必 timeout → 改 read 一次重用快照(935a8839)

**系統性根因**:多個模組假設「reply 一定回 success cmd」,但**失敗一律走 0x0201**。已修 turntable/farm(redpack/dungeon 本來就對)。寫進 lessons.md。

### ❌ 卡住、需要 recon(已記在交接)
- **dungeon sweep**:5554 sweep type2 任何 dungeon_id 都回 0x0201 **code 173**。真 dungeon_id 來源未解、深淵是否支援掃蕩未知。
- **🔑 error 173 是轉盤/農場/深淵跨功能的通用拒絕碼** — 解掉它(抓真實客戶端封包 / CDP 讀 error-code config)會一次釐清三個功能。
- farm harvest-of-mature(四台目前 0 成熟地)、farm `start_work`(無 team_cfg_id)、carpark(cross 活動休眠)。

### 已更新
交接 `tasks/ws_token_backend_todo.md`(新增「mutate 驗證結果」+ 改寫下一步)、memory、lessons.md 都同步了。下一步第一順位是**解 error 173**(抓客戶端 sweep 封包或 CDP 讀 error config),會把轉盤冷卻/農場種子/深淵掃蕩三者一次講清楚。要我接著解 173 嗎?

---

## 2026-06-09 21:07:21  ·  session 853290f1

`configJobs` turned out to be the **character classes** (戰士/弓箭手 — the rows carry `zj_ICON_zhanshi` etc.), not 打工 — so the farm worker `team_cfg_id` lives in a different table (a separate dig, not configJobs). But the main blocker is fully solved. Here's what I pulled directly from the client, no asking:

## 我直接 CDP 抓出來了 — error code 全解

連小寶 H5 client(port 9226)讀 `configErrorInfo`,解碼(159=次數不足 對上我們本來就知道的 → 證實這表是對的):

| code | 字義 |
|---|---|
| **173** | **活動已結束** |
| **90** | **冷卻時間未到** |
| **159** | **次數不足** |

### 三個功能一次釐清(173 不是通用拒絕碼,是「活動關了」)
- **轉盤 = 事件輪盤**:活動開時可轉(live 真的轉到 slot 5),轉完 `cd` 設冷卻 → 下次 90;活動關 → 173。**能用,按冷卻節奏轉**。
- **農場 seed 102 = 事件作物**:活動結束 → 173。常態路徑是**打工(免費種)**,不是手動種事件作物。
- **深淵/萬神 掃蕩(3596)→ 173 活動已結束**:掃蕩是限時活動,現在沒開。**日常 深淵/萬神 要走 battle(3591/3592)**(有 anti-cheat),不是 sweep。

### 順手從 `chapterDataCache` 釘到的(都是之前標 live-confirm 的)
- `day_times`(156/748)**確認是累計總數,不是今日剩餘**。
- 深淵每日上限 = **2**(`getLimit(2)` → `[2,2,[1003,1]]`)、**門票 gtid = 1003**(新增 `ABYSS_TICKET_GTID`)。

全部修進 code 註解 + 交接 doc + memory + lessons,已 commit(c4d57b84 解碼 / 82758bd7 lesson)。**CDP 解碼法也寫進 lessons 跟交接**(`tools/_auth_capture_probe.py 9226` + `configErrorInfo.getDataByKey(code)`),以後卡在「某個碼/值是什麼」直接讀 client,不猜不問。

下一個 CDP 可解的是農場打工 `team_cfg_id`(不在 configJobs,要找對的 farm-worker 表 / live worker cache)——我繼續往那挖。

---

## 2026-06-09 22:16:26  ·  session 853290f1

全部完成並 commit。我全程自己用 CDP 抓,沒再問你。

## 先解掉 error 173(你要的)
CDP 讀小寶 client `configErrorInfo`,一次解清:
- **173 = 活動已結束**(不是通用拒絕碼!)→ 深淵掃蕩/農場事件種子是活動沒開
- **90 = 冷卻時間未到**(轉盤 spin 間冷卻)、**159 = 次數不足**、**2 = 參數不合法**、**3 = 物品不足**、**369 = 贈送成功**

## 四個家園功能全建好(新分支 `feat/ws-token-home`,worktree `C:\Users\Eric\ws-token-home`)

| 功能 | cmd | live 驗 |
|---|---|---|
| **守護靈** spirit | draw_info 19743 / draw 19744 | ✅ **真抽到 2 次免費**(rewards 80002/81004);免費抽是單抽 count=1 |
| **加工坊** workshop | info 18434 / choose_food 18435 / cancel 18438 | ✅ 讀到 2 工坊;switch_recipe=先取消再換(你的規則);脆脆餅乾 8001/精英拼盤 8005 |
| **比格先生=伴侶** couple | give_flower 15140 / reward_fetch 15142 / ring_levup 15135 | ✅ 讀到伴侶(大意了沒有閃 favor303)+戒指 lv224;**真送 1 奶茶 ok** |
| 打工 team_cfg_id | = p_worker.team_cfg_id | 解了(不用猜) |

各模組 TDD(spirit 17 / workshop 25 / couple 21 測),read 路徑全 live 驗過,各自 commit。

## 兩個關鍵新發現(寫進 lessons + memory)
1. **0x0201 不是純錯誤通道,也帶成功碼**:贈禮成功回的是 `0x0201 code 369 = 贈送成功`!所以收到 0x0201 要先解 code 再判成敗(我加了 `OK_NOTICE_CODES={369}`)。
2. **CDP fake-cnet 法**:H5 斷線也能離線抓 cmd 號(暫換 `_cnet` + dummy proto class,`send()` 的第一參數就是 cmd),驗證 home_mine_info=3073。以後不用猜 cmd。

## 待處理 / 沒做的
- **mutate 多數沒 live 跑**:加工坊 switch 會擾動小寶正在跑的生產(不敢亂動)、戒指錘鍊 `type` 值待確認、買招喚貨幣會花費 → 留監督跑。
- **鮮花 1031** 送出回 code 2(小寶可能沒鮮花或 id 待確認;奶茶機制已證明對)。
- **比格先生「切磋」** cmd 還沒找到(marry family 裡沒明顯的)。
- 接 runner / new_main_v2 留你審。

要我接著:(a) 跑剩下的 mutate 驗證(會動小寶的料/生產)、(b) 找切磋 cmd、還是 (c) 把這批接進 runner?

---

## 2026-06-09 22:34:47  ·  session 853290f1

已寫好交接:**`tasks/ws_token_home_todo.md`**(主 checkout,下個 session 開主目錄就看得到;memory `project_ws_token_home` 也指向它)。

內容:
- **程式在哪**:worktree `C:\Users\Eric\ws-token-home`(branch `feat/ws-token-home`),未 merge/未接 bot;auth_state 要從主 checkout 複製。
- **三模組表**(守護靈/加工坊/比格先生=伴侶)含每個 cmd 號、測試數、live 驗狀態 + 10 個 commit hash。
- **各功能細節**:守護靈單抽機制、加工坊「先取消再換」+ 配方 food id(脆脆餅乾 8001/精英拼盤 8005)、伴侶贈禮/默契考驗/戒指錘鍊 + goods id(奶茶1106/鮮花1031/真愛之石1114)。
- **🔑 兩個新發現**:0x0201 也帶成功碼(369=贈送成功)、CDP fake-cnet 離線抓 cmd 法。
- **error 碼表**(2/3/90/159/173/369)+ CDP 解碼指令。
- **▶ 下一個 session 優先序**:mutate live 驗(挑安全的;加工坊 switch 會擾動小寶生產要小心)、找「切磋」cmd、補 live-confirm 值(shop_id/ring type/workshop_id)、接 runner。
- **CDP 探針工具清單**。

下個 session 直接讀這份就能接。

---

## 2026-06-09 23:25:41  ·  session 317c14a3

A 組代理已在背景接手 marry_mark 調查,完成會通知我。趁這空檔給你目前的確認結果(全部都是我親自 live 跑過 + 自己跑測試驗證,非盡信代理)。

## 目前驗證結果(四帳號:小寶7fe98fc6 / 5554 / 5556 / 5560 全 code=0 登入,互不踢)

| 功能 | 結果 | 證據 |
|------|------|------|
| **守護靈 免費抽** | ✅ 可用 | 5554 真抽 2 次,rewards 81003/81004;count>1 單抽機制正確 |
| **伴侶 送奶茶 1106** | ✅ 可用 | 5554 + 小寶 ok=True |
| **伴侶 送花 1614(玫瑰)** | ✅ 可用 | 小寶 + 5560 ok=True。**抓到 bug:模組原本 `FLOWER=1031` 是錯的**(送 1031 一律 code2);源碼 `MarrySendFlowerView m=[1106,1614]` 證實合法禮物只有奶茶1106+玫瑰1614。已修 |
| **戒指錘鍊 type=1** | ✅ 可用 | 全 4 帳號 ok;5554 lev 215→216;真愛之石耗盡時 loop graceful 停(code3) |
| **加工坊 workshop_id** | ✅ 語意確認 + 修 bug | 權威確認 **workshop_id=configWorkshop.id(1/2/3),不是 team_cfg_id(6001/6002)**。原 `switch_recipe` 誤用 team_cfg_id = bug,已修(加映射 helper)。四帳號唯一 idle 是「手動加工」(不吃 choose_food),小隊加工全 running → **不碰 running 的前提下,choose 成功路徑無法 live 驗**(已據實記錄) |
| **守護靈 買招喚貨幣** | ⚠ 路徑存疑,未花費 | **item 800003 在整個 client 源碼+configMall(1502筆)都不存在** → handoff 假設錯誤;真正召喚走「召喚」付費商城。已文件化,**不用瞎掰參數觸發花費** |
| **默契考驗** | 🔄 背景驗證中 | favor_reward_fetch 只對**未婚**帳號有效;四帳號全已婚 → 一律 graceful code2(不 crash)。已婚每週領走 `marry_mark_info`,A 組代理正在實作+live 驗 |

**程式變更**:`couple.py`(FLOWER 1031→1614)、`spirit.py`(800003 警告)、`workshop.py`(workshop_id 映射 + switch_recipe 修正),`couple_smoke.py` 標籤。**67 測全綠(我親自跑)**。全在 worktree `C:\Users\Eric\ws-token-home`,未 merge。

等 marry_mark 代理回報後,我會把默契考驗結論補上,並彙整進 `tasks/ws_token_home_todo.md` + memory。

---

## 2026-06-10 00:35:05  ·  session 317c14a3

Runner 34 passed. 全部測試確認綠(runner34 + couple24 + workshop29 + spirit17 + main_tasks25 = 129)。更新 MEMORY.md 索引行反映關鍵修正,然後完成。

---

## 2026-06-10 00:48:28  ·  session 317c14a3

完整驗證報告已寫入:

**`C:\Users\Eric\ws-token-home\docs\protocol\HOME_FEATURES_MUTATE_VERIFY_2026-06-10.md`**(跟 `HOME_FEATURES_RECON.md` 同處,隨 branch merge)

內容涵蓋:驗證條件(四帳號/方法/測試 129 綠)、結果總表、三模組逐項證據、3 個修掉的 bug(FLOWER 1031→1614、workshop_id=configWorkshop.id、默契考驗誤標)、默契考驗 live 領取的完整指令輸出、已改檔清單、仍待事項。

另外這次也已同步寫入:
- `tasks/ws_token_home_todo.md` — 追加「MUTATE LIVE 驗證 + 修復」review 段
- memory `project_ws_token_home.md` + `MEMORY.md` 索引 — 修正錯誤舊事實(FLOWER/workshop_id/默契)並記錄驗證結果

---

## 2026-06-10 03:50:24  ·  session 20cac720

## 方案比較

需求總結：每天送光奶茶+玫瑰；ws_token 接入 new_main_v2 成為裝置的執行形式；同輪序列化（WS 先、Playwright 補跑未蓋任務）；pilot = 小寶 7fe98fc6。

### A. WS-first 階段嵌入現有 web_h5 裝置 thread（建議）

裝置仍是 `backend: "web_h5"`，新增裝置級設定 `"ws_token": {"enabled": true, ...}`。裝置 thread 每輪喚醒時：

1. **WS 階段**：in-thread 跑 `ws_token.runner.run_device()`（含新接的守護靈/加工坊/伴侶送禮），跑完斷線。
2. **Playwright 階段**：照舊開瀏覽器，但任務迴圈拿著 WS 階段的 `RunReport` 推導出 skip-set，跳過 WS 已成功的任務（農場/寶箱/家族/轉盤/神燈/管家…），只跑挖礦/武道會/航海/戰鬥類/商店購買。
3. **Ticket 自然刷新**：Playwright 階段本來就會開遊戲頁，順手把新 ticket 寫回 `_auth_capture_7fe98fc6.json`，給下一輪 WS 階段用；WS 登入失敗時 fallback 跑 CDP probe 主動刷。

- 優點：排程/暫停/儀表板/wake 機制全部沿用；改動集中在「任務管線入口 + skip 邏輯」；WS 階段任一任務失敗自動落回 Playwright（skip-set 只含成功項）——**天然的降級路徑**。
- 缺點：要動 `new_main_v2` 任務管線（你的慢改規則區，本設計流程就是在補這個手續）。

### B. 真正的第三 backend 類型（`backend: "ws_token"` + 新裝置類）

新增 WS 裝置類與獨立狀態機，Playwright 補跑變成它的子階段。概念上乾淨，但 wake/pause/dashboard/worker-sync 每條路都要新開，重複造一套排程整合，風險與工作量都是 A 的數倍，pilot 階段不值得。

### C. 外部獨立 runner process

已被你否決（選了第三 backend），不展開。

**建議 A。**「第三 backend」的精神（任務走 WS）由 A 實現，但複用現有 thread 機殼，是最小衝擊路徑。

---

### 送禮數量（送光全部）的實作策略

WS 讀不到庫存，所以「送光」靠 server 行為，兩條路擇一，**用一次 live 測試定案**（在小寶上送大數 `num=999`，反正目標就是送光，怎樣都不虧）：

- 若 server **自動封頂**到庫存 → 一發 `give_flower(num=999)` 解決（最優雅）。
- 若 server **拒絕超量**（code 3）→ 逐顆送直到 code 3 物品不足，設上限 60 次護欄（10 杯約 2 秒）。

兩種結果都寫成 `couple.give_all_in_hand()`，runner 不用知道細節。

這個方向 OK 嗎？OK 的話我就分節呈現完整設計（含分支合併順序、config 形狀、skip-set 對照表、錯誤處理、測試）。

---

## 2026-06-10 03:58:01  ·  session 20cac720

Glob 在這台機器對子目錄 pattern 不可靠，改用 Grep 列檔案：

---

## 2026-06-10 04:01:46  ·  session 20cac720

事實核清楚了，第三個 subagent 的衝突分析是錯的——實際上 **feat/ws-token-home 已是 superset**：它包含 integration 的 runner 全部內容（且已接 claim_marry_tasks），再加家園三件模組。合併負擔趨近於零。設計分節如下：

---

## 設計：ws_token 接入主 workflow（pilot：小寶 7fe98fc6）

### §1 分支策略

以 `feat/ws-token-home` 為基準開新分支 `feat/ws-backend`（實作時先用 `git merge-base` 確認 integration 沒有 home 缺的 commit，有就先補 merge）。主 repo 的修改（new_main_v2 / daily_pipeline / config_manager）也在這條分支上做，最後一次 merge 回 main。

### §2 ws_token 端新增

**couple 送禮（你指定的批次法）**：新函式 `couple.give_all_in_hand(client, friend_id, flower_id, *, batch=10)`——以 10 為單位連送，收到 `code 3 物品不足` 後改以 1 為單位收尾殘量（<10 的餘數），再次 code 3 即停；護欄 30 批上限、每批 spacing 0.2s。奶茶 1106 與玫瑰 1614 各跑一次。`369=贈送成功` 通道已處理。

**runner 接三個新任務**，`TASK_ORDER` 變為 `... "carpark", "spirit", "workshop", "couple", "lamp"`：
- `spirit`：`draw_all_free()` 免費召喚（不買召喚貨幣——800003 已證實不存在）
- `workshop`：照 mutate 驗證文件的已驗流程封裝 `workshop.auto_cycle()`（收成品 → 用 configWorkshop.id 指派食物開工）
- `couple`：無伴侶 skip；有 → 奶茶+玫瑰送光；戒指錘鍊（消耗全部真愛之石）掛 `spend` 閘門

### §3 主 repo 接入（你的慢改區，最小衝擊）

- `bot_config.json` 裝置級新欄位：
  ```json
  "ws_token": {"enabled": true, "spend": true, "open_lamp": true,
               "farm": {"seed_id": null, "team_cfg_id": null},
               "dungeon_sweeps": [], "carpark_target": null,
               "couple_gifts": true, "forge_ring": false}
  ```
- `new_main_v2.py`：喚醒後、**Playwright 瀏覽器啟動之前**插入 WS 階段（順序很關鍵——WS 登入會踢頁面，所以必須先 WS 後開瀏覽器）。整段包 try/except：WS 階段任何失敗 → 空 skip-set → Playwright 全跑（**天然降級**）。
- `daily_pipeline.DailyContext` 加 `ws_done: frozenset[str]`，任務迴圈逐項查表跳過。

**Skip 對照表（保守原則：只跳語意完全等價的；不確定的不跳，免費 claim 本來就冪等）**：

| WS 任務成功 | Playwright 跳過 |
|---|---|
| redpack | #0 紅包檢查 |
| farm | #2 農場任務 |
| idle_reward | #3 點擊寶箱 |
| guild | #4 家族任務 |
| spirit | #5 領取守護靈 |
| main_tasks | #12 所有日常任務 |
| dungeon(有配 sweep) | #15 萬神試煉 |
| lamp | #19 開神燈 |
| turntable | #20 轉盤金幣 |

**刻意不跳**：商店購買（Store≠管家代購）、好友每日禮物（≠伴侶送禮）、停車調和（WS 只停跨界車，Playwright 還管一般位）、挖礦/武道會/航海/戰鬥類（WS 沒做）。

### §4 Ticket 生命週期（自癒迴圈）

ticket 實測 ≥6h 可用、時間欄位 server 不驗。小寶每 2h 喚醒，所以：**每輪 Playwright 階段遊戲載入完成後，順手用 CDP 讀 `LoginDataCache`（同 `_auth_capture_probe` 邏輯，~2 秒、不踢 session）把新 ticket 寫回 `auth_state/_auth_capture_7fe98fc6.json`**，給下一輪 WS 階段用。WS 登入失敗（`WSLoginError`）→ 本輪 WS 空轉、Playwright 全跑並刷新 ticket → 下輪自動恢復。

### §5 測試

- ws_token：`give_all_in_hand` fake-client 測試（10→code3→1→code3 邊界、開局即 code3、369 成功、護欄）；runner 三新任務 wiring 測試。
- 主 repo：skip-set 生效測試（stub pipeline）、WS 失敗→全跑測試、config 預設值測試。
- Live（小寶）：① 先單獨 live 驗批次送禮 ② `python -m ws_token.runner --device 7fe98fc6` 全任務 ③ 主 repo 完整 wake cycle 看兩階段 log ④ 觀察數日再擴。

### §6 明確不做（YAGNI）

不做 `backend:"ws_token"` 新裝置類、不動 ADB 裝置、不開 WS 自動挖礦、不做 dashboard 新 UI（先靠 log）。

---

每一節都 OK 嗎？有要改的指出節號即可；都 OK 我就寫設計文件 + commit，然後請你過目。

---

## 2026-06-10 04:06:54  ·  session 20cac720

Shell 工具的安全分類器暫時不可用，commit 先欠著（檔案已落地，下次 shell 恢復就補 commit）。

**Spec 已寫好**：`C:\Users\Eric\ws-token-home\docs\superpowers\specs\2026-06-10-ws-token-workflow-integration-design.md`

重點回顧：
- §2 送禮 `give_all_in_hand`：10 一批送到 code 3，殘量 1 顆收尾，30 批護欄
- §2.3 online_guard 前置：小寶在線就整輪禮讓（checker 預設 5554，可設定）
- §3.3 skip 表已含你確認的兩組等價（商店購買==管家代購、好友每日禮物==伴侶送禮）
- §4 ADB 模式：lazy refresh（能登就用，登不上且在線才冷啟重撈）+ 常駐 thread
- §8 四個 live-confirm 開放問題（超量行為、workshop 食物策略、ticket 絕對 TTL、checker 與小寶的好友/家族關係）

另外 `tasks/lessons.md` 補了三條本次修正模式（批次法不取巧、任務等價表要問你、手機帳號先查在線）。

請過目 spec，有要改的告訴我；核可後我就進 writing-plans 寫實作計畫（會拆成 ws_token 端 → 主 repo 端 → live 驗證三段）。

---

## 2026-06-10 04:27:31  ·  session 20cac720

計畫已 commit（`67b5d0f3`）。更新記憶後交棒：

---

## 2026-06-10 04:58:12  ·  session 20cac720

71 passed 全綠。逐 task commit：

---

## 2026-06-10 05:02:49  ·  session 20cac720

等測試結果回來後 commit Batch B，接著 Batch C 與 dashboard 併發開工。

---

## 2026-06-10 05:08:26  ·  session 20cac720

卡片顯示補完中（背景）。完成後我會審查 Batch C+D 全部未 commit 變更、commit，然後進 live 驗證。

---

## 2026-06-10 05:13:30  ·  session 20cac720

Runner live 在背景跑（含全部 14 任務 + 新三件），完成會通知。結果出來後：成功 → 核對 couple/spirit/workshop 摘要；登入失敗 → 記 TTL 數據點並安排 ticket 刷新路徑。

---

## 2026-06-10 05:17:04  ·  session 20cac720

文件同步完成（CLAUDE.md 模組表、AUTH_HANDSHAKE_SPEC TTL 數據點、backend todo 進度段）。等 workshop idle-cancel 修復回報後一起 commit，再跑最終整體 review。

---

## 2026-06-10 05:22:02  ·  session 20cac720

Workshop live 核驗在背景跑（順便重試 farm 3077 看是不是間歇性）。結果回來後跑最終整體 review 收尾。

---

## 2026-06-10 05:23:44  ·  session 20cac720

Best-effort 修復進行中。完成後我會：live 重驗 workshop（看 choose 是否能動）+ farm 3077 重試 → commit → 最終整體 review → 給你完整收尾報告（含合併到 main 與重啟 bot/中控的步驟）。

---

## 2026-06-10 05:31:51  ·  session 20cac720

82 passed。Live 重驗 workshop + farm：

---

## 2026-06-10 05:46:06  ·  session 20cac720

全套合跑進行中，綠了就 commit 最後修正並給你完整收尾報告。

---

## 2026-06-10 05:50:08  ·  session 20cac720

## 收尾報告

**`feat/ws-backend` 完成：17 commits、193 個焦點測試全綠、working tree 乾淨、live 驗證通過。**

### 交付內容

| 區塊 | 內容 |
|---|---|
| WS 任務 | `couple.give_all_in_hand`（批 20 封頂送光）、`workshop` 12h 兩配方輪換（best-effort + `ws_state/` cadence）、`spirit` 免費召喚，全接進 runner |
| 主迴圈 | `new_main_v2` 喚醒後、瀏覽器啟動前跑 WS 階段；`daily_pipeline` 11 處 guard 跳過 WS 成功項；任何失敗自動降級全跑 |
| Ticket 自癒 | Playwright 載入後 `ws_ticket_refresh` 回寫 capture 檔 |
| Dashboard | 「方案」選擇器 `adb` / `adb+ws` / `h5` / `h5+ws` + 卡片 `+WS` 標記 |
| Pilot | 小寶 7fe98fc6 已開（spend + lamp on） |

### Live 驗證重點

- **29.5h 舊 ticket 登入成功**——TTL ≥29.5h（已記進 AUTH_HANDSHAKE_SPEC，production 會持續 log ticket age）
- 12/14 任務 OK：紅包 222 金、掛機雙領、轉盤、守護靈抽 2、管家代購、神燈開 20 賣 20、couple 正確（庫存 0 優雅結束）
- Live 抓到並修掉 2 個 bug：workshop cancel 對伺服器靜默（`worker_status` 不是加工訊號，真實狀態在未解的 `pw_worker_info#7`，整段改 best-effort）；跨檔測試 stub 汙染
- farm 3077 timeout 是間歇性（重試正常解析 6 塊地）

### 上線需要你做的（merge 是你的決定，我沒動 production）

1. `feat/ws-backend` merge 回 main（或先讓我發 PR）
2. 重啟 `new_main_v2.py` 與中控（兩個都有改）
3. 等小寶下一個 odd-hour 喚醒看 `logs/7fe98fc6/main.log` 的「WS 階段完成 → 跳過」序列

### 觀察期待辦（不擋上線）

- `_CAPTURE_JS` 欄位名待小寶瀏覽器在線時用 probe 驗一次（驗壞了 ticket 回寫只會 log warning，不影響其他功能）
- `ws_token.farm.seed_id` 填了才會 skip 農場任務（目前 Playwright 照跑，零風險）
- 加工坊輪換等帳號食堂有食物時自然生效；`pw_worker_info#7` blob 解碼排進下次 live recon
- **ADB 模式（adb+ws 離線降級、常駐 thread）= spec §4 phase 2**，dashboard 選項已就位，pilot 穩定後我再開下一份實作計畫

---

## 2026-06-10 05:56:31  ·  session 20cac720

已合併完成：**main 已 fast-forward 到 `4ff58132`**（= feat/ws-backend 全部 17 commits + 之前 ws-token 系列共 91 commits，零衝突）。

兩件事提醒：

1. **生產 bot 還沒吃到**——主 checkout（`C:\nas同步_project\菇勇者全自動掛機`）目前在 `feat/dragon-realm` 且有未 commit 的變更，我不動它。要上線時你在主 checkout 做：
   ```
   git merge main        # 把 ws backend 帶進 dragon-realm 分支（或直接 checkout main）
   ```
   然後重啟 `new_main_v2.py` 和中控（dashboard 方案選擇器、`/api/status` 的 +WS 都要重啟才生效）。

2. **main 領先 origin/main 93 commits**（只在本機）——要不要 `git push` 看你的同步習慣，說一聲我就推。

---

## 2026-06-10 06:04:21  ·  session 20cac720

合併完成。`feat/dragon-realm`（你 bot 跑的這個 checkout）現在已含 main 的全部 ws_token workflow——dashboard 方案選擇器、WS-first 階段、家園三件都在了。tracked 檔案全乾淨，剩的 `??` 都是你本來就在用的 gitignore 探針工具，無影響。

**現在要做的：重啟中控**（Flask 不會自動 reload 改過的 `control_panel_app.py` / `dashboard.html`）。重啟後刷新瀏覽器，設定彈窗的 backend 下拉就會變成四個選項：`adb` / `adb + WS` / `h5` / `h5 + WS`。

提醒一個小落差：dashboard 卡片的 `+WS` 標記要 `/api/status` 帶 `ws_enabled`，這也是同一次中控重啟才生效；在那之前選項是齊的、儲存也正常，只是卡片暫時不顯示 `+WS` 後綴。

要不要我幫你把中控重啟（如果它是這台機器上的某個 process），還是你自己用 `! ` 跑啟動指令？

---

## 2026-06-10 06:07:01  ·  session 20cac720

完成。**main 現在在 `2841fa12`，與 `feat/dragon-realm` 完全一致。**

三條分支現在的關係：
- `main` = `feat/dragon-realm`（同一個 commit，dragon-realm 的內容全進 main 了）
- `feat/ws-backend` 是 main 的祖先（已被吸收）

所有東西都匯到 main 了：ws_token workflow 接入、家園三件、dashboard 方案選擇器、龍骸聖域、以及 dragon-realm 上的全部本地修改。

兩件提醒：
1. **main 領先 origin/main 96 commits**（純本機）。要同步到遠端的話說一聲，我幫你 `git push`。
2. 你的 bot checkout（`C:\nas同步_project\菇勇者全自動掛機`）目前還停在 `feat/dragon-realm` 分支上——內容跟 main 一模一樣，所以照跑沒問題。要不要我幫你切到 `main`（純換 branch 指標，工作目錄不會變動），讓之後都在 main 上面跑？

---

## 2026-06-10 06:08:16  ·  session 20cac720

已切到 `main`（`2841fa12`）。因為 dragon-realm 和 main 指向同一個 commit，工作目錄一個檔案都沒動——bot 不受影響，可繼續跑。

現在 bot checkout 在 main 上，tracked 檔案乾淨。接下來只剩**重啟中控**讓 dashboard 方案選擇器生效。要我幫你重啟，還是你自己跑？

---

## 2026-06-10 06:14:14  ·  session d4e8b6e7

已移除 fly-pet 顯示「階」的兩個地方（`templates/fly_pet.html`）：

1. 卡片 meta 列的「階N」badge（原第 973 行）整個拿掉。
2. 詳情彈窗的「成長 / 階」欄位改成只顯示「成長」。

沒有測試引用「階」，相關 48 個 fly_pet 模板測試全數通過。瀏覽器重新整理頁面即可看到效果（這是 template，不需重啟中控）。

---

## 2026-06-10 16:59:00  ·  session d4e8b6e7

設計如下，請過目：

## 設計：飛寵池設定檔 + 配對詞條約束

### 1. 資料模型與儲存
- 新檔 `config/fly_pet_pools.json`（中控主機，UTF-8），結構：`{ "<device_ip>": [ {id, name, pet_ids: [...]} ] }`，每池上限 **50** 隻，每裝置池數量不設硬上限。
- 新增 Flask API（皆掛 `@_fly_pet_auth`）：
  - `GET /api/fly_pet_pools/<ip>` 列出
  - `POST /api/fly_pet_pools/<ip>` 新增/更新（同 id 覆蓋；驗證 pet_ids ≤50、去重）
  - `DELETE /api/fly_pet_pools/<ip>/<pool_id>` 刪除
- 寫檔用原子寫（temp + replace），讀檔 `utf-8-sig`。

### 2. 挑選流程（列表頁卡片牆）
- 沿用現有勾選機制（搜尋/篩選/按種類全選都可用）。選取動作列新增「**存為飛寵池**」按鈕：
  - 點擊後彈窗：輸入新名稱建立，或下拉選既有池「覆蓋更新成員」。
  - 超過 50 隻直接擋下並提示目前勾選數。

### 3. 繁殖頁：方案整合
- 方案編輯器新增兩個欄位：
  - **飛寵池**：下拉選池（空 = 不限，從全部飛寵挑，現行為不變）。
  - **配對詞條約束**：取代舊「詞條白名單」。每列 = 詞條 + 「至少 1 次 / 至少 2 次」，多列 AND。語意是**兩隻加總**（每隻同詞條只計 1 次，所以上限 2；至少 2 = 兩隻都要有）。
- 繁殖頁新增「飛寵池管理」小區塊：列出各池（名稱 + 隻數），可改名、刪除、展開看成員名單；成員編輯仍回列表頁重存。
- 池被刪除時，引用它的方案自動清空 pool 引用（退回不限）。

### 4. `find_pair` 演算法調整（`control_panel_app.py`）
- criteria 新增 `pet_pool_ids`（pet id 白名單，空 = 不限）與 `pair_entry_constraints`（`[{entry_id, min_count}]`）。
- 候選過濾照舊（排除鎖定/出戰/冷卻/繁殖中），多加 pool id 過濾。
- 配對從「直接取排序前 2 名」改為**配對搜尋**：取排序後前 30 名候選，枚舉兩兩組合（≤435 對），找第一組滿足全部約束的（枚舉順序即現有排序優先序）。無約束時行為等同現狀（前兩名）。
- 回傳新增診斷欄位：池內可用隻數、因約束被刷掉等，方便 UI 顯示「為何配不出來」。

### 5. 遷移相容
- 舊方案的 `entry_whitelist`（兩隻都要有全部詞條）自動轉成 `pair_entry_constraints` 每條 `min_count=2`，語意完全等價，一次性轉換後存回。

### 6. 測試（TDD）
- 先寫測試：pools API CRUD/上限/auth、find_pair 注入 JS 的斷言（pool 過濾、pair 約束、枚舉邏輯）、模板測試（存為飛寵池按鈕、方案編輯器欄位、舊白名單遷移）。

這樣的設計 OK 嗎？還是有想調整的地方（例如池管理放的位置、配不出來時的提示方式）？

---

## 2026-06-10 17:00:39  ·  session 77b44777

找到原因了:這不是程式 bug,而是**檔案沒同步完整**。

`runtime_services/startup_sleep.py` 第 22 行新增了 `from runtime_services.wake_override_service import apply_manual_wake_override`,而 `wake_override_service.py` 是一個**新檔案**(在這個 NAS repo 裡目前還是 untracked、未 commit 的狀態)。

你跑的那台機器(從 traceback 看是 `C:\Users\user\...`,跟我現在這台 `Eric` 不同機器)上的 `C:\python_project\菇勇者全自動掛機` 副本,只同步到了「被修改的舊檔」`startup_sleep.py`,但「新增的檔案」`runtime_services/wake_override_service.py` 還沒同步過去,所以 import 直接炸掉。

解法(擇一):

1. **等同步工具(Syncthing)把新檔案補過去**,或在那台機器上手動確認 `runtime_services\wake_override_service.py` 出現後再啟動。可以順便檢查 Syncthing 是否有同步錯誤/衝突卡住。
2. **手動複製**:把 NAS repo 的 `runtime_services\wake_override_service.py` 複製到那台機器的同路徑。
3. 注意同批未 commit 的新檔案不只這一個,還有 `tests/test_wake_override_service.py`、`tools/adb_token_login.py`、`tools/token_viewer.py` 等。如果那台機器是靠這份程式碼跑 bot,建議等整批變更都同步完(或先把這批變更 commit,讓狀態有明確版本)再啟動,不然可能補了一個檔又缺下一個。

確認方法:在那台機器上跑 `python -c "import runtime_services.wake_override_service"`,沒報錯就能正常啟動 `new_main_v2.py`。

---

## 2026-06-10 17:14:43  ·  session d4e8b6e7

三個版型已放在 `mockups/fly_pet_pools/`，用瀏覽器開來比較即可。三版共用同一套流程（列表頁勾選 → 存為飛寵池；方案上選池 + 設詞條約束），差別在繁殖頁的呈現方式：

**版型 A：行內堆疊式**（`version-a-inline.html`）
方案編輯器和飛寵池管理全部內嵌在繁殖頁現有「方案」區塊，由上往下排，池成員可摺疊展開。不跳窗、一眼全覽，但頁面會變長。實作量最小，最貼近現有程式結構。

**版型 B：雙欄工作區**（`version-b-split.html`）
繁殖頁加一個「配對設定」分頁：左欄是飛寵池卡片（成員頭像預覽 + 顯示被哪些方案引用），右欄是方案編輯器，底部有即時預覽「池內可用幾隻、滿足約束的有幾對」。關係最清楚、回饋最即時，但實作量最大。

**版型 C：彈窗精簡式**（`version-c-modal.html`）
繁殖頁只留一條方案摘要清單（每列用 pill 顯示「池：xxx / 約束 N 條」），編輯走置中彈窗（基本條件 / 飛寵池 / 詞條約束 三段步驟），池管理是另一個彈窗。主頁面最乾淨、跟現有詳情彈窗風格一致，但資訊要點進去才看得到。

三版的詞條約束列都長一樣：詞條名稱 + 「至少 1 次 / 2 次」切換鈕（2 次 = 兩隻都要有）。

你選一版（或混搭，例如 A 的佈局 + B 的即時預覽），我就把設計定稿寫成 spec。

---

## 2026-06-11 03:44:54  ·  session a6ebde58

修復完成，所有測試綠。

## 根因（你看到的卡死畫面，完整因果鏈）

你的手機是接在 worker 電腦（`desktop_ov0asq4`）上的 wifi ADB。人帶手機離開後：

1. **worker 同步迴圈只回報還在 `adb devices` 清單裡的裝置**（`worker_sync_service.py`）→ 手機一斷線，worker 就完全停止回報它，master 儀表板永遠停在最後一筆快照（所以「更新 23:22:35」凍結）。
2. **master 收到回報時根本沒寫入 status 欄位** → remote 裝置在儀表板上永遠是 ONLINE。
3. 倒數計時只看 `next_wake_at` 是否過期 → 過了預計喚醒時間就永遠顯示「喚醒中...」。
4. worker 端的喚醒程式對直連手機有一個**無限重試迴圈**（每 60 秒），既不更新狀態也不理儀表板控制，所以看起來像當掉。

## 修了什麼（門檻照你要求 = 1 小時）

| 層 | 行為 |
|---|---|
| master | scan loop 每 30 秒掃一次：remote 裝置**超過 60 分鐘沒回報 → 卡片直接轉 OFFLINE**（步驟顯示「Worker 超過 X 分鐘未回報，判定離線」），倒數變 `--`，卡片不會消失 |
| master | ingest 改為直通 worker 回報的 status，手機回來後 worker 恢復回報，卡片**自動翻回 ONLINE**，不用手動 |
| worker/本機 | ADB 掃描發現裝置**消失超過 60 分鐘 → 標離線**；回到清單後自動恢復（順手修掉一個既有 bug：斷線超過 3 小時後回連會被永久封鎖、不再重啟 thread） |
| worker | 手機重連迴圈保留 60 秒自癒重試，但現在每輪會更新狀態為「連線中斷（已中斷 X 分鐘）」，且暫停/強制休眠按鈕在這期間有效 |

## 驗證

- 新增 21 個測試 + 擴充 2 個既有測試檔，全綠；bot_state 相關既有 5 個套件 50 測全綠；改動的 5 個檔案 `py_compile` 通過。
- 混跑時看到的 `test_bootstrap` ↔ `test_pause_routing` 失敗是**既有的跨檔 stub 汙染**（在未改動的 HEAD 上可重現、失敗集合相同），與本次無關，已記錄在 todo.md。
- 依你指示，三個檔案的實作由三個 Opus subagent 併行完成，計畫與 review 都在 `tasks/todo.md` 頂部。

**注意：要重啟才生效** — master（infinite）和 worker（desktop_ov0asq4）兩台的 `new_main_v2.py` 都要重啟，改檔不重啟等於沒修。重啟後的預期：手機離家 1 小時內維持原狀（寬限），滿 1 小時卡片轉離線；手機回家後約 1 分鐘內自動接回、卡片自動恢復 ONLINE。

---

## 2026-06-11 03:46:34  ·  session a6ebde58

不會，目前的行為是這樣：

**現狀（手機帶出門後）**

1. 斷線後的**第一次喚醒**：WS 階段排在 ADB 喚醒**之前**跑（`new_main_v2.py` 迴圈順序），所以那一輪的純 WS 任務（用快取 ticket）**會跑完一次**。
2. 然後流程走到 ADB 喚醒 → 連不上手機 → 進入「連線中斷，60 秒重試」迴圈**卡住等手機回來**——不會再回到睡眠，也就**不會再有下一輪 WS**。
3. 1 小時後卡片轉 OFFLINE——這只是顯示層的判定，不會改變 thread 行為，也不會切成純 WS 模式。

所以整體來說：離線期間 WS 最多只多跑一輪，之後就停了。

**如果你要「手機不在家也持續跑純 WS」**，可以加一個降級模式，改動不大：

- 喚醒時偵測到手機連不上 → 不再死等，這輪只跑 WS 階段，然後照常排程休眠；每次喚醒重複（等於暫時變成純 WS 後端）。
- 手機回到 wifi → 自動恢復完整 ADB 流程（喚醒、解鎖、開 App 跑全部任務）。
- 限制：純 WS 只能撐到快取 ticket 過期為止。adb 後端的 ticket 要靠 ADB+logcat 重撈（`tools/adb_token_login.py`），手機不在就沒辦法換新 ticket，過期後那幾輪會顯示「WS 登入失敗」，等手機回家自動重撈恢復。

要做這個降級模式嗎？要的話我照同一套 TDD 流程加上去。

---

## 2026-06-11 03:50:05  ·  session a6ebde58

已寫入，新 session 直接開工即可。

**`tasks/todo.md` 頂部新增「[NEXT SESSION 待做] 手機離線 WS 降級模式 + 純 adb 自動撈 token」**，包含：

- 兩個需求的明確定義（降級模式 + 純 adb 自動撈 token）
- 本次 session 已查清、不用重查的事實：WS 階段在 ADB 喚醒之前（所以降級 = 喚醒失敗就去睡）、卡死點在 `_wait_for_phone_connection`、收尾段 428-443 要跳過的陷阱、`--verify` 會踢 session 絕不能帶、web_h5 的 `ws_ticket_refresh` 可當 hook 範本
- 開工前要先讀的 4 個檔案清單
- 分步 Plan（A1-A3 降級模式、B1-B3 token 自動撈取、驗證）

memory 也補了 follow-up 指標。別忘了這次已完成的「掉線 1 小時判離線」修復要**重啟 master 和 worker 的 `new_main_v2.py`** 才生效。

---

## 2026-06-11 04:24:10  ·  session 26638362

全部 72 測試通過。收尾：更新 todo 的 review 與 memory。

---

## 2026-06-11 04:54:32  ·  session 864cbac5

可以，而且你的專案其實已經有相當完整的「已驗證重構地圖」，不用從零分析。我先給你架構總結與我建議的計畫骨架，你確認方向後我再把正式計畫寫進 `tasks/todo.md`。

## 架構現況（摘要）

- **執行模型**：`new_main_v2.py` 掃裝置 → 一裝置一 thread → 喚醒 → 主頁守衛 → 跑每日任務 pipeline（`game_actions/daily_pipeline.py`）→ 對齊整點休眠。雙後端 adb / web_h5（`device_wrapper.py` 抽象）。
- **協調層**：master/worker 模式，`control_panel_app.py`（Flask :5002）+ `runtime_services/`（lazy 啟動的掃描、睡眠、worker 同步、web session 等服務）。
- **辨識層**：OCR（`img_tools.py` 多 server fallback + circuit breaker）+ CNN 分頁（`new_cnn/`）；挖礦走 `miner/`（planner v4 預設）。
- **新生代模組已逐步取代舊腳本**：`opengold_v2/`（神燈）、`farm_v2/`、`sea_v2/`、`ws_token/`（純 WS 任務），但根目錄仍留大量 legacy 腳本（`park.py`、`Mission.py` 等）與死碼。

## 主要結構問題（皆已在 `docs/REFACTORING_OPPORTUNITIES.md` 驗證過）

1. **God modules**：`control_panel_app.py` 2576 行（12 個子系統擠一個 Flask app）、`device_wrapper.py` 1359 行、`new_main_v2.py` 主迴圈過大。
2. **重複實作**：主頁 9 點像素守衛複製 5 份、cocos worldToScreen JS 重抄 10+ 處、device-id 正規化 / JSON 讀寫繞過正規 helper。
3. **死碼與 cruft**：V1 神燈、空 `main.py`、root `test_*.py` 污染 pytest 收集、3 萬多個 sync-conflict 檔。
4. **狀態管理**：`bot_state` 重構已做完 Phase 0–3，Phase 2 在工作區未 commit，Phase 4（web_launch/online_check mailbox 抽出）未做。

## 建議的計畫骨架（沿用文件中已排好的順序）

- **Phase 0 安全速贏**：cruft 清理、死碼刪除、`pyproject.toml` testpaths（零 runtime 風險，半天內）。
- **Phase 1 低風險去重**：共用 helper 收斂（safe_device_id、world_to_pixel、json_io、JsonDataManager）。
- **Phase 2 效率**：文件顯示已大致完成，但有幾項（`oracle()`、`get_stage` 合併 OCR、bot_state Phase 2）**在工作區未 commit**——應先收尾這些。
- **Phase 3 高風險拆分**：control_panel blueprint 拆分、carpark JS 共用化、`_start` 分解、V1 神燈刪除（需停 bot 視窗、逐項 pinning test）。

一個重要前置事項：目前工作區有 30+ 個已修改檔案（ws_token 相關工作未 commit），重構開工前得先把這些收乾淨，否則 diff 會混在一起。

要我直接把這份計畫展開成 `tasks/todo.md` 的可勾選項目嗎？或者你想先聚焦某一個 Phase（例如只做 control_panel 拆分）？

---

## 2026-06-11 05:02:42  ·  session 864cbac5

整理完成。做了兩件事：

1. **`tasks/todo.md` 重整為精簡版**，結構如下：
   - 🔒 **另一 session 進行中**：挖礦 WS 明確標記為別的 session 在處理（含涉及檔案 `ws_token/mining*.py`），本 session 不動、不重複規劃。
   - ⚠ **待重啟生效**：兩個已完成 fix（掉線判離線、手機離線降級+被動撈 token）都還沒重啟 master/worker。
   - **進行中/待辦**：WS 後端整合殘項（S0/S5/S7 等）、6 個 recon 完待 build 的 WS feature 表、降運算 Phase 2、重構待辦（指向 `docs/REFACTORING_OPPORTUNITIES.md` 為真相來源）。
   - 其他子待辦檔的指標清單。

2. **已完成的大段內容（含完整 Review）原文搬到 `tasks/archive/todo_archive_2026-06-11.md`**，沒有刪掉任何資訊：手機離線降級、掉線 1h 判離線、S5b online-check 解耦、降運算 Phase 1+分流、神燈 V2 重構、2026-05-19 統整計畫。

一個提醒：重構待辦裡最該先做的是「收尾工作區未 commit 項」（`oracle()`、`get_stage` OCR 合併、bot_state Phase 2），但目前工作區的 30+ 個修改檔多半屬於挖礦 WS 那個 session 的工作，commit 收尾建議等那邊告一段落再做，避免 diff 互混。

---

## 2026-06-11 05:05:19  ·  session 864cbac5

已補完。`tasks/todo.md` 的重構段落現在展開成完整可勾選清單，照 `docs/REFACTORING_OPPORTUNITIES.md` 已驗證的優先序分四個 Phase：

- **Phase 0 死碼/cruft**（9 項）：tracked .pyc、空 main.py、`new_stage_check` 死碼、scratch 腳本歸位、pytest testpaths、孤兒 worktree（~31k 檔）、垃圾目錄、gitignore 補充、停機後的 sync-conflict sweep。
- **Phase 1 去重**（5 項）：`safe_device_id`、`world_to_pixel`、BOM-safe json 讀取、`JsonDataManager` 收斂、主頁 9 點守衛共用。
- **Phase 2 效率**：標記已完成部分（commit `4d2766e3`），剩 OCR 詞表收斂與待 commit 的工作區項。
- **Phase 3 高風險拆分**（8 項）：control_panel blueprint 拆分順序、Flask 封套、carpark JS walker 共用化、`_start` 分解、V1 神燈刪除、device_wrapper except 清理等。

每項都帶了文件裡的關鍵陷阱備註（例如 worker_sync 必須 import 同一個 `_commands_lock`、`world_to_pixel` 要保留 `round()`、carpark 遷移要先 assert 新舊座標相等），避免之後動手時漏掉。前置項也標了警告：工作區目前混有挖礦 WS session 的改動，commit 收尾要等那邊結束。

---

## 2026-06-11 05:37:18  ·  session b901da45

Commit 完成。更新 `tasks/todo.md` 進度。

---

## 2026-06-11 13:30:35  ·  session 5bb8240e

其實它們已經在同一個資料夾了。兩個檔案都放在 `logs/<裝置>/` 底下：

```
logs/
└── emulator-5554/
    ├── main.log        ← 日常主迴圈
    ├── miner.log       ← 挖礦
    ├── ocr_trace.log
    ├── error_screenshots/
    └── action_trace/
```

這是 2026-05-02 Phase 01 log 重整後的佈局：每個裝置一個資料夾，main 和 miner 只是分檔，不是分路徑。rotated 歷史檔會移到 `logs/_archive/<裝置>/`。

所以不需要搬。如果你的疑問是「為什麼看 log 要開兩個檔」，那是刻意的分流（挖礦量大、會洗掉主迴圈訊息），要合併成一個檔反而會讓 main.log 很難讀。

---

## 2026-06-11 13:32:04  ·  session 5bb8240e

有，兩層機制都有：

**檔案大小限制（RotatingFileHandler）**
| 檔案 | 上限 | 備份數 |
|------|------|--------|
| main.log | 10MB | 5 份 |
| miner.log | 5MB | 3 份 |
| ocr_trace.log | 512KB | 4 份 |

超過大小就滾動成 `main.log.1`、`main.log.2`…，最舊的自動刪掉。

**歷史保留（history）**
- 每次 bot 啟動時，上一輪的 active log 會 rotate 成 `main.YYYYMMDD_HHMMSS.log`，搬到 `logs/_archive/<裝置>/`
- 啟動時自動 purge：rotated 副本超過 7 天就刪（`_DEVICE_LOG_RETENTION_DAYS = 7`，ocr_trace 是 5 天）

所以磁碟占用有上限：每裝置大約 main 60MB + miner 20MB + 7 天內的啟動歷史檔，不會無限長大。

---

## 2026-06-11 13:35:16  ·  session 5bb8240e

掃完了（排除 `.conda`、PaddleOCR vendor、worktree 副本），你的專案程式碼超過千行的只有 5 個檔案：

| 行數 | 檔案 | 評估 |
|------|------|------|
| 2546 | `control_panel_app.py` | 唯一真正該拆的。Flask 路由 + 狀態管理 + 各功能 API 全擠在一起，且持續在長（這次 diff 又改了它）。可按 blueprint 拆：fly_pet、裝置控制、live view、worker 路由各自一個模組 |
| 1554 | `miner/scripts/Mining_等待改進.py` | 檔名就說明了：是待改進的 scratch/舊腳本，不在 live 路徑上，不值得重構，考慮歸檔或刪除 |
| 1239 | `Open_gold_paddle_ocr.py` | V1 神燈，已廢棄（live 走 `opengold_v2/`），CLAUDE.md 只留作除錯參考。不用重構，未來可直接刪 |
| 1231 | `device_wrapper.py` | 核心熱路徑，adb/web 雙後端 wrapper。略超標但職責單一，動它風險高於收益，建議不動 |
| 1141 | `utils/carpark_auto.py` | 單一功能（停車自動化），內聚度高，可不動 |

職責重複的部分：你之前已經做過系統性檢查，結論在 `docs/REFACTORING_OPPORTUNITIES.md`（已驗證的跨子系統去重待辦），而且 Phase 0~2 已經執行了一部分（最近的 commit：死碼清理、共用 helper 去重、OCR 詞表收斂）。

建議：真正需要動的只有 `control_panel_app.py` 拆 blueprint，其餘是「數字超標但不痛」。要不要我把 control_panel 拆解計畫寫進 `tasks/todo.md`？

---

## 2026-06-11 13:40:13  ·  session 5bb8240e

掃完了（排除 `.conda`、PaddleOCR vendor、worktree 副本），你的專案程式碼超過千行的只有 5 個檔案：

| 行數 | 檔案 | 評估 |
|------|------|------|
| 2546 | `control_panel_app.py` | 唯一真正該拆的。Flask 路由 + 狀態管理 + 各功能 API 全擠在一起，且持續在長（這次 diff 又改了它）。可按 blueprint 拆：fly_pet、裝置控制、live view、worker 路由各自一個模組 |
| 1554 | `miner/scripts/Mining_等待改進.py` | 檔名就說明了：是待改進的 scratch/舊腳本，不在 live 路徑上，不值得重構，考慮歸檔或刪除 |
| 1239 | `Open_gold_paddle_ocr.py` | V1 神燈，已廢棄（live 走 `opengold_v2/`），CLAUDE.md 只留作除錯參考。不用重構，未來可直接刪 |
| 1231 | `device_wrapper.py` | 核心熱路徑，adb/web 雙後端 wrapper。略超標但職責單一，動它風險高於收益，建議不動 |
| 1141 | `utils/carpark_auto.py` | 單一功能（停車自動化），內聚度高，可不動 |

職責重複的部分：你之前已經做過系統性檢查，結論在 `docs/REFACTORING_OPPORTUNITIES.md`（已驗證的跨子系統去重待辦），而且 Phase 0~2 已經執行了一部分（最近的 commit：死碼清理、共用 helper 去重、OCR 詞表收斂）。

建議：真正需要動的只有 `control_panel_app.py` 拆 blueprint，其餘是「數字超標但不痛」。要不要我把 control_panel 拆解計畫寫進 `tasks/todo.md`？

---

## 2026-06-11 14:11:41  ·  session 9719ee75

todo.md 第 102 行已有這項的占位。我把詳細拆分計畫寫成獨立段落，並讓 Phase 3 條目指過去：

---

## 2026-06-11 14:57:37  ·  session 9719ee75

規劃完成，已寫進 `tasks/todo.md` 的「control_panel_app 拆分計畫」段落，等你過目後再動手。另外收到指示：之後所有 subagents 一律用 model: opus 調用（這次盤點的 Explore agent 之後都會照辦）。

重點摘要：

**現況**：2902 行、63 條 HTTP 路由 + 1 條 WebSocket、18 個 module-level 全域狀態。最大塊是飛寵 CDP API（17 條路由約 750 行），其次是 web 登入（含 287 行的 Playwright worker）、live view、worker 同步、labeler/trainer。

**拆分方案**：新增 `control_panel/` 套件，按功能拆 9 個 blueprint 模組 + 3 個 shared 模組（cdp、command_queue、auth）。`control_panel_app.py` 留作 façade，`app`/`run_server`/`_FLY_PET_ICON_DIR` 介面全部不變，所以 `new_main_v2.py` 和現有測試零改動。

**三個關鍵風險**已寫進計畫：
1. `_commands_lock` 和三個指令 dict 被 worker 與 control 兩群共用，拆完必須只有一份真相（shared/command_queue.py），blueprint 以模組屬性存取。
2. flask-sock 的 `/ws/live_view` 要延後綁定，且保留無 flask_sock 時的優雅降級。
3. 測試 monkeypatch 的符號（如 `_FLY_PET_ICON_DIR`）要 re-export 且路由內用模組屬性讀，否則 patch 失效。

**執行順序**：P3-CP-0 先建骨架抽 shared，然後從測試覆蓋最齊、最自含的 fly_pet 開始，依序 labeler → web_session → worker_sync → control → live_view → 收尾 pages/status/config，每步一個 commit、各自跑對應測試、可獨立回滾。最後需要停機窗重啟 master+worker 並做 dashboard live smoke。

確認沒問題我就從 P3-CP-0 開始。

---

## 2026-06-11 14:58:24  ·  session 9719ee75

control blueprint 完成（7 個 agents 之 1）。其餘 6 個還在跑，等全部完成後我再做 façade 整合與測試。

---

## 2026-06-11 14:58:41  ·  session 9719ee75

labeler/trainer blueprint 也完成（2/7）。繼續等其餘 5 個。

---

## 2026-06-11 14:59:23  ·  session 9719ee75

labeler/trainer blueprint 也完成（2/7）。繼續等其餘 5 個。

---

## 2026-06-11 14:59:48  ·  session 9719ee75

labeler/trainer blueprint 也完成（2/7）。繼續等其餘 5 個。

---

## 2026-06-11 15:00:22  ·  session 9719ee75

labeler/trainer blueprint 也完成（2/7）。繼續等其餘 5 個。

---

## 2026-06-11 15:00:57  ·  session 9719ee75

labeler/trainer blueprint 也完成（2/7）。繼續等其餘 5 個。

---

## 2026-06-11 15:16:05  ·  session 9719ee75

拆分完成，80 個相關測試全綠。總結：

**完成內容**（7 個 Opus subagents 平行抽取 + 我做 shared 層與 façade 整合）：

- `control_panel_app.py`：2902 行 → **139 行 façade**（建 app、註冊 9 個 blueprints、Sock 初始化、re-export 測試符號、`run_server`）。對外介面不變，`new_main_v2.py` 零改動。
- `control_panel/shared/`：`command_queue.py`（遠端指令佇列唯一真相，`_commands_lock` 只剩一份）、`cdp.py`、`auth.py`。
- 9 個 blueprint 模組：fly_pet(840L)、web_session(585L)、labeler(435L)、status(327L)、worker(225L)、live_view(161L)、pages(142L)、control(84L)、config(53L)，全部純 code-motion。

**驗證**：
- 路由 parity 與 HEAD 完全一致（63+2 條，含 trailing-slash 變體與 `/ws/live_view/<ip>`）。
- monkeypatch 面全部生效：可被 patch 的符號（`_cdp_json_response`、`_FLY_PET_ICON_DIR`、`_push_to_worker_webhook`、`_run_web_login_worker` 等）在 blueprint 內一律經 façade 晚綁定查找。
- `test_fly_pet_template` / `test_fly_pet_breed_template` 讀原始碼的路徑常數已跟著指向 `routes_fly_pet.py`。

**順手修了一個既有 bug**（與拆分無關，HEAD 乾淨 worktree 驗證過）：`test_bootstrap_api_services` 在收集期塞 stub `worker_webhook_api` 進 `sys.modules`，害掉線判離線 pending diff 新加的 `test_worker_webhook_applies_wake_delay` import 到 no-op stub 而 flaky。現在該測試 import 前用 `monkeypatch.delitem` 暫時逐出 stub，測後自動還原。

**未完成、需要你配合的**：
1. **未 commit**——工作區混有挖礦 WS session 與掉線判離線的 diff，照 todo 既定原則等解纏後再 commit。
2. **需重啟 `new_main_v2.py` 才生效**（sys.modules cache），重啟後做 P3-CP-8 dashboard live smoke（狀態頁/暫停/飛寵頁/live view 直播）。已記進 todo 的「待重啟生效」清單。

---

## 2026-06-11 15:16:37  ·  session 9719ee75

這是先前排程的喚醒提示——該工作已全部完成：pages/status/config agent 早已交付，façade 整合也做完了，三個注意點都已落實：

1. `routes_fly_pet._FLY_PET_ICON_DIR` 已修成 repo 根路徑（`static/flypet_icons`，驗證過實際值）。
2. Façade 已 re-export `_run_web_login_worker`/`_normalize_web_login_state`/`_web_login_lock`/`_web_login_state`、command_queue 全部物件、`_cdp_json_response`、`_FLY_PET_ICON_DIR`，另補了測試需要的 `requests`。
3. `Sock(app)` 之後已呼叫 `routes_live_view.init_ws(sock)` 與 `routes_status.init_ws(sock)`。

最終狀態：80 個相關測試全綠、路由 parity 與 HEAD 一致。剩餘兩件事在 todo 追蹤：等工作區 diff 解纏後 commit、停機窗重啟 `new_main_v2.py` 後做 dashboard live smoke（P3-CP-8）。沒有需要再處理的事項。

---

## 2026-06-11 15:46:41  ·  session 4fa00f64

測試正在背景跑（NAS 路徑下 pytest 啟動慢），完成後我會回報結果與修改摘要。

---

## 2026-06-11 15:47:51  ·  session 4fa00f64

75 個測試全綠。問題與修法總結：

**為什麼 dashboard 卡在「純 WS 任務執行中」**
`new_main_v2.py` 只在 WS 階段開始時寫一次狀態，之後 `ws_token/runner.py` 跑完整串 15 個任務（main_tasks → redpack → guild → … → lamp）期間完全沒有回報，所以畫面整段都停在同一行。

**為什麼 log 也看不到**
`ws_token/runner.py` 和各任務模組用的是模組 logger（root），而每台裝置的 `logs/<device>/main.log` 是 `propagate=False` 的獨立 logger——runner 的逐任務訊息根本進不了裝置 log，你只看得到「WS 階段開始 / 完成」兩行。

**修改內容**
1. `ws_token/runner.py`：`run_device` 新增 `progress(name, status, detail)` callback，每個任務開始 / 完成 / 失敗都會呼叫；callback 本身炸掉也不會中斷任務。
2. `game_actions/ws_phase.py`（喚醒後的 WS-first 階段）：接上 callback，逐任務更新 dashboard step（`WS 任務執行中: redpack` / `WS 任務完成: …` / `WS 任務失敗: …`）並寫進裝置 log（失敗含錯誤摘要）。
3. `runtime_services/ws_runner_service.py`（純 WS 後端裝置）：同樣接上，dashboard 與 `logs/<device>/main.log` 都有逐任務進度。
4. 測試：runner 新增 2 個 progress 測試（順序回報、錯誤回報且 callback 異常不破壞 run），並更新 ws_phase / wiring 測試的 fake 簽名。`tests/test_ws_token_runner.py + test_ws_phase.py + test_ws_runner_wiring.py` 共 75 passed。

注意：要看到效果需要重啟 `new_main_v2.py`（sys.modules 快取，改檔不重啟等於沒改）。

---

## 2026-06-11 15:54:58  ·  session 1a2aa08a

排查完成，根因和修法如下。

**根因：WS 挖礦是 opt-in，7fe98fc6 根本沒開。** `ws_token/runner.py:531` 只有在 `mining_config.enabled=True` 時才會跑 mining，但這台的 `ws_token` 設定裡完全沒有 `mining` 區塊，所以 `mining_config=None`、整段被跳過。Log 也佐證：15:30 那輪 WS 階段跑完的 ok 清單有 14 項任務，唯獨沒有 `mining`，且無任何錯誤——不是執行失敗，是從未被排入。前一個 commit（`feat(ws-token): wire supervised mining execution`）只把執行端接好，沒人開啟設定。

順帶一提，那輪 Playwright 階段也沒挖到礦：15:38 因「連續 4 個任務不在主頁面」pipeline 提前中止，挖礦排在後面沒輪到。但那是另一個頁面判定問題，與 WS 挖礦無關。

做了兩件事：

1. **`bot_config.json`**：給 7fe98fc6 的 `ws_token` 補上 `"mining": {"enabled": true, "allow_bomb": false, "allow_drill": false, "max_steps": 200}`（安全預設，只用鎬子）。`config_manager` 有 mtime cache 會自動重讀，下次喚醒生效，不用重啟 bot。
2. **Dashboard**（`templates/dashboard.html`）：設定視窗「功能開關」區新增「WS 挖礦」checkbox。讀取時從 `ws_token.mining.enabled` 帶入，儲存時 merge 進既有 mining dict（沿用 `_existingWsToken` 的 stash-and-spread 模式，不會蓋掉 allow_bomb/allow_drill/max_steps 或其他 ws_token 欄位）。

驗證：`tests/test_dashboard_template.py` + `tests/test_ws_phase.py` 共 17 個測試全綠，`bot_config.json` JSON 解析正常。注意中控頁面改了 template，需要重啟 control panel（或重新整理頁面即可，因為是 template 渲染——若 Flask 沒開 auto-reload 就要重啟）才會看到新 checkbox。下次 7fe98fc6 喚醒時看 WS 階段 ok 清單有沒有 `mining`，以及 skip 裡出現「挖礦任務」即代表生效。

---

## 2026-06-11 15:57:07  ·  session 1a2aa08a

已提交 `3ed7ccac feat(ws-mining): 7fe98fc6 啟用純 WS 挖礦 + dashboard 開關`，只含這次的兩個檔案（`bot_config.json` 的 mining 區塊 + `templates/dashboard.html` 的 WS 挖礦 checkbox），其他未相關的工作區修改未動。

---


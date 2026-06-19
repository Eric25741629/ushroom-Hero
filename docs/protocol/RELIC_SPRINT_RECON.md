# 遺物碎片衝刺活動 (Relic Sprint / 衝刺榜) — 唯讀協議 recon

> 2026-06-19 唯讀分析。資料來源:client 源碼
> `docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`
> (22MB minified,re.finditer 切片)、`docs/protocol/{ACT2,TYPE}_PROTO_SCHEMA.json`、
> `ws_token/relic.py`、`docs/protocol/RELIC_ALLOC_RECON.md`。
> **全程唯讀,未動 live 裝置、未送任何封包。** 門檻數值 (225K…) + 計數語意的最終確認
> 列在「待 live 確認」。

## TL;DR(最重要的結論)

- **衝刺活動 = 跨服限時排行榜「衝刺榜 (RankRush / Cross Limited Rank)」**,是通用活動框架
  的一種 `ActivityType`。「遺物碎片衝刺」對應 **`ActivityType.RankRush_8 = 13`**
  (舊版輪替) / **`RankRush_New_7 = 269`**(新版輪替)。
  證據:client `JumpView` / `openView` 對這兩個 type 都 `uiMgr.openView("RelicMainView")`
  —— 點衝刺活動直接跳到遺物頁,完全對上「用遺物升級衝刺」。
- **協議走 `act2` 模組 (module 25 = 0x19) 的 `cross_limited_rank` 系列**(NOT relic 模組,
  也 NOT act/24)。讀進度 / 領輪獎都在這裡;見下方 cmd 表。
- **4 輪 = `small_group_id` 1..4**(client `setTaskList` 以 `is_stage==1` + `small_group_id`
  分頁,每頁一輪;`maxPage` = 輪數)。每輪一個 stage task,門檻在
  `configCross_limited_rank_task.condition`,獎勵在 `.reward`。
- **🔑 relic 升級的碎片消費「自動計入」衝刺進度,server 端累計,不需另送提交/兌換 cmd。**
  根據:client 端**只**「讀進度 (`info` / `task_update` push) + 領獎 (`task_reward`)」,
  從不回報「我消費了多少」。`p_cross_limited_rank_task.count` 純由 server 推
  `act_cross_limited_rank_task_update_s2c` 更新;領獎 c2s 只帶 `{act_type, small_group_id}`,
  不帶 count。這是典型 server-side 行為累計活動(做行為 → server 自動加 count → 達門檻 status
  轉 CanGet → client 領)。
  → **實作上:照常用既有 `relic.relic_level_up` (0x1103) 升級消碎片,server 自己會把消費灌進
    衝刺 count;到輪門檻後再對 4 輪各送一次 `act_cross_limited_rank_task_reward` 即可。**
- **relic 升級 cmd 與現行 client 一致**:client 把 sub=3 命名為 `relic.relic_level_up_c2s`
  (cmd 4355 = 0x1103) body `{id}`,**與 `ws_token/relic.py` 的 `CMD_RELIC_UP=0x1103 {relic_uid#1}`
  是同一個 wire shape**(只是早期 recon 寫的 proto 名 `relic_up` 已更名,sub 不變)。relic.py 不需改協議。
- **每帳號各自**:衝刺進度 (`count`)、輪獎 (`status`) 都掛在登入帳號的 act 狀態;碎片是帳號
  貨幣 (item 100022 / currency 7)。每帳號獨立(與 memory 一致)。

## 活動結構(client 證據)

`ActivityType` 枚舉(源碼 @2426600):
```
OpenTask:1, AccRechargeDaily:2, ..., HorseCarnival:5,
RankRush_1:6, RankRush_2:7, RankRush_3:8, RankRush_4:9, RankRush_5:10,
RankRush_6:11, RankRush_7:12, RankRush_8:13, RankRush_9:14,
RankRush_New_1:263 .. RankRush_New_7:269 .. RankRush_New_16:278, ...
```

各衝刺榜對應的養成系統(`JumpView`,源碼 @19742500):

| RankRush type | 跳轉頁 | 養成 / 消費資源 |
|---|---|---|
| RankRush_8 (13) / RankRush_New_7 (269) | **RelicMainView** | **遺物碎片**(本任務目標) |
| RankRush_7 (12) / RankRush_New_3 | ScienceView | 科技 |
| RankRush_4 (9) / RankRush_New_4 | HorseView | 坐騎 |
| RankRush_5 (10) / RankRush_6 (11) | StatueView | 雕像 |
| RankRush_9 (14) / RankRush_New_5 | FateTabView | 命運 |
| RankRush_2 (7) | Shop_Draw | 抽卡 |

衝刺榜資源圖示 `RankRushRes` (源碼 @2436215):`RankRush_8` =
`{ icon:"ccrl_icon_yiwu" (遺物), title:"xscb_txt_ywcc" (遺物衝刺), barImg:ccrl_ui_jindutiao08 }`。
(`xscb` = 衝刺寶/衝刺榜資源前綴;`ywcc` = 遺物衝刺;`ccrl` = 衝刺榮燿進度條資源。)

活動有時間窗 + 輪替:`p_act_calendar { act_type#1, stime#2, etime#3 }`
(`act_cross_limit_rank_calendar`),對上「周一開始、每月輪一次」。
活動狀態 `ActivityState {Null:0, Preview:1, Open:2, EndShow:3}`;只有 `Open` 才能領獎
(client `GetTaskRedNum` 以 `state==Open` 為前提)。

## act2 (module 25) cmd 表 — cross_limited_rank

來源:源碼 cmd_id 對映 (@19536000) + `docs/protocol/ACT2_PROTO_SCHEMA.json`。
`cmd = module(25)*256 + sub`;c2s/s2c 共用 id;失敗一律回 `0x0201`。

| cmd | 0x | name | c2s body | s2c body |
|---|---|---|---|---|
| 6572 | 0x19AC | `act2.act_cross_limited_rank_info` | `{act_type#1}` | `{act_type#1, group_id#2, task_list#3: p_cross_limited_rank_task[]}` |
| 6573 | 0x19AD | `act2.act_cross_limited_rank_group` | `{act_type#1}` | `{act_type#1, serv_list#2: uint32[]}`(跨服分組,只用於開 RankJoinServerView,不影響領獎) |
| 6574 | 0x19AE | `act2.act_cross_limited_rank_task_update` | — (push) | `{act_type#1, update_list#2: p_cross_limited_rank_task[]}` |
| **6575** | **0x19AF** | **`act2.act_cross_limited_rank_task_reward`** | **`{act_type#1, small_group_id#2}`** | (成功更新 task status 為 HadGet,獎勵 0x0402 push;失敗 0x0201) |
| 6576 | 0x19B0 | `act2.act_cross_limit_rank_calendar` | `{}` | `{calendars#1: p_act_calendar[]}` |

型別(`docs/protocol/TYPE_PROTO_SCHEMA.json`):
```
p_cross_limited_rank_task { task_id#1:uint32, status#2:uint32, count#3:uint64 }
   status: ActivityTaskState {Normal:0, CanGet:1, HadGet:2}
   count : server 累計的進度量(uint64,可達 900K+)
p_act_calendar { act_type#1, stime#2, etime#3 }
```

> 注意:client send 函式是 `RankingRushControl.send_25_144(act_type, group_id)`,
> 其中傳入的 `group_id` 實際就是 stage task 的 `small_group_id`(輪次 1..4)。
> 領獎按鈕 `btnGet` → 對「當前頁 (=當前輪)」的 group_id 送一次。

## 門檻 / 計數 config — `configCross_limited_rank_task`

表 schema(源碼 @13929300,`ConfigCross_limited_rank_task.ts`):

| col | 欄位 | 意義 |
|---|---|---|
| 0 | `id` | task_id |
| 1 | `task_group_id` | 大組(對應某個衝刺活動 type) |
| 2 | `small_group_id` | **輪次 (1..4)** |
| 3 | `is_stage` | 是否階段任務(衝刺輪 = 1) |
| 4 | **`condition`** | **門檻條件**(目標值,如 225000 / 450000 / 675000 / 900000) |
| 5 | `reward` | 該輪獎勵 |
| 6 | `desc` (langID) | 任務描述文字(運行時語言表) |
| 7 | `desc_num` | 顯示用數字 |
| 8 | `difference` | (差額/分檔) |

排名獎額外用 `configCross_limited_rank_reward`(排名段) + `configActivity_rank_reward`
(衝刺榜「排行」獎,與「累計衝刺輪獎」是兩條線;本任務只關心輪獎)。

**⚠ config 的 row data 不在 bundle**(`BaseConfig` 運行時載入;源碼無
`setDatas`/inline 表)。所以 4 輪門檻 225K/450K/675K/900K 的**精確值 + 計數單位**
(是「累計消費碎片量」還是「升級次數」)**只能 live 讀**(見下)。
從結構強烈推斷:遺物衝刺榜的 condition 計的是「消費遺物碎片累計量」(對上使用者所述
「4 輪 × 225K 碎片 = 900K」),count 為 uint64 也符合碎片量級而非次數量級。

## relic 升級協議(交叉印證)

relic 模組 (module 17 / 0x11) 現行 cmd(源碼 cmd_id 對映):

| cmd | 0x | name | c2s body |
|---|---|---|---|
| 4353 | 0x1101 | `relic.relic_info` | `{}` |
| 4354 | 0x1102 | `relic.relic_equip` | `{id}` |
| **4355** | **0x1103** | **`relic.relic_level_up`** | **`{id}`** ← 升級消碎片,即 relic.py 的 relic_up |
| 4356 | 0x1104 | `relic.relic_find` | `{}`(寻找遗物 gacha,另消碎片,**勿用**) |
| 4357 | 0x1105 | `relic.relic_tab_info` | `{}` |
| 4359 | 0x1107 | `relic.relic_choose_tab` | `{tab}` |
| 4362 | 0x110A | `relic.relic_unlock` | `{cfg_id}` |

→ `ws_token/relic.py` 的 `CMD_RELIC_UP=0x1103` + `build_relic_up_body = pb_uint(1, uid)`
**與現行 client `relic_level_up {id}` 完全一致**,協議無需更動。
每級碎片成本在 `configRelic[cfg_id][lv].col10 = [[7, C]]`(`RELIC_ALLOC_RECON.md` 已記:
4017 號 lv99→100 = 442080;成本隨等級上升後 plateau)。client config row data 同樣不在
bundle,實際成本只能靠 **0x0402 consume push 即時追蹤**(升一步、讀碎片減少量)。

## 待 live 確認(NOT done,維持唯讀/不消耗)

1. **4 輪 condition 精確值**:登入後送
   `act_cross_limited_rank_info_c2s {act_type=13(或269)}`,讀 `task_list`,
   交叉 `configCross_limited_rank_task.getDataByKey(task_id).condition`(CDP 在 web_h5
   port 9226 讀 config,讀 config 不踢線)。確認是否 225K/450K/675K/900K。
2. **計數單位**:升 1 級遺物(消耗 N 碎片)後,觀察 `act_cross_limited_rank_task_update_s2c`
   的 `count` 增量 == N(碎片量)還是 == 1(次數)。一步即可定論。也順帶確認 relic 衝刺
   是否真的綁「消費碎片」而非「升級次數 / 升到 X 級」。
3. **遺物衝刺當前用 RankRush_8(13) 還是 RankRush_New_7(269)**:讀 `act_list` /
   `act_cross_limit_rank_calendar`,看本期 Open 的是哪個 type(月輪替,值會換)。
4. **領獎成功/失敗回應**:`task_reward` 成功是否回同 cmd(更新 status=HadGet)還是只靠
   0x0402 / task_update push;失敗 0x0201 的 error_code(未達門檻 vs 已領 vs 活動關閉)。
5. **`act_type` 是否需先 `info` 訂閱**:多數 act 子活動要先 `info_c2s` 才會推 update;
   確認 task_reward 前是否必須先 info。
6. **碎片 item id 100022 與 currency 7 的對映**在 0x0402 快照確認(relic.py 已用 100022)。

## 不確定點 / 風險

- config row data 全在運行時表,bundle 不含 → 門檻數值 + 計數單位是**推斷**,須 (1)(2) live 坐實。
- `RankRush_8` vs `RankRush_New_7`:活動每月輪替,act_type 值會切換,實作須**動態從 act_list /
  calendar 取當前 Open 的遺物衝刺 type**,不可寫死 13 或 269。
- 活動關閉 (state != Open) 時送 reward 會被 0x0201 拒(對照 tycoon/farm 的
  `code=173 event ended`);消費碎片本身永遠可做,但**活動沒開時消費不會進衝刺 count**
  → 衝刺只該在 state==Open 時跑。
- anti-cheat:relic_level_up 是 server-authoritative(client 只送 uid,server 扣費+升級+回推),
  衝刺 count 也是 server 累計,**無 client 計算 / 無偽造面**,風險低(同 `RELIC_ALLOC_RECON.md`
  的乾淨 verdict)。唯一限制是同帳號異地登入互踢(ws_token 既有限制)。

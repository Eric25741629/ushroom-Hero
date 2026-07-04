# 遺物碎片衝刺活動 (Relic Sprint / 衝刺榜) — LIVE 實證協議 recon

> **2026-06-19 LIVE recon（5556, CDP 9223, web_h5 host mushroomh5.acenetgame.com）。**
> 推翻了 2026-06-19 早上的快速 recon（「4 task / accrued=max(count) / 門檻 225K-900K
> 4 輪」)。真機讀 6572、讀 config 表、實際升 2 次遺物坐實 count 語意。
> 資料來源:live 6572 reply bytes、`configCross_limited_rank_task.getDataByKey`(CDP 讀)、
> client 源碼 `docs/game_client_sources/...index.966f5.js`(RankingRushDataCache /
> RankingRushView setTaskList)。**升級只在 5556、總共 2 次（lv88→90 同一遺物 cfg=4029），
> 確認語意後即停,未把碎片升光、未跑到 900K、未領獎。**

## TL;DR(最重要、與舊 recon 的差異)

| 項目 | 舊快速 recon(錯) | LIVE 實證(對) |
|---|---|---|
| task 數 | 4 (= 4 輪) | **32**(28 milestone + 4 stage round) |
| 當期 act_type | 13 或 269 | **269**(`RankRush_New_7`),group_id=2698,task_group_id=2691 |
| accrued | `max(task.count)` | **非 stage task 的 count = 累計消費碎片**;`max(非stage count)` 仍正確 |
| count 語意 | 不確定(碎片 or 次數) | **碎片量**(升 1 級 lv88→89 消 125290,28 個 milestone count 全 0→125290) |
| 領獎 small_group_id | 硬寫 1..4 | **由 config `is_stage==1` 的 stage task 推導**(269129→sgid1 .. 269132→sgid4) |
| 門檻 | 推斷 225K/450K/675K/900K | **LIVE config 坐實**:輪累計門檻 = 各輪最後一個 milestone 的 `condition[2]` = 225K/450K/675K/900K |

## 真實 32-task 結構(LIVE,act 269 / group_id 2698 / task_group_id 2691)

6572 (`act_cross_limited_rank_info`) 對 `{act_type:269}` 回 `{act_type#1=269,
group_id#2=2698, task_list#3: 32 × p_cross_limited_rank_task}`。

config `configCross_limited_rank_task` row schema(源碼 ConfigCross_limited_rank_task.ts
@13929300):`[id, task_group_id, small_group_id, is_stage, condition, reward, desc, desc_num, difference]`。

### A. 28 個 milestone task(`is_stage=0`),task_id 269101~269128

- 每輪 7 個,共 4 輪;`small_group_id` 1/1.../1(7)、2(7)、3(7)、4(7)。
- `condition = [6, 7, 累計門檻]`:`[0]=6`(條件類型)、`[1]=7`(currency 7 = 遺物碎片)、
  **`[2]=該 milestone 的「絕對累計」消費碎片門檻**。
- `reward = [[2,100],[1002,5]]`(小獎:gold 100 + item 1002 ×5)。
- **`count` = 該帳號「累計消費的遺物碎片」**(server 端單一累計器,理論上鏡射到全部 28 個)。
- **⚠ count 凍結語意(LIVE 重要細節)**:milestone 一旦 `count >= condition[2]` → `status` 轉
  CanGet(1),**其 count 會凍結在「跨過當下的累計值」**;仍 Normal(status=0) 的 milestone
  才持續顯示「即時累計」。例:升 1 次後累計 125290,269101(門檻15K)轉 CanGet 且 count 凍在
  125290;升 2 次後累計 265870,269101 仍是 125290,而仍 Normal 的 269110 顯示 265870。
  → **真實當前累計 = 「最高的仍 Normal 的非 stage task 的 count」= `max(非 stage count)`**(凍結值
  恆 ≤ 即時值,所以 max 正確)。

各輪 7 個 milestone 的 `condition[2]`(LIVE 讀):
```
輪1 (sgid1): 15000  30000  60000  90000  135000 180000 225000
輪2 (sgid2): 240000 255000 285000 315000 360000 405000 450000
輪3 (sgid3): 465000 480000 510000 540000 585000 630000 675000
輪4 (sgid4): 690000 705000 735000 765000 810000 855000 900000
```
(`desc_num` 是「輪內相對值」15000..225000;`difference` 是輪起始基準 0/225K/450K/675K。
真正判定用的是絕對的 `condition[2]`。)

### B. 4 個 STAGE round task(`is_stage=1`),task_id 269129~269132

- `small_group_id` = 1/2/3/4;`condition = []`(無自身門檻);`reward = [[1017,2000],[2,400],[1002,20]]`
  (**大獎:item 1017 ×2000 + gold 400 + item 1002 ×20** — 這才是衝刺要領的回合獎)。
- **`count` = 該輪「已完成的 milestone 子任務數」(0..7)**;`status` 在 count==7(該輪 7 個 milestone
  全跨過)時轉 CanGet(1)。LIVE:升 2 次後累計 265870 ≥ 225000(輪1全部 7 個門檻),269129 的
  count 4→7、status 0→1(CanGet);輪2 (269130) count=2(240K/255K 跨過)。
- **領獎 = `send_25_144(act_type, small_group_id)` = 6575 `{act_type#1, small_group_id#2}`**;
  small_group_id 取自 stage task 的 config(269129=1..269132=4)。

> **stage task 的辨識(pure-WS 無 config 表時)**:wire 上 4 個 stage task 是「task_id 最大的 4 個」
> (排在 28 個 milestone 之後);依 task_id 升冪給 small_group_id 1..4。`relic_sprint._derive_rounds`
> 用此結構規則(task 總數須 == 4×7+4=32,否則回空,不亂猜)。

## client 分組邏輯(源碼交叉印證)

`RankingRushDataCache.updateInfo`(@19739000):對每個 task `cfg = configCross_limited_rank_task.getDataByKey(task_id)`,
`is_stage==1` → 放進 `group_task_list[small_group_id]`(=輪);否則放進 `task_list[task_id]`(=輪內子任務)。

`RankingRushView setTaskList`(@19781972):
```js
for (r of configCross_limited_rank_task.getDatas())
  if (r.task_group_id == group_id && r.is_stage == 1) { sgid=r.small_group_id; push(group_task_list[sgid]); }
this.maxPage = taskInfo.length;   // 輪數 = stage task 數
```
`btnGet` 點擊 → `send_25_144(act_type, taskInfo[curPage-1].group_id)`,其中 `.group_id == small_group_id`。
→ **領獎用的就是 stage task 的 small_group_id,不是位置 index、不是硬寫 1..4。**

`JumpView`:`RankRush_8(13)` / `RankRush_New_7(269)` → `RelicMainView`(遺物頁),對上「升遺物衝刺」。

## act2 (module 25) cmd 表 — cross_limited_rank

`cmd = module(25)*256 + sub`;c2s/s2c 共用 id;失敗一律 `0x0201`。

| cmd | 0x | name | c2s body | s2c body |
|---|---|---|---|---|
| 6572 | 0x19AC | `act_cross_limited_rank_info` | `{act_type#1}` | `{act_type#1, group_id#2, task_list#3: p_cross_limited_rank_task[]}` |
| 6574 | 0x19AE | `act_cross_limited_rank_task_update` | — (push) | `{act_type#1, update_list#2: p_cross_limited_rank_task[]}` |
| **6575** | **0x19AF** | **`act_cross_limited_rank_task_reward`** | **`{act_type#1, small_group_id#2}`** | 成功 → status=HadGet(獎勵 0x0402 push);失敗 0x0201 |
| 6576 | 0x19B0 | `act_cross_limit_rank_calendar` | `{}` | `{calendars#1: p_act_calendar[]}` |

```
p_cross_limited_rank_task { task_id#1:uint32, status#2:uint32, count#3:uint64 }
   status: 0 Normal / 1 CanGet / 2 HadGet
```

## relic 升級協議(交叉印證,協議無需更動)

relic module 17 / 0x11:`relic_level_up` = **cmd 4355 (0x1103)** body `{id#1:uint64}`,server-authoritative
(client 只送 uid,server 扣碎片+升級+回 `{p_relic#1}`)。`ws_token/relic.py` `CMD_RELIC_UP=0x1103
{relic_uid#1}` 完全一致。

LIVE 升級成本(5556, cfg=4029):lv88→89 = **125290** 碎片、lv89→90 = **140580** 碎片
(成本隨等級遞增)。**→ 在 lv~88,單一遺物升 1 級就消 12-14 萬碎片,整個 900K 衝刺只需約 7 次升級。**
(對照 `RELIC_ALLOC_RECON.md`:cfg=4017 lv99→100=442080,成本更高。)

升級的碎片消費「自動計入」衝刺 count(server 端),**無另送提交 cmd**:LIVE 升 1 次後 6572 的 28 個
milestone count 全部 0→125290。

## LIVE 升級觀測(碎片 delta vs count delta)

| 升級 | 遺物 | 碎片消耗(=count delta) | 累計 count(全 28 milestone) | stage task 變化 |
|---|---|---|---|---|
| #1 | cfg=4029 lv88→89 | **125290** | 0 → 125290 | 269101-104 status→1(凍 125290);269129 count 0→4 |
| #2 | cfg=4029 lv89→90 | **140580** | 125290 → 265870 | 269105-109 status→1;**269129 count 4→7 status→1 (CanGet!)**;269130 count→2 |

> 碎片現量讀法:`0x0402` consume push 在升級後**未帶 item 100022**(與挖礦鎬子 9800001 同樣的「貨幣型」
> 推送可能走不同 evt;`parse_inventory_push` 沒抓到 100022)。頁面也無全域 `getGoodsCountXxx(100022)`
> 函式可直接讀。**→ 碎片即時量在 pure-WS 下「未知」是預期的**;`spend_to_target` 已有 `frag_unknown`
> fallback(靠 0x0201 拒絕 / max_steps / 衝刺 accrued 為界)。**count(累計消費)才是可靠的進度真值**,
> 規劃用 `remaining = SPRINT_TOTAL - accrued`。

## 實作對齊(`ws_token/relic_sprint.py`)

- `parse_sprint` → `Sprint{tasks(32 raw), rounds(4 stage), accrued, claimable_rounds}`;
  `_derive_rounds` 用「最大 4 個 task_id = stage」結構規則,small_group_id 升冪 1..4。
- `accrued` = `max(非 stage task count)`(robust:凍結值 ≤ 即時值)。
- `claimable_rounds` = stage round 中 status==CanGet 的 small_group_id。
- `claim_round` = 6575 `{act_type, small_group_id}`;small_group_id 由 round 推導(非硬寫)。
- `run_relic_sprint`:`remaining = max(0, target - accrued)` → `relic.spend_to_target`(最低等級先升)→
  重讀 → 對每個 claimable stage round 領獎。`SPRINT_TOTAL=900000`、`ROUND_THRESHOLDS=(225K,450K,675K,900K)`。

## 仍不確定 / 待確認

1. **領獎 6575 的成功回應形狀**:LIVE 已造出 round1 CanGet,但**未實際送 6575 領獎**(auto-mode
   classifier 擋下;且使用者要求保守)。`parse_claim` 依源碼把回同 cmd(0x19AF)當成功、0x0201 當失敗
   ——成功/失敗 error_code(未達/已領/活動關閉)的精確值待真領一次坐實。
2. **活動關閉(state != Open)時 6572 是否仍回 task_list**:目前 269 是 Open;13 關閉時是否回 echo-only /
   0x0201 / timeout 由 `find_active_act_type` 容錯(三種都當關閉)。13 真關閉時的回應形狀未在本次坐實。
3. **碎片即時現量的可靠來源**(0x0402 哪個 evt 帶 100022)未找到;目前靠 count(accrued)規劃,夠用。
4. **dashboard 預覽**:`control_panel/routes_relic_sprint.py::_rounds_view` 仍用舊模型(取 `tasks[0..3]`
   當 4 輪),需改成讀 `read_sprint` 新增的 `rounds`(已提供正確的 per-round status / small_group_id)。
   本次 recon 邊界禁改 control_panel,留待後續。

## anti-cheat / 風險

`relic_level_up` + 衝刺 count 全 server-authoritative,無 client 計算 / 無偽造面(同 `RELIC_ALLOC_RECON.md`
乾淨 verdict)。唯一限制:同帳號異地登入互踢(ws_token 既有)。**唯一真實成本 = 碎片消耗**:在 lv~88
單次升級即 12-14 萬,開 auto 前務必確認願意把碎片投進此活動。

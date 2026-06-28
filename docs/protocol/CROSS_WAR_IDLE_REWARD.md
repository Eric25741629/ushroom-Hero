# 跨服戰 放置獎勵 (cross_war idle reward) — 純 WS 自動領取

LIVE-decoded 2026-06-28 on `emulator-5560` (s1467, CDP 9225), cross-checked
against the client source (`docs/game_client_sources/...index.966f5.js`,
`CrossWarControl` ~line 5843, `CrossWarOutlinePopView` ~line 5859) and the
generic Activity system (`ActivityDefine` line 659, `ActivityControl` line 655).

跨服戰 = 跨服戰 (cross-server war), `ActivityType.CrossWar = 33`, biweekly,
開放窗口大約 週六 10:00 → 週日 22:00。「左下角寶箱」= 放置(掛機)獎勵入口
(`CrosswarMapSceneView/skillPanel/btnOutline` → `CrossWarOutlinePopView`)。

## Module 45 `cross_war` (cmd = 45*256 + sub)

| cmd | dec | name | c2s body | s2c |
|-----|-----|------|----------|-----|
| `0x2d03` | 11523 | `cross_war_idle_reward`（查詢累積）| 空 | `last_time#1`（上次領取 unix ts）, `report_list` |
| **`0x2d04`** | **11524** | **`cross_war_get_idle_reward`（領取）** | **空** | **`new_last_time#1`**（重置後 ts = now）|

其餘 module 45 cmd（場景/戰鬥/轉移等）見子代理研究，本功能不需要。

### 領取 = 直接送 `0x2d04` 空 body（伺服器權威，無前置）

`CrossWarOutlinePopView` 的「領取」鈕只做一件事：
```js
reqCrossWarGetIdelReward = () => netManager.send("cross_war.cross_war_get_idle_reward_c2s", {})
```
回覆只帶 `new_last_time`，客戶端僅 `las_reward_time = new_last_time`。累積量
**伺服器端**用 `(serverTime - last_time)` 算，與場景/連線狀態無關 → **任何已登入連線
（含純-WS phase 的新連線）皆可直接領，不需先進跨服場景**。

失敗（活動關閉 / 未參戰）回 `0x0201`；完全休眠的活動可能**不回任何 frame**（call 逾時）。
兩者都當 benign skip。

### 累積模型
`amount = ratePerMin * floor(min(serverTime - last_time, CAP) / 60)`，
`CAP = 28800s = 8h`，**溢出丟棄**，rate 依戰力分級。輸 PvP 會把 `last_time` 往後推
（吃掉累積）。實證：456000 金幣 ÷ 950/分 = 480 分 = 8h。
→ 8h 上限決定「至少每 8h 領一次」才不溢出；本 bot 採 **每 4h 領一次**（留 headroom，領更勤無損失，只是每次拿少一點）。

## 開放窗口判斷 = `act_list`（伺服器權威，無硬編日期）

| cmd | dec | name | c2s body | s2c |
|-----|-----|------|----------|-----|
| `0x180c` | 6156 | `act_list`（module 24 `act`）| 空 | `activities#1`: repeated `p_activity` |

`p_activity`（live 解出）：

| field | 意義 |
|-------|------|
| `#1` | activity id |
| `#2` | **type**（跨服戰 = 33）|
| `#3` | round（biweekly 期數）|
| `#5` | **state**（Null=0 / Preview=1 / **Open=2** / EndShow=3）|
| `#6` | start_time（unix）|
| `#7` | end_time（unix）|
| `#8` | repeated 期程 `{phase, start, end}` |
| `#9` | repeated 獎勵設定 |

**開放 = 找 `type==33` 的 entry 且 `state==2`。** 不硬編兩週錨點 → 不會因伺服器排程漂移而漏領。

## 自動化（`ws_token/xwar_idle.py` + runner gate）

- `read_window(client)` → 送 `0x180c` → `parse_act_list` → `CrossWarWindow.is_open`。
- `claim_idle(client)` → `call_for(0x2d04, b"", expect_cmds=(0x2d04, 0x0201))` → `ClaimResult`。
- `claim_if_due(client, device, ...)` gate（per-device `ws_state/<device>.json` ledger
  `xwar_idle = {last_attempt_ts, last_success_ts, last_new_time}`）：
  1. 距 `last_attempt_ts` < 4h（`MIN_INTERVAL_S`）→ skip（純本地，不發任何封包；把 off-window chatter 壓到每 4h 一次）。
  2. `read_window`；非 Open → skip（記 `last_attempt_ts`）。
  3. `claim_idle`；成功記 `last_success_ts`/`last_new_time`。
  - timeout / `0x0201` → benign skip，不記 success。
- 接線：`runner._run_xwar_idle` + `run_device(xwar_idle_enabled=...)`；
  caller `game_actions/ws_phase.py` 讀 `cfg.ws_token.xwar_idle`、
  `runtime_services/ws_runner_service.py` 啟用才入 extra_kwargs；
  設定 `config_manager` `ws_token.xwar_idle`（預設 False）；
  儀表板「活動」頁 `WS_EXTRA_FIELDS` slot `xwar`（純宣告，無 bespoke JS）。

> ponytail: 窗口末端 ≤4h 殘餘可能因 4h 節流落在關閉後而未領（每兩週至多漏一截）；
> 升級路徑＝`end_ts` 距今 < 4h 時放寬節流補領一次。先不做。

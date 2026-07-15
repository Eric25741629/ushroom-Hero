# 切磋 (Solo PVP / 莊園切磋) 協議 RECON

> Live-verified on 小寶 (`7fe98fc6`, web_h5, CDP 9226), 2026-06-24。
> 角色 id = `89562953025122`，對手 = `89565100511322`「不講武德偷襲」。
> 抓包工具：`tools/probe_qiecuo.py`（CDP attach + WS ring probe + emit('click')）。
> 用戶端原始碼佐證：`docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`。

## 一句話

切磋 = **solo PVP**（proto family `solo`，module 36 / `0x24`）。伺服器發
`seed + 雙方完整陣容`，用戶端的 `battleMain` 跑**確定性模擬**算出贏家，再回報 winner。
勝負不在任何封包裡，只有引擎算得出來；偽造被 client `checkCheat()` + server 驗算雙重擋下。

## 入口

- 玩家檔案卡 `RoleNoticeView` 內的「切磋」按鈕。
  - cocos path: `/UIRoot/NormalView/RoleNoticeView/root/content/btnSolo`（label = 切磋）
  - `emit('click', node)` 即可觸發（注意 find 路徑要去掉場景根 `launch`）。
- 結果視窗：`FarmPvpResultView`（`ui/module/plant/FarmPvpResultView`），顯示「勝利／失敗」。
- 每日上限：`farm_pvp_quantity = 10`（ConfigGlobal）。

## cmd ids（用戶端 cmd map 確認）

| proto | cmd | hex |
|---|---|---|
| `solo.solo_start_c2s` | 9217 | `0x2401` |
| `solo.solo_result_c2s` | 9218 | `0x2402` |
| `solo.solo_video_c2s` | 9219 | `0x2403` |
| `solo.solo_video_share_c2s` | 9220 | `0x2404` |

cmd = module*256 + N，module 36 (`0x24`)。

## 封包流程（live 捕捉 + protobuf 解碼）

```
tx 0x2401 solo_start_c2s  { f1 target_id }
rx 0x2401 solo_start_s2c  { f1 code=0, f2 target_id, f3 vid, f4 seed, f5 陣容A(~2KB), f6 陣容B(~1.7KB) }
   -> 用戶端 battleMain 用 (seed, 陣容A, 陣容B) 跑確定性模擬，得出 winner
tx 0x2402 solo_result_c2s { f1 vid, f2 winner(role_id) }
rx 0x2402 solo_result_s2c { f1 vid, f2 winner, f3 陣容A, f4 陣容B, f5 (~66B) }
```

用戶端方法（原始碼）：
```js
reqSoloStart(target_id, reopenQueue) => netManager.send("solo.solo_start_c2s", {target_id})
reqSoloResult(vid, winner)          => netManager.send("solo.solo_result_c2s", {vid, winner})
```

實測 body：
- `0x2401 tx`：`{1: 89565100511322}`（對手）
- `0x2401 rx`：`{1:0, 2:89565100511322, 3:1830(vid), 4:1048776742(seed), 5:陣容, 6:陣容}`
- `0x2402 tx`：`{1:1832(vid), 2:89562953025122(winner=自己→我贏)}`

陣容 sub-message（f5/f6）內含：role_id、name、attribs(屬性 k-v list)、裝備/技能 list、戰力等。

## 勝負怎麼算（官方 headless 引擎）

**關鍵：不是邊播動畫邊算。** `PvpControl.on_solo_start_s2c` 一收到
`seed + atk_data + def_data`，立刻用 **`BattleMainServer`（無圖）** 跑完整場，
算出 `result` 後馬上 `reqSoloResult`；畫面上的 `battleMain` 之後才播動畫。

```js
// PvpControl.on_solo_start_s2c（index.966f5.js）
const data = new BattleData();
data.chapterId = 120001;
data.chapterType = ChapterType.RoleSolo; // 12
data.seed = e.seed;
data.playerList[1] = new PlayerData(1);  // atk
data.playerList[2] = new PlayerData(2);  // def
setPlayerList(e.atk_data, data.playerList[1]);
setPlayerList(e.def_data, data.playerList[2]);

const sim = new BattleMainServer(e.seed);
for (sim.start(data); sim.runState == RunState.Running; ) sim.update(sim.frameTime);
// result: 0 = atk 勝, 非 0 = def 勝
this.reqSoloResult(e.vid, 0 == sim.result ? e.atk_data.id : e.def_data.id);
```

Live-verified（5554 閃電, CDP 9230, 2026-07-16）::

| 項目 | 值 |
|---|---|
| atk | 下不維力炸醬麵 `89555436834913` power=178,588,325 |
| def | 菇單老人 `89565100511322` power=268,731,412 |
| seed | `1090392521` / vid `32` |
| sim | 49 frames, ~5ms, **deterministic**（重跑 3 次一致） |
| winner | atk（戰力較低仍可贏 — 不是比戰力） |

RNG / 定點數：

- `FixRandom.seed = seed`；`randomInt(0, 10000)` 決定 miss/crit/block 等
- `FixMath.round(x) = floor/ceil(x*10000 ± 0.5)/10000`；`roundInt = floor(round(x))`
- `frameTime = 0.033`（約 30 tick/s）

### 傷害公式（`HurtUtil.ts`，摘要）

普通攻擊 `normalHurt(attacker, target, isCrit, ...)`：

```
raw = max(att - def * (1 + def_coe), 1)
raw = roundInt(raw * att_dam * (1 - att_resist'))   # resist 經 armor/block 修正
raw = calHurt(raw, target, attacker)                # * (1+pve_dam) * (1-resist) * (1-pve_resist)
if crit: raw = roundInt(raw * max(1.5, crit_dam / max(0.5, crit_def)))
return max(1, raw)
```

命中表 `checkHit`（`randomInt(0,10000)` 落帶）：

```
miss_rate = f(miss - hit) 再 clamp（PVP 有 battle_up_limit）
crit_rate = max(crit_rate - ignore_crit_rate, 0)
bands: Miss | Normal | Crit
```

穿甲/格擋 `calArmorAndBlock` 同樣用 seed RNG。

完整 tick 模擬還包含：技能 CD/能量、Buff 樹、寵物/飛寵/精靈、`injuryReduce` 等 —
**無法用幾條公式手算勝負**，必須跑整套 `BattleMainServer`。

### 為什麼不能偽造 winner

- `solo_result` 只帶 `{vid, winner}`；server 可自行用同一 seed+陣容重算驗證
- `toResult` / `checkCheat()`：屬性完整性（`MetaAttrib._calculateValue(32^_checkValue)`）
- 亂報 winner 會被擋

**結論：真實 winner 必須跑 `BattleMainServer`（或同等引擎）。**

## 本地模擬（推薦）

不要純 Python 重寫引擎。在已登入的 H5 頁用 CDP 呼叫官方模組：

```bash
# 5554 閃電 web_debug_port=9230；需先開好對方 RoleNoticeView
python tools/solo_battle_sim.py spar --port 9230 --times 3
python tools/solo_battle_sim.py wait --port 9230          # 等手動點切磋
python tools/solo_battle_sim.py replay last_solo_start.json --port 9230
```

實作：`System.import('chunks:///_virtual/BattleMainServer.ts')` +
`BattleData` / `BattleDataFill.setPlayerList`，與 client 同路徑。

開發者 UI：`BattleSimulateView` 吃 JSON
`{chapter_id, chapter_type, random_seed, roles_left[], roles_right[]}`，
同樣 `new BattleMainServer(seed)` 迴圈 `update`。

## 可行的自動化形態（用戶端驅動，合法）

1. 取對手：好友 `0x0f02` friend_list（`utils/web_game_api.parse_friend_list`）
2. 每場：`emit btnSolo` → client 自動 headless 模擬 + 送 `solo_result` → 關 `FarmPvpResultView`
3. 每日上限 `farm_pvp_quantity = 10`

省動畫：BattleHub 加速 + 偵測結果窗立即關閉。勝負本身已在開場 ~10ms 內算完。

## 重現

```bash
PYTHONIOENCODING=utf-8 python tools/probe_qiecuo.py find
python tools/solo_battle_sim.py spar --port 9230 --times 3
python tools/probe_qiecuo.py shot tools/_state.png
```

## 相關

- 競技場同引擎：[[ARENA_BATTLE_RECON.md]]（`ChapterType.Arena=5`, `chapterId=50001`）
- 本地模擬工具：`tools/solo_battle_sim.py`、`tools/arena_battle_sim.py`
- 源碼：`docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`
  （`PvpControl` / `BattleMainServer` / `HurtUtil` / `FixRandom` / `BattleDataFill`）
- 副本同模式：`ws_token/dungeon.py`（seed + operators 回放）
- 友列表 parser：`utils/web_game_api.parse_friend_list`（0x0f02）

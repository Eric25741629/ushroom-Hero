# 競技場 (Arena PVP) 戰鬥計算 RECON

> Live-verified on 閃電 (`emulator-5554`, web_h5, CDP 9230), 2026-07-16。  
> 與切磋同一套 **client-side 確定性模擬**（`BattleMainServer`），僅 protocol / chapter 參數不同。  
> 源碼：`docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`  
> （`PvpControl.on_arena_combat_s2c` / `BattleMainServer` / `HurtUtil`）。

## 一句話

競技場挑戰 = server 發 `seed + 雙方完整陣容`，client 用 **`BattleMainServer` 無圖 headless** 算出 winner，再回報 `wid`。畫面動畫只是回放；勝負不是比戰力。

## 與切磋的對照

| | 切磋 (RoleSolo) | 競技場 (Arena) |
|---|---|---|
| proto family | `solo` module `0x24` | `arena` module `0x14` |
| start cmd | `0x2401` solo_start | `0x1403` arena_combat (5123) |
| result cmd | `0x2402` solo_result | `0x1404` arena_result (5124) |
| ChapterType | `RoleSolo = 12` | `Arena = 5` |
| chapterId | `120001` | `50001` |
| start 參數 | `target_id` | `eid`（對手角色 id） |
| result 參數 | `{vid, winner}` | `{vid, wid}`（winner id） |
| result s2c | 回陣容 + winner | 回分數/排名變動 + `is_win` |
| 入口 UI | `RoleNoticeView/btnSolo` | `PvpChalleneView/.../btnGo` |
| 結果 UI | `FarmPvpResultView` | `PvpResultView` |

schema 見 [[ARENA_PROTO_SCHEMA.json]]。

## 封包流程

```
tx 0x1403 arena_combat_c2s  { f1 eid }
rx 0x1403 arena_combat_s2c  {
  f1 code=0, f2 eid, f3 vid, f4 seed,
  f5 atk_data, f6 def_data
}
  -> BattleMainServer(seed) headless 模擬
tx 0x1404 arena_result_c2s  { f1 vid, f2 wid }   // wid = winner role_id
rx 0x1404 arena_result_s2c  {
  is_win, my_score, my_rank, my_score_change,
  e_name, e_rank, e_score, e_score_change, e_head
}
```

用戶端：

```js
reqArenaCombat(eid)  => send("arena.arena_combat_c2s", {eid})
reqArenaResult(vid, wid) => send("arena.arena_result_c2s", {vid, wid})
```

## 勝負怎麼算（官方 headless）

`PvpControl.on_arena_combat_s2c`：

```js
const data = new BattleData();
data.chapterId = 50001;
data.chapterType = ChapterType.Arena; // 5
data.seed = e.seed;
data.playerList[1] = new PlayerData(1); // atk = 自己
data.playerList[2] = new PlayerData(2); // def = 對手
setPlayerList(e.atk_data, data.playerList[1]);
setPlayerList(e.def_data, data.playerList[2]);

const sim = new BattleMainServer(e.seed);
for (sim.start(data); sim.runState == RunState.Running; )
  sim.update(sim.frameTime); // frameTime = 0.033

// result: 0 = atk 勝, 非 0 = def 勝
this.reqArenaResult(e.vid, 0 == sim.result ? e.atk_data.id : e.def_data.id);
```

之後才把 `battleData` 塞進 `chapterDataCache` 播動畫（`battleCheckout=2`）。

### 傷害 / RNG（與切磋共用）

- `FixRandom.seed = seed`；`randomInt(0,10000)` 決定 miss/crit/block
- `FixMath.round` 四位定點
- `HurtUtil.normalHurt` / `calHurt` / `checkHit` / `calArmorAndBlock`
- 細節見 [[SOLO_PVP_RECON.md]]「傷害公式」

**不能只比戰力、不能只傳雙方 id。** 必須 `seed + atk_data + def_data` 整包。

## Live 驗證（5554, 2026-07-16）

| 項目 | 值 |
|---|---|
| atk | 下不維力炸醬麵 `89555436834913` power=178,588,325 |
| def | 瞎忙中的龍菇 `89612345148686` power=256,736,492 |
| eid | `89612345148686` |
| vid | `89616640843430` |
| seed | `15381` |
| 本地 sim | 364 frames, ~32ms × 3 次，**deterministic** |
| sim winner | def（result=1） |
| official `is_win` | `0`（失敗） |
| **match** | **True**（本地結果與 server 結算一致） |

證據檔：`tools/_tmp_battle_extract/arena_test_result.json`。

## 本地模擬

在已登入 H5 頁（任一台有完整 client 的 CDP）呼叫官方模組：

```bash
# 點 PvpChalleneView 列表第 0 個對手並重算
python tools/arena_battle_sim.py spar --port 9230 --times 3

# 等手動點挑戰
python tools/arena_battle_sim.py wait --port 9230

# 用已存的 arena_combat_s2c JSON 重算
python tools/arena_battle_sim.py replay tools/_tmp_battle_extract/last_arena_combat.json --port 9230
```

### A 算 / B 打 分工（與切磋相同）

```
B: arena_combat_c2s(eid) → 收到 seed/vid/atk/def
B → A: 整包（不能只傳 id）
A: BattleMainServer 算出 winner（wid）
A → B: wid
B: arena_result_c2s { vid, wid }   // 必須用 B 的連線送
```

- A = 任一台已載入遊戲的 H5（當計算機，不需是同一帳號）
- B = 發起挑戰的帳號連線（ADB / H5 / 無畫面皆可，只要能收發 WS）
- 亂報 wid 會被 server 用同一 seed+陣容驗算擋下

## 入口 UI

- 主介面 `btnPvp` → `PvpMainView`
- 挑戰列表：`PvpChalleneView`（client 拼字缺 i）
- 開戰按鈕：`PvpChalleneView/content/scrollPvp/view/content/<i>/btnGo`
- 注意：`PvpMainView/.../btnChallenge` 是「打開挑戰列表/刷新」，**不是**開戰

## 相關

- 切磋：[[SOLO_PVP_RECON.md]]
- schema：[[ARENA_PROTO_SCHEMA.json]]
- 工具：`tools/arena_battle_sim.py`、`tools/solo_battle_sim.py`
- 通用引擎：`BattleMainServer` / `BattleDataFill` / `HurtUtil` / `FixRandom`

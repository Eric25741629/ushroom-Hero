# 戰鬥本地模擬架構（切磋 / 競技場 / 通用）

> 2026-07-16 live-verified on 閃電 `emulator-5554` CDP 9230。  
> 相關：[[SOLO_PVP_RECON.md]]、[[ARENA_BATTLE_RECON.md]]。

## 核心結論

多數 PVP / 部分副本的勝負由 **用戶端 `BattleMainServer` 確定性模擬** 算出，再回報 server：

```
server: seed + 雙方（或我方+怪）完整戰鬥資料
client: BattleMainServer(seed).start(data) → while Running: update(0.033)
client: result_c2s { vid, winner/wid/... }
server: 用同一資料驗算（偽造會被擋）
```

- **不需要戰鬥畫面**（`BattleMainServer` = 無圖 headless）  
- **需要已載入的遊戲 client runtime**（JS 引擎 + config 表）  
- 任意已登入 H5（含新號 / 小號 / headless Chrome）都可當 **計算機 A**  
- **發起戰鬥的連線 B** 才負責 `start` / `result` 回傳  

## A 算 / B 打

```
B: xxx_combat/start_c2s → 收到 seed, vid, atk_data, def_data, ...
B → A: 整包資料（不能只傳 role id）
A: System.import(BattleMainServer) 算出 winner
A → B: winner
B: xxx_result_c2s { vid, winner }
```

| 角色 | 需要 | 不需要 |
|------|------|--------|
| A 計算機 | 任一已載入 H5 + CDP | 同一帳號、有畫面 |
| B 實戰 | 該帳號 WS 連線 | 自己跑引擎 |

注意：

- **純本地算**（不 start）→ 不用回傳  
- **已 start** → 次數/門票通常已扣；不回傳 ≠ 悔棋，多半超時/異常  
- 輸了亂報 winner 會被驗算擋  

## 已驗證模式

| 模式 | ChapterType | chapterId | start | result | 工具 |
|------|-------------|-----------|-------|--------|------|
| 切磋 RoleSolo | 12 | 120001 | `solo.solo_start` 0x2401 | `{vid,winner}` 0x2402 | `tools/solo_battle_sim.py` |
| 競技場 Arena | 5 | 50001 | `arena.arena_combat` 0x1403 | `{vid,wid}` 0x1404 | `tools/arena_battle_sim.py` / `battle_calc.pure_ws_arena` |
| 萬神 rogue | 37 | 50001 | `rogue_main_combat` 0x4c04 | `{result,precent}` 0x4c05 | 小寶 CDP 9226 live 2026-07-17 |

兩邊都 live 驗證：**重跑 N 次 deterministic，且與官方 result/is_win 一致**。

### pure WS + B 算（競技場，2026-07-17 小寶 live）

```
A pure WS: arena_combat_c2s → body(seed+atk+def raw protobuf)
B 全新瀏覽器（無 profile）: decode + BattleMainServer → wid
A pure WS: arena_result_c2s {vid, wid} → server is_win / score_change
```

- **B 預設 ephemeral**：`chromium.launch` / Chrome channel，**不帶 user_data_dir、不登入**
- 同帳 pure WS 會踢實戰 H5；B 是另一個乾淨 process，互不干擾
- 可改 `global.battle_calc.mode=cdp` 連既有 CDP（需登入過的頁）
- 裝置開關：`arena_battle_mode=pure_ws`，`arena_fight_gap_sec≥7`
- 手動：`python -m battle_calc.pure_ws_arena --device 7fe98fc6 --fights 3`

## Runtime 需求

`BattleMainServer` 依賴整包 client（`BattleDataFill`、技能/Buff、`FixMath`/`FixRandom`、`config*`）。

| 方案 | 可行 |
|------|------|
| 有畫面 H5 + CDP | ✅ |
| Headless Chrome 載入同一 H5 | ✅ |
| 新帳號 H5 當 A | ✅ |
| 純 Python 重寫 | ❌ 不現實 |
| 不載入遊戲 | ❌ |

## 工具

```bash
# 切磋
python tools/solo_battle_sim.py spar --port 9230 --times 3
python tools/solo_battle_sim.py replay last_solo_start.json --port 9230

# 競技場
python tools/arena_battle_sim.py spar --port 9230 --times 3
python tools/arena_battle_sim.py replay last_arena_combat.json --port 9230
```

## 其他玩法擴充檢查清單

新活動若要「模擬縮時」：

1. 找 `PvpControl` / `*Control` 裡是否 `new BattleMainServer(seed)` + `while Running update`  
2. 對照 `ChapterType` / `chapterId`  
3. 抓 start s2c 是否含 `seed` + 完整單位資料  
4. result 回報欄位（winner / operators / percent…）  
5. **若是即時掛機、無 seed 回報、或 server 權威計時** → 不能用本架構跳過時長  

### 已確認例外：穿越深淵之門

見 [[HELL_GATE_RECON.md]]：

- `ChapterType.WorldBoss = 13`，`maxChapterTime = 600`（真 10 分鐘 DPS）
- **不能**用 `BattleMainServer` 秒算 winner
- **可以** client 注入 `timeScale` + `chapter.onUpdate` 把時長壓到數十秒（live ~30×）
- 結算是傷害回報，不是 PVP winner

### web_h5 戰鬥加速（官方管線）

- 官方廣告 2x：`ChapterDataCache.updateSpeedScale(2)` → `battleMain.timeScale`
- bot 預設 4x：`utils/battle_speed.py`，設定鍵 `battle_speed_scale`（1=關，上限 10）
- 掛載點：`device_wrapper` 開頁後 + 每次 screenshot 冪等 re-arm

## A 打 / B 算 / A 回（進行中）

實戰帳號 **A** 開打與回傳；**B** 專職計算機（任意已載入 H5，可免洗 / 可被踢線後的同帳頁）。

```
A: start_c2s → 收到 seed/vid/atk/def（整包 raw 或 decoded）
A → B: combat body bytes 或 decoded payload（不可只傳 role id）
B: BattleMainServer 算出 winner
B → A: winner / result / precent
A: result_c2s
```

| 狀態 | 說明 |
|------|------|
| 競技場 pure_ws | ✅ 小寶 live + 已接 bot：`ws_token.arena_fight` / runner task `arena` / dashboard `pure_ws` |
| 競技場 local_sim | ✅ H5 攔截 + 本頁 sim |
| 萬神 local_sim | ✅ 小寶 live 5 次 deterministic 對齊官方 |
| remote_calc HTTP | 骨架 `battle_calc.server`；需常駐免洗 B |
| dashboard | `arena_battle_mode` + `arena_fight_gap_sec`（≥7） |

模組：`battle_calc/`、`ws_token/arena.py`、`game_actions/arena_battle.py`。
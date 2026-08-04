# 穿越深淵之門（地獄之門 / WorldBoss）RECON

> Live-verified on 閃電 `emulator-5554` CDP 9230, 2026-07-16。  
> 與「深淵之門 dungeon type=2」（`ws_token/dungeon.py`）**不是同一副本**。

## 一句話

穿越深淵之門 = **`ChapterType.WorldBoss (13)`** 的 **10 分鐘即時 DPS 戰**（`maxChapterTime=600`），  
**不是** 切磋/競技場那種 `BattleMainServer` 秒算 winner。  
正式純 WS 流程只使用官方引擎的 `timeScale` 與正常 frame pacing，結算回報的是
**傷害**，不是勝負 id；不得直接改 `chapterTime` 或手動呼叫 `onUpdate`。

## 與切磋 / 競技場對照

| | 切磋 / 競技場 | 穿越深淵之門 |
|---|---|---|
| 引擎 | `BattleMainServer` headless 秒算 | 即時 `battleMain` + `ChapterWorldBoss` |
| 時長 | 毫秒～數十 ms | **600 秒** wall-clock（可加速） |
| 回報 | `winner` / `wid` | `reqDungeonBattleResult` + **傷害/血量** |
| seed 秒算 | ✅ | ❌（`battleMain.data.seed` 常為 null） |
| 縮時手段 | 不需要 | 官方 `timeScale` + 每輪正常 `sim.update(frameTime)` |

## 入口 UI

- 副本列表 → **穿越深淵之門**
- 詳情：`WorldBossEnterView`
- 開戰：`WorldBossEnterView/content/btnGo`（label「挑戰」）
- 次數：詳情頁挑戰鈕旁數字（live 見 ×2）
- 窗口：每小時 **:00–:20**（與 `sleep_service` / ADB `hell_door` 一致）

client 開戰：

```js
// WorldBossEnterView
IS(DungeonControl).reqDungeonBattleMoreStart(CHAPTER_TYPE_WORLDBOSS, 1)
battleMain.enterChapter(0, true)
```

## 執行期狀態（live）

| 欄位 | 值 |
|------|-----|
| `chapterType` | **13** (`WorldBoss`) |
| `chapterId` | `100`（進場後；cdc 另有 `33858` 等） |
| `maxChapterTime` | **600** |
| `timeModel` | true |
| `bossList` | 長度 100 的 boss 配置列 |
| `selfRank` | 傷害排名（live 見 11） |
| `battleFlag` | `LIMIT_SKILL \| OPEN_GRAPHIC`（CC 版 init） |
| 標題 UI | `BattleHub` / `guildgvebossPanel` 文字「穿越深渊之门」 |

進場當下曾短暫出現 `battleData.seed=12007`，隨後 `battleData` 清空；**主迴圈不靠 PVP 式 seed 回放**。

## 協議（部分）

| 方向 | cmd | 備註 |
|------|-----|------|
| rx | `0x0e0d` (3597) ~10KB | 進場後大包（dungeon 模組） |
| rx | `0x0e10` (3600) | 進場後 |
| 結算 | `dungeon_battle_result_c2s` (`3592`) | `manual_operators#4=0`，`args#6=[{k:1,v:hpNum},{k:4,v:lastHurtNum}]` |

ADB 舊流程（`battle/special.py::hell_door`）等 OCR「討伐結束」「恭喜獲得」，對應 client 時間到 / 結束後的 UI。

## 純 WS 實作（已落地）

`ws_token/hellgate.py` 將流程拆成兩條通道：

1. A 帳號只用 WebSocket 查 3594、進場 3597，並在結算時送 3592：
   `manual_operators=0`、`args=[{k:1,v:boss.hpNum},{k:4,v:boss.lastHurtNum}]`。
2. 進場回傳的完整 3597 body 交給獨立 B 頁的官方 `BattleMainServer`，依
   `DungeonControl.on_dungeon_battle_more_start_s2c` 建立所有 `p_battle_role`，
   正常跑完 600 秒邏輯後取出 WorldBoss 的血量/傷害欄位。結算 ack 可能是
   send-only；只有結算後 3594 的 `times`/`my_hurt` 更新才標記成功。

B 頁只作本地計算，不使用 ADB、UI 點擊或 B 頁的遊戲登入；既有 CDP 會優先用
raw CDP attach，避免部分 Chrome 的 Playwright multi-target attach 卡住。事件關閉、
次數用完或沒有可用 CDP 時不送 3597；進場後計算失敗則送一次失敗結算，避免帳號
停留在待結算戰鬥。

同帳號由 H5 session 切換到 WS session 時，伺服器 mutation gate 需要短暫完成
handoff；剛登入的連線會等待最多 8 秒才查 3594／送 3597，避免讀取成功後進場仍回 173。

WS-first 的 web_h5 裝置會從 `enable_hellgate` 與 `web_debug_port` 自動組態；需要
手動調整時可在 `ws_token.hellgate` 設定 `b_mode`、`cdp_port`、`max_frames`。

## 時鐘邏輯（`ChapterWorldBoss.onUpdate`）

```js
if (player.isDead) { over=true; toResult(0); }
if (timeModel && chapterTime <= 0) { over=true; toResult(0, true); }
chapterTime = FixMath.round(chapterTime - dt);
```

結束後 `ChapterWorldBossCC.endResult` → `reqDungeonBattleResult`，帶 **boss 血量進度 + 總傷害**。

## 能否「模擬縮時」？

### 不能用的方式

- **`BattleMainServer` 秒算 winner**（PVP 架構）→ 本副本不是比 winner
- **不開戰只估** → 需要進場後的即時傷害累積
- **手動呼叫 `chapter.onUpdate(1.0)` 或用超大 dt 快轉** → 不符合正式流程

### 正式縮時

由 B 端官方 `BattleMainServer` 維持 `timeScale=2`，每輪只傳入官方
`frameTime` 並依真實時間 pacing。官方引擎完成或玩家死亡前，不讀取或改寫
`chapterTime`；超過 frame／時間上限則回傳未完成並走失敗結算。

| 風險 | 說明 |
|------|------|
| 傷害可能偏低 | 只允許官方 frame pacing；未完成時不得估算或補填傷害 |
| 窗口/次數 | 仍耗挑戰次數；窗口外點挑戰可能 disabled |

**正式自動化**：進場後等待 B 端官方引擎完成；只有拿到官方
`boss.hpNum` / `boss.lastHurtNum` 且 3594 狀態更新時才回報成功。

## 舊 UI fallback（非純 WS）

1. `hell_door` web_h5 路徑：導航 `WorldBossEnterView` → 點 `btnGo`
2. 偵測 `chapterType==13` 後注入加速 interval（可調 20–50×）
3. 偵測 `chapter.over` 或結果 UI → 點領獎 / 關閉
4. **不要**走 `solo_battle_sim` / `arena_battle_sim` 那套路徑

可選工具名：`tools/hell_gate_speed.py`（待寫；proof 腳本 `_tmp_hell_force_speed.py`）。

## 證據檔

- `tools/_tmp_battle_extract/hell_fight_meta.json`
- `tools/_tmp_battle_extract/hell_frames.json`
- `tools/_tmp_battle_extract/hell_force_speed.json`
- `tools/_tmp_battle_extract/hell_fight.png` / `hell_force_speed.png`
- client 切片：`wb_ChapterWorldBoss.ts` / `wb_ChapterWorldBossCC.ts` / `wb_WorldBossEnterView.ts`

## 相關

- 通用模擬架構：[[BATTLE_SIM_ARCHITECTURE.md]]（本副本屬「不可 PVP 秒算」例外）
- ADB：`battle/special.py::hell_door`
- 勿混淆：`ws_token/dungeon.py` TYPE_ABYSS=2 深淵之門（掃蕩/battle 另一套）

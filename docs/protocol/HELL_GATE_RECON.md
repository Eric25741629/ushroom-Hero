# 穿越深淵之門（地獄之門 / WorldBoss）RECON

> Live-verified on 閃電 `emulator-5554` CDP 9230, 2026-07-16。  
> 與「深淵之門 dungeon type=2」（`ws_token/dungeon.py`）**不是同一副本**。

## 一句話

穿越深淵之門 = **`ChapterType.WorldBoss (13)`** 的 **10 分鐘即時 DPS 戰**（`maxChapterTime=600`），  
**不是** 切磋/競技場那種 `BattleMainServer` 秒算 winner。  
可透過 client 側 **加速 `chapterTime` / `timeScale`** 把 10 分鐘壓到數十秒，結算回報的是 **傷害**，不是勝負 id。

## 與切磋 / 競技場對照

| | 切磋 / 競技場 | 穿越深淵之門 |
|---|---|---|
| 引擎 | `BattleMainServer` headless 秒算 | 即時 `battleMain` + `ChapterWorldBoss` |
| 時長 | 毫秒～數十 ms | **600 秒** wall-clock（可加速） |
| 回報 | `winner` / `wid` | `reqDungeonBattleResult` + **傷害/血量** |
| seed 秒算 | ✅ | ❌（`battleMain.data.seed` 常為 null） |
| 縮時手段 | 不需要 | `timeScale` + `chapter.onUpdate` 注入 |

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
| 結算 | `DungeonControl.reqDungeonBattleResult(WorldBoss, chapterId, result, …)` | 帶 collects + `[{k:1,v:hpNum},{k:4,v:hurtNum}]` |

ADB 舊流程（`battle/special.py::hell_door`）等 OCR「討伐結束」「恭喜獲得」，對應 client 時間到 / 結束後的 UI。

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
- **只改 `timeScale` 一次** → 會被蓋回 1，幾乎無效

### 可行縮時（live 已證）

在戰鬥中每 50ms：

```js
battleMain.timeScale = 50;
battleMain.chapter.onUpdate(1.0);  // 直接扣 chapterTime + 推進章節
battleMain.update(0.033);
```

實測：剩餘 **~447s → 0** 約 **15 秒 wall-clock**（約 30×），`over=true`。

| 風險 | 說明 |
|------|------|
| 傷害可能偏低 | 若只快轉時間、DPS tick 沒等比跟上，結算 hurt 會變差 |
| 需邊打邊加速 | 應同時 `timeScale` + `update` 讓戰鬥邏輯也加速 |
| 窗口/次數 | 仍耗挑戰次數；窗口外點挑戰可能 disabled |

**建議自動化**：進場後掛加速器 → 等 `over` /「討伐結束」→ 領「恭喜獲得」，取代死等 10 分鐘。

## 最小實作建議

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

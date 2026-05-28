# 家族烈焰山洞 / 副本天梯 / 萬神試煉 — 分析與實作藍圖

第一輪結構分析 (2026-05-20, CDP 9230, 5554 web_h5 帳號)
舊版 ADB 流程位置：`battle/cloud.py`、`battle/weekly_trials.py`、`battle/store.py`

---

## 1. 家族 tab (`content/6` → GuildMainView)

### Cocos 結構（淺）
```
/UIRoot/NormalView/MainView/container/GuildMainView/GuildMainView/
  ├── load            (loading mask, active=false)
  ├── load1           (map loading bg)
  └── GuildMapSceneView   ← 真正的家族場景
      ├── btnChatRoot     聊天按鈕
      ├── nodeTreasure    家族搶寶倒計時 (60s/輪, 5/輪上限)
      ├── nodeHandle      搖桿 (drfb_ui_yaogan*) — 角色移動
      ├── Infobg          家族 logo + 「显示其他家族成员」toggle
      └── riceParty       米宴會
```

### 關鍵發現
- 地圖建築物（家族商店 / 任務 / 搶寶 / **烈焰山洞**）都是 sprite texture，沒有 cocos label
- 進入建築物的方式是「角色 walk + collision」，不是 cocos button 直點
- `emit('click', node)` 無法直接觸發建築物進入
- 替代方案：直接 send `guild_area_enter_c2s` 或建築物對應 cmd

---

## 2. 烈焰山洞 = `ChapterType.Seven` 七章副本

### 證據
- `ChapterSevenCC.ts` 播放 `audioMgr.playMusic("bgm_lieyanshandong")`
- `bgm_lieyanshandong` 為「烈焰山洞」漢語拼音
- `configSeven_trial_chapter` 為設定表（被 `r.Seven` enum 引用）

### 系列 cmd
- `dungeon_battle_start_c2s` / `dungeon_battle_result_c2s` (通用)
- `dungeon_fate_daily_reward_c2s` （命運每日獎勵 — 候選）
- `dungeon_league_solo_get_reward_c2s` （家族 SOLO 每日獎勵 — 候選）

### 用戶語意（"每天要去領取"）
推測 ≠ 戰鬥型副本，而是每日結算後 **領取獎勵**。需 LIVE WS 抓樣本確認 cmd。

### 待驗證
- [ ] 開啟 WS probe，user 進入烈焰山洞按一次「領取」→ 抓 WS 對應 cmd
- [ ] 同步抓取 cmd body 解出領取參數 (chapter_id? area_id? )

---

## 3. 副本 tab (`content/3` → DungeonMainView)

### Cocos 結構
```
/UIRoot/NormalView/MainView/container/DungeonMainView/DungeonMainView/BG/
  ├── bg                   ← 普通難度 (active=true 預設)
  │   ├── title            上方標題
  │   ├── scrollDungeon/view/content/    ← 副本卡片 (4 cells)
  │   └── zyfb_ui_banner
  ├── difficultyDungeon    ← 噩夢難度 (active=false)
  │   ├── title
  │   ├── scrollDungeon
  │   └── zyfb_ui_banner
  └── difficultBtn         ← 普通／噩夢 切換 (有 RedPoint)
```

### scrollDungeon/view/content 4 cells (普通)

每個 cell 有 4 個 node 槽：
| Cell | node1 (主副本)   | node3 (週末事件)   | node4 (週試煉) |
|------|------------------|--------------------|----------------|
| 0    | 金币副本         | 穿越深淵之門       | 云缠天梯试炼    |
| 1    | 突襲神燈小偷     | 决战天山之巅       | 云缠天梯试炼    |
| 2    | 挑戰冰巢龍穴     | 决战天山之巅       | 云缠天梯试炼    |
| 3    | 守衛殘垣古城     | 决战天山之巅       | 云缠天梯试炼    |

每個 nodeX 都有 `btnGoto/Label` (入场/組建隊伍) 可 emit click。

### 噩夢 (`difficultyDungeon`)
- 突襲神燈小偷-噩夢
- 挑戰冰巢龍穴-噩夢
- 金币副本 (難度 4-1)

---

## 4. 天梯試煉 = `云缠天梯试炼`

### 確認
- 副本頁每個 cell 的 node4 都掛 `云缠天梯试炼`
- 舊 ADB 流程 `battle/cloud.py` 已實作: `into_cloud → friend_help → cloud_fighting`
- ADB 入口: 副本 → swipe down 3x → tap (239,752) → OCR find `雲纏天梯試煉`
- Web 等效: 副本 tab → 找 `bg/scrollDungeon/view/content/N/node4/btnGoto` 點擊

### 待做
- [ ] 點 node4 → 抓 enter cmd
- [ ] 確認進入後的 view name + 戰鬥 / 跳過 flow

---

## 5. 萬神試煉 (尚未在 cocos 中定位)

### 可能位置
1. **副本 tab 下方的隱藏 cell**：scrollDungeon 顯示 0-3 cell；ADB 流程要 swipe down 3 次才 OCR 到，可能有 cell 4+ 需要 lazy load
2. **node3 (週末事件)**：`决战天山之巅` 中文意譯不確定，但時段限定 + boss 形式符合萬神試煉特徵
3. **獨立彈窗 / 主頁面 entry**：副本 tab 不在 — 在主頁的某個入口

### 已知 ADB 流程
```python
# battle/weekly_trials.py
副本 tab → swipe down 3x → tap center → OCR `萬神試煉`
→ 7 場循環: 開始 → 開始 → 確定 → tap close → 開始挑戰
   → wait 7s → 跳過 → tap close → tap exit → 結束本局 → 確定
→ buy_god_everyweek (秘寶閣)
```

### 待做
- [ ] LIVE WS 抓「進入萬神試煉」cmd，找對應 view
- [ ] 對比 `决战天山之巅` 是否 = 萬神試煉

---

## 6. 意外彈窗處理機制

### 已知 web_h5 彈窗類型
1. 連線斷線 (ReconnectView)
2. 戰報自動結算
3. 系統公告 / 活動廣告
4. 紅包提示 (chat)
5. 副本鑰匙不足 / 入場券扣除確認
6. 戰鬥失敗結算

### 設計（沿用 cocos_navigator 風格）
```python
def sweep_popups(page, *, max_iter=12) -> int:
    """關閉所有目前可見的 popup 直到主頁面或預期 view。

    優先序（參考 cocos-app-analysis skill）:
       btnClose > close > btnCancel > btnBack > back   (skip btnReturn)
    """
    for _ in range(max_iter):
        closed = _close_topmost_overlay_if_any(page)
        if not closed:
            return _
    return max_iter
```

### 待做
- [ ] 在 `utils/cocos_navigator.py` 加 `sweep_popups()` helper
- [ ] 在 task loop 各 task 開始前後 hook 此 sweep
- [ ] 維護黑名單 (`btnReturn` = 「捲動回頂」不能按)

---

## 7. Web_h5 實作建議分階段

### Phase A — Popup sweep + 副本 navigation
1. `utils/cocos_navigator.sweep_popups()`
2. `utils/cocos_navigator.open_dungeon_tab()` (already-style)
3. `utils/cocos_navigator.click_dungeon_cell(row, col)`

### Phase B — 烈焰山洞 daily claim
1. LIVE WS probe → 抓「點烈焰山洞」cmd → 解 schema
2. `utils/family_volcano.claim_daily(page)`
3. 接入 task loop（每日一次，配 `time_recording`）

### Phase C — 天梯試煉 weekly (web)
1. 副本 tab → click node4 of any cell → enter view name
2. `battle_web/cloud_h5.py` 對應 `battle/cloud.py` flow
3. Hook weekly scheduler

### Phase D — 萬神試煉 weekly (web)
1. 找入口（可能要 swipe scrollDungeon 或在 cell 4+）
2. `battle_web/weekly_trials_h5.py` 7 場循環
3. 7 場後跑 `battle/store.py` 秘寶閣購買（adb / web 通用）

---

## 8. 關鍵 cmd 索引（從 bundle 提取）

```
guild_*                     (~50 個) — 家族整體
  ├── guild_boss_*          家族 BOSS 戰 (sign_up/enter/combat/stage/skill/result/rank)
  ├── guild_treasure_*      家族搶寶 (info/open)
  ├── guild_area_enter_c2s  進入家族子場景 ★
  ├── guild_area_move_c2s   角色移動
  └── guild_area_broadcast_*  區域廣播

dungeon_*                   (~40 個) — 通用副本
  ├── dungeon_battle_start_c2s        進入戰鬥
  ├── dungeon_battle_result_c2s       戰鬥結算
  ├── dungeon_fate_daily_reward_c2s   命運每日獎勵 ★ (烈焰山洞候選)
  ├── dungeon_league_solo_*           家族 SOLO 副本 ★
  ├── dungeon_league_hard_*           家族困難副本
  ├── dungeon_dc_*                    DC 副本
  ├── dungeon_idol_main_info_c2s      偶像主頁
  ├── dungeon_mount_battle_result_c2s 騎乘戰鬥
  └── dungeon_red_c2s                 紅點刷新
```

---

## 9. 下一步行動清單

立即可做（無 LIVE WS）：
1. ✅ 把分析成果 commit 進 `docs/protocol/FAMILY_DUNGEON_ANALYSIS.md`
2. 寫 `utils/cocos_navigator.sweep_popups()` + 測試
3. 寫 `utils/cocos_navigator.open_dungeon_tab()` + 各 cell 點擊

需 LIVE WS 抓樣本：
4. user 點一次「進入烈焰山洞 → 領取」→ 抓 cmd schema
5. user 點 node4 (云缠天梯试炼) → 抓 cmd schema
6. user 找出萬神試煉入口 → 抓 cmd schema

---

關聯：
- `battle/cloud.py` (ADB cloud 流程)
- `battle/weekly_trials.py` (ADB 萬神)
- `battle/store.py` (秘寶閣)
- `utils/cocos_navigator.py` (web_h5 導航)
- `docs/protocol/PAGE_NAVIGATION.md` (頁面切換協議)

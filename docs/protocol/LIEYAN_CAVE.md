# 烈焰山洞 (Family League Solo) — 協議文件

家族頁面 → 中央火紅色建築 = **烈焰山洞**（漢語拼音 `bgm_lieyanshandong` 對應）
是 `dungeon.dungeon_league_solo` 系列副本。每日 4 個寶箱可領（每箱 1/天）。

---

## CMD ID 對照（從 bundle index.966f5.js 抽取）

| cmd_id | name | 用途 |
|--------|------|------|
| **3598 (0x0e0e)** | `dungeon.dungeon_league_solo_info_s2c` | 普通模式 排行榜+box_info |
| **3599 (0x0e0f)** | `dungeon.dungeon_league_solo_get_reward_s2c` | 領取寶箱回應 |
| **3600 (0x0e10)** | `dungeon.dungeon_league_solo_update_box_s2c` | 寶箱狀態更新 |
| **3590 (0x0e06?)** | `dungeon.dungeon_league_hard_info_c2s` | 噩夢模式 (待驗證 id) |
| **3618 (0x0e22?)** | `dungeon.dungeon_league_hard_info_s2c` | 待驗證 |
| (0x0e18) | hard info s2c (live confirmed) | 噩夢狀態 |
| **3593** | `dungeon.dungeon_fate_daily_reward_c2s` | 命運每日獎勵 (試過 error 73 = 與 league_solo 無關) |

---

## c2s 請求

### `dungeon.dungeon_league_solo_info_c2s` (3598 → 0x0e0e)
- payload: `{}`
- 回應 (0x0e0e s2c): 大型 message，包含
  - 多筆 `{f1: type, f2: got_count, f3: chest_limit, f4: ext_str}` 的 box_info 子訊息
  - 排行榜：玩家名 + uid 列表

### `dungeon.dungeon_league_solo_get_reward_c2s` (3599 → 0x0e0f)
- payload: `{type: 1 | 2 | 3 | 4}`
  - **type 1**: 普通模式 普通寶箱
  - **type 2**: 普通模式 稀有寶箱
  - **type 3**: 噩夢模式 普通寶箱
  - **type 4**: 噩夢模式 稀有寶箱
- 成功回應：
  - `0x0402` (`bag_change_s2c`) 251 bytes — 背包項目變化
  - `0x0406` 90 bytes — 另一個背包更新
  - `0x0e10` (`update_box_s2c`) 10-12 bytes — `{box_info: [{type:N, got_count: K, limit: L}]}`
  - 可能附帶 `0x0324` (currency) / `0x070d` (額外獎勵) / `0x0c05` (377 bytes)
- 失敗回應：`0x0201` error 2 (payload `\x08\x02`) 或 error 159 (payload `\x08\x9F\x01`)
  - error 159 = 已達 chest_limit (已領)

### `dungeon.dungeon_league_solo_update_box_c2s` (3600)
- payload: `{box_id: N}` 待 schema 驗證（送 `{box_id: 1}` 噴 `null encode` 錯誤 — 需正確 proto 結構）

### `dungeon.dungeon_league_hard_info_c2s`
- payload: `{}`
- 回應 (0x0e18) 例子:
  ```
  f1=2 f2=1 f3=1779544800 (unix ts, daily reset) f4=0 f5=0
  f6="0" f7=[1019,1467,1445,1328]
  ```

---

## 上層 API (in bundle)

```js
// GuildWarControl methods:
e.onLogin = function() { reqSoloBossInfo(); reqSoloHardInfo(); }
e.onReconnect = function() { reqSoloBossInfo(); reqSoloHardInfo(); }
e.reqSoloBossInfo = () => netManager.send('dungeon.dungeon_league_solo_info_c2s', {})
e.reqGetSoloBoxReward = (type) => netManager.send('dungeon.dungeon_league_solo_get_reward_c2s', {type})
e.reqSoloHardInfo = () => netManager.send('dungeon.dungeon_league_hard_info_c2s', {})

// UI 邏輯（SingleBoxView 普通 / SoloHardBoxView 噩夢）:
btn1.click = () => {
  const cfg = configLeague_solo_chapter_chest.getDataByKey(GetGuildInfo().level||1);
  if (boxes[1].got_count >= cfg.normal_chest_limit) showFlyTip('已達上限');
  else reqGetSoloBoxReward(1);
}
btn2.click = () => {  // 稀有
  if (boxes[2].got_count >= cfg.rare_chest_limit) showFlyTip('已達上限');
  else reqGetSoloBoxReward(2);
}
// HardBox view: types 3 & 4
```

---

## 自動領取流程（建議實作）

```python
def claim_lieyan_daily(page) -> dict:
    """每日領取烈焰山洞 4 箱。回傳 {claimed: [...], skipped: [...]}.

    流程：
      1. 送 dungeon_league_solo_info_c2s → 解析 box_info 4 種 type 的 got_count/limit
      2. 對每個 got_count < limit 的 type，送 get_reward_c2s({type})
      3. 服務端回 0x0e10 update_box (success) 或 0x0201 error (skip)
    """
    # implementation...
```

實作位置建議：`utils/family_lieyan.py`

接入點：`new_main_v2._run_*` 後端任務 — 每日 reset 後執行一次（00:00 / 10:00 之類重置時段）

---

## Live capture 紀錄 (2026-05-21)

成功領取 type=3 (噩夢普通) + type=4 (噩夢稀有):
- 0x0402 len=251 (bag delta)
- 0x0406 len=90 (bag delta 2)
- 0x0e10 len=10-12 (box update)
- 0x0324 len=256 (currency)
- 0x070d len=83 (rewards)
- 0x0c05 len=377 (additional)

普通 type=1/2 已先被 user 領過，回 error 159。

---

## 關聯

- `dungeon.dungeon_league_solo_get_reward_s2c` event handler: `IS(p).updateBoxList(e.box_info)`
- `dungeon.dungeon_league_solo_info_s2c` event handler: `IS(p).initBoxInfo(e)`
- UI 觸發點: `SingleBoxView` (普通) / `SoloHardBoxView` (噩夢)
- 開啟 view 流程: `reqSoloBossInfo()` → `uiMgr.openView('SingleBoxView')`

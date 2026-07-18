# A 打 / B 算 — 競技場 + 萬神試煉

> 2026-07-17。Live：競技場 / 切磋既有工具；萬神小寶 CDP 9226（5 次重算對齊官方 result）。

## 角色

| 角色 | 職責 |
|------|------|
| **A** | 實戰帳：start combat、送 result、關 UI |
| **B** | 免洗同網址 H5：只跑 `BattleMainServer` |

## 裝置開關

| key | 值 | 說明 |
|-----|-----|------|
| `arena_battle_mode` | `animation` / `local_sim` / `remote_calc` | 競技場戰鬥路徑（dashboard 可選） |
| `wanshen_battle_mode` | 同上 | 萬神關卡戰鬥路徑 |
| `arena_fight_gap_sec` | ≥7（預設 7） | 競技場兩場挑戰間隔下限 |

全域 B：

```json
"global": {
  "battle_calc": {
    "enabled": false,
    "http_host": "127.0.0.1",
    "http_port": 18765,
    "cdp_port": 9240,
    "timeout_sec": 15
  }
}
```

## 模式語意

- **animation**：現有等動畫/OCR（或 rogue_h5 等結果窗）。
- **local_sim**：A 頁攔截官方 result send → 本頁 `BattleMainServer` → A 送 result → 快關結果 UI。
- **remote_calc**：同上，模擬改 POST B HTTP；B 用免洗 CDP 頁算完回傳。

非 `web_h5`：sim 模式自動 fallback `animation`（無 page）。

## 模式參數

| mode | ChapterType | chapterId | result |
|------|-------------|-----------|--------|
| arena | 5 | 50001 | `{vid, wid}` |
| rogue | 37 | 50001 | `{result, precent}`；`atk_data[0]`/`def_data[0]` |

`BattleDataFill.setPlayerList(..., chapterType)` **必傳**第三參。

## 不做

- 純 Python 重寫引擎
- 地獄之門
- 未算完瞎報 winner
- ADB 當 B
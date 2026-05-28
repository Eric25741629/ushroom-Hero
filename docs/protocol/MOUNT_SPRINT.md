# 坐騎衝刺 (衝刺-發條) 自動化

實測：2026-05-25,emulator-5554 (web_h5/CDP 9230) + 實機 (adb,u2)。
兩後端遊戲畫面皆為 **540x960**,座標可共用。
程式：`rank_events.park_spring` → `_run_feed_flow`。測試：`tests/test_rank_events.py`。

## 活動

坐騎衝刺每月一次,活動週的週二~週四開放。期間餵養坐騎「無限時發條」
可累積祝福值並衝排名。本任務在活動週只執行一次 (成功才記錄)。

## 入口

主頁角色裝備列,坐騎 = `mountEquipItem`,在神器 `artifactEquipItem` 左邊一格:

```
/UIRoot/NormalView/MainView/subRoots/equipRoot/base/
    ├── mountEquipItem      ← 坐騎 (有紅點)  world x=295  px (221,656)
    ├── artifactEquipItem   ← 神器          world x=406  px (304,656)
    └── relicEquipItem      ← 聖物
```

`mountEquipItem` 是自訂組件 (非 cc.Button)，`emit('click')` **無效**,
必須用真實點擊 (web: `page.mouse.click`;adb: `d.click`)。

## 流程 (每步 OCR 驗證)

| 步驟 | 座標 | 節點 | OCR 驗證 |
|------|------|------|----------|
| 1 開坐騎 | (221,656) | `mountEquipItem` | `坐騎升級`/`坐騎賦能`/`升一級` |
| 2 切升級分頁 | (86,808) | tab `content/81` | `升一級`/`一鍵`/`祝福值` |
| 3 一鍵餵養 | (360,748) | `btnTen` | `發條` |
| 4 輸入數量 | (270,478) | `EditBox` | (見下) |
| 5 使用 | (270,575) | `btnUse` | `消耗`/`確定` |
| 6 確定 | (371,556) | `TopView/MessageView/boxTips/dialog/.../btnEnsure` | (確認視窗消失) |
| 7 關閉 | (270,896) | `HorseView/btnClose` | |

關鍵點:
- **步驟 2 必須做**：坐騎頁可能停在「賦能」分頁 (顯示一鍵賦能),不是「升級」分頁。
  實機開啟時即停在賦能分頁,5554 停在升級分頁 — 不可假設。
- **步驟 6 是舊版的 bug**：`使用` 後會跳「是否消耗無限時發條*N」確認視窗
  (`取消` 在 (168,558),`確定` 在 (371,556))。舊碼點 (271,556)/(331,553) 都沒中,
  確定從未生效。確認視窗在 `TopView`,不在 `NormalView` children,故 overlay 掃描看不到。

## 數量輸入 (兩後端不同)

- **web_h5**：設定 cocos `EditBox.string` 並 emit `text-changed` / `editing-did-ended`：
  ```js
  eb.string = String(n); node.emit("text-changed", eb); node.emit("editing-did-ended", eb);
  ```
  實測 set 3 → 確認視窗顯示「是否消耗無限時發條*3」。
- **adb**：u2 `send_keys` **不可用** (`AdbKeyboard.clearText() null` — H5 webview 的 HTML input
  接不到 AdbKeyboard IME)。改用:
  ```
  d.click(270,478)            # focus
  input keyevent 123          # MOVE_END
  input keyevent 67 (x8)      # DEL 清掉預設值
  input text <N>
  input keyevent 66           # ENTER：commit + 收鍵盤 (關鍵,否則鍵盤蓋住使用鈕)
  ```
  實測 input 2 → 確認視窗顯示「*2」。

## OCR 注意

OCR 常漏字 (「一鍵餵養」→「一鍵餐」、「一鍵#餐」)。驗證一律用**短子字串**
(`一鍵`/`發條`/`消耗`),不要比對完整詞。

## 排程

`json_manager.should_execute_cycle(cycle_weeks=4, allowed_weekdays=[1,2,3])`
以最後一次記錄日期為 anchor,每 4 週的週二~週四觸發,當週成功一次即記錄
(`is_next_week` 防重跑)。週四 22:00 後視為結算,不再執行。

注意：真實活動是「每月」,4 週週期會慢慢漂移。5554 現有 record 2026-05-05
→ 6/2 (剛好 28 天) 正確觸發。若日後活動日期與 4 週不符,需重新 seed record 日期對齊。

## 設定

`bot_config.json` 裝置層級:
```json
"enable_mount_sprint": true,
"mount_sprint_quantity": 7000
```
程式預設 7000 (`rank_events.DEFAULT_QUANTITY`)。

## 驗證紀錄 (2026-05-25)

- web 5554：真實 `_run_feed_flow(qty=1)` 回 True,祝福值 6838→6839→6840 (每次 +1)。
- adb 實機：走到確認視窗「*2」後取消,未消耗 (排程 gate 邏輯 + 數量輸入皆驗過)。
- 排程：5/26=False,6/2~6/4=True (用真實 record timestamp 計算)。

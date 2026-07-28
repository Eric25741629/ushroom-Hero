# 坐騎衝刺 (衝刺-發條) 規範化重寫

## 背景
舊 `rank_events.park_spring` 為座標/OCR 版。現已新增純 WS 主路徑：活動期間先由
`ws_token.mount_sprint` 送 `mount_levup_c2s`，只有 WS 失敗才降級到原 UI 流程。

## 已驗證事實 (live, 兩後端皆 540x960)
| 步驟 | 座標/節點 | 備註 |
|------|-----------|------|
| 坐騎入口 | (221,656) `mountEquipItem` | 在神器(406)左邊;icon,須座標點 |
| 坐騎升級分頁 | (86,808) | **必須先選**,否則可能停在賦能分頁 |
| 一鍵餵養 | (360,748) `btnTen` | |
| 數量 | EditBox(270,478) | web: cocos `EditBox.string`+emit;adb: click→`input text`→keyevent 66 |
| 使用 | (270,575) `btnUse` | |
| 確定 | (371,556) `MessageView/.../btnEnsure` | 舊碼點 271/331 → 沒中,主要 bug |
| 取消 | (168,558) | |
| 關閉 | (270,896) `btnClose` | |

## 需求 (使用者確認 2026-05-25)
- 數量: 3200 (config `mount_sprint_quantity`;程式預設仍 7000,實際以 config 為準,2026-06-04 使用者調降)
- 排程: 4 週週期 + 週二~週四 (`allowed_weekdays=[1,2,3]`);現有 5554 record 2026-05-05
  → 6/2 (28天=4週) 正確觸發,不需重 seed
- 後端: web_h5 + ADB 皆支援
- 一個活動週只餵一次 (成功才 `time_recording`)

## TODO — DONE
- [x] `ws_token/mount_sprint.py` — `0x1f02` 純 WS 餵養、成功回應驗證、週期記錄
- [x] WS-first runner / phase wiring — 成功後跳過 `坐騎強化` UI 任務，失敗保留 fallback
- [x] 2026-07-28 `7fe98fc6` live 送 `cost=1` 成功（發條 -1、經驗 +1）
- [x] tests/test_rank_events.py — 16 tests (排程 gate + flow + 數量輸入),全綠
- [x] rank_events.py — 重寫 park_spring + helpers (跨後端 + OCR 驗證)
- [x] bot_config.json — 5554 加 enable_mount_sprint:true / mount_sprint_quantity:3200 (原 7000,2026-06-04 調降)
- [x] docs/protocol/MOUNT_SPRINT.md — 驗證流程紀錄
- [x] pytest (20 pass: rank_events 16 + daily_pipeline 4) + py_compile OK
- [x] 清理 tools/_probe_*.py 暫存腳本

## 待辦 (交接給使用者)
- bot 若正在執行,需重啟 new_main_v2.py 才會載入新 rank_events (sys.modules cache)。
  6/2 活動前重啟即可。
- 真實活動 (qty=7000) 尚未在活動週實跑;6/2 觀察首次自動執行。

## 驗證結果
- 排程: 5/26=False, 6/2~6/4=True (用真實 record timestamp 算過)
- web 餵1成功(祝福值6838→6839, 發條37424→37423)
- ADB 走到 confirm "*2" 後取消(未消耗); u2 send_keys 不可用,須 adb shell input text + keyevent 66

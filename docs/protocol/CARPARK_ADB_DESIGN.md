# ADB 跨服停車（OCR-based）設計

Date: 2026-05-25
Status: 設計 + 骨架已落地（`utils/carpark_adb.py`），**未校準、未 live 驗證**。

## 目標
讓 ADB 後端裝置也能做「跨服停泊銀車」，且**整個泊銀車座沒空位就跳過**（對齊 H5 已完成的行為）。

## 為什麼不能共用 H5
H5（`utils/carpark_auto.py`）全程用 `page.evaluate` 讀 cocos scene tree——精確知道哪個 tier 滿、哪格空、哪台車今日 0 分鐘。**ADB 沒有 scene tree，只有截圖像素**，所以所有「狀態判讀」必須改用 OCR（`img_tools.get_all_text`）。

兩邊都 render 在 **540×960**，所以**點擊座標可移植**；不可移植的是判讀。→ 採 **hybrid**：H5 維持 scene-read，ADB 走 tap+OCR，不把 H5 降級成 OCR。

## 架構
```
game_actions/carpark_scheduler.run_carpark_check_if_due(d, ip)
  ├─ carpark.enabled? 否 → return
  ├─ backend == web_h5 → _run_h5 → carpark_auto.reconcile(page, cfg)        (原樣)
  └─ backend == adb    → _run_adb → carpark_adb.reconcile_adb(d, cfg)       (新)
                            └─ 需 carpark.adb_enabled:true（opt-in，預設 off）
                            └─ 模組內 _CALIBRATED gate：未校準 → dry-run，不發點擊
```
- 純邏輯共用：`is_daytime_window` / `target_state` / `parse_occupied_total` / `silver_tier_full`（都在 `carpark_auto`）。
- 安全：未校準時 `_tap` 直接 no-op，`park_one_silver_adb` 不會在真帳號亂點。

## 流程（H5 步驟 → ADB pixel+OCR 對照）
| 步 | H5（scene tree） | ADB（tap + OCR） | 狀態 |
|----|------------------|------------------|------|
| 1 導航到 ParkingMainView | CocosNavigator 點 home→車位 | 沿用共用主頁/家園導航 + OCR 驗證 | 待接 |
| 2 開車位選擇 + 跨界 tab | emit/worldPos click | `_tap('btnSpace')` → `_tap('cross_tab')` → OCR 見「泊銀/鎏金/車座」 | 骨架 ✅（缺座標） |
| 3 **判滿/跳過** | 讀 tier cell numSpot | crop 泊銀數字區 → OCR → `parse_occupied_total` → `占用>=總數` 跳過 | **核心已實作** ✅（缺 region） |
| 4 進泊銀 tier | 點 tier cell | `_tap('silver_tier')` | 待接 |
| 5 找非滿 lot | 讀每 lot nodeFull | 逐 lot OCR「滿」badge/數字（per-run，需 live 建） | **stub** |
| 6 找空位 | 讀 buildingRoot nodeName.active | OCR 空位（per-run，需 live 建） | **stub** |
| 7 選車（今日 0 分） | 讀 picker today_park_min | OCR 車格停車時間 | **stub** |
| 8 開始停車 + 驗證 | 點 nodePark | `_tap('confirm_park')` → OCR 驗證成功 | 待接 |

> 第 5–7 步是「per-run 動態座標」——每次空 lot/空位/車格位置不同，**不能 replay 固定座標**，必須當場 OCR/template 找。這部分誠實標 stub，回 None（不假裝停車成功），要對著真 ADB 裝置開發+驗證。

## 座標來源（不需 ADB 裝置就能先拿固定鈕）
H5 的 `carpark_click_recorder` 已把每個 click 以 **action tag + x,y** 寫進 `logs/<dev>/carpark_clicks.jsonl`（tag 如 `pool.btnParkingSpace`、`lot.silver.btnParkingSpace`、`spot.empty`、`picker.car`、`picker.confirm_park`）。
- **固定 UI 鈕**（btnSpace、跨界 tab、泊銀 tier、開始停車）位置穩定 → 從 H5 跑一次 park flow 擷取，填進 `carpark_adb._CALIB`，移植到 ADB。
- **動態座標** → 不可 replay（見上）。

## OCR 策略 + 風險
- 讀「占用/總數」：`parse_occupied_total(texts)`
  - 優先抓 `(\d+)/(\d+)`（斜線最可靠）；否則「剛好兩個整數」。
  - 採信條件：`總數>0 且 總數%10==0 且 0<=占用<=總數`。ADB 層再加嚴：`總數 == 300`（30 lots×10），擋 OCR 把 300 看成 30。
  - 讀不到 → None → **保守跳過本輪**（符合「不要 churn」），絕不亂停。
- **主要風險**：OCR 要把 `299/300` 讀準（3 位數 + 斜線）。斜線被漏掉就切不出 occ/total。
  - 緩解：保守 gate（上面）+ region crop 上採 3× 放大 + 讀不到就 skip。
  - 校準時務必用真截圖實測 OCR 命中率；若不穩，改用替代訊號（例如偵測 lot 的「滿」字 badge）。

## 校準程序（D5，需 H5 擷取 / 之後 ADB live）
1. H5 跑一次（或手動走一次）跨服停車，從 `carpark_clicks.jsonl` 取固定鈕座標 → 填 `_CALIB`。
2. 對一張「跨界 tier list」截圖框出泊銀數字區 → 填 `_REGION['silver_tier_num']`，OCR 實測對照 scene-read 真值。
3. ADB 裝置上線後：dry-run 觀察 OCR 讀數正確 → 再實作第 5–7 步動態 OCR → 最後拿掉 dry-run gate（`_CALIBRATED=True`），qty=1 / 走到確認再取消，live 驗證。

## 目前已交付 vs 待辦
- ✅ 設計（本文件）、scheduler 分派、`carpark_adb.py` 骨架、OCR 解析核心 + 單元測試、dry-run 安全 gate。
- ⏳ 固定鈕座標 + region 校準（需 H5 擷取）、第 5–7 步動態 OCR、ADB live 驗證。

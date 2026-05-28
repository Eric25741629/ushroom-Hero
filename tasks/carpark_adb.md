# ADB 跨服停車（OCR-based）— 從零建

User 決定：整個 ADB 跨服停車流程從零建；先給設計/程式，**先不 live 驗證**。

## 為什麼 ADB 要重寫（不能共用 H5）
- H5（`carpark_auto.py`）全靠 `page.evaluate` 讀 cocos scene tree（精確）。ADB 沒有 scene tree，只有截圖像素。
- 540x960 兩邊一致 → **點擊座標可移植**，但**狀態判讀不可移植**：ADB 只能 OCR。
- 目前 `carpark_scheduler` gate `backend=='web_h5'`，ADB 完全沒有跨服停車。

## 架構決定
- 新模組 `utils/carpark_adb.py`（uiautomator2 + img_tools OCR），不污染 H5 路徑（hybrid，非統一 OCR）。
- 共用純邏輯：`is_daytime_window`/`target_state`（reuse carpark_auto），新增 backend-agnostic OCR 數字解析。
- `carpark_scheduler` 依 backend 分派：web_h5→carpark_auto.reconcile；adb→carpark_adb.reconcile_adb。
- **安全 gate**：ADB park flow 在「未校準/未驗證」前不可發點擊（dry-run），避免在真帳號亂點花資源。
  - `carpark.adb_enabled`（預設 false）+ 模組內 `_CALIBRATED` 旗標。

## 流程（對照 H5 各步 → ADB pixel+OCR）
1. 導航到 ParkingMainView：tap（座標 = 從 H5 carpark_click_recorder 取得的可移植座標）→ OCR 驗證到車位頁。
2. 開 bottom/btnSpace → 跨界 tab(content/128)：tap → OCR 驗證 ParkingCrossSpaceView2。
3. **判滿/跳過**：截圖 → crop 泊銀 tier 數字區 → OCR → `occ/total`；occ>=total → skip（核心需求）。
4. 有空位：進泊銀 tier → 逐 lot OCR「滿」badge / 數字找非滿 → 進 lot → OCR 找空位 → tap。
5. 選車（OCR 今日停車時間=0）→ 開始停車 → OCR 驗證成功。

## 座標來源（不需 ADB 裝置就能拿）
- H5 `carpark_click_recorder` 已把每個 click 以 action tag + x,y 寫進 `logs/<dev>/carpark_clicks.jsonl`。
- 固定 UI 鈕（btnSpace/跨界 tab/泊銀 tier）座標穩定 → 從 H5 跑一次 park flow 擷取即可移植到 ADB。
- 動態座標（空 lot/空位/車格）每次不同 → ADB 端必須 OCR/template 當場找，不能 replay。

## 核心風險
- OCR 讀「299/300」要準（3 位數 + 斜線）。斜線被 OCR 漏掉就無法切 occ/total。
  → 解析器保守：優先抓 `(\d+)/(\d+)`；total 必須是 10 的倍數且 occ<=total 才採信；否則回 None。
  → OCR 不可讀時的策略：保守 skip（符合「不要 churn」），不亂停。

## 待辦
- [ ] D1 設計文件 docs/protocol/CARPARK_ADB_DESIGN.md
- [ ] D2 backend-agnostic OCR 解析 `parse_occupied_total(texts)` + `silver_tier_full(occ,total)` + 單元測試（可現在驗）
- [ ] D3 carpark_adb.py 骨架：OCR reads 寫實、tap nav 用校準常數 + dry-run gate + 誠實 TODO
- [ ] D4 scheduler 依 backend 分派（flag off 預設）
- [ ] D5 （之後）H5 擷取固定座標 + ADB live 校準/驗證

## Review (D1-D4 DONE 2026-05-25, no-live per user)
- D1 ✅ `docs/protocol/CARPARK_ADB_DESIGN.md`
- D2 ✅ `parse_occupied_total` + `silver_tier_full`（在 carpark_auto，backend-agnostic）+ 13 單元測試
- D3 ✅ `utils/carpark_adb.py` 骨架：OCR reads 寫實、skip-check（判滿）完整、找空位/選車/停車是誠實 stub；
       `_CALIBRATED=False` + `_tap` no-op → dry-run 證實不會發點擊（FakeD.click 會 raise，沒被呼叫）
- D4 ✅ `carpark_scheduler` 依 backend 分派；ADB 需 `carpark.adb_enabled`(預設 off)
- 全測試 43 passed。
- ⏳ D5：H5 擷取固定座標(`carpark_clicks.jsonl`) + region 校準 + ADB 上線後動態 OCR + live 驗證 → 才 flip `_CALIBRATED`。

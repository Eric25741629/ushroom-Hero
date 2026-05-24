# 航海每日任務重構 (sea_v2) — 階段 A

設計：`docs/protocol/SEA_DAILY.md`
範圍（使用者已核准 2026-05-25）：模組骨架、定位/座標導航/挑格/滑動校準、領獎流程。
動作(駐守/進攻)留待白天 10:00–24:00 窗口（階段 B）。

## 純邏輯（TDD，先寫失敗測試）— DONE

- [x] `sea_v2/navigator.py` 座標數學：`world_to_pixel`、`world_delta_to_drag_px`(=−Δ/2.0)、`is_on_screen`、`center_drag`(closed-loop, max_step clamp) — `tests/test_sea_v2_navigator.py` 8 pass
- [x] `sea_v2/tiles.py`：`parse_tiles` / `home_base`(離鏡頭最近) / `nearest` / `pick_daily_targets` — `tests/test_sea_v2_tiles.py` 9 pass
- [x] `sea_v2/map_cache.py`：load/save(atomic,utf-8)/record_account_base/record_targets/get_* — `tests/test_sea_v2_map_cache.py` 6 pass

## 需 live 探測（夜間可做）— DONE

- [x] 領獎 UI：每個任務一個 `btnGet`（完成才 active）→ `claim_rewards` 只點 active 的
- [x] live 驗證：定位回家(✓)、worldToScreen 點擊落點(base→123,792 ✓)、pan 置中閉環 off-x 712→1 單調**無過頭**(✓)
  - 垂直 pan 在此緯度受限，但目標都在垂直視野內 → 導航目標改為「進安全框可點」而非置中

## 串接 — DONE

- [x] `sea_v2/tasks.py` `run_daily(session)` 每日狀態機 + 領獎 + 寫 cache — `tests/test_sea_v2_tasks.py` 6 pass
- [x] `sea_v2/session.py` H5SeaSession 實體（定位/讀格/閉環置中/領獎/離開；駐守/進攻/修船 = 階段B stub）
- [x] `sea_v2/__init__.py` `sea(ip,d)` + `use_sea_v2` flag（預設 off，**未接 runtime**）— `tests/test_sea_v2_entry.py` 4 pass
- [x] `python -m pytest tests/test_sea_v2_*.py -q` → **33 pass**
- [x] live 端對端 smoke：`sea_v2.sea("emulator-5554", shim)` 跑完整路徑無 crash，home/挑格/置中/領獎/離開皆正確，cache 寫入

## 修船套件流程 — 已映射 + 實作（夜間）

- [x] 路徑：`btnPort`→SeasonMainView →`btnRestore`(維修站)→SeasonRestoreView →`一鍵修築`(root/bot/btnRestore)
- [x] **emit('click') 對一鍵修築無效 → 改用 pixel tap**（UI 節點 worldPosition→pixel）；session 全面改 pixel tap
- [x] 材料閘：缺「木材」跳 `ItemGetWayView` → 偵測=料不足→關閉→return False
- [x] 導航發現：關閉港口=退出賽季回主頁 → run_daily 把修船排最後（exit 即離開賽季）
- [x] live 驗證：use_repair_kit 走完整路徑、缺料正確 return False、收尾乾淨（回主頁）
- [ ] 白天有木材時驗證一鍵修築「成功」分支落地畫面（深夜耐久滿+缺料，無法驗證成功路徑）

## 階段 B（白天 10:00–24:00，另開）
- [ ] 動作選單 `駐守/進攻/開始航行` 節點映射（填 session.garrison/attack）
- [ ] 確認「退出港口→主頁」即離開賽季，及修船成功分支落地畫面
- [ ] 完整 live 一輪（駐守×2 + 進攻×1 + 領獎 + 修船）+ H5 多台實跑 → 通過後翻 `sea_v2_enabled` flag 接 runtime

## 階段 C（後續）
- [ ] ADB(fc65396d) 定向：定位置中 + 2.0 世界/px 校準換算滑動 + OCR 確認；自家 base 角落用小地圖/一次性設定

## Review

階段 A 完成 2026-05-25（夜間）。純新增檔案，未改動任何 runtime：
- `sea_v2/`（6 檔）+ `tests/test_sea_v2_*.py`（5 檔, 33 測試）+ `docs/protocol/SEA_DAILY.md` + `tools/probe_sea.py`
- 根因修復：以 `定位` 取得確定原點 + worldToScreen 座標導航 + 閉環置中，取代舊「固定往右盲滑」。已 live 證明無過頭。
- 待白天：駐守/進攻/修船動作選單節點（深夜無法行動，今晚無法驗證）。

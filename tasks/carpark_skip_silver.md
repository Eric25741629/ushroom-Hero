# 跨服停車：整個泊銀車座沒有銀空位 → 自動跳過

## 問題（user 確認：現況「卡住/狂點很久」）
`park_one_silver` 在泊銀車座沒有空位時，逐格試最多 8 個 lot，每個滿 lot 都做完整 re-navigation
（開 space view→跨界 tab→泊銀 tier→scrollTo→click→等 server 3.5s），才放棄。

## 要求
- 自動停跨服車位（沿用）
- 整個泊銀車座沒有銀空位 → **立刻跳過**（user 確認範圍 = 整個泊銀，非單一偏好 lot）

## 關鍵發現（cocos source 靜態分析）
- `ParkingDataCache.null_space`（`car_park_search_s2c` 寫入；開跨界 tab 時 `reqParkSearch(crossSpaceList,"")` 觸發）
  每 lot：`{ceng, null_num, master_id, ext}`；`null_num`=空位數；`0`=滿。
- lot detail cell：`nodeFull.active=(0==null_num)`，名稱 `"(occupied/10)"`，occupied=10-null_num。
- 泊銀 tier cell（`.../ParkingCrossSpaceView2/root/scroll/view/content/3` 之 `numSpot/num` RichText）
  顯示彙總 `occupied/total`（pool_type==SILVER(3) lot 加總）。判斷有空位：`total>0 and occupied<total`。
- `window.IS` = 單例存取（`o.IS=t`）；`IS(Class)`→單例。需 ParkingDataCache class ref。
- silver pool id=3（POOL_TYPE_TO_ID["silver"]）；lot ceng 範圍 5..34（現有 SILVER_LOT_ID_BASE=5）。

## 待辦
- [ ] S1 read-only live probe（5556/9223）：確認 null_space 存取路徑（純讀，不導航；bot 在跑也安全）
- [ ] S2 helper `_read_silver_availability(page)`：優先 null_space → per-lot {lot_idx,null_num,group}；
      讀不到 fallback tier 彙總 label（至少 has_empty bool）
- [ ] S3 改 `park_one_silver`：tier 全滿→log+return None（不 churn）；有空位→直接挑非滿 lot
- [ ] S4 單元測試（mock evaluate）
- [ ] S5 live 驗證（H5 only）
- [ ] S6 docs / memory 更新

## Review (DONE 2026-05-25)

### 改了什麼
- `utils/carpark_auto.py`：
  - 新增 `_SILVER_TIER_OCC_JS` + `_silver_tier_has_empty(page)`：讀 ParkingCrossSpaceView2
    tier list 中「泊銀」cell（用 icon `gg_icon_dijichewei` 辨識，不靠固定 index，因為
    奇星車場開啟與否會位移順序）的 `numSpot` 彙總 `occupied/total`。
  - `park_one_silver`：開跨界 tab 後先 `_silver_tier_has_empty`；回 False（整個泊銀滿）→
    立刻 `return None` 跳過，不再逐格 churn。回 True/None → 沿用原逐格 iteration（safety net）。
  - reconcile 的 skip log 改為「跳過跨服停車 (無銀空位 / 無可停車)」。
- `tests/test_carpark_auto.py`：+7 測試（has_empty true/false/sanity-gate/None + park_one_silver
  skip/iterate/fallback）。共 30 passed（carpark_state 13 也綠）。

### 關鍵教訓（踩過的坑）
1. **ParkingDataCache.null_space 讀不到**：netManager callbackTable 的 s2c listener target
   全是 dispatcher（ctor "a"），不是 data cache。`carpark_tracker` 的 heuristic 是壞的
   （memory 早標「需驗證」）。改走 rendered scene tree。
2. **Cocos rich-text 解析陷阱**：label 是 `<b><color=#4a9d3e>299</color>.../300...</b>`，
   `\d+` 會先抓到色碼 `#4a9d3e` 的數字 → 誤判 4/9。必須先 `replace(/<[^>]*>/g,'')` 去 tag
   再抓數字。← user 質疑「怎麼可能9」一語中的。
3. 泊銀 total 永遠是 10 的倍數（10 spots × lot 數，例如 300=30 lots）→ 拿來當 sanity gate。

### Live 驗證（H5 only；carpark 是 web_h5 專屬）
- 5560：raw 299/300 → 正確解析 → has_empty=True（fix 前會誤判 4/9）。
- 本 session 稍早 5556/5560 都讀到 300/300（滿）→ skip 路徑在實際會發生。
- 300/300 → has_empty False → skip 由單元測試覆蓋。

### ⚠ 待辦
- 跑著的 bot（PID 67304）是舊 code（sys.modules cache）→ **需重啟 new_main_v2.py 才生效**。
- 文件 `docs/protocol/CARPARK_AUTOMATION.md` 可補上 skip 行為（nice-to-have）。

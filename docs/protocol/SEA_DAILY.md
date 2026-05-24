# 航海每日任務 (Sea Daily) — 設計與協議

狀態：設計定稿待審 / 實作分兩階段（夜間骨架 + 白天動作驗證）
後端：H5 優先（6 台裝置中 5 台 web_h5），ADB(fc65396d) 後續
相關 skill：`dual-backend-task-dev`、`cocos-app-analysis`；同類完整範例見 `MOUNT_SPRINT.md`

---

## 1. 問題

舊 `Sea.py`（54 行硬寫）永遠 `swipe(400,400,100,400)`（固定往右），但賽季地圖是方形、四個角各有一座主城（大本營），**每個帳號的主城在不同角落**。方向固定 → 滑錯方向 → 找不到資源/遺跡 → 任務前判定「不在主頁面」中止。近兩個月 70+ 次失敗，幾乎全在 H5 帳號。

舊流程也沒有：自動領取任務獎勵、修船套件子任務。

## 2. 核心洞察

賽季地圖的資料**不在封包，而在 cocos 場景樹 + 靜態 config**，每個 H5 客戶端本地都完整持有，全服一致：

- 進賽季後出現獨立場景節點 `SeasonMapScene`（與主場景 `launch` 並存）。
- 地圖物件在 `SeasonMapScene/unit/obj/*`，**節點名稱即型別**：

  | 節點名 | 意義 |
  |--------|------|
  | `base` | 主城/大本營（**所有**玩家的，含自家與鄰居） |
  | `resource_1` / `resource_2` / `resource_3` | 資源 Lv1/2/3 |
  | `remain` | **遺跡（relic）** |
  | `s4_totem` | 圖騰 |
  | `s4_empire` | 帝國中心 |

- 每個 obj 節點都有世界座標 `node.worldPosition`（單位是賽季地圖世界座標，數值約 -32000 ~ -28000）。
- `window.configSeason_target.datas` = 賽季目標格，每筆 `_data = [id, type, [gridX,gridY], descId..., rewards, order]`（全服靜態）。
- `window.configMap` = 全服地圖定義。

## 3. 已 live 驗證的導航原語（5554, 2026-05-25 夜間）

| 原語 | 驗證 | 設計用途 |
|------|------|---------|
| **定位** `/UIRoot/NormalView/SeasonMapSceneView/root4/bottom/btnLocate` | 鏡頭由鄰居家 (-28267) 一鍵跳回自家 (-31733)，自家 base (-31910,-1867) 置中 | **每次先按定位取得確定的「家」原點**，根治方向錯誤 |
| `camera.worldToScreen` | 自家 base 世界 (-31910,-1867) → logical 螢幕 (164,224) | 任一已知世界座標的格子可直接點 |
| 邏輯→實體像素換算 | visible 720×1280，frame 540×960 → `px = sx*0.75`, `py = (1280-sy)*0.75` | worldToScreen 結果轉成 Playwright/ADB 點擊像素 |
| 拖曳校準 | 拖 260px → 鏡頭世界 +520（dy=0），跨多步一致 → **2.0 世界單位 / px，方向相反**（拖左露出較大 world-x） | 把離畫面目標滑到置中：`pan_px = (targetWX - camWX) / 2.0`，**算精確距離、不盲滑、不會過頭** |
| 自家 base 辨識 | 進場/定位後鏡頭中心最近的 `base` 即自家（其餘為鄰居） | 取原點 |
| 最近遺跡 | `remain` @ (-29999,-1709)，距家 dx≈+1900 ≈ 向右約 3 個螢幕寬（與使用者經驗「第三輪見遺跡」一致） | 進攻/挑戰遺跡目標 |
| HUD 按鈕（節點名） | `btnTask`(任務/領獎)、`btnPort`(港口)、`btnSupply`(補給)、`btnMinimap`(小地圖)、`btnSearch`(搜索)、`btnLocate`(定位) | 各子任務入口 / ADB 定向 |
| 修船套件流程（節點，已驗證可達） | `btnPort`→`SeasonMainView` → `btnRestore`(維修站)→`SeasonRestoreView` → `一鍵修築`=`SeasonRestoreView/root/bot/btnRestore` | 完成「使用1次修船套件」 |
| 點擊方式 | **一鍵修築 emit('click') 無效、需真實 pixel tap**（UI 節點 `worldPosition` → `px=wx*.75, py=(1280-wy)*.75`） | season UI 按鈕一律用 pixel tap，不要 emit |
| 材料閘 | 點一鍵修築缺料時跳 `ItemGetWayView`（材料獲取，需「木材」，來源：探索神秘海域） | 偵測此 view = 修船失敗（料不足）→ 關閉 |
| 導航層級（已驗證） | 關閉 `港口`(SeasonMainView btnClose) **會退出整個賽季回遊戲主頁**，不是回地圖 | 故每日流程把「修船」排在最後，退出港口即等同離開賽季 |

座標範例：home base (-31910,-1867)；resource_1 多顆散在右上；resource_2 (-31500,-1156)；remain (-29999,-1709)。所有日常目標皆在家的「右上」方向 → 對應「左下角主城要往右滑」。

## 4. 每日任務（讀自遊戲內「任務」面板）

全部 5 項都要自動完成 + **領取獎勵**：

1. `完成1次駐守操作` (0/1)
2. `本方佔領2塊資源Lv1` (0/2) — 與駐守同源：對 2 顆 `resource_1` 駐守即同時滿足 1+2
3. `完成1次進攻操作` (0/1)
4. `挑戰1次遺跡` (0/1) — 與進攻同源：進攻 1 個 `remain` 即同時滿足 3+4
5. `使用1次修船套件` (0/1) — 走 `btnRepair`
6. **領取**：`btnTask` → 一鍵/逐項領取已完成獎勵

實質動作集合：**駐守 2 顆 resource_1 + 進攻 1 個 remain + 用 1 次修船套件 + 領獎**。

## 5. 每次執行流程（H5）

```
1. 進賽季 (主頁 btnSeason) → 等 SeasonMapScene 載入
2. 按 定位(btnLocate) → 讀最近 base = 自家原點 (homeWX, homeWY)
3. 選目標（就近、用世界座標）：
   - 2 顆最近的 resource_1
   - 1 個最近的 remain
4. for 每個目標 tile：
   a. 讀 tile.worldPosition
   b. 若 worldToScreen 不在安全框內 → pan_px=(tileWX-camWX)/2.0 水平 + 垂直同理，拖曳置中
   c. 由 worldToScreen→像素點擊 tile
   d. 等動作選單 → 點 駐守 / 進攻（按節點名；白天補映射）→ 開始航行
   e. 等船出航確認（OCR/節點狀態其一）
5. btnTask → 領取所有 active btnGet 獎勵 → 關閉
6. 修船（最後）：btnPort→維修站(btnRestore)→一鍵修築（pixel tap）；缺料跳 ItemGetWayView 就關掉
7. 關閉港口即退出賽季回主頁（= 離開）
```

導航全程用世界座標 + 節點名，OCR 僅作「動作是否成功」的備援驗證，不再用於找位置/找字。

## 6. 模組結構（重構 `Sea.py` → `sea_v2/`）

仿 `farm_v2/` 風格，邏輯與 IO 分離、純函式可單測：

| 檔案 | 職責 | 可單測 |
|------|------|--------|
| `sea_v2/tiles.py` | 從場景 obj 解析 tile 清單（型別/世界座標）；辨識自家 base；就近挑目標 | ✅（餵假 obj 清單） |
| `sea_v2/navigator.py` | 世界座標↔像素換算、定位、pan_px 計算、置中、點擊 tile | ✅（純算術 + 假 page/device） |
| `sea_v2/tasks.py` | 每日任務狀態機：駐守×2 / 進攻×1 / 修船×1 / 領獎 | ✅（假 navigator + staged OCR） |
| `sea_v2/map_cache.py` | 解析結果寫共享 JSON（全服目標格 + 各帳號 base 座標） | ✅ |
| `sea_v2/__init__.py` `sea(ip, d)` | 對外入口，沿用既有呼叫點（`daily_pipeline.py:289`） | — |

排程不動（`should_execute_sea_with_cooldown` 既有 4 週週期 + 4h 冷卻 + 10:00–24:00 平日窗口）。

## 7. 共享 & ADB（後續）

- H5 帳號各自讀自家場景 + 本地 configSeason_target，**本身自足、無需互相分享**。
- 共享 JSON 的真正用途：餵那台 ADB（webview 無 cocos JS 存取）+ 觀測。內容：全服目標格世界座標（H5 解析）、各帳號自家 base 世界座標。
- ADB 落地路徑（本階段不做，設計不堵死）：tap 定位置中 → 用 2.0 世界/px 校準把「目標相對自家的世界位移」換成滑動距離與方向 → 滑到置中 → tap → OCR 確認 `駐守`/`進攻` 短字串 → tap。自家 base 角落以 `小地圖` 或一次性設定取得。

## 8. 分階段交付

- **階段 A（現在，夜間可驗證）**：`sea_v2/` 骨架 + tiles/navigator/map_cache + 單元測試；live 驗證定位、worldToScreen 點擊落點、pan 置中、領獎流程（皆不需「行動」權限）。
- **階段 B（白天 10:00–24:00 窗口）**：補 `駐守/進攻/開始航行` 動作選單節點映射 → 完整 live 驗證一輪（resource 駐守×2 + remain 進攻×1 + 修船 + 領獎），H5 兩台以上實跑。
- **階段 C（後續）**：ADB 定向 + 共享消費。

## 9. 注意

- 深夜顯示「深夜無法行動」：可移動視角、可領獎、可點格子，但駐守/進攻動作被禁用 → 階段 B 必須白天做。
- 拖曳用慢速分段（避免慣性甩動造成過頭）；ADB `d.swipe` 同理需慢。
- 探測工具：`tools/probe_sea.py`（search/tree/click/text/hook/drain/shot）。

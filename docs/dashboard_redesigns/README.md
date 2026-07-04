# 菇勇者控制台 · 儀表板改版方案（7 套）

這是現有控制面板 `templates/dashboard.html` 的 **7 種完整視覺改版方向**。每一套都是可獨立開啟的 standalone HTML（只靠 Google Fonts + vanilla JS，無其他相依），內含 7-8 台假裝置（涵蓋 ONLINE / PAUSED / OFFLINE / DEGRADED / DISABLED 各狀態）、所有 modal、倒數計時、泊車展開列、每日進度與 live-view canvas 等真實結構，並刻意保留了原儀表板的 **element ID、status class、API 介面意圖、polling 節奏語意與按鈕 optimistic pending-lock 行為**。

## 怎麼看

1. 直接開 **`index.html`** —— 一頁可比較全部 7 套：各方案的名稱、一行摘要、signature moves、配色與字體說明，加上「開啟此方案」連結與內嵌縮放預覽（iframe，lazy + 縮放至 35%）。
2. 想看完整效果就點該方案的「全螢幕開啟」/「開啟此方案」，會在新分頁載入該 standalone HTML。
3. 預覽是縮放呈現，可能與全螢幕略有差異，最終以全螢幕為準。

> 直接用瀏覽器開 `docs/dashboard_redesigns/index.html` 即可，不需要起 server。

## 七個方向

| # | 名稱 | slug | 一句話定位 |
|---|------|------|------------|
| 1 | 深色玻璃座艙 | `dark-glass-cockpit` | 真分層玻璃擬態、發光狀態環，任務控制中心般的深色科技座艙 |
| 2 | Swiss / International | `swiss-grid` | 零間距模組化網格 + 數字當主角，單一朱紅 accent 的極簡紀律 |
| 3 | 新粗獷主義中控台 | `neo-brutalist` | 粗黑邊 + 硬偏移陰影 + 高飽和原色，會實體下壓的張揚海報感 |
| 4 | The Daily Dispatch | `editorial-magazine` | 把控制面排版成高級報紙：報頭、編號編輯脊、裝置卡即文章 |
| 5 | Bento 模組 | `bento-modular` | 溫暖榻榻米便當盒，大小不一漆面磚 + emoji + 喚醒倒數錶盤 |
| 6 | 復古終端機 CRT | `retro-terminal` | 命令列框架 + 磷光 monospace + 掃描線 CRT 作戰室氛圍 |
| 7 | 淺色柔光 Soft Ops | `light-soft-ops` | 暖紙底 + neumorphic 雙光源陰影 + Fraunces serif 的明亮輕奢 |

## 各方向重點

### 1 · 深色玻璃座艙 (`dark-glass-cockpit`)
深藍紫漸層底 + 徑向光池 + SVG 顆粒 + 飄移掃描光帶。每層面板都是半透明玻璃（backdrop-blur+saturate）配內高光/外陰影與髮絲邊。發光脈動狀態環、依狀態著色的 accent rail、2x2 遙測格、SVG sparkline、青→洋紅泊車進度條、會動的挖礦 live-view canvas。字體 Sora + Noto Sans TC + Space Mono 三聲部。**適合**喜歡科技儀表/任務控制感、偏好深色的人。

### 2 · Swiss / International (`swiss-grid`)
嚴格國際主義排版：可見模組化網格、裝置卡共用 1px ink 髮絲線（零間距讀成一張帳本）、超大 tabular-mono 倒數、索引號 00-08、比例排版。近單色米白紙 + 唯一朱紅 #E63916（幾乎只給主動作與 live pulse）。狀態用左緣 2px 垂直色脊表達。零圓角、零陰影。**適合**重視可讀性、長時間盯著看、喜歡克制理性的人。

### 3 · 新粗獷主義中控台 (`neo-brutalist`)
4-6px 純黑邊框 + 零模糊硬偏移投影 + 高飽和原色平塗，鋪在暖報紙紙理。旋轉貼紙狀態標、終端綠日誌、可展開泊車進度條。按鈕 :active 實體下壓、斜紋 disabled、脈動 spinner。Archivo Black + Space Mono。**適合**想要強烈個性、不怕張揚、anti-corporate 風格的人。

### 4 · The Daily Dispatch（編輯報刊風，`editorial-magazine`）
高級印刷報紙隱喻：置中斜體 Playfair 報名 + 三重橫線報頭、編號編輯脊（01 數字看板 / 02 田野報告）、裝置卡當雜誌文章（狀態欄邊條、serif 導言、髮絲帳本統計、反白 WIRE 日誌）。Playfair + Spectral + Archivo + JetBrains Mono。**適合**喜歡編輯式、有內容質感、紙感氛圍的人。

### 5 · Bento 模組 (`bento-modular`)
溫暖榻榻米米白底 + 大小不一漆面圓角磚（真 12 欄、混合 span）。每磚 emoji 頭像 + 左緣漆框（依狀態著色）+ 色相暈染標頭 + 便當分隔格 + 會動的 SVG 喚醒倒數錶盤。Feature 磚內嵌挖礦深度長條圖。Sora + Manrope + JetBrains Mono。**適合**想要友善、好掃描、活潑但仍和諧的人。

### 6 · 復古終端機 CRT (`retro-terminal`)
賽博龐克 CRT 作戰室：命令列框架（`root@ops_` 提示符 + 閃爍游標、按鈕帶 sigil、modal 是終端視窗、ASCII 橫線分隔）。脈動 LED 狀態點、彩色 INFO/WARN/ERR 日誌、層疊 CRT 氛圍（掃描線/顆粒/掃描光暈/管面曲率）。JetBrains Mono + VT323 雙 monospace。**適合**喜歡駭客/終端機美學、重資訊密度的人。

### 7 · 淺色柔光 Soft Ops (`light-soft-ops`)
暖去飽和紙底 + 多層粉彩漸層 + 一致雙光源 neumorphic 陰影 + 超大圓角。編輯式 sticky 頂欄（超大 Fraunces serif 標題）、非對稱機隊總覽條（健康條 + SVG 甜甜圈 + sparkline）、便當但不均一的裝置網格。Fraunces + Manrope + Spline Sans Mono。**適合**想要明亮、輕奢、柔和耐看、深色座艙對立面的人。

## 怎麼挑

- **氛圍**：深色科技感 → 1 / 6；極簡紙感 → 2；溫暖明亮 → 5 / 7；編輯印刷感 → 4；張揚個性 → 3。
- **資訊密度**：座艙(1)、終端機(6)最密；Swiss(2)、編輯(4)最克制。
- **長時間盯著**：建議優先試 7（淺色柔光）、2（Swiss）、5（Bento）。
- **個性強度**：粗獷(3)最強；柔光(7)/Swiss(2)最低調耐看。

## 融合實驗（2 + 5 三種混血，2026-06-14）

使用者最喜歡 2 號（Swiss）與 5 號（Bento），要看兩者融合的樣子。三套沿「紀律 ↔ 溫暖」光譜，共用同一份 8 台假裝置資料與全部區塊/modal/互動，只交叉授粉這兩套的字族，方便直接比較。

| slug | 名稱 | 配比 | 一句話 |
|------|------|------|--------|
| `fusion-1-tatami-grid` | 榻榻米網格 Tatami Grid | 70% Swiss / 30% Bento | 瑞士零間距髮絲網格 + 數字主角，鋪在暖榻榻米紙、單一柿紅、狀態色降到極淡 |
| `fusion-2-bento-ledger` | 便當帳本 Bento Ledger | 50 / 50（真中點） | Bento 大小不一圓角磚的構圖，磚內用瑞士髮絲帳本列 + tabular 大數字 + 收斂雙色 |
| `fusion-3-warm-swiss` | 暖色瑞士 Warm Swiss | 30% Swiss / 70% Bento | Bento 漆面暖磚 / emoji / 錶盤全留，但收進嚴格等距網格 + 字級層級 + 索引數字紀律 |

開 `index.html` 即可在最下方「融合實驗」區塊比較三套，或回覆 **「用 融合N」/「用 fusion-N」** 落地。

## 選定之後

挑好後回覆 **「用 N 號」**（例如「用 1 號」/「用 7 號」，融合案用「用 融合2」）。我會把該方案落地成正式 `templates/dashboard.html`：

- **完整保留現有 JS / data hook**：element ID、status class、`data-*` 屬性、polling 節奏與 optimistic / pending button-lock 行為一律不動。
- **只替換視覺層（HTML 結構 + CSS）**，不動後端、不動裝置輪詢與 control panel 路由邏輯。
- 落地後在 Playwright（1440 / 390）live-verify，確認 0 console error 且所有控制流程仍正常。

## 檔案清單

```
docs/dashboard_redesigns/
├── index.html              # 比較用 landing（先開這個）
├── README.md               # 本檔
├── dark-glass-cockpit.html # 1
├── swiss-grid.html         # 2
├── neo-brutalist.html      # 3
├── editorial-magazine.html # 4
├── bento-modular.html      # 5
├── retro-terminal.html     # 6
├── light-soft-ops.html     # 7
├── fusion-1-tatami-grid.html   # 融合 2+5 · Swiss 主
├── fusion-2-bento-ledger.html  # 融合 2+5 · 50/50
└── fusion-3-warm-swiss.html    # 融合 2+5 · Bento 主
```

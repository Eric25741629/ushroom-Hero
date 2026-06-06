# 飛寵管理 UI 重設計 — 探索與決策交接文檔

> 狀態:**已實作完成**(2026-06-06)。選定 **B 卡片牆 + C 左側種類欄**,真實圖標已接。詳見下方第 9 節。
> 產物:`mockups/fly_pet/`(離線原型,保留作參考)→ 已落地進正式 `templates/fly_pet.html`
> 待辦:控制台**需重啟**才會註冊圖標路由(見第 9 節)

---

## 1. 為什麼做這件事(使用者痛點)

現行 `templates/fly_pet.html` 的飛寵列表,使用者反映:

1. **抓不到指定的飛寵類別** — UI 沒有「種類/config_id」篩選,無法只看某一種飛寵。
2. **看不到圖標** — 列表全是文字,沒有飛寵頭像。
3. **過濾太粗糙** — 快捷篩選只有 4詞條/變異/工作/史詩/負面/全部;進階篩選還要「選類型 → 選值 → 按新增」,很麻煩。
4. **300+ 隻怎麼顯示與管理** — 規模化的顯示與批次操作要想清楚。
5. **以種類為介面可以,但要有開關** — 種類分組視圖要能跟平鋪切換。
6. **介面別太複雜** — 站在使用者角度,過複雜難用。
7. **手機要能自適應**。

歸納成 6 個驗收痛點(原型與 review 都對齊這 6 點):

| # | 痛點 |
|---|------|
| 1 | 精準篩出「單一指定種類」 |
| 2 | 每隻有可見圖標(設計主體之一) |
| 3 | 更細但更簡單的篩選(即時、一鍵、單一搜尋框同比對種類名+詞條名) |
| 4 | 300+ 隻流暢顯示與管理(視窗化/分頁、總數+篩選數、批次選取+分解、sticky 批次列) |
| 5 | 種類分組 ↔ 平鋪 一鍵切換開關 |
| 6 | 手機自適應(viewport、不橫捲、觸控≥40px、篩選收抽屜、版面重排) |

---

## 2. 產出了什麼

用 workflow 開 10 個 subagent,3 版各走 **build → 對抗式 review → refine** 三關,再合成比較頁。

| 檔案 | 內容 |
|------|------|
| `mockups/fly_pet/index.html` | 比較頁(先開這個):三版連結 + 6 痛點 ✓/◐/○ 對照表 |
| `mockups/fly_pet/version-a-ops-table.html` | **A 密集戰情表** |
| `mockups/fly_pet/version-b-gallery.html` | **B 圖鑑卡片牆** |
| `mockups/fly_pet/version-c-species-browser.html` | **C 種類瀏覽器** |
| `mockups/fly_pet/README.md` | 三方向簡述 + 開啟說明 |

三版皆為**單檔離線 HTML**,內含 312 隻 mock 飛寵(40 種類、固定亂數種子 `20260605`,三版資料一致方便比較),`node --check` 通過、無外部資源。

### 怎麼打開(未來回來時)

桌面:直接雙擊任一 `.html` 即可(離線自足,不需伺服器)。

手機測自適應(同 Wi-Fi)需起一個靜態伺服器:

```powershell
cd "C:\nas同步_project\菇勇者全自動掛機\mockups\fly_pet"
python -m http.server 8769 --bind 0.0.0.0
```

- 桌面:http://127.0.0.1:8769/index.html
- 手機:`http://<本機Wi-Fi_IP>:8769/index.html`(上次偵測到的 Wi-Fi IP 是 `192.168.31.175`,可能會變;用 `ipconfig` 確認。手機連不上多半是 Windows 防火牆擋了 python)

---

## 3. 三個方向

### A · 密集戰情表 (`version-a-ops-table.html`)
給「300 隻要快速管理/批次分解」的進階使用者。頂部一條工具列:全域搜尋 + 可搜尋種類多選下拉 + 品質/詞條一鍵 chip + 分組/平鋪開關。桌面表格**視窗化**(312 筆只渲染約 20 列)。sticky 批次列(全選略過鎖定/反選/分解)。手機轉成每隻一張堆疊卡 + 篩選抽屜。
**最適合**:效率、密度、大量批次操作。

### B · 圖鑑卡片牆 (`version-b-gallery.html`)
圖標當主角,靠外觀辨識。響應式卡片牆(桌機多欄、平板 2-3 欄、手機 1-2 欄),平鋪用 60/批無限捲動。種類藥丸橫向捲,品質快捷,搜尋同比對種類+詞條。分組時用種類分區標題隔開。手機篩選收進 bottom sheet。
**最適合**:視覺辨識、圖標需求發光、手機瀏覽舒適。

### C · 種類瀏覽器 (`version-c-species-browser.html`)
左種類欄(圖標+種類名+數量+最高品質)/右內容雙層瀏覽。點種類 → 右側只顯示該種類。收合左欄 = 平鋪全部(這就是種類視圖開關)。手機是「種類清單 → 點進詳情 → 返回」兩屏下鑽。
**最適合**:直接打中「我要抓指定類別」的雙層瀏覽。

---

## 4. 對抗式 review 結論(誠實版)

下表 status 是 **review 當下(refine 前)**;括號註明 refine 已修的部分。

| 痛點 | A | B | C |
|------|---|---|---|
| 1 精準篩種類 | ✓ | ✓ | ✓ |
| 2 圖標 | ✓ | ✓ | ✓ |
| 3 細但簡單篩選 | ✓ | ✓ | ✓ |
| 4 300+ 顯示管理 | ◐→✓ | ◐→✓ | ◐→✓ |
| 5 種類視圖開關 | ✓ | ✓ | ✓ |
| 6 手機自適應 | ◐→✓ | ◐→✓ | ◐→✓ |
| review 當下 scale_ok | ✗→已修 | ✗→已修 | ✓ |
| review 當下 mobile_ok | ✗→已修 | ✓ | ✓ |

**痛點 1/2/3/5 三版都紮實達標**(經各 review 的靜態+harness 驗證:種類精準隔離、petIcon 為一級圖標、搜尋同比對種類+詞條、分組/平鋪一鍵切換)。

差異集中在痛點 4(規模化視窗化)與痛點 6(觸控目標),refine 已逐項修補:

- **A**:refine 修了①篩選後留白空屏 bug(`refresh()` 補 `viewport.scrollTop=0`)、②手機卡片用 `content-visibility:auto` 緩解 312 卡渲染、③手機觸控目標補到 ≥40px,另修 3 個 low(搜尋 trim、鎖定列分解鈕 toast 語意、`currentFiltered` 重算)。
- **B**:refine 修了①群組視圖補 60/批視窗化(`appendGroups`/`appendNext`)、②**加 `esc()` 防 XSS**(接真實資料前必修)、③首屏補滿、④觸控/help note 裁切。
- **C**:review 當下兩項 ok 都 true(最完整);refine 再強化①每組卡片 cap(首屏 224→95 卡)、②載入更多改增量 append、③刪死碼/殘留樣式/手機核取方塊命中區。

> 完整 review JSON + refine 清單見 workflow 結果(本 session task `wacmydooc`)。

---

## 5. 跨版共通的已知限制(實作前必讀)

1. **圖標是佔位**:原型用 `petIcon()` 依 `config_id` 算 hue 的漸層頭像(品質色環+星級),**不是真實遊戲貼圖**。真正落地要另做「真實 sprite dump + 快取」(見下節)。三版的 icon 槽都設計成「把 `.glyph`/`.av` 內層換成 `<img>` 即可」。
2. **資料是 mock**:312 隻、40 種、固定種子。真實資料來自 `/api/fly_pet_list`。
3. **真實資料要跳脫**:B/C 的 review 指出真實 `display_name`/`name`(玩家可命名)若不 `esc()` 會有儲存型 XSS。B 已加 `esc()`;**選 A 或 C 落地時務必補上跳脫**(A 的列表用硬編 mock 沒踩到,但接真實資料同樣要 esc)。

---

## 6. 待決策

**使用者要選:哪一版當基底,或 A/B/C 混搭哪些點。**

可能的混搭方向(供參考):
- 桌面用 A 的密集表 + 手機用 B 的卡片牆(同一資料、兩種 render)。
- 用 C 的「左種類欄」當主導航 + 右側內容借 A 的視窗化表格。

---

## 7. 落地實作參考(選定方向後)

### 要改的正式檔
- `templates/fly_pet.html`(現 1678 行)。**只重做「飛寵列表」區塊的篩選與顯示**;繁殖/自動繁殖/搭檔三個 section 要保留。

### 資料來源(已存在的後端)
- `GET /api/fly_pet_list/<ip>` → `pets[]`,每隻欄位:
  `id, config_id, name, display_name(種類名), quality(0-3 白藍紫金), level, fight(0/1), generation, growth(/10000 顯示), step, lock(0/1), star, entries[]{id, level, name, quality(1-7), desc, ...}`
  (`control_panel_app.py` ~line 2067)
- `GET /api/fly_pet_catalog/<ip>` → `{species:[{id,name}], entries:[{id,name,quality}]}`
  全種類清單(來自 `configFly.datas`,連你 0 隻的種類也有)+ 全詞條,可餵種類篩選下拉。(~line 2692)
- 分解:`POST /api/fly_pet_resolve/<ip>`;品質色 EQ/PQ 對照在現行 `fly_pet.html` 開頭已有。

### 真實圖標 dump + 快取(另一個子任務)
- 飛寵 sprite 在遊戲 bundle 路徑(如 `fly_pet/petN`),部分頭像走 `loadRemote` + `/head/` CDN。
- 建議作法:讓**運行中的遊戲**把每個 unique `config_id` 的 sprite 畫到 offscreen canvas → `toDataURL` → 後端依 `config_id` 快取(約 40-80 個 unique 種類,不是 300,可行)。前端 `<img src>` 指向快取;抓不到就 fallback 回原型的彩色頭像。

### 硬性約束(別踩雷)
- **「詞條品質」欄已於 2026-06-04 應使用者要求移除,別加回去**;`entry_quality` 排序邏輯保留為預設,詞條 chip 照品質排序只留顏色。相關測試已鎖死(`tests/test_fly_pet_template.py` 等)。改版時沿用此約束。
- 改 `templates/` 後記得對照 bot 啟動時間,正式頁要重啟才生效。

### TDD
- 動 `templates/fly_pet.html` 前先看/補 `tests/test_fly_pet_template.py`、`tests/test_dashboard_template.py`,先寫失敗測試再實作。

---

## 8. 相關檔案索引

- 原型:`mockups/fly_pet/{index,version-a-ops-table,version-b-gallery,version-c-species-browser}.html` + `README.md`
- 後端:`control_panel_app.py`(`fly_pet_list` 2067 / `fly_pet_catalog` 2692 / `fly_pet_resolve` 2199 / `fly_pet_find_pair` 2572)
- 正式前端:`templates/fly_pet.html`
- 協議:`docs/protocol/FLYPET_PROTO_SCHEMA.md` / `.json`
- mock 資料產生器:三版 HTML 內嵌的 `generatePets()`(種子 20260605)

---

## 9. 實作完成 (2026-06-06)

**選定方向**:B 圖鑑卡片牆為基底 + C 的左側種類欄。圖標決議改為**直接做真圖標**(使用者要求與遊戲內一致,可抓包)。

### 已落地
- `templates/fly_pet.html`:列表區換成 `.flypet-gallery`(B 卡片牆 + C 種類欄 + 分組/平鋪開關 + 單一搜尋同比對種類+詞條 + pq/eq chips + 60/批 IntersectionObserver 視窗化 + sticky 批次列 + 手機 bottom-sheet/兩屏下鑽 + 單隻詳情抽屜含 設為基底/A/B + 分解)。CSS 全 scoped 於 `.flypet-gallery`,繁殖/自動繁殖/搭檔/連線**零改動**。鎖死契約(詞條品質欄不回歸、`var sortCol='entry_quality',sortAsc=false`、`ecHTML`/`sortedEntries`/`POSITIVE_QUALITY_RANK` 等)全保留。
- 測試:`tests/test_fly_pet_gallery.py`(21)+ `tests/test_fly_pet_icon_endpoint.py`(3);與既有回歸合計 **50 passed**。
- `petIcon(p)`:`<img src="/api/fly_pet_icon/<config_id>">`,404 時 onerror 隱藏 img、露出彩色佔位頭像(slot 設計成日後只換 img src)。

### 真實圖標管線(canvas dump 受阻 → 改 atlas 同源重抓裁切)
- 來源:bundle-res 每隻一張 spriteFrame `ui/atlas/icon_flypet/fly_{config_id}`(共 **38 種**)。
- 取法:CDP attach live 遊戲(device `7fe98fc6` 小寶帳號,`web_debug_port` 9226)→ 對每個 config_id 載入 `cc.SpriteFrame` → `cc.assetManager.utils.getUrlWithUuid(imgAsset._uuid, {isNative:true,...})` 解出**同源** atlas PNG URL(`assets/bundle-res/native/<2>/<uuid>.<hash>.png`)→ `new Image()`(同源不 taint)重抓整張 atlas → 依 `sf.rect` 裁出單張 → `toDataURL` → 寫 `static/flypet_icons/{config_id}.png`(154x154)。
  - 關鍵坑:Cocos 3.6.3 web 把貼圖上傳 WebGL 後**釋放 CPU 原圖**,`imgAsset.data`/`getHtmlElementObj()`/`nativeUrl` 皆空 → 不能直接 `drawImage(texture)`;必須走 `getUrlWithUuid` 重抓原 PNG。
- 工具:`tools/dump_flypet_icons.py`(`probe <id>` / `urlprobe <id>` / `dump`)、`tools/flypet_icon_recon.py`(只讀 recon)。**新增/改版飛寵時重跑 `dump` 即可**(需 live 遊戲)。
- 後端:`control_panel_app.py` `/api/fly_pet_icon/<int:config_id>` 服務快取 PNG(404 → 前端 fallback;`<int:>` 擋路徑穿越);已從全域 `add_no_cache_headers` 的 no-store **豁免**以便瀏覽器快取。

### 待辦 / 注意
- **控制台需重啟**才會註冊新路由 `/api/fly_pet_icon`(templates 會 auto-reload,但 Python route 不熱載;見 [[feedback_bot_restart_after_file_fix]])。重啟前新版頁面會顯示但圖標暫走佔位;重啟後顯示真圖。
- `static/flypet_icons/*.png`(38 檔)是 live dump 產物,是否進版控待決(進版控=他機免重抓;或 gitignore 由工具重生)。
- 真實 pet `config_id` ↔ `fly_{config_id}` 對應高度確信(同為 species id),live 頁面實渲染待重啟後最終確認。

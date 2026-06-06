# 前端串流服務 + 截圖管線優化報告

> 產出方式：多代理人稽核（29 agents），每條建議都做過「是否已實作 / API 是否屬實 / 真實效益」對抗式驗證。
> 關鍵數據以**實測**為準（直接讀取已安裝的 playwright 1.58.0 原始碼、實際呼叫 CDP `Page.captureScreenshot` 量測 byte 大小、追蹤所有 OCR/CNN 消費端）。
> 日期：2026-06-05

---

## 0. 結論先講（最重要的前提）

你把「截圖」當成一件事，但程式裡其實是**兩條完全獨立的路徑**，優化方向相反：

| 路徑 | 程式碼 | 用途 | 瓶頸性質 | 現況 |
|------|--------|------|----------|------|
| **A. Bot 自身截圖** | `device_wrapper.py` `screenshot()` | 餵 OCR / CNN / 挖礦 / 神燈 | **速度（latency）**；本機 / 區網 IPC，非 WAN | **用 PNG，沒優化過 → 真正的大獎在這** |
| **B. Live-view 串流** | `runtime_services/live_view_bridge.py` + `dashboard.html` | 人工遠端接管時看畫面 | **流量（WAN 頻寬）** | **已高度優化**；剩下的多是邊際小贏 |

- 你說「截圖速度應該可以再優化」→ 主要指 **A**，而 A 確實有 **2~4 倍**的實測加速空間（PNG → JPEG）。
- 你說「想降低流量」→ 主要指 **B**（以及儀表板的 JSON 輪詢），但 B 的低垂果實大多已經被你自己摘過了；真正剩下的最佳槓桿是**可設定的 fps 上限**。

**一句話路線圖**：先做 P0 的準確率 benchmark（門檻）→ 過了就把 A 改 JPEG（最大速度贏）→ 再對 B 加 fps 上限與設定化（流量贏）。

---

## A. Bot 自身截圖 —— 速度的最大贏面

### 現況（已驗證）
`device_wrapper.py` 的 `PlaywrightGameDevice.screenshot()`（約 1020-1067 行）：
- 預設 `web_screenshot_method='playwright'`（`bot_config.json` 全部 6 台都是這個）→ 呼叫 `_page.locator(canvas).screenshot()`，**Playwright 預設輸出 PNG**（無損、肥、編碼慢）。
- 另一條 `canvas_capture` 走 `canvas.toDataURL('image/png')`（998 行）→ PNG + base64（+33% 膨脹）+ JS→CDP 橋接序列化，是最慢的。
- 兩條最後都 `cv2.imdecode` → BGR，再 `cv2.cvtColor` → RGB → PIL。

消費端（決定能不能無腦改）：OCR 數字（`miner/core/ocr_utils.py` 的 `check_pickaxe_count` / `check_drill_num` / `check_boom_num`）、紅點偵測（`img_tools.py` HSV `contourArea>46`）、CNN 方塊分類（`miner/models/classifier.py`，每格縮到 64x64）。

### 實測數字（同一張 540x960 真實遊戲畫面）

| 方法 | 每張耗時 | 每張 bytes | 對比預設 PNG |
|------|---------|-----------|-------------|
| Playwright PNG（現況預設） | **130.4 ms** | ~549 KB | 1.0x |
| Playwright JPEG q85 | **62.4 ms** | ~225 KB | **2.1x 快 / 2.4x 小** |
| **CDP `Page.captureScreenshot` jpeg q85** | **33.1 ms** | ~225 KB | **3.9x 快** |
| CDP `captureScreenshot` webp q85 | ~同上 | **~186 KB** | 最小 |
| `toDataURL` jpeg q85 | ~17 ms* | ~225 KB | *合成畫面，非真實值，僅參考 |

> 一趟挖礦約 100 張截圖 → 光 Playwright→JPEG 就省約 6.8 秒/趟，dashboard 的 `avg_screenshot_ms` 直接砍約一半（**僅 web_h5 裝置**；ADB/uiautomator2 走另一條 `device_wrapper.py:356`，不受影響）。

### ⚠️ 唯一的真風險：JPEG 失真會不會傷 OCR/CNN
q85 並非「近乎無損」——實測平均像素差 5.7、**最大差 147**（文字/邊緣的高頻 ringing）。最敏感的是小數字 OCR 與紅點 `contourArea` 門檻；CNN 風險較低（縮到 64x64），但**模型是用 PNG 訓練的**，存在 train/serve 偏移。**所以這條一定要先過 benchmark，不能直接預設 JPEG。**

### 建議
- **P0（門檻，先做）— 建立準確率 benchmark**：擴充 `benchmark_screenshot.py`，在 q70/80/85/90/100 各跑一次，(a) OCR 字串 diff、(b) `classifier.classify_board` label diff，挑「零 drift 的最低 q」當預設。需要先補真實挖礦盤面 PNG fixtures（`tests/images/` 目前只有神燈 UI 圖）。
- **P1（速度大獎，benchmark 過了再做）**：
  - 低改動版：`device_wrapper.py:1045-1057` 兩處 `.screenshot()` 傳 `type='jpeg', quality=<config>`，新增 config key `web_screenshot_jpeg_quality`（預設 None=維持 PNG 當逃生門）。**API 已驗證**：playwright 1.58.0 的 `Page.screenshot`/`Locator.screenshot` 都吃 `type:Literal['jpeg','png']` + `quality:int`。
  - 進階版（最快）：開一個 per-device 持久 CDP session 走 `Page.captureScreenshot{format:'jpeg',quality}`（3.9x）。代價是要管 CDP session 生命週期（`_restart_browser_session` / headful 切換 / page swap 都要 re-bind），複雜度較高，建議先上低改動版、之後再評估。
- **P1（順手、零風險）**：`miner/planning/executor.py:264` 與 `:506` 兩處 `d.screenshot()` 補 `format='opencv'`（其餘 5 處 173/178/237/260/438 早就這樣寫了），省掉每張多餘的 `cvtColor`+`Image.fromarray`。像素結果不變，**不需 benchmark**。

---

## B. Live-view 串流 —— 流量優化

### 重要事實：這不是閒置就沒流量
這是 Cocos Creator / WebGL 遊戲（`cc.director`），有**連續的 rAF render loop**，所以即使在「靜態」選單，畫面也持續重繪 → CDP screencast 會**持續吐 frame**（不是靜止就 0 frame）。這帶來兩個結論：
1. **fps 上限是這裡最有效的槓桿**（因為串流是連續的，封頂真的會咬到）。
2. **byte-hash 去重（靜態抑制）效果差**：連續重繪的 JPEG 因 GPU readback 次像素差異而非 byte-identical，hash 命中率低 → **不建議做**。

### 建議（皆為 adopt-with-care，預設不改變現況）
| 項目 | 改法 | 效益（流量） | 風險 |
|------|------|-------------|------|
| **fps 上限**（最佳槓桿） | `live_view_bridge.py:272-275` 加可設定 `target_fps`(預設 15-20)；收到 frame **照常立刻 ack**（維持 Chrome backpressure），只在時間窗內跳過 outbox 存放 | 持續動態時 **省 ~50-75%**；過低會讓拖曳/捲動變鈍 | UX，靠 config 調 |
| **設定化 tunables** | `control_panel_app.py:1700-1719` 從 `global.live_view` 讀 `jpeg_quality`(現況永遠 default 60，根本沒傳)、`everyNthFrame`(現況硬編 1)、viewport | q60→q40 約小 20-35%；`everyNthFrame=2` 砍半擷取 cadence | 低，預設不變 |
| **降解析度（慢線路 opt-in）** | `startScreencast` 的 `maxWidth/maxHeight` 由 540x960 降到 405x720(0.75x) | 每張 **省 ~30-44%** bytes | 小字/圖示變糊；座標映射不受影響（`_norm_to_css` 用 metadata，已驗證安全） |

> 量級感（每張 q60 ~60KB、連續重繪假設）：現況約 ~14 Mbps → 封 15fps 約 ~7 Mbps → 再加降解析度/降質可到 ~3-4 Mbps。**僅手動接管串流，內容相依，非保證值。**

### 客戶端 render（`dashboard.html`，零頻寬影響、純流暢度）
- **`alpha:false`（建議直接做，免費）**：`dashboard.html:2770` 改 `getContext('2d', { alpha: false })`。JPEG frame 全不透明，省每次 present 的 alpha 合成。一行、無風險。
- **rAF-gate 繪製（adopt-with-care）**：把 `decodeAndDraw` 的 `drawImage` 從「每解一張畫一張」改成「每個 compositor tick 只畫最新一張」（`dashboard.html:2786-2818`）。只有在 server 爆發 >60fps 時有意義（本遊戲連續重繪有可能），但要小心 `ImageBitmap` 生命週期 + 關窗時 cancel rAF。

---

## C. 傳輸 / 部署 / 輪詢（流量的另一半）

| 項目 | 評級 | 重點 |
|------|------|------|
| **JSON 回應加 gzip** | adopt-with-care | `/api/status` 每秒輪詢，實測 2880B→412B（~7x）。用 stdlib `gzip` 寫 `after_request`（守 `application/json`、設 `Vary`、跳過 `/ws/`），不必引入 Flask-Compress。**但**遠端走 Cloudflare Tunnel 已在 edge 壓過，所以對遠端使用者只省 origin→CF 那段；量級單位數 MB/hr，區網可忽略 |
| **daily_progress 快取被 heartbeat 沖掉** | adopt-with-care | **只有 master+worker 拓撲才嚴重**（worker 每 ~1.2s POST 全狀態 → `update_state` 重蓋 `last_update` → 每台每 1.2s 重抓一次 per-device JSON）。你目前是單機全本機 master，幾乎無 churn。修法選 client 端加 30-60s min-refresh 地板即可。注意：原提案點名的 `update_watchdog_probe` 其實**沒有任何 runtime 呼叫者**（死碼） |
| **pin gunicorn worker class** | adopt-with-care | 上 VPS 前的部署防呆，非 runtime 改善。flask-sock 官方文件：sync 模式**只支援 `--worker-class gthread --threads N`**（plain sync worker 不行）。repo 內沒有任何 Procfile/gunicorn 設定檔，建議補一份部署文件並 live-verify |

---

## D. 不要做 / 已經做好了（省下你的時間）

驗證後判定 **reject 或 already-implemented** 的項目，附原因：

| 項目 | 判定 | 原因 |
|------|------|------|
| startScreencast 改 WebP | ❌ 不可能 | CDP `startScreencast` 的 format **只允許 jpeg/png**（WebP 只在 `captureScreenshot` 有，即 A 路徑） |
| 立即 ack 策略 | ✅ 已最佳 | `live_view_bridge.py:253-258` 已是 ack-first + drop-old，文件字串也寫了；只差一行註解 |
| 統一 motion-gated 狀態機（fps+抑制+自適應質） | ❌ 過度設計 | 單一人工接管功能塞三個啟發式 + 狀態機，違反專案 KISS/YAGNI；先各別做 fps 上限再說 |
| OffscreenCanvas + Worker | ❌ 過頭 | 解碼**已經**靠 `createImageBitmap` 在主執行緒外做；無 jank 證據 |
| WebCodecs ImageDecoder | ❌ 無益 | 單張靜態 JPEG，`createImageBitmap` 已夠好；ImageDecoder 的優勢（持久 decoder/動畫軌）用不到 |
| backing-store 依 DPR 縮小 | ❌ 邊際 | 已是 sub-MB texture，省 sub-ms；HiDPI 上還會自我抵銷 |
| byte-hash 靜態抑制 | ⚠️ 命中率低 | 遊戲連續重繪 → 幀非 byte-identical，hash 抓不到，省不了多少 |
| 降頻 `fetchStatus` + 關閉隱藏面板輪詢 | ✅ 大半已做 | labeler/trainer 輪詢**早就 gated**（`currentPage!=='labeler'` early-return）；降 `fetchStatus` 頻率前得先做 client 端倒數 ticker，否則倒數會跳格、pause/resume 變鈍 |
| CNN 幀降解析度/灰階 | ❌ 已做/做不到 | 每格**已縮到 64x64**；灰階不可能（`simplecnn.py` 硬編 `Conv2d(3,...)`，且該幀同時餵 OCR 需全解析度） |

---

## 落地順序（建議）

```
P0  準確率 benchmark（門檻，沒它不准把 A 路徑改 JPEG）         [medium]
     └─ 補挖礦盤面 fixtures + OCR 字串/CNN label diff，挑安全 q 下限

P1  A 路徑速度大獎（benchmark 過了）
     ├─ device_wrapper JPEG（config flag，預設 None=PNG）      [low]   2.1x 快
     ├─（之後）持久 CDP captureScreenshot                       [medium] 3.9x 快
     └─ executor.py:264/506 補 format='opencv'（免風險）        [low]

P2  B 路徑流量（你的「降低流量」主訴）
     ├─ live_view fps 上限（最佳槓桿，config 化）               [low]   省 50-75%(動態)
     ├─ live_view tunables 設定化（quality/everyNthFrame/vp）   [low]
     ├─ client：getContext alpha:false（免費）                  [low]
     └─（慢線路 opt-in）降解析度 maxWidth/maxHeight             [low]   省 30-44%/張

P3  傳輸/部署衛生
     ├─ JSON gzip（stdlib after_request）                       [low]
     ├─ daily_progress min-refresh 地板（只在 worker 拓撲有感）  [low]
     └─ pin gunicorn gthread worker + 部署文件（上 VPS 前）      [medium]
```

**重點取捨**：你的「截圖更快」幾乎全在 **P1 的 A 路徑**（但被 P0 benchmark 把關）；你的「降低流量」幾乎全在 **P2 的 B 路徑 fps 上限**。其餘是邊際或部署衛生。

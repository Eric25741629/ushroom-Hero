# push_project 優化建議

> 分析日期：2026-05-27
> 分析範圍：`push_project/` 下的 server（app.py, generate_vapid.py, requirements.txt）、web（菇勇者.html, sw.js, manifest.json）及 README.md

---

## 一、安全性問題 🔴

### 1.1 Flask 以 `debug=True` + `host='0.0.0.0'` 啟動（嚴重）

**位置：** `server/app.py` 最後一行

```python
app.run(host='0.0.0.0', port=PORT, debug=True)
```

**風險：**
- `debug=True` 會啟用 Werkzeug debugger，暴露互動式 Python shell。若服務對外開放，攻擊者可在伺服器上執行任意程式碼。
- `host='0.0.0.0'` 表示監聽所有網路介面，區域網路內任何裝置皆可存取。

**建議：**
```python
# 生產環境
if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='127.0.0.1', port=PORT, debug=debug)
```
生產部署時應改用 Gunicorn / Waitress 等 WSGI 伺服器，不要用 Flask 內建開發伺服器。

---

### 1.2 無任何 API 鑑權（嚴重）

**受影響端點：**
- `POST /send-push` — 任何人都能對所有訂閱者發送推播
- `POST /send-push-single` — 任何人都能對單一訂閱者發送推播
- `POST /clear-subscriptions` — 任何人都能清除所有訂閱
- `GET /subscriptions` — 任何人都能查看所有訂閱資訊（含 endpoint、金鑰）

**建議：**
- 加入 API Key 或 Bearer Token 驗證（至少在管理端點上）
- `/subscriptions` 應限制為僅本機存取或加密碼保護
- `/send-push` 與 `/clear-subscriptions` 必須有鑑權

簡易實作：
```python
import os

API_KEY = os.environ.get('PUSH_API_KEY', '')

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if API_KEY:
            auth = request.headers.get('Authorization', '')
            if auth != f'Bearer {API_KEY}':
                return ('Unauthorized', 401)
        return f(*args, **kwargs)
    return decorated

@app.route('/send-push', methods=['POST'])
@require_auth
def send_push():
    ...
```

---

### 1.3 VAPID 私鑰片段被印到 console

**位置：** `server/app.py` 第 46-53 行

```python
print(f'ℹ️ VAPID_PRIVATE_B64 snippet: {repr(snippet)}')
```

**風險：** 日誌可能被收集、外洩。私鑰片段不應出現在任何日誌中。

**建議：** 移除所有印出私鑰內容的 diagnostic log，只保留長度資訊即可。

---

### 1.4 `/subscriptions` 端點暴露敏感資料

**位置：** `server/app.py` `list_subscriptions()` 函式

**風險：** 回傳所有訂閱者的 `endpoint`、`p256dh`、`auth` 金鑰。這些是推播加密所需的完整材料。

**建議：** 移除此端點，或至少加密碼保護 + 限制為 `127.0.0.1`。

---

### 1.5 VAPID claims 使用 placeholder email

**位置：** `server/app.py` 第 147 行

```python
'vapid_claims': {"sub": "mailto:you@example.com"}
```

**風險：** 推播服務供應商（如 FCM、Mozilla）需要真實的聯繫方式。使用假 email 可能導致推播被拒絕或無法排查問題。

**建議：** 設為環境變數或真實聯繫信箱。

---

### 1.6 無 rate limiting

`/send-push` 與 `/save-subscription` 沒有速率限制，可能被濫用發送垃圾推播或耗盡伺服器資源。

**建議：** 使用 `flask-limiter` 或 Nginx 反向代理做 rate limiting。

---

### 1.7 無 CORS 限制

Flask 未設定 CORS，任何外部網站都可呼叫 API。

**建議：** 若前端與 API 同源，明確限制 CORS：
```python
from flask_cors import CORS
CORS(app, origins=["http://127.0.0.1:5000"])
```

---

### 1.8 無輸入驗證

`/save-subscription` 僅檢查 `endpoint` 是否存在，未驗證 subscription 格式。惡意資料可能導致後續 `webpush()` 呼叫產生非預期錯誤。

**建議：** 驗證 subscription JSON 結構，確認 `keys.p256dh` 與 `keys.auth` 存在且格式正確。

---

## 二、架構問題 🟡

### 2.1 訂閱資料以 JSON 檔案持久化（不適合多實例/高併發）

**現狀：** `subscriptions.json` 在每次新增訂閱時全量寫入。

**問題：**
- 每次 `save-subscription` 都對磁碟做完整序列化，效能差
- 多程序/多實例部署時會有競爭條件
- JSON 檔案損毀時無備份機制

**建議：**
- 短期：改用 atomic write（先寫 tmp 再 rename）
- 中長期：遷移到 SQLite（單機）或 Redis/PostgreSQL（多實例）

```python
import tempfile, os

def atomic_write_json(path, data):
    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except:
        os.unlink(tmp)
        raise
```

---

### 2.2 訂閱未去重

`save_sub()` 直接 `append`，同一個 endpoint 可能被加入多次，導致重複推播。

**建議：** 以 `endpoint` 為 key 做去重：
```python
with _subs_lock:
    existing = next((s for s in SUBSCRIPTIONS if s.get('endpoint') == sub.get('endpoint')), None)
    if existing:
        existing.update(sub)  # 更新 keys
    else:
        SUBSCRIPTIONS.append(sub)
```

---

### 2.3 線程安全問題

`send_push()` 中以 `SUBSCRIPTIONS[:]` 做淺拷貝迭代，但在移除無效訂閱時又修改原始列表。雖然有 `_subs_lock`，但迭代本身未加鎖。

**建議：** 在 `send_push` 開始時加鎖做 snapshot：
```python
with _subs_lock:
    subs_snapshot = list(SUBSCRIPTIONS)
```
然後只在移除時加鎖。

---

### 2.4 `_norm_b64url` 重複定義

`send_push()` 和 `send_push_single()` 各自定義了相同的 `_norm_b64url` 函式。

**建議：** 提取為模組級工具函式。

---

### 2.5 VAPID 金鑰轉換邏輯過於複雜

`app.py` 中 PEM → base64url 的轉換、診斷日誌、自動生成等邏輯混雜在模組頂層。若轉換失敗，靜默 fallback 可能導致後續推播以不明原因失敗。

**建議：**
- 將金鑰載入邏輯抽取為獨立函式 `load_vapid_keys()`
- 轉換失敗時應明確報錯退出，不要靜默繼續
- 考慮統一為一種金鑰格式（全部用 base64url 或全部用 PEM）

---

### 2.6 `generate_vapid.py` 產出格式與 `app.py` 預期不完全一致

`generate_vapid.py` 寫入 PEM 格式的私鑰，但 `app.py` 需要 base64url 格式。雖然 `app.py` 有自動轉換，但增加了複雜度與失敗點。

**建議：** 讓 `generate_vapid.py` 同時輸出兩種格式，或統一為一種。

---

### 2.7 缺少健康檢查端點

無 `/health` 或 `/ready` 端點，無法用於監控或負載均衡器健康檢查。

**建議：**
```python
@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'subscriptions': len(SUBSCRIPTIONS),
        'vapid_configured': VAPID_PUBLIC != 'PLACEHOLDER_PUBLIC_KEY'
    })
```

---

### 2.8 `requirements.txt` 缺少版本鎖定

**現狀：** 使用 `>=` 最低版本約束，不同環境可能安裝不同版本。

**建議：** 使用 `pip freeze` 產出 `requirements.lock`，或改用 `==` 精確鎖定。

---

## 三、前端 PWA 設計問題 🔵

### 3.1 Service Worker 缺少 `fetch` 事件處理（無離線支援）

**現狀：** `sw.js` 僅處理 `push` 和 `notificationclick` 事件，沒有 `fetch` 事件。

**影響：** 離線時無法載入頁面，不是真正的 PWA。

**建議：** 加入 Cache-First 或 Network-First 策略：
```javascript
const CACHE_NAME = 'mushroom-v1';
const STATIC_ASSETS = ['/', '/菇勇者.html', '/manifest.json'];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', event => {
    // Network-first for JSON data, cache-first for static assets
    if (event.request.url.includes('car_fight.json')) {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request))
        );
    } else {
        event.respondWith(
            caches.match(event.request).then(cached => cached || fetch(event.request))
        );
    }
});
```

---

### 3.2 HTML 缺少 manifest 引用

**現狀：** `<head>` 中沒有 `<link rel="manifest">`，瀏覽器無法自動發現 `manifest.json`。

**建議：** 加入：
```html
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#16213e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
```

---

### 3.3 圖示檔案不存在

`manifest.json` 引用了 `/icons/icon-192.png` 和 `/icons/icon-512.png`，但 `web/icons/` 目錄不存在。Service Worker 的 `showNotification` 也引用了這些圖示。

**影響：** PWA 安裝時無圖示，推播通知無圖示。

**建議：** 生成並放置圖示檔案，至少需要 192×192 和 512×512 兩個尺寸。

---

### 3.4 `manifest.json` 欄位不完整

缺少以下建議欄位：
- `description` — PWA 描述
- `orientation` — 建議鎖定為 `portrait`
- `scope` — PWA 作用範圍
- `id` — 用於識別 PWA 身份

```json
{
    "name": "菇勇者 戰情室",
    "short_name": "菇勇者",
    "description": "跨界車位即時監控與推播提醒",
    "start_url": "/菇勇者.html",
    "scope": "/",
    "id": "/菇勇者.html",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#1a1a2e",
    "theme_color": "#16213e",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
    ]
}
```

---

### 3.5 XSS 風險 — innerHTML 注入未轉義

**位置：** `renderGrid()` 中

```javascript
card.innerHTML = `
    <button class="close-btn" onclick="event.stopPropagation(); hideSpot('${spot.name}')" ...>
    ...
    <div class="spot-name">${spot.name} ...
    <div class="attacker">搶佔者: <span ...>s${attackerShort}</span></div>
`;
```

`spot.name` 和 `attackerShort` 直接插入 innerHTML。若 `car_fight.json` 被竄改或包含惡意內容，可導致 XSS。

**建議：** 使用 `textContent` 或轉義 HTML：
```javascript
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
```

---

### 3.6 AudioContext 在頁面載入時即建立

**現狀：** `const audioCtx = new (AudioContext || webkitAudioContext)();` 在腳本頂層執行。

**問題：** Chrome 等瀏覽器要求用戶互動後才能啟動 AudioContext，否則會自動 suspend。

**建議：** 改為延遲建立，或在首次用戶互動時 resume：
```javascript
let audioCtx = null;
function ensureAudioCtx() {
    if (!audioCtx) audioCtx = new (AudioContext || webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return audioCtx;
}
```

---

### 3.7 時間同步 fallback 到多個外部 API

**現狀：** `syncTaipeiTime()` 依賴 `/api/time`，失敗後依序嘗試 3 個外部時間 API。

**問題：**
- 外部 API 可能不可靠或已停用（如 `worldclockapi.com`）
- 沒有 CORS 設定，跨域請求可能失敗
- 每次同步都嘗試所有 fallback，增加延遲

**建議：**
- 移除已知不可靠的外部 API
- 快取上次成功的同步結果，避免頻繁重試
- 考慮用 `Date.now()` 為主，伺服器校正為輔（而非反過來）

---

### 3.8 1 秒重繪全部卡片的效能問題

**現狀：** `setInterval(() => renderGrid(), 1000)` 每秒完全重建 DOM。

**問題：** 卡片數量多時會造成明顯的 CPU 與記憶體壓力，尤其在行動裝置上。

**建議：**
- 改為只更新倒數數字，不重建整個 DOM
- 使用 `requestAnimationFrame` 代替 `setInterval`
- 或採用 virtual DOM / 差異更新策略

```javascript
// 只更新倒數，不重建卡片
function updateCountdowns() {
    document.querySelectorAll('.card[data-spot]').forEach(card => {
        const endTime = card.dataset.endTime;
        const remaining = calculateRemainingSeconds(endTime);
        const el = card.querySelector('.countdown');
        if (el) el.textContent = formatCountdown(remaining);
        // 更新 CSS class...
    });
}
setInterval(updateCountdowns, 1000);
```

---

### 3.9 `car_fight.json` 使用 cache-busting 但 Service Worker 無快取策略

`fetchData()` 使用 `?t=${new Date().getTime()}` 繞過瀏覽器快取，但若加入 SW 快取策略後，需要妥善處理此 JSON 的更新邏輯。

**建議：** 對動態資料使用 Network-First 策略，對靜態資源使用 Cache-First。

---

### 3.10 Push Status UI 缺失

`initPush()` 引用了 `document.getElementById('pushStatus')` 和 `document.getElementById('enablePushBtn')`，但 HTML 中未見這些元素。

**建議：** 在 HTML 中加入推播啟用按鈕與狀態顯示：
```html
<div class="control-group">
    <label>推播通知</label>
    <button id="enablePushBtn" class="action-btn apply-btn" onclick="initPush()">啟用通知</button>
    <div id="pushStatus" style="font-size:0.8em; color:#888;">未啟用</div>
</div>
```

---

## 四、建議優先順序

| 優先級 | 項目 | 影響 |
|--------|------|------|
| 🔴 P0 | 關閉 `debug=True`，改綁 `127.0.0.1` | 防止 RCE |
| 🔴 P0 | 加入 API 鑑權（至少管理端點） | 防止濫用 |
| 🔴 P0 | 移除 VAPID 私鑰日誌輸出 | 防止金鑰洩漏 |
| 🔴 P0 | 修復 XSS（innerHTML 轉義） | 防止注入攻擊 |
| 🟡 P1 | 訂閱去重 + atomic write | 資料正確性 |
| 🟡 P1 | 線程安全修正 | 穩定性 |
| 🟡 P1 | 加入 manifest link + 圖示 | PWA 可安裝 |
| 🟡 P1 | 補齊 Push Status UI 元素 | 功能完整性 |
| 🔵 P2 | Service Worker 離線快取 | PWA 體驗 |
| 🔵 P2 | DOM 更新效能優化 | 使用者體驗 |
| 🔵 P2 | AudioContext 延遲初始化 | 瀏覽器相容性 |
| 🔵 P2 | 健康檢查端點 | 運維 |
| 🔵 P2 | manifest 欄位補齊 | PWA 標準合規 |

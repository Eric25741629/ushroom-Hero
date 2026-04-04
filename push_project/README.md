# 菇勇者 推播通知專案

這是一個獨立的 Web Push 專案，包含前端與 Python 伺服器，支援桌面與行動裝置的推播通知。

## 專案結構

```
push_project/
├── web/                    # 前端檔案
│   ├── 菇勇者.html          # 主頁面
│   ├── sw.js              # Service Worker
│   ├── manifest.json      # PWA manifest
│   └── car_fight.json     # 範例數據
└── server/                # Python 伺服器
    ├── app.py             # Flask 推播伺服器
    └── requirements.txt   # Python 依賴
```

## 快速開始

### 1. 安裝依賴

```powershell
# 啟用 conda 環境（如果使用）
conda activate mushroom1

# 安裝 Python 套件
cd push_project\server
pip install -r requirements.txt
```

### 2. 產生 VAPID Keys（必要步驟）

Web Push 需要 VAPID 密鑰對。有兩種方式產生：

**方式 A: 使用 Python**
```powershell
python -c "from pywebpush import webpush; print(webpush.generate_vapid_keys())"
```

**方式 B: 使用 Node.js (如果已安裝 npm)**
```powershell
npm install -g web-push
web-push generate-vapid-keys
```

將輸出的 Public Key 和 Private Key 設為環境變數：

```powershell
# Windows PowerShell
$env:VAPID_PUB = "YOUR_PUBLIC_KEY_HERE"
$env:VAPID_PRI = "YOUR_PRIVATE_KEY_HERE"
```

### 3. 啟動伺服器

```powershell
cd push_project\server
python app.py
```

伺服器將在 http://127.0.0.1:5000 啟動

### 4. 開啟網頁

在瀏覽器訪問：http://127.0.0.1:5000

點擊「啟用通知」按鈕，允許通知權限。

### 5. 測試推播

使用 PowerShell 或 curl 發送測試通知：

```powershell
# PowerShell
Invoke-RestMethod -Uri http://127.0.0.1:5000/send-push -Method POST -ContentType "application/json" -Body '{"title":"測試通知","body":"還有 1 分鐘"}'

# 或使用 curl
curl -X POST http://127.0.0.1:5000/send-push -H "Content-Type: application/json" -d "{\"title\":\"測試通知\",\"body\":\"還有 1 分鐘\"}"
```

## 功能特色

- ✅ 倒數最後 60 秒時視覺閃爍
- ✅ 三短音（滴滴滴）提醒
- ✅ Web Push 背景通知（桌面 + 行動裝置）
- ✅ 可自訂提醒時間與重複間隔
- ✅ 支援排除特定伺服器 ID
- ✅ 台北時間自動校正

## 行動裝置測試

### Android (Chrome/Firefox)
- 需要 HTTPS（本機測試用 127.0.0.1，行動裝置需使用 ngrok 或部署到支援 HTTPS 的伺服器）
- 支援完整 Push API 與震動

### iOS (Safari)
- 需要 iOS 16.4 或更新版本
- 需要 HTTPS
- 部分功能可能有限制，建議實機測試

## 使用 ngrok 在行動裝置測試

1. 安裝 ngrok: https://ngrok.com/download
2. 啟動 ngrok:
   ```powershell
   ngrok http 5000
   ```
3. 使用 ngrok 提供的 HTTPS URL 在手機瀏覽器訪問

## 注意事項

- VAPID keys 需妥善保管，不要提交到版本控制
- 生產環境建議使用資料庫儲存 subscriptions（目前使用記憶體）
- 必須使用 HTTPS 才能在實際裝置上測試 Push（127.0.0.1:5000 除外）
- 背景推播無法直接播放自訂音效，需透過點擊通知後由頁面播放

## API 端點

- `GET /` - 主頁面
- `GET /vapidPublicKey` - 取得 VAPID 公鑰
- `POST /save-subscription` - 儲存推播訂閱
- `POST /send-push` - 發送推播通知
- `GET /api/time` - 取得伺服器時間（用於時間同步）

## 疑難排解

**問題：無法取得 VAPID 公鑰**
- 確認已設定 `VAPID_PUB` 和 `VAPID_PRI` 環境變數
- 重新啟動伺服器

**問題：行動裝置收不到通知**
- 確認使用 HTTPS（localhost 不適用於行動裝置）
- 檢查瀏覽器版本（iOS 需 16.4+）
- 確認已授予通知權限

**問題：推播發送失敗**
- 檢查 subscription 是否有效
- 確認 VAPID keys 正確
- 查看伺服器 console 錯誤訊息

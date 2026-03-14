快速說明：用 Flask 提供前端 + 後端（本機測試）

檔案清單
- `app.py` : Flask 後端，會把 `菇勇者.html` 當首頁，並提供 `/api/car_fight` 讀取 `car_fight.json`。
- `requirements.txt` : 需要安裝的套件。

安裝與啟動（PowerShell）
1. (建議) 建立並啟用虛擬環境：
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
2. 安裝套件：
```powershell
pip install -r requirements.txt
```
3. 啟動伺服器：
```powershell
python app.py
```
4. 在瀏覽器開啟：

http://localhost:5000/

注意
- 預設綁定 `127.0.0.1:5000`（只允許本機存取）。若想在同網路的其他設備也能訪問，請把 `app.py` 最下方的 `app.run(host='127.0.0.1',...)` 改成 `host='0.0.0.0'` 並確認防火牆允許該 port。
- 若你原本使用 `菇勇者.html` 的 fetch（例如 `car_fight.json`），在透過此伺服器開啟頁面時會以同一 origin（http://localhost:5000）載入，fetch 就不會被瀏覽器封鎖。
- `app.py` 已啟用 CORS 供開發測試使用（會回傳 Access-Control-Allow-Origin: *），生產環境請收緊規則。

進階
- 若要服務於 HTTPS 或要做更多路由（例如管理介面），可以改用 gunicorn、uvicorn 或加入反向代理 (nginx)。

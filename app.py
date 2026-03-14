from flask import Flask, send_from_directory, jsonify
try:
    from flask_cors import CORS
    _CORS_AVAILABLE = True
except Exception:
    CORS = None
    _CORS_AVAILABLE = False
from pathlib import Path
import json
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=str(BASE_DIR))
# 優先使用 flask_cors，如果沒有安裝則以 after_request 自行加入 CORS headers
if _CORS_AVAILABLE:
    CORS(app)  # 允許跨來源存取 (本機開發用)
else:
    @app.after_request
    def _add_cors_headers(response):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

# 根目錄導向 index HTML
@app.route('/')
def index():
    # 預設開啟菇勇者.html
    return send_from_directory(str(BASE_DIR), '菇勇者.html')

# 靜態檔案（包含 car_fight.json、JS、CSS、圖片等）
@app.route('/<path:filename>')
def static_files(filename):
    # 防止路徑跳脫，僅允許 BASE_DIR 內的檔案
    return send_from_directory(str(BASE_DIR), filename)

# API：直接回傳 car_fight.json 的內容（方便程式化存取）
@app.route('/api/car_fight')
def api_car_fight():
    jf = BASE_DIR / 'car_fight.json'
    if not jf.exists():
        return jsonify({"error": "car_fight.json not found"}), 404
    try:
        with jf.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/time')
def api_time():
    """嘗試從多個公開時間提供者抓取網路標準時間，依序回傳第一個成功的結果。
    若所有外部提供者都失敗，會回傳 502。此路由讓前端可以透過你的域名獲得可靠的網路時間。
    """
    import urllib.request
    import json as _json

    providers = [
        'https://worldtimeapi.org/api/timezone/Asia/Taipei',
        'https://timeapi.io/api/Time/current/zone?timeZone=Asia/Taipei',
        'http://worldclockapi.com/api/json/utc/now'
    ]

    for url in providers:
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                raw = resp.read().decode('utf-8')
            data = _json.loads(raw)

            # 嘗試從常見欄位解析時間字串
            dt_str = None
            for key in ('datetime', 'dateTime', 'currentDateTime', 'utc_datetime', 'utcDateTime'):
                if key in data and data[key]:
                    dt_str = data[key]
                    break

            server_ms = None
            # 有些 provider 可能直接回傳 timestamp 欄位
            for k in ('server_ms', 'timestamp', 'currentEpochMillis'):
                if k in data:
                    try:
                        server_ms = int(data[k])
                        break
                    except Exception:
                        pass

            # 若拿到時間字串，嘗試以 fromisoformat 解析（含 offset）
            if not server_ms and dt_str:
                try:
                    server_dt = datetime.fromisoformat(dt_str)
                    server_ms = int(server_dt.timestamp() * 1000)
                except Exception:
                    # 允許解析失敗，繼續嘗試下一個 provider
                    server_ms = None

            if server_ms:
                return jsonify({'datetime': dt_str or '', 'server_ms': server_ms, 'source': url})
        except Exception:
            # 該 provider 失敗，繼續嘗試下一個
            continue

    return jsonify({'error': 'all external time providers failed'}), 502

if __name__ == '__main__':
    # 開發模式啟動，綁定到本機 5000 埠
    # 若要在同一網路其他設備存取，將 host 改為 '0.0.0.0'
    app.run(host='127.0.0.1', port=5000, debug=True)
from flask import Flask, request, jsonify, abort
import numpy as np
import torch
from paddleocr import PaddleOCR
import cv2
import base64
import time
import os
import ipaddress
import traceback
import threading

app = Flask(__name__)
ocr_lock = threading.Lock()  # 初始化鎖
app = Flask(__name__)

OCR_FAILS_DIR = "ocr_fails_new"
OCR_FAILS_COUNT_FILE = os.path.join(OCR_FAILS_DIR, "count.txt")
MAX_OCR_FAIL_IMAGES = int(os.environ.get("MAX_OCR_FAIL_IMAGES", "10000"))
MIN_OCR_FAIL_SCORE = float(os.environ.get("MIN_OCR_FAIL_SCORE", "0.2"))
MAX_OCR_FAIL_SCORE = float(os.environ.get("MAX_OCR_FAIL_SCORE", "0.8"))
ocr_fails_count_lock = threading.Lock()
ocr_fails_count = None

# IP白名單設定 - 只允許 100.64.0.* 網段
ALLOWED_NETWORK = ipaddress.IPv4Network('100.64.0.0/24')
# ===== 新增：通用 decode 與 dataURL 支援 =====
def strip_data_url(b64_str: str) -> str:
    # 支援 data:image/png;base64,xxxx
    if not b64_str:
        return b64_str
    s = b64_str.strip()
    if s.lower().startswith("data:") and "," in s:
        return s.split(",", 1)[1]
    return s

def decode_base64_image(b64_str: str):
    """base64 -> cv2 image, with retries. return (img, err_str_or_None)"""
    b64_str = strip_data_url(b64_str)

    img = None
    decode_err = None
    for attempt in range(IMG_DECODE_RETRIES):
        try:
            img_data = base64.b64decode(b64_str, validate=True)
            nparr = np.frombuffer(img_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                return img, None
            decode_err = 'cv2.imdecode returned None'
        except Exception as ex:
            decode_err = str(ex)
        time.sleep(RETRY_DELAY)
    return None, decode_err


def init_ocr_fail_counter():
    """Load cached fail count from txt or count files once."""
    global ocr_fails_count
    if ocr_fails_count is not None:
        return

    count = 0
    if os.path.exists(OCR_FAILS_COUNT_FILE):
        try:
            with open(OCR_FAILS_COUNT_FILE, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                count = int(raw) if raw else 0
        except Exception as e:
            print(f"讀取低信心截圖計數失敗，改為重新計算: {e}")

    elif os.path.isdir(OCR_FAILS_DIR):
        try:
            count = sum(
                1
                for name in os.listdir(OCR_FAILS_DIR)
                if os.path.isfile(os.path.join(OCR_FAILS_DIR, name))
                and name.lower().endswith((".jpg", ".jpeg", ".png"))
            )
        except Exception as e:
            print(f"統計低信心截圖數量失敗，預設為 0: {e}")

    ocr_fails_count = max(0, count)
    persist_ocr_fail_counter()


def persist_ocr_fail_counter():
    """Persist counter to txt for fast reload."""
    try:
        if not os.path.exists(OCR_FAILS_DIR):
            os.makedirs(OCR_FAILS_DIR)
        with open(OCR_FAILS_COUNT_FILE, "w", encoding="utf-8") as f:
            f.write(str(ocr_fails_count or 0))
    except Exception as e:
        print(f"保存低信心截圖計數失敗: {e}")

def check_ip_allowed():
    """檢查請求IP是否在允許的網段內"""
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', ''))
    
    # 如果有多個IP（經過代理），取第一個
    if ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    
    try:
        # 使用通用的 ip_address，支援 IPv4/IPv6
        client_address = ipaddress.ip_address(client_ip)

        # 允許回環位址（例如 127.0.0.1 或 ::1）用於本機測試/健康檢查
        if client_address.is_loopback:
            return

        # 目前只允許 IPv4 的特定網段
        if isinstance(client_address, ipaddress.IPv4Address):
            if client_address not in ALLOWED_NETWORK:
                print(f"[Security] Blocked access from {client_ip} - not in allowed network {ALLOWED_NETWORK}")
                abort(403)  # Forbidden
        else:
            # 若為 IPv6 且非回環，預設拒絕（可視需求放寬）
            print(f"[Security] Blocked access from IPv6 {client_ip} - not allowed")
            abort(403)
    except ValueError:
        print(f"[Security] Invalid IP address: {client_ip}")
        abort(403)

@app.before_request
def before_request():
    """每個請求前都檢查IP"""
    check_ip_allowed()

# 全局 OCR 實例
#ocr = PaddleOCR(
#    lang='chinese_cht',
#    use_doc_orientation_classify=False,
#    use_doc_unwarping=False,
#    use_textline_orientation=False,
#    ocr_version='PP-OCRv5'
#    
#)
ocr = PaddleOCR(
    # ocr_version="PP-OCRv5",

    # ✅ 指到你導出的資料夾（裡面有 inference.pdiparams/inference.yml/json）
    text_recognition_model_dir=r"A:\OCR_model\v2",
    text_detection_model_dir=r"A:\OCR_model\det_v2",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)


# 詞條映射表
SKILL_MAP = {
    '反擊': '反', '反': '反',
    '暴擊': '爆', '暴': '爆', 
    '連擊': '連', '連': '連',
    '擊暈': '暈', '暈': '暈', '量': '暈',
    '閃避': '閃', '閃': '閃',
    '回復': '回', '回': '回',
    '技能暴擊': '技', '技': '技'
}

# 不要的技能組合
UNWANTED_COMBOS = {
    ('技', '反'), ('技', '連'), ('技', '爆'), ('技', '閃'),
    ('連', '暈'), ('連', '反'), ('連', '回'),
    ('暈', '閃'), ('暈', '爆'), ('暈', '反'),
    ('反', '閃'), ('爆', '回'), ('爆', '暈')
}

def normalize_text(text):
    """統一處理 OCR 誤辨"""
    text = text.replace("擊量", "擊暈")
    text = (text.replace("撃", "擊")
                .replace("學", "擊")
                .replace("舉", "擊")
                .replace("暴馨", "暴擊")
                .replace("擎量", "擊暈")
                .replace("暴撃", "暴擊")
                .replace("量", "暈")
                .replace("额", "額").replace("閃遊", "閃避"))
    return text

STAGE_TEXT_REPLACEMENTS = [
    ("展反", "爆反"),
    ("暈眩回", "暈回"),
    ("耳眩回", "暈回"),
    ("反爆_6", "反爆"),
    ("技爆耳", "技爆暈"),
    ("口爆", "回爆"),
    ("6", ""),
    ("4", ""),
    ("©", ""),
    ("技暈眩", "技暈"),
    ("技眩", "技暈"),
]

def normalize_stage_text(text):
    """依序處理關卡文字誤辨"""
    text = normalize_text(text)
    for src, dst in STAGE_TEXT_REPLACEMENTS:
        text = text.replace(src, dst)
    return text

# 重試參數
IMG_DECODE_RETRIES = int(os.environ.get('IMG_DECODE_RETRIES', '3'))
OCR_EMPTY_RETRIES = int(os.environ.get('OCR_EMPTY_RETRIES', '2'))
RETRY_DELAY = float(os.environ.get('OCR_RETRY_DELAY', '0.6'))

def text_to_skill(text):
    """將文字轉為技能縮寫"""
    text = normalize_text(text)
    
    if text is None:
        return None
    
    # 完整詞優先
    if "技能暴擊" in text or "技能爆擊" in text: return "技"
    if "反擊" in text: return "反"
    if "暴擊" in text or "爆擊" in text: return "爆"
    if "連擊" in text: return "連"
    if "擊暈" in text: return "暈"
    if "閃避" in text: return "閃"
    if "回復" in text: return "回"
    
    # 部分匹配
    if "技" in text: return "技"
    if "反" in text: return "反"
    if "暴" in text or "爆" in text: return "爆"
    if "連" in text: return "連"
    if "暈" in text or "眩" in text: return "暈"
    if "閃" in text: return "閃"
    if "回" in text: return "回"
    
    return None

def get_skill_combo(ocr_results):
    """從 OCR 結果提取技能組合（使用 dict 結構）"""
    skills = []
    for result in ocr_results:
        text = result.get("text", "")
        skill = text_to_skill(text)
        if skill and skill not in skills:
            skills.append(skill)
            if len(skills) == 2:
                break
    return ''.join(skills)


def normalize_combo(combo):
    """統一處理簡寫"""
    if len(combo) != 2:
        return combo
        
    # 特殊排序規則
    if set(combo) == {'閃', '爆'}: combo = '閃爆'
    elif set(combo) == {'連', '暈'}: combo = '連暈'  
    elif set(combo) == {'技', '回'}: combo = '技回'
    elif set(combo) == {'反', '爆'}: combo = '反爆'
    
    # 正規化替換
    replacements = {
        '爆閃': '連閃', '閃閃': '連閃', '閃爆': '連閃', '閃連': '連閃',
        '爆連': '連爆', '回技': '技回', '回閃': '閃回', '爆反': '反爆',
        '回暈': '暈回', '回反': '反回', '暈技': '技暈'
    }
    
    return replacements.get(combo, combo)

def is_unwanted_combo(combo):
    """判斷是否為不要的組合"""
    if len(combo) != 2:
        return False
    return (combo[0], combo[1]) in UNWANTED_COMBOS or (combo[1], combo[0]) in UNWANTED_COMBOS

def extract_ocr_results(img_roi):
    """提取 OCR 結果（含座標）"""
    try:
        if img_roi is None:
            return []
        if not hasattr(img_roi, "shape"):
            return []
        if img_roi.size == 0:
            return []

        if img_roi.ndim == 2:
            img_roi = cv2.cvtColor(img_roi, cv2.COLOR_GRAY2BGR)
        elif img_roi.ndim == 3 and img_roi.shape[2] == 4:
            img_roi = cv2.cvtColor(img_roi, cv2.COLOR_BGRA2BGR)

        if img_roi.dtype != np.uint8:
            img_roi = img_roi.astype(np.uint8, copy=False)

        img_roi = np.ascontiguousarray(img_roi)

        H, W = img_roi.shape[:2]
        if H <= 0 or W <= 0:
            return []
        if H < 2 or W < 2:
            return []
        with ocr_lock:
            # 呼叫 OCR 引擎，任何例外由上層 endpoint 處理
            res_list = ocr.predict(input=img_roi)
        if not res_list:
            return []

        res_obj = res_list[0]
        texts = res_obj.get("rec_texts", []) or []
        scores = res_obj.get("rec_scores", []) or []
        polys = res_obj.get("rec_polys", None)
        boxes = res_obj.get("rec_boxes", None)

        H, W = img_roi.shape[:2]
        results = []

        for i, text in enumerate(texts):
            score = float(scores[i]) if i < len(scores) else 0.0

            # 取多邊形/方框
            if polys is not None and len(polys) > i:
                poly = np.asarray(polys[i]).reshape(-1, 2)
            elif boxes is not None and len(boxes) > i:
                b = np.asarray(boxes[i]).flatten().astype(int)
                if b.size == 4:
                    x1, y1, x2_or_w, y2_or_h = b.tolist()
                    if x2_or_w > x1 and y2_or_h > y1:
                        x2, y2 = x2_or_w, y2_or_h
                    else:
                        x2, y2 = x1 + x2_or_w, y1 + y2_or_h
                    poly = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
                else:
                    continue
            else:
                # 沒有座標就退回整圖
                poly = np.array([[0, 0], [W-1, 0], [W-1, H-1], [0, H-1]])

            # 由 polygon 推 bbox
            x1y1 = poly.min(axis=0)
            x2y2 = poly.max(axis=0)
            x1, y1 = int(x1y1[0]), int(x1y1[1])
            x2, y2 = int(x2y2[0]), int(x2y2[1])
            w, h = x2 - x1, y2 - y1
            cx, cy = x1 + w/2.0, y1 + h/2.0

            # 相對座標（0~1）
            bbox_rel = [x1 / W, y1 / H, x2 / W, y2 / H]

            results.append({
                "text": text,
                "score": score,
                "poly": poly.astype(int).tolist(),
                "bbox": [x1, y1, x2, y2],
                "center": [cx, cy],
                "size": [w, h],
                "bbox_rel": bbox_rel
            })

            # 保存截圖（不考慮信心度，上限由 MAX_OCR_FAIL_IMAGES 控制）
            save_low_confidence_screenshot(img_roi, poly, text, score, "general")

        # 以閱讀順序排序（先上後下，再左到右）
        results.sort(key=lambda r: (r["bbox"][1] // 10, r["bbox"][0]))
        return results
    except Exception as e:
        tb = traceback.format_exc()
        print(f"OCR 處理失敗: {e}\n{tb}")
        # 重新拋出，讓 API 層可以回傳詳細錯誤
        raise
def save_low_confidence_screenshot(img, poly, text, score, stage_type):
    """保存低信心度截圖"""
    global ocr_fails_count
    try:
        # 只保存信心度在 0.2 到 0.8 之間的截圖
        if score <= MIN_OCR_FAIL_SCORE or score >= MAX_OCR_FAIL_SCORE:
            return
            
        timestamp = int(time.time() * 1000)
        pts = np.asarray(poly, dtype=int)
        x1, y1 = pts.min(axis=0)
        x2, y2 = pts.max(axis=0)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = max(x1 + 1, x2), max(y1 + 1, y2)

        if y2 <= y1 or x2 <= x1:
            return

        crop = img[y1:y2, x1:x2]
        filename = os.path.join(OCR_FAILS_DIR, f"{stage_type}_low_confidence_{score:.3f}_{timestamp}.jpg")

        with ocr_fails_count_lock:
            init_ocr_fail_counter()
            if ocr_fails_count >= MAX_OCR_FAIL_IMAGES:
                print(f"低信心截圖已達上限 {ocr_fails_count}/{MAX_OCR_FAIL_IMAGES}，不再保存")
                return
            if not os.path.exists(OCR_FAILS_DIR):
                os.makedirs(OCR_FAILS_DIR)

            if not cv2.imwrite(filename, crop):
                print("截圖保存失敗: cv2.imwrite 回傳 False")
                return

            ocr_fails_count = (ocr_fails_count or 0) + 1
            persist_ocr_fail_counter()

        print(f"低信心截圖: {filename} ('{text}', 信心: {score:.3f})")
    except Exception as e:
        print(f"截圖保存失敗: {e}")

@app.route('/analyze_skill', methods=['POST'])
def analyze_skill():
    """分析技能組合"""
    try:
        if not request.is_json:
            return jsonify({'success': False, 'message': 'Content-Type 必須為 application/json'}), 400

        data = request.get_json(silent=True) or {}
        if 'image' not in data:
            return jsonify({'success': False, 'message': '缺少 image 欄位'}), 400

        # 解析 base64 圖片，若解碼為 None 則重試數次（環境可透過 IMG_DECODE_RETRIES 調整）
        img = None
        decode_err = None
        for attempt in range(IMG_DECODE_RETRIES):
            try:
                img_data = base64.b64decode(data['image'], validate=True)
                nparr = np.frombuffer(img_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    break
                decode_err = 'cv2.imdecode returned None'
            except Exception as ex:
                decode_err = str(ex)
            # 若未成功，短暫等待後重試
            time.sleep(RETRY_DELAY)

        if img is None:
            return jsonify({'success': False, 'message': '無法解碼圖片', 'detail': decode_err}), 400

        # 呼叫 OCR，若回傳空結果則重試幾次（OCR_EMPTY_RETRIES）
        ocr_results = extract_ocr_results(img)
        if not ocr_results:
            for _ in range(OCR_EMPTY_RETRIES):
                time.sleep(RETRY_DELAY)
                ocr_results = extract_ocr_results(img)
                if ocr_results:
                    break
        if not ocr_results:
            return jsonify({'success': False, 'message': '無法識別文字'}), 200

        combo = get_skill_combo(ocr_results)
        normalized_combo = normalize_combo(combo)
        is_unwanted = is_unwanted_combo(normalized_combo)  # 用正規化後的

        return jsonify({
            'success': True,
            'ocr_results': [
                {
                    'text': r.get('text', ''),
                    'score': r.get('score', 0.0),
                    'bbox': r.get('bbox', []),
                    'bbox_rel': r.get('bbox_rel', [])
                } for r in ocr_results
            ],
            'combo': combo,
            'normalized_combo': normalized_combo,
            'is_unwanted': is_unwanted
        }), 200

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'success': False, 'message': f'處理錯誤: {str(e)}', 'traceback': tb}), 500

@app.route('/analyze_stage', methods=['POST'])
def analyze_stage():
    """分析階段文字"""
    try:
        data = request.json
        img_data = base64.b64decode(data['image'])
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        # 若 decode 失敗或回傳 None，重試 IMG_DECODE_RETRIES 次
        img = None
        decode_err = None
        for attempt in range(IMG_DECODE_RETRIES):
            try:
                img_data = base64.b64decode(data['image'], validate=True)
                nparr = np.frombuffer(img_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    break
                decode_err = 'cv2.imdecode returned None'
            except Exception as ex:
                decode_err = str(ex)
            time.sleep(RETRY_DELAY)

        if img is None:
            return jsonify({'success': False, 'message': '無法解碼圖片', 'detail': decode_err})

        all_results = extract_ocr_results(img)
        if not all_results:
            for _ in range(OCR_EMPTY_RETRIES):
                time.sleep(RETRY_DELAY)
                all_results = extract_ocr_results(img)
                if all_results:
                    break
        
        if not all_results:
            return jsonify({
                'success': False,
                'message': '無法識別階段資訊'
            })
        
        stage_texts = []
        for result in all_results:
            text = result.get("text", "")
            if not text:
                continue
            text = normalize_stage_text(text)
            stage_texts.append(text)
        
        stage_texts = [s for s in stage_texts if s.strip()]
        
        return jsonify({
            'success': True,
            'stage_texts': stage_texts
        })
        
    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({
            'success': False,
            'message': f'處理錯誤: {str(e)}',
            'traceback': tb
        })

@app.route('/ocr', methods=['POST'])
def ocr_general():
    """
    通用 OCR：只回傳 OCR 原始結果 + 座標，不做技能/關卡解析。
    payload: { "image": "<base64 or dataURL>" }
    """
    try:
        if not request.is_json:
            return jsonify({'success': False, 'message': 'Content-Type 必須為 application/json'}), 400

        data = request.get_json(silent=True) or {}
        if 'image' not in data:
            return jsonify({'success': False, 'message': '缺少 image 欄位'}), 400

        img, decode_err = decode_base64_image(data['image'])
        if img is None:
            return jsonify({'success': False, 'message': '無法解碼圖片', 'detail': decode_err}), 400

        # OCR + 空結果重試
        ocr_results = extract_ocr_results(img)
        if not ocr_results:
            for _ in range(OCR_EMPTY_RETRIES):
                time.sleep(RETRY_DELAY)
                ocr_results = extract_ocr_results(img)
                if ocr_results:
                    break

        return jsonify({
            'success': True,
            'ocr_results': [
                {
                    'text': r.get('text', ''),
                    'score': r.get('score', 0.0),
                    'bbox': r.get('bbox', []),
                    'bbox_rel': r.get('bbox_rel', []),
                    # 若你想保留 poly（比較大），就取消註解
                    # 'poly': r.get('poly', [])
                } for r in ocr_results
            ],
            'meta': {
                'h': int(img.shape[0]),
                'w': int(img.shape[1]),
            }
        }), 200

    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({'success': False, 'message': f'處理錯誤: {str(e)}', 'traceback': tb}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """健康檢查"""
    return jsonify({'status': 'ok'})

@app.route('/', methods=['GET'])
def index():
    """根路由 - 提供基本信息"""
    return jsonify({
        'service': 'OCR Server',
        'version': '1.0',
        'endpoints': [
            '/analyze_skill',
            '/analyze_stage', 
            '/health'
        ],
        'status': 'running'
    })

@app.route('/favicon.ico')
def favicon():
    """處理 favicon 請求"""
    return '', 204

@app.errorhandler(400)
def bad_request(error):
    """處理 400 錯誤"""
    return jsonify({'error': 'Bad Request', 'message': 'Invalid request format'}), 400

@app.errorhandler(403)
def forbidden(error):
    """處理 403 錯誤 - IP不在白名單"""
    return jsonify({'error': 'Forbidden', 'message': 'Access denied - IP not in whitelist'}), 403

@app.errorhandler(404)
def not_found(error):
    """處理 404 錯誤"""
    return jsonify({'error': 'Not Found', 'message': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    """處理 500 錯誤"""
    return jsonify({'error': 'Internal Server Error', 'message': 'Server encountered an error'}), 500

# 捕捉未處理例外，輸出 traceback 方便定位行號
@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    tb = traceback.format_exc()
    print(f"[Unexpected 500] {error}\n{tb}")
    return jsonify({
        'error': 'Internal Server Error',
        'message': str(error),
        'traceback': tb,
    }), 500

if __name__ == '__main__':
    print("OCR 服務器啟動中...")
    print(f"IP 限制: 只允許來自 {ALLOWED_NETWORK} 網段的連接")
    
    # 添加日誌配置，減少無關日誌
    import logging
    log = logging.getLogger('werkzeug')
    
    log.setLevel(logging.WARNING)  # 只顯示警告和錯誤
    
    # 允許透過環境變數改變綁定地址/埠；預設綁定到所有介面 (0.0.0.0)
    # 這樣既可接受本機 (localhost) 請求，也可接受來自 100.64.0.0/24 的遠端請求
    bind_host = os.environ.get('OCR_BIND_HOST', '0.0.0.0')
    bind_port = int(os.environ.get('OCR_PORT', '5001'))

    print(f"啟動綁定: http://{bind_host}:{bind_port} (預設對外可用，保留白名單檢查)")
    # 若希望僅限本機，啟動時可設定環境變數 OCR_BIND_HOST=127.0.0.1
    app.run(host=bind_host, port=bind_port, debug=False)

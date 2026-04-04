import os
import time
import cv2
import numpy as np
import logging
import mask
import requests
import base64
import opencc
import uiautomator2 as u2
import traceback
import inspect
import threading
logger = logging.getLogger(__name__)

OCR_SERVER_MAIN = "http://100.64.0.5:5001"
OCR_SERVER_BACKUP = "http://100.64.0.7:5001"
OCR_SERVER_LOCAL = "http://localhost:5001"
# main / backup / auto
_OCR_SERVER_MODE = os.getenv("OCR_SERVER_MODE", "main").strip().lower()
_OCR_CB_LOCK = threading.Lock()
_OCR_SERVER_FAIL_UNTIL = {}  # {server_base_url: unix_ts}
_OCR_PROBE_THREAD = None
_OCR_LAST_SUCCESS_SERVER = None


def set_ocr_server_mode(mode: str):
    """Set OCR server mode at runtime: main / backup / auto."""
    global _OCR_SERVER_MODE
    normalized = (mode or "main").strip().lower()
    if normalized not in {"main", "backup", "auto"}:
        raise ValueError(f"Invalid OCR server mode: {mode}")
    _OCR_SERVER_MODE = normalized


def get_ocr_server_mode() -> str:
    return _OCR_SERVER_MODE


def _get_ocr_mode_from_config() -> str:
    """Read OCR mode from config_manager if available; fallback to runtime/env mode."""
    try:
        import config_manager
        cfg = config_manager.get_ocr_config()
        mode = str(cfg.get("server_mode", _OCR_SERVER_MODE)).strip().lower()
        if mode in {"main", "backup", "auto"}:
            return mode
    except Exception:
        pass
    return _OCR_SERVER_MODE


def _build_ocr_server_priority(explicit_url=None):
    """Build server priority list from configured mode while preserving compatibility."""
    servers = []

    if explicit_url:
        servers.append(explicit_url.rstrip('/'))

    mode = _get_ocr_mode_from_config()
    if mode == "backup":
        preferred = [OCR_SERVER_BACKUP, OCR_SERVER_MAIN, OCR_SERVER_LOCAL]
    elif mode == "auto":
        preferred = [OCR_SERVER_MAIN, OCR_SERVER_BACKUP, OCR_SERVER_LOCAL]
    else:
        preferred = [OCR_SERVER_MAIN, OCR_SERVER_BACKUP, OCR_SERVER_LOCAL]

    for s in preferred:
        if s and s not in servers:
            servers.append(s)

    # Also honor configured custom servers order if provided in global OCR config.
    try:
        import config_manager
        cfg = config_manager.get_ocr_config()
        for s in cfg.get("servers", []):
            s = str(s).strip().rstrip('/')
            if s and s not in servers:
                servers.append(s)
    except Exception:
        pass

    return servers


def _get_ocr_circuit_config():
    cfg = {
        "fail_cooldown_sec": 300,
        "probe_interval_sec": 120,
        "probe_timeout_sec": 2,
    }
    try:
        import config_manager
        ocr_cfg = config_manager.get_ocr_config()
        cfg["fail_cooldown_sec"] = int(ocr_cfg.get("fail_cooldown_sec", cfg["fail_cooldown_sec"]))
        cfg["probe_interval_sec"] = int(ocr_cfg.get("probe_interval_sec", cfg["probe_interval_sec"]))
        cfg["probe_timeout_sec"] = int(ocr_cfg.get("probe_timeout_sec", cfg["probe_timeout_sec"]))
    except Exception:
        pass
    cfg["fail_cooldown_sec"] = max(30, min(3600, cfg["fail_cooldown_sec"]))
    cfg["probe_interval_sec"] = max(60, min(600, cfg["probe_interval_sec"]))
    cfg["probe_timeout_sec"] = max(1, min(10, cfg["probe_timeout_sec"]))
    return cfg


def _mark_server_failed(server: str):
    now = time.time()
    conf = _get_ocr_circuit_config()
    until = now + conf["fail_cooldown_sec"]
    with _OCR_CB_LOCK:
        _OCR_SERVER_FAIL_UNTIL[server] = until
    logger.warning(f"[OCR-CB] mark failed: {server}, skip until {time.strftime('%H:%M:%S', time.localtime(until))}")


def _mark_server_recovered(server: str):
    global _OCR_LAST_SUCCESS_SERVER
    with _OCR_CB_LOCK:
        _OCR_LAST_SUCCESS_SERVER = server
        if server in _OCR_SERVER_FAIL_UNTIL:
            _OCR_SERVER_FAIL_UNTIL.pop(server, None)
            logger.info(f"[OCR-CB] recovered: {server}")


def _is_server_in_cooldown(server: str) -> bool:
    now = time.time()
    with _OCR_CB_LOCK:
        until = _OCR_SERVER_FAIL_UNTIL.get(server, 0)
    return now < float(until)


def _filter_servers_by_circuit(servers):
    active = [s for s in servers if not _is_server_in_cooldown(s)]
    if active and len(active) != len(servers):
        skipped = [s for s in servers if s not in active]
        logger.info(f"[OCR-CB] skip cooled-down servers: {skipped}")
    return active if active else servers


def _ocr_probe_loop():
    while True:
        try:
            conf = _get_ocr_circuit_config()
            now = time.time()
            with _OCR_CB_LOCK:
                candidates = [s for s, until in _OCR_SERVER_FAIL_UNTIL.items() if now >= float(until)]
            for srv in candidates:
                try:
                    r = requests.get(f"{srv}/health", timeout=conf["probe_timeout_sec"])
                    if r.status_code == 200:
                        _mark_server_recovered(srv)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(_get_ocr_circuit_config()["probe_interval_sec"])


def _ensure_ocr_probe_thread():
    global _OCR_PROBE_THREAD
    if _OCR_PROBE_THREAD is not None and _OCR_PROBE_THREAD.is_alive():
        return
    with _OCR_CB_LOCK:
        if _OCR_PROBE_THREAD is None or not _OCR_PROBE_THREAD.is_alive():
            _OCR_PROBE_THREAD = threading.Thread(target=_ocr_probe_loop, daemon=True, name="OCRCircuitProbe")
            _OCR_PROBE_THREAD.start()
            logger.info("[OCR-CB] probe thread started")


def get_ocr_runtime_status(explicit_url=None):
    mode = _get_ocr_mode_from_config()
    servers = _build_ocr_server_priority(explicit_url=explicit_url)
    active_servers = _filter_servers_by_circuit(servers)
    now = time.time()
    with _OCR_CB_LOCK:
        last_success = _OCR_LAST_SUCCESS_SERVER
        cooldown = {
            s: max(0, int(until - now))
            for s, until in _OCR_SERVER_FAIL_UNTIL.items()
            if now < float(until)
        }
    return {
        "mode": mode,
        "last_success_server": last_success,
        "active_servers": active_servers,
        "cooldown_seconds": cooldown,
    }

def check_red_dot(d, roi: tuple) -> bool:
    """
    Checks for a red dot within a specified region of interest (ROI) on the screen.
    Args:
        d: The uiautomator2 device object.
        roi: A tuple (y1, y2, x1, x2) defining the ROI to check.
    Returns:
        True if a red dot of sufficient size is found, False otherwise.
    """
    img = d.screenshot(format='opencv')
    if img is None:
        logger.warning("Failed to capture screenshot.")
        return False
    y1, y2, x1, x2 = roi
    cropped_img = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(cropped_img, cv2.COLOR_BGR2HSV)
    lower_red = mask.red_mask_lower
    upper_red = mask.red_mask_upper
    red_mask = cv2.inRange(hsv, lower_red, upper_red)
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(c) > 46 for c in contours)

def save_stage_debug_image(stage_name: str, img: np.ndarray):
    """Helper function to save an image for debugging a specific stage."""
    dir_path = f"debug_{stage_name}"
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"{dir_path}/{stage_name}_{timestamp}.jpg"
    # cv2.imwrite(filename, img)
    logger.info(f"Saved debug image for stage '{stage_name}' to {filename}")
def encode_image(img):
    """將圖片編碼為 base64"""
    _, buffer = cv2.imencode('.jpg', img)
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    return img_base64

def analyze_skill_via_http(img_roi, OCR_SERVER_URL=None, max_servers=None):
    """透過 HTTP 分析技能，若預設伺服器無法連線則自動退回到備援伺服器。

    參數:
        img_roi: ROI 圖片 (OpenCV ndarray)
        OCR_SERVER_URL: 可選的主要 OCR 服務器 URL (例如 "http://100.64.0.5:5000")

    回傳: server json 或 {'success': False, 'error': ...}
    """
    # 獲取調用者信息
    caller_frame = inspect.currentframe().f_back
    caller_info = inspect.getframeinfo(caller_frame)
    caller_function = caller_frame.f_code.co_name
    caller_file = os.path.basename(caller_info.filename)
    caller_line = caller_info.lineno
    
    logger.debug(f"[OCR調用追蹤] 被調用於: {caller_file}:{caller_line} in {caller_function}()")
    
    img_base64 = encode_image(img_roi)
    mode = _get_ocr_mode_from_config()
    servers = _build_ocr_server_priority(explicit_url=OCR_SERVER_URL)
    _ensure_ocr_probe_thread()
    servers = _filter_servers_by_circuit(servers)
    if isinstance(max_servers, int) and max_servers > 0:
        servers = servers[:max_servers]
    logger.info(f"OCR servers priority (mode={mode}): {servers}")

    # 讀取 timeout 與重試設定
    timeout_sec = 20
    try:
        import config_manager
        timeout_sec = int(config_manager.get_ocr_config().get('timeout_sec', timeout_sec))
    except Exception:
        pass

    errors = []
    for idx, srv in enumerate(servers):
        try:
            url = f"{srv}/analyze_skill"
            logger.info(f"[{caller_file}:{caller_line}] OCR request -> {url} (timeout={timeout_sec}s)")
            response = requests.post(url, json={'image': img_base64}, timeout=timeout_sec)
            if response.status_code == 200:
                try:
                    _mark_server_recovered(srv)
                    logger.info(f"[{caller_file}:{caller_line}] OCR success from {srv}")
                    return response.json()
                except Exception as e_json:
                    err = f"解析 JSON 失敗 from {srv}: {e_json}"
                    logger.warning(f"[{caller_file}:{caller_line} in {caller_function}()] {err}")
                    errors.append(err)
                    # 嘗試下一個伺服器
                    continue
            else:
                err = f"HTTP {response.status_code} from {srv}"
                logger.warning(f"[{caller_file}:{caller_line} in {caller_function}()] {err}")
                _mark_server_failed(srv)
                try:
                    error_detail = response.text[:500]
                    logger.debug(f"[{caller_file}:{caller_line}] Server response: {error_detail}")
                except Exception:
                    pass
                errors.append(err)
                # 若還有下一個伺服器，繼續嘗試
                if idx < len(servers) - 1:
                    logger.info(f"[{caller_file}:{caller_line}] 切換到下一個 OCR 伺服器...")
                continue
        except Exception as e:
            err = f"HTTP 請求失敗 to {srv}: {e}"
            logger.warning(f"[{caller_file}:{caller_line} in {caller_function}()] {err}")
            _mark_server_failed(srv)
            errors.append(err)
            if idx < len(servers) - 1:
                logger.info(f"[{caller_file}:{caller_line}] 切換到下一個 OCR 伺服器 due to error...")
            continue

    # 所有伺服器均失敗，回傳詳細錯誤資訊
    logger.error(f"[{caller_file}:{caller_line} in {caller_function}()] 所有 OCR 伺服器失敗: {errors}")
    return {'success': False, 'error': 'all servers failed', 'errors': errors}


def get_all_text(img, OCR_SERVER_URL=None, max_servers=None):
    """Return a list of detected text strings (converted to traditional Chinese).

    Args:
        img: OpenCV image (ndarray) or file path.
        OCR_SERVER_URL: optional explicit server URL to prefer.
    Returns:
        list of strings (texts). Empty list on failure.
    """
    try:
        if isinstance(img, str) and os.path.exists(img):
            img_cv = cv2.imread(img)
        else:
            img_cv = img
        if img_cv is None:
            return []

        res = analyze_skill_via_http(img_cv, OCR_SERVER_URL=OCR_SERVER_URL, max_servers=max_servers)
        if res.get('success') is False:
            return []
        ocr_results = res.get('ocr_results', [])
        converter = opencc.OpenCC('s2t')
        texts = [converter.convert(item.get('text', '')) for item in ocr_results]
        return texts
    except Exception as e:
        logger.exception(f"get_all_text error: {e}")
        return []


def analyze_stage_via_server(img, OCR_SERVER_URL=None):
    """Call remote stage analyzer endpoint. Returns server JSON or {'success': False}.
    Falls back across configured servers similar to analyze_skill_via_http.
    """
    caller_frame = inspect.currentframe().f_back
    caller_info = inspect.getframeinfo(caller_frame)
    caller_file = os.path.basename(caller_info.filename)
    caller_line = caller_info.lineno

    img_base64 = encode_image(img)
    servers = _build_ocr_server_priority(explicit_url=OCR_SERVER_URL)
    _ensure_ocr_probe_thread()
    servers = _filter_servers_by_circuit(servers)
    errors = []
    for srv in servers:
        try:
            url = f"{srv}/analyze_stage"
            logger.debug(f"[{caller_file}:{caller_line}] stage request -> {url}")
            response = requests.post(url, json={'image': img_base64}, timeout=20)
            if response.status_code == 200:
                try:
                    _mark_server_recovered(srv)
                    return response.json()
                except Exception as e_json:
                    errors.append(f"解析 JSON 失敗 from {srv}: {e_json}")
                    continue
            else:
                _mark_server_failed(srv)
                errors.append(f"HTTP {response.status_code} from {srv}")
                continue
        except Exception as e:
            _mark_server_failed(srv)
            errors.append(f"HTTP 請求失敗 to {srv}: {e}")
            continue
    logger.error(f"analyze_stage_via_server: all servers failed: {errors}")
    return {'success': False, 'error': 'all servers failed', 'errors': errors}
def find_and_click(d, findImgPath, threshold=0.8, x=0, y=0):
    img = d.screenshot(format='opencv')
    if not os.path.exists("find_img"):
        os.makedirs("find_img")
    findImg = cv2.imread(findImgPath)
    res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    if len(loc[0]) > 0:
        pt_x, pt_y = loc[1][0], loc[0][0]
        h, w = findImg.shape[:2]
        matched_region = img[pt_y:pt_y + h, pt_x:pt_x + w]
        if not os.path.exists("found_matches"):
            os.makedirs("found_matches")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        template_name = os.path.splitext(os.path.basename(findImgPath))[0]
        save_path = os.path.join("found_matches", f"matched_{template_name}_{timestamp}.jpg")
        # cv2.imwrite(save_path, matched_region)
        # logger.info(f"Matched region saved to {save_path}")
        center = [int(pt_x + w / 2), int(pt_y + h / 2)]
        d.click(center[0] + x, center[1] + y)
        logger.info(f"Clicked at: {center[0] + x}, {center[1] + y}")
        return True
    else:
        return False
def click_str_by_server(d: u2.Device, target_str: str, shift_x=0, shift_y=0, x_range=None, y_range=None, sort_by_y=False, wait_timeout=0) -> bool:
    """
    在畫面中尋找目標文字並點擊
    
    Args:
        d: uiautomator2 設備對象
        target_str: 要尋找的目標文字
        shift_x: X軸偏移量
        shift_y: Y軸偏移量
        x_range: X軸範圍限制 (x_min, x_max)
        y_range: Y軸範圍限制 (y_min, y_max)
        sort_by_y: 是否按Y軸排序（選擇最上方的匹配項）
        wait_timeout: 等待超時時間（秒），0表示不等待，直接檢查3次
    
    Returns:
        bool: 是否成功找到並點擊
    """
    # 獲取調用者信息
    caller_frame = inspect.currentframe().f_back
    caller_info = inspect.getframeinfo(caller_frame)
    caller_function = caller_frame.f_code.co_name
    caller_file = os.path.basename(caller_info.filename)
    caller_line = caller_info.lineno
    
    logger.debug(f"[click_str_by_server] 被調用於: {caller_file}:{caller_line} in {caller_function}(), 尋找文字: '{target_str}'")
    
    target_str = opencc.OpenCC('s2t').convert(target_str)
    
    if wait_timeout > 0:
        # 等待模式：在指定時間內持續檢查
        start_time = time.time()
        attempt = 0
        while time.time() - start_time < wait_timeout:
            attempt += 1
            img = d.screenshot(format='opencv')
            original_shape = img.shape
            if y_range:
                img = img[y_range[0]:y_range[1], :]
            if x_range:
                img = img[:, x_range[0]:x_range[1]]
            
            logger.debug(f"[{caller_file}:{caller_line}] 嘗試 #{attempt}, 圖片大小: {img.shape}, 原始: {original_shape}, y_range={y_range}, x_range={x_range}")
            
            result = analyze_skill_via_http(img)
            if result.get('success') is False:
                logger.warning(f"[{caller_file}:{caller_line}] 嘗試 #{attempt} OCR失敗: {result.get('error')}")
                time.sleep(0.5)
                continue
            
            ocr_results = result['ocr_results']
            matches = []
            # 將文字都轉為繁體
            for item in ocr_results:
                item['text'] = opencc.OpenCC('s2t').convert(item['text'])
                if target_str in item['text']:
                    matches.append(item)

            if sort_by_y:
                matches.sort(key=lambda m: m['bbox'][1])
                
            if matches:
                item = matches[0]
                x1, y1, x2, y2 = item['bbox']
                if y_range:
                    y1 += y_range[0]
                    y2 += y_range[0]
                if x_range:
                    x1 += x_range[0]
                    x2 += x_range[0]
                click_x = (x1 + x2) // 2 + shift_x
                click_y = (y1 + y2) // 2 + shift_y
                logger.info(f"[{caller_file}:{caller_line}] 找到 '{target_str}' 在 ({click_x}, {click_y})")
                d.click(click_x, click_y)
                return True
            
            time.sleep(0.5)
        logger.warning(f"[{caller_file}:{caller_line}] 等待超時 ({wait_timeout}s)，未找到 '{target_str}'")
        return False
    else:
        # 原有模式：嘗試3次
        for attempt in range(1, 4):
            img = d.screenshot(format='opencv')
            original_shape = img.shape
            if y_range:
                img = img[y_range[0]:y_range[1], :]
            if x_range:
                img = img[:, x_range[0]:x_range[1]]
            
            logger.debug(f"[{caller_file}:{caller_line}] 嘗試 #{attempt}/3, 圖片大小: {img.shape}, 原始: {original_shape}")
            
            result = analyze_skill_via_http(img)
            if result.get('success') is False :
                logger.warning(f"[{caller_file}:{caller_line}] 嘗試 #{attempt}/3 OCR失敗: {result.get('error')}")
                continue
            ocr_results = result['ocr_results']
            matches = []
            # 將文字都轉為繁體
            for item in ocr_results:
                item['text'] = opencc.OpenCC('s2t').convert(item['text'])
                if target_str in item['text']:
                    matches.append(item)

            if sort_by_y:
                matches.sort(key=lambda m: m['bbox'][1])
                
            if matches:
                item = matches[0]
                x1, y1, x2, y2 = item['bbox']
                if y_range:
                    y1 += y_range[0]
                    y2 += y_range[0]
                if x_range:
                    x1 += x_range[0]
                    x2 += x_range[0]
                click_x = (x1 + x2) // 2 + shift_x
                click_y = (y1 + y2) // 2 + shift_y
                logger.info(f"[{caller_file}:{caller_line}] 找到 '{target_str}' 在 ({click_x}, {click_y})")
                d.click(click_x, click_y)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                return True
        logger.warning(f"[{caller_file}:{caller_line}] 嘗試3次後未找到 '{target_str}'")
        return False
def check_str_in_region(d_or_img, target_str: str, x_range: tuple = None, y_range: tuple = None) -> bool:
    """檢查指定區域內是否存在目標文字。

    支援三種輸入型別：
    - u2.Device (或類似有 screenshot 方法)
    - OpenCV 圖片 ndarray
    - 圖片檔案路徑字串
    """
    retry = 3
    converter = opencc.OpenCC('s2t')
    target_str = converter.convert(target_str)

    for _ in range(retry):
        # 取得 img_cv：支援 ndarray / path / device
        img_cv = None
        try:
            if isinstance(d_or_img, np.ndarray):
                img_cv = d_or_img
            elif isinstance(d_or_img, str) and os.path.exists(d_or_img):
                img_cv = cv2.imread(d_or_img)
            else:
                # assume device-like object with screenshot()
                img_cv = d_or_img.screenshot(format='opencv')
        except Exception:
            img_cv = None

        if img_cv is None:
            continue

        img = img_cv
        if y_range:
            img = img[y_range[0]:y_range[1], :]
        if x_range:
            img = img[:, x_range[0]:x_range[1]]

        result = analyze_skill_via_http(img)
        print(result)
        if result.get('success') is False:
            continue
        for item in result.get('ocr_results', []):
            if target_str in converter.convert(item.get('text', '')):
                return True
    return False

def wait_for_any_text(d: u2.Device, text_list: list, timeout: float = 10, check_interval: float = 0.5, 
                      y_range: tuple = None, click_if_found: bool = True, shift_x: int = 0, shift_y: int = 0) -> str:
    """
    等待多個文字中的任意一個出現
    
    Args:
        d: uiautomator2 設備對象
        text_list: 要檢測的文字列表
        timeout: 超時時間（秒）
        check_interval: 檢查間隔（秒）
        y_range: Y軸範圍限制 (y_min, y_max)
        click_if_found: 找到後是否點擊
        shift_x: 點擊X軸偏移量
        shift_y: 點擊Y軸偏移量
    
    Returns:
        str: 找到的文字，如果超時則返回空字串
    """
    converter = opencc.OpenCC('s2t')
    text_list_t = [converter.convert(text) for text in text_list]
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        img = d.screenshot(format='opencv')
        if y_range:
            img = img[y_range[0]:y_range[1], :]
        
        result = analyze_skill_via_http(img)
        if result.get('success') is False:
            time.sleep(check_interval)
            continue
        
        ocr_results = result.get('ocr_results', [])
        
        # 檢查所有目標文字
        for target_str, target_str_t in zip(text_list, text_list_t):
            for item in ocr_results:
                item_text = converter.convert(item.get('text', ''))
                if target_str_t in item_text:
                    # 找到匹配的文字
                    if click_if_found:
                        x1, y1, x2, y2 = item.get('bbox', [0, 0, 0, 0])
                        if y_range:
                            y1 += y_range[0]
                            y2 += y_range[0]
                        d.click((x1 + x2) // 2 + shift_x, (y1 + y2) // 2 + shift_y)
                    return target_str
        
        time.sleep(check_interval)
    
    return ""  # 超時，未找到任何文字

import numpy as np
import uiautomator2 as u2
import time
class img_tools:
    '''暫時不可用'''
    def find_and_click(self,img, findImgPath, threshold=0.8, x=0, y=0):
        """尋找圖像並點擊"""
        img = self.device.screenshot(format='opencv')
        findImg = cv2.imread(findImgPath)
        res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        if len(loc[0]) > 0:
            center = [int(loc[1][0] + findImg.shape[1] / 2),
                      int(loc[0][0] + findImg.shape[0] / 2)]
            self.device.click(center[0] + x, center[1] + y)
            return True
        return False

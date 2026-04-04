# OCR 服務器安全配置
import os
from datetime import datetime

# 允許的 IP 地址（可以根據需要修改）
ALLOWED_IPS = {
    '127.0.0.1',      # 本地
    '192.168.1.0/24', # 本地網段
    '100.64.0.0/24',  # 您的內網段
}

# 日誌配置
LOG_CONFIG = {
    'level': 'WARNING',  # 只記錄警告和錯誤
    'format': '[%(asctime)s] [%(levelname)s] %(message)s',
    'filename': f'ocr_server_{datetime.now().strftime("%Y%m%d")}.log'
}

# 速率限制配置
RATE_LIMIT = {
    'max_requests_per_minute': 60,
    'max_requests_per_hour': 1000
}

# 安全標頭
SECURITY_HEADERS = {
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'X-XSS-Protection': '1; mode=block',
    'Strict-Transport-Security': 'max-age=31536000; includeSubDomains'
}

import logging
import os
import threading
import atexit

from logging.handlers import RotatingFileHandler

# 建立 logs 資料夾
if not os.path.exists("logs"):
    os.makedirs("logs")

# 執行緒鎖，確保 logger 初始化的執行緒安全
_logger_lock = threading.Lock()

def setup_logger_for_device(device_id: str) -> logging.Logger:
    """為指定的設備建立獨立 logger，按 IP 分檔並加上 [IP] 標籤。"""
    with _logger_lock:
        logger_name = f"logger_{device_id}"
        logger = logging.getLogger(logger_name)
        
        # 清除舊的 handler（避免重複或混淆）
        logger.handlers = []
        logger.propagate = False
        
        logger.setLevel(logging.INFO)
        
        # 檔案 handler：各設備獨立檔案
        log_file = f"logs/{device_id}.log"
        # 使用 RotatingFileHandler，設定最大 10MB，保留 5 個備份
        file_handler = RotatingFileHandler(
            log_file, 
            maxBytes=10*1024*1024, 
            backupCount=5, 
            encoding='utf-8', 
            mode='a'
        )
        file_handler.setLevel(logging.INFO)
        
        # 格式：包含 [檔案:行號]
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # 也加入控制台 handler（可選）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger

def setup_miner_logger(device_id: str) -> logging.Logger:
    """建立挖礦專用的獨立 logger (miner_{IP}.log)"""
    with _logger_lock:
        logger_name = f"miner_{device_id}"
        m_logger = logging.getLogger(logger_name)
        m_logger.handlers = []
        m_logger.propagate = False
        m_logger.setLevel(logging.INFO)
        
        log_file = f"logs/miner_{device_id}.log"
        handler = RotatingFileHandler(
            log_file, 
            maxBytes=5*1024*1024, # 5MB
            backupCount=3, 
            encoding='utf-8'
        )
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        m_logger.addHandler(handler)
        
        # 同時輸出到控制台以便觀察
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        m_logger.addHandler(console)
        
        return m_logger

# 預設 logger（用於主執行緒或不帶 IP 的日誌）
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
default_logger = logging.getLogger(__name__)

# 使用 threading.local() 為每個線程維護獨立的 logger
_thread_local = threading.local()

def get_thread_logger():
    """獲取當前線程的 logger，如果未設定則返回預設 logger"""
    return getattr(_thread_local, 'logger', default_logger)

def set_thread_logger(logger_instance):
    """為當前線程設定專屬 logger"""
    _thread_local.logger = logger_instance

# 為了向後兼容，使用屬性訪問
class LoggerProxy:
    def __getattr__(self, name):
        return getattr(get_thread_logger(), name)

logger = LoggerProxy()
# 在程式結束時強制關閉 logging handlers，確保所有日誌已 flush 並關閉
atexit.register(logging.shutdown)

import logging
import os
import sys
import threading
import atexit
import errno
import time
import glob

from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler variant that avoids stream seek issues on Windows/SMB."""

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()

        if self.maxBytes > 0:
            try:
                file_size = os.path.getsize(self.baseFilename)
                msg = "%s\n" % self.format(record)
                if file_size + len(msg) >= self.maxBytes:
                    return 1
            except Exception:
                pass
        return 0


class SafeConsoleHandler(logging.StreamHandler):
    """Console handler that ignores EINVAL flush/write errors on Windows."""

    def __init__(self, stream=None):
        super().__init__(stream or sys.stdout)
        try:
            if hasattr(self.stream, "reconfigure"):
                self.stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    def emit(self, record):
        try:
            super().emit(record)
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.EINVAL:
                return
            raise


def _ensure_utf8_stdio():
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_ensure_utf8_stdio()
os.makedirs("logs", exist_ok=True)

_logger_lock = threading.Lock()
_startup_logs_rotated = False
_ocr_logger_cache = {}


def _build_rotated_log_path(log_path: str, stamp: str) -> str:
    base, ext = os.path.splitext(log_path)
    candidate = f"{base}.{stamp}{ext}"
    if not os.path.exists(candidate):
        return candidate
    index = 1
    while True:
        candidate = f"{base}.{stamp}.{index}{ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1


def rotate_existing_logs_once(log_dir: str = "logs") -> None:
    """Rename existing .log files once per process so startup begins with fresh logs."""
    global _startup_logs_rotated
    with _logger_lock:
        if _startup_logs_rotated:
            return
        os.makedirs(log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for name in sorted(os.listdir(log_dir)):
            if not name.endswith(".log"):
                continue
            old_path = os.path.join(log_dir, name)
            if not os.path.isfile(old_path):
                continue
            try:
                if os.path.getsize(old_path) <= 0:
                    continue
            except OSError:
                continue
            new_path = _build_rotated_log_path(old_path, stamp)
            try:
                os.replace(old_path, new_path)
            except OSError:
                # Another process may still be holding the file.
                continue
        _startup_logs_rotated = True


def _reset_handlers(logger_obj: logging.Logger) -> None:
    for handler in logger_obj.handlers[:]:
        try:
            handler.close()
        finally:
            logger_obj.removeHandler(handler)


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def _purge_old_files(pattern: str, max_age_days: int) -> None:
    cutoff_ts = time.time() - (max_age_days * 86400)
    for path in glob.glob(pattern):
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getmtime(path) < cutoff_ts:
                os.remove(path)
        except OSError:
            continue


def setup_logger_for_device(device_id: str) -> logging.Logger:
    """Create a per-device logger writing to logs/<device>.log and console."""
    safe_device_id = device_id.replace(":", "_").replace(" ", "_")

    with _logger_lock:
        logger_name = f"logger_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)

        formatter = _build_formatter()
        log_file = f"logs/{safe_device_id}.log"
        file_handler = SafeRotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8',
            mode='a'
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        console_handler = SafeConsoleHandler(stream=sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        logger_obj.addHandler(file_handler)
        logger_obj.addHandler(console_handler)
        return logger_obj


def get_or_create_ocr_logger(device_id: str) -> logging.Logger:
    """Create a per-device OCR trace logger with aggressive size and age limits."""
    safe_device_id = device_id.replace(":", "_").replace(" ", "_")
    log_dir = os.path.join("logs", "ocr_trace")
    os.makedirs(log_dir, exist_ok=True)

    with _logger_lock:
        cached = _ocr_logger_cache.get(safe_device_id)
        if cached is not None:
            return cached

        logger_name = f"ocr_trace_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)

        formatter = _build_formatter()
        log_file = os.path.join(log_dir, f"{safe_device_id}.log")
        file_handler = SafeRotatingFileHandler(
            log_file,
            maxBytes=512 * 1024,
            backupCount=4,
            encoding='utf-8',
            mode='a'
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)

        _purge_old_files(os.path.join(log_dir, f"{safe_device_id}.log*"), max_age_days=5)
        _ocr_logger_cache[safe_device_id] = logger_obj
        return logger_obj


def setup_miner_logger(device_id: str) -> logging.Logger:
    """Create miner logger writing to logs/miner_<device>.log and console."""
    safe_device_id = device_id.replace(":", "_").replace(" ", "_")

    with _logger_lock:
        logger_name = f"miner_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)

        formatter = _build_formatter()
        log_file = f"logs/miner_{safe_device_id}.log"
        file_handler = SafeRotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8',
            mode='a'
        )
        file_handler.setFormatter(formatter)

        console_handler = SafeConsoleHandler(stream=sys.stdout)
        console_handler.setFormatter(formatter)

        logger_obj.addHandler(file_handler)
        logger_obj.addHandler(console_handler)
        return logger_obj


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)
default_logger = logging.getLogger(__name__)

_thread_local = threading.local()


def get_thread_logger():
    return getattr(_thread_local, 'logger', default_logger)


def set_thread_logger(logger_instance):
    _thread_local.logger = logger_instance
    logger_name = getattr(logger_instance, "name", "")
    if logger_name.startswith("logger_"):
        _thread_local.device_id = logger_name[len("logger_"):]


def get_thread_device_id():
    return getattr(_thread_local, "device_id", None)


def log_ocr_trace(message: str) -> None:
    device_id = get_thread_device_id()
    if not device_id:
        return
    try:
        get_or_create_ocr_logger(device_id).info(message)
    except Exception:
        return


class LoggerProxy:
    def __getattr__(self, name):
        return getattr(get_thread_logger(), name)


logger = LoggerProxy()
atexit.register(logging.shutdown)

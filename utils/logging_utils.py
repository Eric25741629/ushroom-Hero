import logging
import os
import re
import sys
import threading
import atexit
import errno
import time
import glob

from logging.handlers import RotatingFileHandler

from utils.log_paths import LogPaths

# A file already rotated by us has form `<base>.YYYYMMDD_HHMMSS[.N].log`.
# We must NOT re-rotate it, otherwise filenames balloon to `name.t1.t2.t3.log`.
_ROTATED_SUFFIX_RE = re.compile(r"\.\d{8}_\d{6}(?:\.\d+)?\.log$")


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


def _is_rotatable_active_log(name: str) -> bool:
    """Only rotate active log files, never previously-rotated copies or sync-conflicts.

    Active form: `<base>.log` (no embedded timestamp suffix).
    Skip:
      - already rotated: `<base>.YYYYMMDD_HHMMSS[.N].log`
      - syncthing residue: `*sync-conflict-*.log`
    """
    if not name.endswith(".log"):
        return False
    if "sync-conflict" in name:
        return False
    if _ROTATED_SUFFIX_RE.search(name):
        return False
    return True


_SKIP_ROTATE_SUBDIRS = {"_archive"}


def _iter_active_log_files(log_dir: str):
    """Walk log_dir + first-level device subdirs, yielding (parent, filename)
    tuples for files that pass `_is_rotatable_active_log`."""
    for name in sorted(os.listdir(log_dir)):
        full = os.path.join(log_dir, name)
        if os.path.isfile(full) and _is_rotatable_active_log(name):
            yield log_dir, name
            continue
        if os.path.isdir(full) and name not in _SKIP_ROTATE_SUBDIRS:
            for sub in sorted(os.listdir(full)):
                sub_full = os.path.join(full, sub)
                if os.path.isfile(sub_full) and _is_rotatable_active_log(sub):
                    yield full, sub


def rotate_existing_logs_once(log_dir: str = "logs") -> None:
    """Rename existing active .log files once per process so startup begins with fresh logs.

    Walks both `<log_dir>/*.log` (legacy flat layout) and `<log_dir>/<device>/*.log`
    (per-device layout). Skips `_archive/` subtree.
    """
    global _startup_logs_rotated
    with _logger_lock:
        if _startup_logs_rotated:
            return
        os.makedirs(log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for parent, name in _iter_active_log_files(log_dir):
            old_path = os.path.join(parent, name)
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


# Map a per-device logger name back to its device id. The per-device loggers are
# named "<prefix><device_id>" with the ORIGINAL device_id (not the filename-safe
# form), so the bridge handler can recover the device id from the LogRecord alone
# (no thread-local lookup needed) and it matches bot_state's ip key exactly.
#   setup_logger_for_device       -> "logger_<id>"
#   setup_miner_logger            -> "miner_<id>"
#   get_or_create_ws_mining_logger   -> "ws_mining_<id>"    (WS 純掛機挖礦)
#   get_or_create_ws_farm_logger     -> "ws_farm_<id>"      (WS 純掛機農場)
#   get_or_create_ws_ad_reward_logger-> "ws_ad_reward_<id>" (WS 看廣告獎勵)
#   get_or_create_ws_lamp_logger     -> "ws_lamp_<id>"      (WS 開神燈)
# Order: longest/most-specific WS prefixes first so e.g. "ws_mining_" is not
# accidentally shadowed (none currently overlap, but keep the invariant explicit).
_DEVICE_LOGGER_NAME_PREFIXES = (
    "ws_ad_reward_",
    "ws_mining_",
    "ws_farm_",
    "ws_lamp_",
    "logger_",
    "miner_",
)


def _device_id_from_logger_name(name: str):
    for prefix in _DEVICE_LOGGER_NAME_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


class BotStateLogHandler(logging.Handler):
    """Forward each per-device log line into bot_state's dashboard ring buffer.

    The dashboard log-box reads bot_state._states[ip]["logs"] via /api/status.
    Without this bridge that buffer is fed only by the handful of
    update_state(log=...) call sites (error / sleep / scan), so the box renders
    near-empty even while the device emits hundreds of logger.info(...) lines.
    Attaching this handler to the device/miner loggers mirrors the real
    execution log onto the dashboard.

    Defensive by construction: a compact "HH:MM:SS - message" line, a lazy
    bot_state import (logging_utils is imported very early; bot_state must not be
    pulled in at module load), and full exception suppression so a logging path
    can never crash a device thread.
    """

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            device_id = _device_id_from_logger_name(record.name)
            if not device_id:
                return
            import bot_state  # lazy: avoid import cycle / early-load cost
            bot_state.append_log(device_id, self.format(record))
        except Exception:  # never let dashboard logging break the bot
            return


def _attach_bot_state_handler(logger_obj: logging.Logger) -> None:
    """Idempotently attach the dashboard-bridge handler to a device logger."""
    if any(isinstance(h, BotStateLogHandler) for h in logger_obj.handlers):
        return
    handler = BotStateLogHandler()
    handler.setLevel(logging.INFO)
    logger_obj.addHandler(handler)


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


_DEVICE_LOG_RETENTION_DAYS = 7


def setup_logger_for_device(device_id: str) -> logging.Logger:
    """Create a per-device logger writing to logs/<device>/main.log and console."""
    log_path = LogPaths.main_log(device_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with _logger_lock:
        logger_name = f"logger_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)

        formatter = _build_formatter()
        file_handler = SafeRotatingFileHandler(
            str(log_path),
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
        _attach_bot_state_handler(logger_obj)

        _purge_old_files(str(log_path.parent / "main.*.log"), max_age_days=_DEVICE_LOG_RETENTION_DAYS)
        return logger_obj


def get_or_create_ocr_logger(device_id: str) -> logging.Logger:
    """Create a per-device OCR trace logger writing to logs/<device>/ocr_trace.log."""
    safe_device_id = LogPaths.safe_device_id(device_id)
    log_path = LogPaths.ocr_trace_log(device_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

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
        file_handler = SafeRotatingFileHandler(
            str(log_path),
            maxBytes=512 * 1024,
            backupCount=4,
            encoding='utf-8',
            mode='a'
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)

        _purge_old_files(str(log_path.parent / "ocr_trace.*.log"), max_age_days=5)
        _ocr_logger_cache[safe_device_id] = logger_obj
        return logger_obj


_ws_mining_logger_cache: dict = {}


def get_or_create_ws_mining_logger(device_id: str) -> logging.Logger:
    """Return (creating if needed) a per-device WS mining logger → ws_mining.log."""
    safe_id = LogPaths.safe_device_id(device_id)
    with _logger_lock:
        cached = _ws_mining_logger_cache.get(safe_id)
        if cached is not None:
            return cached
        log_path = LogPaths.ws_mining_log(device_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Name with the ORIGINAL device_id (not safe_id) so BotStateLogHandler can
        # recover the exact bot_state ip key and bridge WS-mining lines to dashboard.
        logger_name = f"ws_mining_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)
        formatter = _build_formatter()
        file_handler = SafeRotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            mode="a",
        )
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)
        _attach_bot_state_handler(logger_obj)
        _purge_old_files(str(log_path.parent / "ws_mining.*.log"), max_age_days=_DEVICE_LOG_RETENTION_DAYS)
        _ws_mining_logger_cache[safe_id] = logger_obj
        return logger_obj


_ws_farm_logger_cache: dict = {}


def get_or_create_ws_farm_logger(device_id: str) -> logging.Logger:
    """Return (creating if needed) a per-device WS farm logger → ws_farm.log.

    Mirrors get_or_create_ws_mining_logger: per-device file handler, rotation,
    purge of old rotated copies, propagate=False (so 豐收卡循環/種植/收成/打工
    log lines land in a device-scoped retention file for live verification).
    """
    safe_id = LogPaths.safe_device_id(device_id)
    with _logger_lock:
        cached = _ws_farm_logger_cache.get(safe_id)
        if cached is not None:
            return cached
        log_path = LogPaths.ws_farm_log(device_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Name with the ORIGINAL device_id (not safe_id) so BotStateLogHandler can
        # recover the exact bot_state ip key and bridge WS-farm lines to dashboard.
        logger_name = f"ws_farm_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)
        formatter = _build_formatter()
        file_handler = SafeRotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            mode="a",
        )
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)
        _attach_bot_state_handler(logger_obj)
        _purge_old_files(str(log_path.parent / "ws_farm.*.log"), max_age_days=_DEVICE_LOG_RETENTION_DAYS)
        _ws_farm_logger_cache[safe_id] = logger_obj
        return logger_obj


_ws_ad_reward_logger_cache: dict = {}


def get_or_create_ws_ad_reward_logger(device_id: str) -> logging.Logger:
    """Return (creating if needed) a per-device WS ad-reward logger → ws_ad_reward.log.

    Mirrors get_or_create_ws_farm_logger: per-device file handler, rotation,
    purge of old rotated copies, propagate=False (so 看廣告獎勵領取細節 — 各 AdType
    的 claimed/skipped、total_claimed — land in a device-scoped retention file for
    live verification of 挖礦鎬子/鑽頭/炸彈 廣告 是否真的領到).
    """
    safe_id = LogPaths.safe_device_id(device_id)
    with _logger_lock:
        cached = _ws_ad_reward_logger_cache.get(safe_id)
        if cached is not None:
            return cached
        log_path = LogPaths.ws_ad_reward_log(device_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Name with the ORIGINAL device_id (not safe_id) so BotStateLogHandler can
        # recover the exact bot_state ip key and bridge WS-ad-reward lines to dashboard.
        logger_name = f"ws_ad_reward_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)
        formatter = _build_formatter()
        file_handler = SafeRotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            mode="a",
        )
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)
        _attach_bot_state_handler(logger_obj)
        _purge_old_files(str(log_path.parent / "ws_ad_reward.*.log"), max_age_days=_DEVICE_LOG_RETENTION_DAYS)
        _ws_ad_reward_logger_cache[safe_id] = logger_obj
        return logger_obj


_ws_lamp_logger_cache: dict = {}


def get_or_create_ws_lamp_logger(device_id: str) -> logging.Logger:
    """Return (creating if needed) a per-device WS lamp logger -> ws_lamp.log."""
    safe_id = LogPaths.safe_device_id(device_id)
    with _logger_lock:
        cached = _ws_lamp_logger_cache.get(safe_id)
        if cached is not None:
            return cached
        log_path = LogPaths.ws_lamp_log(device_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger_name = f"ws_lamp_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)
        formatter = _build_formatter()
        file_handler = SafeRotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            mode="a",
        )
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)
        _attach_bot_state_handler(logger_obj)
        _purge_old_files(str(log_path.parent / "ws_lamp.*.log"), max_age_days=_DEVICE_LOG_RETENTION_DAYS)
        _ws_lamp_logger_cache[safe_id] = logger_obj
        return logger_obj


def setup_miner_logger(device_id: str) -> logging.Logger:
    """Create miner logger writing to logs/<device>/miner.log and console."""
    log_path = LogPaths.miner_log(device_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with _logger_lock:
        logger_name = f"miner_{device_id}"
        logger_obj = logging.getLogger(logger_name)
        _reset_handlers(logger_obj)
        logger_obj.propagate = False
        logger_obj.setLevel(logging.INFO)

        formatter = _build_formatter()
        file_handler = SafeRotatingFileHandler(
            str(log_path),
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
        _attach_bot_state_handler(logger_obj)

        _purge_old_files(str(log_path.parent / "miner.*.log"), max_age_days=_DEVICE_LOG_RETENTION_DAYS)
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

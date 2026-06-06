from flask import Flask, jsonify, render_template, request, send_from_directory, session, redirect
import functools
import hashlib
import bot_state
import config_manager  # 新增設定管理器
import json_manager  # 新增資料管理器
import logging
import os
import json
import shutil
import subprocess
from adb_operations import run_adb
import requests
import datetime
import threading
import time
import copy
import contextlib
import importlib.util
import re
from types import SimpleNamespace
from pathlib import Path

# Disable Flask logs to keep console clean
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.secret_key = hashlib.sha256(b"mushroom-fly-pet-dashboard-key").digest()

_FLY_PET_USERS = {"infinite": "infiniteroot"}


def _fly_pet_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("fly_pet_auth"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "unauthorized"}), 401
            return redirect("/fly-pet/login")
        return f(*args, **kwargs)
    return wrapper

# WebSocket support for the remote live-view bridge (Approach B). Imported
# defensively: if flask-sock is not installed the dashboard still works, only the
# /ws/live_view route is unavailable.
try:
    from flask_sock import Sock

    sock = Sock(app)
except Exception as _sock_exc:  # pragma: no cover - optional dep
    sock = None
    logger.warning(f"flask-sock unavailable, live-view disabled: {_sock_exc}")

# Registry of active live-view sessions keyed by real device IP.
# Used by /api/live_view/<ip>/stop to signal teardown when WS close frame
# is not cleanly received (proxy, half-open TCP, etc.).
_live_view_sessions: dict = {}
_live_view_lock = threading.Lock()

_WAR_ROOM_DIR = Path(__file__).resolve().parent / "push_project" / "web"
_REPO_ROOT = Path(__file__).resolve().parent
_README_PATH = _REPO_ROOT / "README.md"
_UPDATE_PATH = _REPO_ROOT / "update.txt"
_BUG_FEEDBACK_PATH = _REPO_ROOT / "reports" / "bug_feedback.jsonl"
_TEMPLATES_DIR = _REPO_ROOT / "templates"

# Master 模式：存放給遠端 Worker 的指令佇列
# { "school_laptop:emulator-5554": { "paused": True, "skip_sleep": False } }
# Worker ID 是 "worker_id:ip"
_remote_commands = {}

# Master 模式：全域指令 (發給所有 Worker)
_global_commands = {"refresh_needed": False}
_worker_webhook_endpoints = {}

# 保護 _remote_commands / _global_commands / _worker_webhook_endpoints。
# Flask worker thread (poll_commands, report_status, queue_command) 與背景
# timer thread (reset_flag) 並發存取這三個 dict。RLock 允許 nested critical
# section。HTTP I/O 一律在鎖外執行：先在鎖內 snapshot，釋放鎖後再 requests.post。
_commands_lock = threading.RLock()
_state_maintenance_started = False

_labeler_lock = threading.Lock()
_labeler_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "done": 0,
    "kept": 0,
    "deleted": 0,
    "errors": 0,
    "current": "",
    "last_error": "",
    "logs": [],
    "paused": False,
}
_trainer_lock = threading.Lock()
_trainer_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "current": "",
    "last_error": "",
    "logs": [],
}
_web_login_lock = threading.Lock()
_web_login_state = {}
_labeler_control_dir = os.path.join(".runtime", "labeler_control")

DEFAULT_LABELER_UI_CONFIG = {
    "labeler_endpoint": "http://127.0.0.1:8080/v1/chat/completions",
    "labeler_model": "local-model",
    "labeler_source_dir": "ocr_fails_new",
    "labeler_output_dir": "OCR_train",
    "labeler_daily_time": "02:00",
    "labeler_timeout_sec": "120",
    "labeler_max_retries": "2",
    "labeler_retry_delay_sec": "1.5",
    "trainer_epochs": "10",
    "trainer_remove_source": "false",
}


def _load_readme_text() -> str:
    try:
        return _README_PATH.read_text(encoding="utf-8-sig")
    except Exception as e:
        return f"README 讀取失敗: {e}"


def _load_update_text() -> str:
    try:
        return _UPDATE_PATH.read_text(encoding="utf-8-sig")
    except Exception as e:
        return f"update.txt 讀取失敗: {e}"


def _file_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except Exception:
        return 0


def _get_frontend_version() -> str:
    tracked = [
        _TEMPLATES_DIR / "dashboard.html",
        _TEMPLATES_DIR / "readme_viewer.html",
        _UPDATE_PATH,
        Path(__file__).resolve(),
    ]
    return "-".join(str(_file_mtime(path)) for path in tracked)


def _append_labeler_log(line: str):
    logs = _labeler_state.get("logs", [])
    logs.append(line.strip())
    _labeler_state["logs"] = logs[-240:]


def _append_trainer_log(line: str):
    logs = _trainer_state.get("logs", [])
    logs.append(line.strip())
    _trainer_state["logs"] = logs[-240:]


def _load_module_from_file(module_name: str, file_path: str):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _LineLogWriter:
    def __init__(self, on_line):
        self._on_line = on_line
        self._buf = ""

    def write(self, s: str):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._on_line(line)
        return len(s)

    def flush(self):
        if self._buf.strip():
            self._on_line(self._buf.strip())
        self._buf = ""


def _get_labeler_ui_config():
    cfg = config_manager.get_ocr_config()
    merged = DEFAULT_LABELER_UI_CONFIG.copy()
    if isinstance(cfg, dict):
        for k in merged.keys():
            if cfg.get(k):
                merged[k] = str(cfg.get(k))
    return merged


def _handle_labeler_line(line: str):
    _append_labeler_log(line)
    if line.startswith("[INFO]"):
        m = re.search(r"processing=(\d+)", line)
        if m:
            _labeler_state["total"] = int(m.group(1))
    elif line.startswith("[STEP]"):
        m = re.search(r"\[STEP\]\s+(\d+)/(\d+)\s+(.+)$", line)
        if m:
            _labeler_state["done"] = max(0, int(m.group(1)) - 1)
            _labeler_state["total"] = int(m.group(2))
            _labeler_state["current"] = m.group(3)
        else:
            _labeler_state["current"] = line
    elif line.startswith("[KEEP]"):
        _labeler_state["kept"] += 1
        _labeler_state["done"] += 1
        _labeler_state["current"] = line
    elif line.startswith("[DROP]"):
        _labeler_state["deleted"] += 1
        _labeler_state["done"] += 1
        _labeler_state["current"] = line
    elif line.startswith("[ERROR]"):
        _labeler_state["errors"] += 1
        _labeler_state["done"] += 1
        _labeler_state["last_error"] = line
        _labeler_state["current"] = line
    elif line.startswith("[DONE]"):
        _labeler_state["current"] = line
    elif line.startswith("[PAUSE]"):
        _labeler_state["paused"] = True
    elif line.startswith("[RESUME]"):
        _labeler_state["paused"] = False


def _run_labeler_once_worker(cfg: dict):
    try:
        with _labeler_lock:
            _labeler_state.update(
                {
                    "running": True,
                    "started_at": time.time(),
                    "finished_at": None,
                    "total": 0,
                    "done": 0,
                    "kept": 0,
                    "deleted": 0,
                    "errors": 0,
                    "current": "",
                    "last_error": "",
                    "logs": [],
                    "paused": False,
                }
            )

        os.makedirs(_labeler_control_dir, exist_ok=True)
        for fn in ("pause.flag", "stop.flag"):
            p = os.path.join(_labeler_control_dir, fn)
            if os.path.exists(p):
                os.remove(p)

        labeler_module = _load_module_from_file(
            "daily_ocr_fail_labeler_runtime",
            str(Path("tools/daily_ocr_fail_labeler.py").resolve()),
        )
        args = SimpleNamespace(
            ocr_server="ocr_server.py",
            source_dir=cfg["labeler_source_dir"],
            output_dir=cfg["labeler_output_dir"],
            endpoint=cfg["labeler_endpoint"],
            model=cfg["labeler_model"],
            timeout=int(str(cfg.get("labeler_timeout_sec", "120")).strip() or "120"),
            max_retries=int(str(cfg.get("labeler_max_retries", "2")).strip() or "2"),
            retry_delay_sec=float(
                str(cfg.get("labeler_retry_delay_sec", "1.5")).strip() or "1.5"
            ),
            control_dir=_labeler_control_dir,
            once=True,
            daily_time=cfg.get("labeler_daily_time", "02:00"),
        )

        def _ocr_log_cb(line: str):
            with _labeler_lock:
                _handle_labeler_line(line)

        args.log_fn = _ocr_log_cb
        labeler_module.process_once(args)

        with _labeler_lock:
            _labeler_state["running"] = False
            _labeler_state["paused"] = False
            _labeler_state["finished_at"] = time.time()
    except Exception as exc:
        with _labeler_lock:
            _labeler_state["running"] = False
            _labeler_state["paused"] = False
            _labeler_state["finished_at"] = time.time()
            _labeler_state["last_error"] = str(exc)
            _append_labeler_log(f"[ERROR] worker exception: {exc}")


def _run_trainer_worker(cfg: dict):
    try:
        with _trainer_lock:
            _trainer_state.update(
                {
                    "running": True,
                    "started_at": time.time(),
                    "finished_at": None,
                    "current": "",
                    "last_error": "",
                    "logs": [],
                }
            )
            _append_trainer_log("[START] trainer worker started")
            _append_trainer_log("[EXEC] native python function call")

        trainer_module = _load_module_from_file(
            "auto_train_workflow_runtime",
            str(Path("OCR/auto_train_workflow.py").resolve()),
        )
        argv = [
            "--source-dir",
            cfg["labeler_output_dir"],
            "--epochs",
            str(cfg.get("trainer_epochs", "10")),
        ]
        if str(cfg.get("trainer_remove_source", "false")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            argv.append("--remove-source")

        def on_line(line: str):
            with _trainer_lock:
                _append_trainer_log(line)
                _trainer_state["current"] = line
                if line.startswith("[ERROR]"):
                    _trainer_state["last_error"] = line

        writer = _LineLogWriter(on_line)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            code = trainer_module.main(argv)
        writer.flush()

        with _trainer_lock:
            if code != 0:
                _trainer_state["last_error"] = f"Trainer exited with code {code}"
            _trainer_state["running"] = False
            _trainer_state["finished_at"] = time.time()
    except Exception as exc:
        with _trainer_lock:
            _trainer_state["running"] = False
            _trainer_state["finished_at"] = time.time()
            _trainer_state["last_error"] = str(exc)
            _append_trainer_log(f"[ERROR] worker exception: {exc}")


def _check_llama_endpoint(
    endpoint: str, model: str, timeout_sec: float = 8.0
) -> tuple[bool, str]:
    endpoint = str(endpoint or "").strip()
    model = str(model or "local-model").strip()
    if not endpoint:
        return False, "endpoint is empty"
    try:
        # Connection check should be text-only (no image payload).
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply OK only."}],
        }
        r = requests.post(endpoint, json=payload, timeout=timeout_sec)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:220]}"
        data = r.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            return False, "response missing choices"
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = msg.get("content", "")
        return True, f"connected, sample: {str(content)[:100]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _normalize_web_login_state(ip: str):
    state = _web_login_state.get(ip)
    if not isinstance(state, dict):
        state = {
            "running": False,
            "started_at": None,
            "finished_at": None,
            "last_error": "",
            "last_message": "",
            "last_state_file": "",
            "last_profile_dir": "",
            "last_backup_file": "",
            "reused_existing_session": False,
        }
        _web_login_state[ip] = state
    return state


def _resolve_web_profile_dir(ip: str, profile_dir_raw: str) -> str:
    raw = (
        str(profile_dir_raw or "playwright_profile/{device_id}").strip()
        or "playwright_profile/{device_id}"
    )
    profile_dir = raw.format(device_id=ip, ip=ip)
    if not os.path.normpath(profile_dir).endswith(ip):
        profile_dir = os.path.join(profile_dir, ip)
    if not os.path.isabs(profile_dir):
        profile_dir = os.path.join(os.getcwd(), profile_dir)
    return os.path.normpath(profile_dir)


def _resolve_web_state_file(ip: str, state_file_raw: str) -> str:
    raw = (
        str(state_file_raw or "auth_state/{device_id}.json").strip()
        or "auth_state/{device_id}.json"
    )
    state_file = raw.format(device_id=ip, ip=ip)
    if "{device_id}" not in raw and "{ip}" not in raw:
        if os.path.basename(state_file).lower() == "auth_state.json":
            state_file = os.path.join(
                os.path.dirname(state_file), "auth_state", f"{ip}.json"
            )
    if not os.path.isabs(state_file):
        state_file = os.path.join(os.getcwd(), state_file)
    return os.path.normpath(state_file)


def _existing_profile_dir(paths) -> str:
    seen = set()
    for path in paths:
        p = os.path.normpath(str(path or "").strip())
        if not p or p in seen:
            continue
        seen.add(p)
        if not os.path.isdir(p):
            continue
        cookies_db = os.path.join(p, "Default", "Network", "Cookies")
        if os.path.exists(cookies_db):
            return p
        try:
            with os.scandir(p) as it:
                for _ in it:
                    return p
        except Exception:
            continue
    return ""


def _existing_state_file(paths) -> str:
    seen = set()
    for path in paths:
        p = os.path.normpath(str(path or "").strip())
        if not p or p in seen:
            continue
        seen.add(p)
        if not os.path.isfile(p):
            continue
        try:
            if os.path.getsize(p) > 0:
                return p
        except Exception:
            continue
    return ""


def _backup_web_state_file(ip: str, state_file: str) -> str:
    if not os.path.isfile(state_file):
        raise FileNotFoundError(state_file)
    backup_root = os.path.join(os.path.dirname(state_file), "backups", ip)
    os.makedirs(backup_root, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    backup_path = os.path.join(backup_root, f"state_{stamp}.json")
    shutil.copy2(state_file, backup_path)
    return backup_path


def _restore_web_state_to_context(context, state_file: str) -> tuple[bool, str]:
    if not os.path.isfile(state_file):
        return False, "state_file_missing"

    try:
        with open(state_file, "r", encoding="utf-8-sig") as f:
            state_data = json.load(f)
    except Exception as exc:
        return False, f"read_state_failed:{exc}"

    cookies = state_data.get("cookies", []) if isinstance(state_data, dict) else []
    origins = state_data.get("origins", []) if isinstance(state_data, dict) else []
    cookie_count = 0
    local_count = 0

    if isinstance(cookies, list) and cookies:
        try:
            context.add_cookies(cookies)
            cookie_count = len(cookies)
        except Exception as exc:
            return False, f"add_cookies_failed:{exc}"

    if isinstance(origins, list) and origins:
        page = context.pages[0] if context.pages else context.new_page()
        for item in origins:
            if not isinstance(item, dict):
                continue
            origin = str(item.get("origin") or "").strip()
            local_entries = item.get("localStorage") or []
            if not origin or not isinstance(local_entries, list) or not local_entries:
                continue
            try:
                page.goto(origin, wait_until="domcontentloaded", timeout=15000)
                page.evaluate(
                    """
                    (entries) => {
                        for (const row of entries || []) {
                            if (!row || typeof row.name !== 'string') continue;
                            const value = row.value === undefined || row.value === null ? '' : String(row.value);
                            localStorage.setItem(row.name, value);
                        }
                    }
                    """,
                    local_entries,
                )
                local_count += len(local_entries)
            except Exception:
                continue

    return (
        cookie_count > 0 or local_count > 0
    ), f"cookies={cookie_count}, localStorage={local_count}"


def _run_web_login_worker(ip: str, payload: dict):
    with _web_login_lock:
        state = _normalize_web_login_state(ip)
        state.update(
            {
                "running": True,
                "started_at": time.time(),
                "finished_at": None,
                "last_error": "",
                "last_message": "starting",
            }
        )

    try:
        from playwright.sync_api import sync_playwright

        cfg = config_manager.get_device_config(ip)
        web_url = str(payload.get("web_url") or cfg.get("web_url") or "").strip()
        if not web_url:
            raise ValueError("web_url is required")

        request_profile_raw = (
            str(
                payload.get("web_profile_dir")
                or cfg.get("web_profile_dir")
                or "playwright_profile/{device_id}"
            ).strip()
            or "playwright_profile/{device_id}"
        )
        request_state_raw = (
            str(
                payload.get("web_state_file")
                or cfg.get("web_state_file")
                or "auth_state/{device_id}.json"
            ).strip()
            or "auth_state/{device_id}.json"
        )
        cfg_profile_raw = (
            str(cfg.get("web_profile_dir") or "playwright_profile/{device_id}").strip()
            or "playwright_profile/{device_id}"
        )
        cfg_state_raw = (
            str(cfg.get("web_state_file") or "auth_state/{device_id}.json").strip()
            or "auth_state/{device_id}.json"
        )

        channel = str(
            payload.get("web_channel") or cfg.get("web_channel") or "chrome"
        ).strip()
        canvas_selector = (
            str(
                payload.get("web_canvas_selector")
                or cfg.get("web_canvas_selector")
                or "canvas"
            ).strip()
            or "canvas"
        )
        headless = bool(payload.get("web_headless", cfg.get("web_headless", False)))
        clear_cookies_on_start = bool(
            payload.get(
                "web_clear_cookies_on_start",
                cfg.get("web_clear_cookies_on_start", False),
            )
        )

        # 手動開啟使用獨立的 viewport 設定，若未設定則回退到原本 viewport
        manual_width = int(cfg.get("web_manual_viewport_width") or 0)
        manual_height = int(cfg.get("web_manual_viewport_height") or 0)
        viewport_width = int(
            payload.get("web_viewport_width")
            or (
                manual_width
                if manual_width > 0
                else cfg.get("web_viewport_width") or 540
            )
        )
        viewport_height = int(
            payload.get("web_viewport_height")
            or (
                manual_height
                if manual_height > 0
                else cfg.get("web_viewport_height") or 960
            )
        )
        prefer_existing_state = bool(payload.get("prefer_existing_state", True))
        force_new_session = bool(payload.get("force_new_session", False))
        backup_before_open = bool(payload.get("backup_before_open", True))

        requested_profile_dir = _resolve_web_profile_dir(ip, request_profile_raw)
        requested_state_file = _resolve_web_state_file(ip, request_state_raw)
        cfg_profile_dir = _resolve_web_profile_dir(ip, cfg_profile_raw)
        cfg_state_file = _resolve_web_state_file(ip, cfg_state_raw)
        default_profile_dir = _resolve_web_profile_dir(
            ip, "playwright_profile/{device_id}"
        )
        default_state_file = _resolve_web_state_file(ip, "auth_state/{device_id}.json")

        profile_dir = requested_profile_dir
        state_file = requested_state_file
        reused_existing_session = False

        if prefer_existing_state and not force_new_session:
            existing_profile = _existing_profile_dir(
                [
                    requested_profile_dir,
                    cfg_profile_dir,
                    default_profile_dir,
                ]
            )
            existing_state = _existing_state_file(
                [
                    requested_state_file,
                    cfg_state_file,
                    default_state_file,
                ]
            )
            if existing_profile:
                profile_dir = existing_profile
                reused_existing_session = True
            if existing_state:
                state_file = existing_state
                reused_existing_session = True

        os.makedirs(profile_dir, exist_ok=True)
        os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)

        backup_file = ""
        if backup_before_open and os.path.isfile(state_file):
            try:
                backup_file = _backup_web_state_file(ip, state_file)
            except Exception as backup_exc:
                app.logger.warning(f"[{ip}] backup cookies/state failed: {backup_exc}")

        with _web_login_lock:
            state = _normalize_web_login_state(ip)
            state["last_message"] = f"opening browser: {web_url}"
            state["last_profile_dir"] = profile_dir
            state["last_state_file"] = state_file
            state["last_backup_file"] = backup_file
            state["reused_existing_session"] = reused_existing_session

        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": profile_dir,
                "headless": headless,
                "viewport": {"width": viewport_width, "height": viewport_height},
                "device_scale_factor": 1.0,
                "ignore_default_args": ["--enable-automation"],
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--force-device-scale-factor=1",
                    "--high-dpi-support=1",
                ],
            }
            if channel:
                launch_kwargs["channel"] = channel

            context = p.chromium.launch_persistent_context(**launch_kwargs)
            if clear_cookies_on_start:
                try:
                    context.clear_cookies()
                except Exception:
                    pass
            else:
                profile_cookie_db = os.path.join(
                    profile_dir, "Default", "Network", "Cookies"
                )
                profile_has_cookie_db = False
                try:
                    profile_has_cookie_db = (
                        os.path.isfile(profile_cookie_db)
                        and os.path.getsize(profile_cookie_db) > 0
                    )
                except Exception:
                    profile_has_cookie_db = os.path.isfile(profile_cookie_db)

                # New profile can still reuse old login state by loading saved cookies/localStorage.
                if os.path.isfile(state_file) and not profile_has_cookie_db:
                    restored, detail = _restore_web_state_to_context(
                        context, state_file
                    )
                    if restored:
                        app.logger.info(
                            f"[{ip}] restored saved web state from {state_file} ({detail})"
                        )
                    else:
                        app.logger.warning(
                            f"[{ip}] restore saved web state skipped/failed: {detail}"
                        )

            page = context.pages[0] if context.pages else context.new_page()
            page.goto(web_url)
            try:
                page.wait_for_selector(canvas_selector, timeout=15000)
            except Exception:
                pass

            with _web_login_lock:
                state = _normalize_web_login_state(ip)
                state["last_message"] = "請在瀏覽器中完成登入，完成後直接關閉瀏覽器即可自動儲存 cookies"

            _state_saved = [False]

            def _save_on_close(_page):
                if not _state_saved[0]:
                    try:
                        context.storage_state(path=state_file)
                        _state_saved[0] = True
                    except Exception as _exc:
                        app.logger.warning(f"[{ip}] page-close state save failed: {_exc}")

            page.on("close", _save_on_close)

            try:
                page.pause()  # blocks until Resume clicked in Inspector
                if not _state_saved[0]:
                    context.storage_state(path=state_file)
                    _state_saved[0] = True
            except Exception:
                pass  # browser closed by user — _save_on_close already ran

            try:
                context.close()
            except Exception:
                pass

        with _web_login_lock:
            state = _normalize_web_login_state(ip)
            state["running"] = False
            state["finished_at"] = time.time()
            msg = f"login state saved: {state_file}"
            if backup_file:
                msg += f" (backup: {backup_file})"
            state["last_message"] = msg
    except Exception as exc:
        with _web_login_lock:
            state = _normalize_web_login_state(ip)
            state["running"] = False
            state["finished_at"] = time.time()
            state["last_error"] = str(exc)
            state["last_message"] = "failed"


def check_ocr_server():
    """Check OCR server health based on current OCR config (main/backup/auto)."""
    try:
        ocr_cfg = config_manager.get_ocr_config()
        mode = str(ocr_cfg.get("server_mode", "main")).strip().lower()
        servers = [
            str(s).strip().rstrip("/")
            for s in ocr_cfg.get("servers", [])
            if str(s).strip()
        ]

        main = "http://100.64.0.5:5001"
        backup = "http://100.64.0.7:5001"
        local = "http://127.0.0.1:5001"

        if mode == "backup":
            priority = [backup, main, local]
        elif mode == "auto":
            priority = [main, backup, local]
        else:
            priority = [main, backup, local]

        for s in servers:
            if s not in priority:
                priority.append(s)

        for base in priority:
            try:
                response = requests.get(f"{base}/health", timeout=2)
                if response.status_code == 200:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


import cv2
import numpy as np
import base64
from game_state.detector import stage_by_str
import new_cnn.cnn_model as cnn_model_module

# 全域模型快取 (避免重複載入)
_cached_models = {"cnn": None}

_PROGRAM_FIX_NOTE_FALLBACK = "(無法讀取最新 commit message)"
_PROGRAM_INFO_CACHE: dict = {"value": None, "expires_at": 0.0}
_PROGRAM_INFO_TTL_SEC = 30.0


def _get_program_info():
    """右上角程式資訊：版本 / git 時間 / 最新修補摘要。

    `fix_note` 取最新 commit 的 subject line，這樣每次 git commit 之後
    儀表板自動反映新內容（30 秒 TTL，避免把每次頁面渲染都付一次 git fork 成本）。
    """
    now = time.time()
    cached = _PROGRAM_INFO_CACHE.get("value")
    if cached and now < _PROGRAM_INFO_CACHE.get("expires_at", 0):
        return cached

    repo_root = Path(__file__).resolve().parent
    version = "dev"
    git_time = "unknown"
    fix_note = _PROGRAM_FIX_NOTE_FALLBACK

    # Windows 預設 locale 常是 cp950 (zh-TW)，git 輸出 UTF-8 bytes 撞 cp950
    # 解碼會 raise UnicodeDecodeError，讓整段資訊全變 fallback。明確指定
    # encoding="utf-8" + errors="replace" 才是穩定做法。
    def _git(args):
        import shutil
        git_exe = shutil.which("git") or r"C:\Program Files\Git\mingw64\bin\git.exe"
        return subprocess.run(
            [git_exe, "-c", "i18n.logOutputEncoding=UTF-8", *args],
            cwd=str(repo_root),
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()

    try:
        short_sha = _git(["rev-parse", "--short", "HEAD"])
        if short_sha:
            version = f"git-{short_sha}"
    except Exception as exc:
        logger.warning("[program_info] read short_sha failed: %s", exc)

    try:
        commit_time = _git(["show", "-s", "--format=%cd", "--date=iso-local", "HEAD"])
        if commit_time:
            git_time = commit_time
    except Exception as exc:
        logger.warning("[program_info] read commit_time failed: %s", exc)

    try:
        commit_subject = _git(["show", "-s", "--format=%s", "HEAD"])
        if commit_subject:
            fix_note = commit_subject
    except Exception as exc:
        logger.warning("[program_info] read commit_subject failed: %s", exc)

    info = {
        "version": version,
        "git_time": git_time,
        "fix_note": fix_note,
    }
    _PROGRAM_INFO_CACHE["value"] = info
    _PROGRAM_INFO_CACHE["expires_at"] = now + _PROGRAM_INFO_TTL_SEC
    return info


@app.route("/api/analyze_stage", methods=["POST"])
def analyze_stage():
    """集中式頁面狀態判定服務"""
    try:
        data = request.json
        img_base64 = data.get("image")
        if not img_base64:
            return jsonify({"success": False, "error": "No image data"}), 400

        # 解碼圖片
        img_bytes = base64.b64decode(img_base64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # 載入模型 (如果尚未載入)
        if _cached_models["cnn"] is None:
            from pathlib import Path

            model_path = "cnn_model.pth"
            if os.path.exists(model_path):
                _cached_models["cnn"] = cnn_model_module.load_cnn_model(model_path)

        # 1. 執行 OCR 獲取文字 (透過本地邏輯)
        from img_tools import get_all_text

        ocr_result = get_all_text(img)

        # 2. 執行判定邏輯
        # 這裡我們需要傳入一個 mock 的 device 物件，因為目前的 stage_by_str 可能會用到 d (雖然目前沒用到)
        stage = stage_by_str(None, ocr_result, img)

        return jsonify({"success": True, "stage": stage, "ocr_text": ocr_result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


from flask import Flask, jsonify, render_template, request, send_from_directory

# ... (其餘 import 與全域變數保持不變) ...


@app.route("/")
def index():
    """主控面板首頁"""
    return render_template(
        "dashboard.html",
        program_info=_get_program_info(),
        frontend_version=_get_frontend_version(),
    )


@app.route("/updates")
@app.route("/updates/")
def updates_page():
    """Serve update.txt content inside the control panel."""
    return render_template(
        "readme_viewer.html",
        page_title="更新公告",
        page_subtitle="目前顯示的是 repo 根目錄的 `update.txt` 內容。",
        page_text=_load_update_text(),
        frontend_version=_get_frontend_version(),
    )


@app.route("/war-room")
@app.route("/war-room/")
def war_room_index():
    """Serve the existing cross-server parking battle room inside control panel."""
    return send_from_directory(str(_WAR_ROOM_DIR), "菇勇者.html")


@app.route("/war-room/<path:filename>")
def war_room_static(filename):
    return send_from_directory(str(_WAR_ROOM_DIR), filename)


@app.route("/fly-pet/login", methods=["GET", "POST"])
def fly_pet_login():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if _FLY_PET_USERS.get(u) == p:
            session["fly_pet_auth"] = True
            return redirect("/fly-pet")
        return render_template("fly_pet_login.html", error="帳號或密碼錯誤")
    return render_template("fly_pet_login.html")


@app.route("/fly-pet/logout")
def fly_pet_logout():
    session.pop("fly_pet_auth", None)
    return redirect("/fly-pet/login")


@app.route("/fly-pet")
@_fly_pet_auth
def fly_pet_page():
    return render_template("fly_pet.html")


@app.route("/api/poll_commands", methods=["POST"])
def poll_commands():
    """Worker 輪詢指令"""
    try:
        data = request.json
        worker_id = data.get("worker_id")
        ips = data.get("ips", [])

        # --- 1+2 在同一把鎖內完成快照，避免讀寫競態 ---
        with _commands_lock:
            global_resp = {}
            if _global_commands["refresh_needed"]:
                global_resp["refresh_needed"] = True
                # 注意：這裡不能立刻設為 False，否則其他 Worker 就收不到了

            response_cmds = {}
            for ip in ips:
                remote_id = f"{worker_id}:{ip}"
                if remote_id in _remote_commands:
                    response_cmds[ip] = _remote_commands[remote_id].copy()
                    # 對於 skip_sleep 這種一次性指令，讀取後刪除
                    if "skip_sleep" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["skip_sleep"]
                    if "recover" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["recover"]
                    if "manual_release" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["manual_release"]
                    if "force_sleep" in _remote_commands[remote_id]:
                        del _remote_commands[remote_id]["force_sleep"]

            refresh_snapshot = _global_commands["refresh_needed"]

        return jsonify(
            {
                "status": "ok",
                "commands": response_cmds,
                "global_commands": {
                    "refresh_needed": refresh_snapshot
                },
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _push_to_worker_webhook(worker_id: str, payload: dict) -> bool:
    # snapshot endpoint URL under lock，HTTP call 放在鎖外
    with _commands_lock:
        endpoint = (_worker_webhook_endpoints.get(worker_id) or {}).get("url", "")
    if not endpoint:
        return False
    try:
        resp = requests.post(endpoint, json=payload, timeout=1.8, verify=False)
        return bool(resp.ok)
    except Exception:
        return False


def _push_remote_command_if_possible(remote_id: str):
    if ":" not in remote_id:
        return
    worker_id, device_ip = remote_id.split(":", 1)
    # 鎖內 snapshot；HTTP push 與後續 one-shot 消費分開兩段鎖
    with _commands_lock:
        queued = _remote_commands.get(remote_id)
        if not isinstance(queued, dict) or not queued:
            return
        payload = {
            "worker_id": worker_id,
            "commands": {device_ip: copy.deepcopy(queued)},
            "global_commands": {"refresh_needed": _global_commands["refresh_needed"]},
        }
    pushed = _push_to_worker_webhook(worker_id, payload)
    if not pushed:
        return

    with _commands_lock:
        queued = _remote_commands.get(remote_id)
        if not isinstance(queued, dict):
            return
        # one-shot commands are consumed after successful push
        if "skip_sleep" in queued:
            del queued["skip_sleep"]
        if "recover" in queued:
            del queued["recover"]
        if "manual_release" in queued:
            del queued["manual_release"]
        if "force_sleep" in queued:
            del queued["force_sleep"]


@app.route("/api/refresh_devices", methods=["POST"])
def refresh_devices():
    """觸發所有端 (Master & Worker) 重新掃描 ADB"""
    print("[Master] 正在重置 ADB Server 以刷新裝置列表...")
    try:
        # 執行 ADB 重啟
        import subprocess

        subprocess.run(["adb", "kill-server"], check=False)
        time.sleep(1)
        subprocess.run(["adb", "start-server"], check=False)
        time.sleep(2)
        print("[Master] ADB Server 已重啟")
    except Exception as e:
        print(f"[Master] 重啟 ADB Server 失敗: {e}")

    # 1. 通知本地 Master
    bot_state.set_refresh_needed()

    # 2. 通知所有遠端 Worker
    with _commands_lock:
        _global_commands["refresh_needed"] = True
        worker_ids_snapshot = list(_worker_webhook_endpoints.keys())
    for worker_id in worker_ids_snapshot:
        _push_to_worker_webhook(
            worker_id,
            {
                "worker_id": worker_id,
                "commands": {},
                "global_commands": {"refresh_needed": True},
            },
        )

    # 3. 設定一個計時器，15 秒後把全域刷新 Flag 關掉 (確保所有 Worker 都有機會讀到)
    def reset_flag():
        time.sleep(15)
        with _commands_lock:
            _global_commands["refresh_needed"] = False
        print("[Master] 全域刷新 Flag 已重置")

    threading.Thread(target=reset_flag, daemon=True).start()

    return jsonify({"status": "ok", "message": "已觸發全域設備掃描"})


@app.route("/api/device_data/<ip>", methods=["GET"])
def get_device_data(ip):
    """讀取設備的執行紀錄 JSON (例如 emulator-5554.json)"""
    try:
        # 處理分散式架構的 IP (例如: school_laptop:emulator-5554)
        # 因為使用 SMB 共用，所有 json 都在同一個目錄下，只要還原出真實的 device_id 即可
        real_device_id = ip
        if ":" in ip:
            # 取最後一部分作為真實 ID
            real_device_id = ip.split(":")[-1]

        manager = json_manager.JsonDataManager(real_device_id)
        data = manager.load_data()
        return jsonify(data)

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/daily_progress/<ip>", methods=["GET"])
def get_daily_progress(ip):
    """獲取設備的今日進度統計"""
    try:
        real_device_id = ip
        if ":" in ip:
            real_device_id = ip.split(":")[-1]

        manager = json_manager.JsonDataManager(real_device_id)

        # 定義要追蹤的任務清單
        # config 格式: { "key": json_key, "cycle": (record_name, weeks) }
        tasks_config = {
            "農場買種": {"key": "farm_seed_purchase"},
            "農場種植": {"key": "farm_plant_click"},
            "挖礦": {"key": ["\u6316\u7926", "挖礦"]},
            "地獄之門": {"key": "地獄之門"},
            "萬神試煉": {"key": "萬神試煉"},
            "家族任務": {"key": ["family_market_timestamp", "donate_family"]},
            "商店購買": {"key": "Store"},
            "每日任務": {"key": "mission_timestamp"},
            "坐騎衝刺": {"key": "衝刺-發條", "cycle": ("衝刺-發條", 4)},
            "菇菇武道會": {
                "key": "mushroom_arena_daily",
                "cycle": ("mushroom_arena_cycle_start", 4),
            },
            "航海": {"key": "sea_last_execution", "cycle": ("sea_cycle_start", 4)},
        }

        results = {}
        data = manager.load_data()

        def check_is_today(key_or_list):
            keys = key_or_list if isinstance(key_or_list, list) else [key_or_list]
            for key in keys:
                if manager.is_same_day(key):
                    return True
                if key in data and isinstance(data[key], dict):
                    last_time = data[key].get("last_time")
                    if last_time:
                        try:
                            record_date = last_time.split(" ")[0]
                            today = datetime.datetime.now(manager.timezone).strftime(
                                "%Y-%m-%d"
                            )
                            if record_date == today:
                                return True
                        except Exception:
                            pass
            return False

        for display_name, config in tasks_config.items():
            # 1. 檢查週期 (如果有的話)
            if "cycle" in config:
                record_name, weeks = config["cycle"]
                should_exec, _ = json_manager._should_execute_cycle(
                    real_device_id, record_name, cycle_weeks=weeks
                )
                if not should_exec:
                    continue  # 本週不執行，直接隱藏

            # 2. 檢查今日是否完成
            results[display_name] = check_is_today(config["key"])

        return jsonify(results)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- Worker 回報 API ---
@app.route("/api/report_status", methods=["POST"])
def report_status():
    """接收 Worker 回報的狀態"""
    try:
        data = (
            request.json
        )  # { "worker_id:ip": {status...}, "__CMD__": {...}, "__META__": {...} }
        if not data:
            return jsonify({"status": "empty"})

        meta = data.get("__META__", {})
        if "__META__" in data:
            del data["__META__"]
        if isinstance(meta, dict):
            worker_id = str(meta.get("worker_id") or "").strip()
            webhook_url = str(meta.get("webhook_url") or "").strip()
            if worker_id and webhook_url.startswith(("http://", "https://")):
                with _commands_lock:
                    _worker_webhook_endpoints[worker_id] = {
                        "url": webhook_url,
                        "updated_at": time.time(),
                    }

        # 處理特殊指令
        if "__CMD__" in data:
            cmd = data["__CMD__"]
            if cmd.get("action") == "clear_offline":
                bot_state.clear_offline_devices()
            del data["__CMD__"]

        for remote_id, state_update in data.items():
            # 使用 bot_state.update_state 統一處理，這會自動處理 Lock 和 last_update
            bot_state.update_state(
                remote_id,
                task=state_update.get("task"),
                step=state_update.get("step"),
                next_wake_at=state_update.get("next_wake_at"),
                paused=state_update.get("paused"),  # 接收暫停狀態
            )

            # 如果有 Log 或截圖平均時間，透過公開 accessor 更新（已含鎖）
            bot_state.update_remote_metrics(
                remote_id,
                logs=state_update["logs"] if ("logs" in state_update and state_update["logs"]) else None,
                avg_screenshot_ms=state_update["avg_screenshot_ms"] if "avg_screenshot_ms" in state_update else None,
            )

        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- 控制 API (修改以支援遠端) ---


def _is_local_command_target(ip: str) -> bool:
    """Decide whether a control command targets a local device thread.

    Must NOT use ``":" in ip``: a TCP-attached local emulator (e.g.
    ``127.0.0.1:5555``) has a colon yet is local, and the old heuristic
    misrouted its pause / force-sleep commands to the remote-worker queue
    where nothing consumed them. Remote worker devices are keyed
    ``worker_id:ip`` and never register as a local thread, so the local
    registry is the reliable discriminator. The colon check survives only as
    a fallback for a local device whose thread has not registered yet.
    """
    if bot_state.is_local_device(ip):
        return True
    return ":" not in ip


def queue_command(ip, cmd_key, cmd_val):
    """將指令加入佇列 (針對遠端) 或直接執行 (針對本地)"""
    if not _is_local_command_target(ip):
        with _commands_lock:
            if ip not in _remote_commands:
                _remote_commands[ip] = {}
            _remote_commands[ip][cmd_key] = cmd_val
        _push_remote_command_if_possible(ip)
    else:
        # 本地設備
        if cmd_key == "paused":
            bot_state.set_pause(ip, cmd_val)
        elif cmd_key == "skip_sleep" and cmd_val:
            bot_state.set_skip_sleep(ip)
        elif cmd_key == "manual_release" and cmd_val:
            bot_state.set_manual_release(ip)
        elif cmd_key == "force_sleep" and cmd_val:
            bot_state.request_force_sleep(ip)
        elif cmd_key == "recover" and cmd_val:
            # 本地直接執行 ADB (recover_screen 函數會處理)
            pass


@app.route("/api/status")
def get_status():
    states = bot_state.get_all_states()
    ocr_alive = check_ocr_server()
    ocr_runtime = {}
    try:
        from img_tools import get_ocr_runtime_status

        ocr_runtime = get_ocr_runtime_status()
    except Exception:
        ocr_runtime = {}
    live_view_enabled = bool(
        (config_manager.get_global_config().get("live_view") or {}).get("enabled", False)
    )
    for ip, info in states.items():
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        cfg = config_manager.get_device_config(real_ip)
        info["name"] = cfg.get("name") or real_ip
        info["is_real_phone"] = cfg.get("is_real_phone", False)
        info["backend"] = cfg.get("backend", "adb")
        info["web_stop_mode"] = cfg.get("web_stop_mode", "keep_page")
        info["mining_planner_version"] = cfg.get("mining_planner_version", "v4")
        info["live_view_available"] = bool(
            live_view_enabled
            and info["backend"] == "web_h5"
            and cfg.get("web_debug_port")
            and sock is not None
        )
    return jsonify(
        {"bots": states, "ocr_server": ocr_alive, "ocr_runtime": ocr_runtime}
    )


@app.route("/api/config/<ip>", methods=["GET"])
def get_device_conf(ip):
    """獲取指定設備的設定 (raw dict, includes non-typed keys like `carpark`,
    `statue_weekly`, `experimental_cocos_navigation`).

    Uses `get_device_config_dict` rather than `get_device_config`: the latter
    returns a `DeviceConfig` dataclass whose `_extra` field jsonify-leaks as a
    nested `_extra` object instead of flattening, hiding fields from frontend
    code that reads `config.<key>` directly.
    """
    # 處理遠端 IP 設定讀取 (需要從檔名反推)
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    return jsonify(config_manager.get_device_config_dict(real_ip))


@app.route("/api/bug_feedback", methods=["POST"])
def submit_bug_feedback():
    try:
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()
        detail = str(data.get("detail", "")).strip()
        reporter = str(data.get("reporter", "")).strip()
        page = str(data.get("page", "")).strip()
        if not title or not detail:
            return jsonify({"status": "error", "message": "title and detail are required"}), 400

        payload = {
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "title": title,
            "detail": detail,
            "reporter": reporter,
            "page": page,
        }
        _BUG_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _BUG_FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/frontend_version", methods=["GET"])
def get_frontend_version():
    return jsonify({"version": _get_frontend_version()})


@app.after_request
def add_no_cache_headers(response):
    # Fly-pet icons are immutable real-sprite PNGs keyed by config_id — let the
    # browser cache them so the card wall doesn't re-fetch ~38 URLs on every scroll.
    if request.method == "GET" and not request.path.startswith("/api/fly_pet_icon/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/api/config/<ip>", methods=["POST"])
def set_device_conf(ip):
    """更新指定設備的設定"""
    try:
        data = request.json
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        config_manager.update_device_config(real_ip, data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ocr_config", methods=["GET"])
def get_ocr_conf():
    """獲取 OCR 全域設定（含位置/重試/伺服器）。"""
    try:
        return jsonify(config_manager.get_ocr_config())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ocr_config", methods=["POST"])
def set_ocr_conf():
    """更新 OCR 全域設定（動態生效，所有讀取端下次呼叫即使用新值）。"""
    try:
        data = request.json or {}
        config_manager.update_ocr_config(data)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/web_login/<ip>", methods=["POST"])
def start_web_login(ip):
    """Start manual Playwright login flow from control panel."""
    try:
        payload = request.get_json(silent=True) or {}
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        persist_settings = bool(payload.get("persist_settings", False))

        # Keep current config by default. Persist only when explicitly requested.
        safe_cfg = {}
        for key in [
            "backend",
            "backend_display_id",
            "web_url",
            "web_canvas_selector",
            "web_profile_dir",
            "web_state_file",
            "web_channel",
            "web_headless",
            "web_clear_cookies_on_start",
            "web_stop_mode",
            "web_viewport_width",
            "web_viewport_height",
            "web_manual_viewport_width",
            "web_manual_viewport_height",
        ]:
            if key in payload:
                safe_cfg[key] = payload.get(key)
        if persist_settings and safe_cfg:
            config_manager.update_device_config(real_ip, safe_cfg)

        with _web_login_lock:
            state = _normalize_web_login_state(real_ip)
            if state.get("running"):
                return jsonify(
                    {"status": "busy", "message": "web login is already running"}
                ), 409

        t = threading.Thread(
            target=_run_web_login_worker,
            args=(real_ip, payload),
            daemon=True,
            name=f"web-login-{real_ip}",
        )
        t.start()
        return jsonify({"status": "ok", "message": "web login started", "ip": real_ip})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/devices/register", methods=["POST"])
def register_device():
    """Create a new web_h5 device entry and immediately start the manual login flow."""
    import re
    try:
        data = request.get_json(silent=True) or {}
        device_id = str(data.get("device_id", "")).strip()
        if not device_id:
            return jsonify({"status": "error", "message": "device_id is required"}), 400
        if not re.match(r'^[A-Za-z0-9_\-\.]+$', device_id):
            return jsonify({"status": "error", "message": "device_id 只能包含英數字、底線、連字號、點"}), 400

        web_url = str(data.get("web_url", "")).strip() or "https://mushroomh5.acenetgame.com/"

        config_manager.update_device_config(device_id, {
            "name": device_id,
            "backend": "web_h5",
            "web_url": web_url,
        })

        with _web_login_lock:
            state = _normalize_web_login_state(device_id)
            if state.get("running"):
                return jsonify({"status": "busy", "message": "web login is already running"}), 409

        login_payload = {
            "web_url": web_url,
            "prefer_existing_state": False,
            "backup_before_open": False,
        }
        t = threading.Thread(
            target=_run_web_login_worker,
            args=(device_id, login_payload),
            daemon=True,
            name=f"web-login-{device_id}",
        )
        t.start()
        return jsonify({"status": "ok", "device_id": device_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/web_login_status/<ip>", methods=["GET"])
def get_web_login_status(ip):
    try:
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        with _web_login_lock:
            state = _normalize_web_login_state(real_ip).copy()
        return jsonify(state)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/web_backup_state/<ip>", methods=["POST"])
def backup_web_state(ip):
    """Backup saved cookies/localStorage state file for a device."""
    try:
        payload = request.get_json(silent=True) or {}
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        cfg = config_manager.get_device_config(real_ip)

        requested_state_raw = str(payload.get("web_state_file") or "").strip()
        cfg_state_raw = (
            str(cfg.get("web_state_file") or "auth_state/{device_id}.json").strip()
            or "auth_state/{device_id}.json"
        )
        default_state_raw = "auth_state/{device_id}.json"

        candidates = []
        if requested_state_raw:
            candidates.append(_resolve_web_state_file(real_ip, requested_state_raw))
        candidates.append(_resolve_web_state_file(real_ip, cfg_state_raw))
        candidates.append(_resolve_web_state_file(real_ip, default_state_raw))

        state_file = _existing_state_file(candidates)
        if not state_file:
            return jsonify(
                {
                    "status": "error",
                    "message": "找不到可備份的 cookies/state 檔案",
                    "candidates": candidates,
                }
            ), 404

        backup_file = _backup_web_state_file(real_ip, state_file)
        with _web_login_lock:
            state = _normalize_web_login_state(real_ip)
            state["last_backup_file"] = backup_file

        return jsonify(
            {
                "status": "ok",
                "ip": real_ip,
                "state_file": state_file,
                "backup_file": backup_file,
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/web_launch/<ip>", methods=["POST"])
def launch_web_page(ip):
    """Ask running device thread to open/restore web page without spawning a new Playwright context."""
    try:
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        payload = request.json if request.is_json else {}
        clear_once = bool((payload or {}).get("clear_cookies_once", False))
        manual_hold = bool((payload or {}).get("manual_hold_until_closed", True))
        # On a headless server there is no display, so a headful window is useless;
        # the live-view bridge streams the headless browser instead. Default comes
        # from the host's global config (set manual_launch_force_headful=false on the
        # VPS), but an explicit request payload may still override it.
        default_force_headful = bool(
            config_manager.get_global_config().get("manual_launch_force_headful", True)
        )
        force_headful = bool((payload or {}).get("force_headful", default_force_headful))
        req_payload = {
            "clear_cookies_once": clear_once,
            "manual_hold_until_closed": manual_hold,
            "force_headful": force_headful,
        }
        if clear_once:
            req_payload["message"] = "clear cookies once"
        bot_state.request_web_launch(real_ip, payload=req_payload)
        return jsonify(
            {"status": "ok", "message": "web launch requested", "ip": real_ip}
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if sock is not None:

    @sock.route("/ws/live_view/<ip>")
    def live_view_ws(ws, ip):
        """Stream a web_h5 device's headless Chrome to the client and forward input.

        Attaches to the device's CDP remote-debugging-port (config web_debug_port)
        as an independent raw-CDP client; does not touch the bot's Playwright object.
        """
        from urllib.parse import urlsplit

        from runtime_services.live_view_bridge import (
            LiveViewSession,
            find_game_page_target,
        )

        real_ip = ip.split(":")[-1] if ":" in ip else ip
        cfg = config_manager.get_device_config(real_ip)
        debug_port = cfg.get("web_debug_port")
        if not debug_port:
            try:
                ws.send(json.dumps({"type": "error", "message": "no web_debug_port configured"}))
            except Exception:
                pass
            return
        url_host = ""
        try:
            url_host = urlsplit(str(cfg.get("web_url", ""))).hostname or ""
        except Exception:
            url_host = ""
        try:
            vw = int(cfg.get("web_viewport_width", 540) or 540)
            vh = int(cfg.get("web_viewport_height", 960) or 960)
        except Exception:
            vw, vh = 540, 960
        # Pause automation during manual takeover so the bot does not fight the
        # user's clicks. Resumes when the live-view WS disconnects (also covers the
        # browser tab being closed). set_pause does NOT touch the browser, so unlike
        # the old web_launch path it cannot trigger a game reload / re-login.
        paused_by_live_view = False
        try:
            bot_state.set_pause(real_ip, True)
            paused_by_live_view = True
        except Exception as pause_exc:
            logger.warning(f"[live_view] pause {real_ip} failed: {pause_exc}")

        # Auto-launch fallback: if no browser is currently running on the debug port,
        # request a headless launch so the user doesn't have to press 開啟網頁 first.
        # Probe quickly — when a page target already exists we must NOT request a launch
        # (app_start would re-navigate the loaded game and force a re-login). The probe
        # preserves the no-relogin path; only the "nothing running" case auto-launches.
        # force_headful=False keeps it headless (fine on a VPS); manual_hold=False lets
        # the device thread open the browser and return immediately — live-view's
        # set_pause already holds automation during the manual takeover. The pending
        # launch request breaks through the pause via bot_state.check_pause's carve-out.
        try:
            existing_target = find_game_page_target(
                int(debug_port), url_host, timeout_sec=1.5, poll_interval=0.3
            )
            if not existing_target:
                logger.info(
                    f"[live_view] {real_ip} no live page on port {debug_port}; "
                    "auto-launching headless browser"
                )
                bot_state.request_web_launch(
                    real_ip,
                    payload={"force_headful": False, "manual_hold_until_closed": False},
                )
        except Exception as launch_exc:
            logger.warning(f"[live_view] {real_ip} auto-launch probe failed: {launch_exc}")

        # Idle auto-disconnect window (default 60 min). Keeps a forgotten manual
        # takeover from pausing the device forever; configurable via global config
        # global.live_view.idle_timeout_sec (seconds; <= 0 disables).
        idle_timeout = 3600
        try:
            lv_cfg = config_manager.get_global_config().get("live_view", {}) or {}
            idle_timeout = int(lv_cfg.get("idle_timeout_sec", 3600))
        except Exception:
            idle_timeout = 3600

        try:
            session = LiveViewSession(
                ws,
                int(debug_port),
                url_host,
                viewport_width=vw,
                viewport_height=vh,
                idle_timeout_sec=idle_timeout,
                logger=logger,
            )
            with _live_view_lock:
                _live_view_sessions[real_ip] = session
            try:
                session.run()
            finally:
                with _live_view_lock:
                    _live_view_sessions.pop(real_ip, None)
        finally:
            if paused_by_live_view:
                try:
                    bot_state.set_pause(real_ip, False)
                except Exception as resume_exc:
                    logger.warning(f"[live_view] resume {real_ip} failed: {resume_exc}")


@app.route("/api/live_view/<ip>/stop", methods=["POST"])
def stop_live_view(ip):
    """Explicitly stop a live-view session and unpause the device.

    Called by the frontend's closeLiveView() as a belt-and-suspenders fallback
    in case the WebSocket close frame is not cleanly received server-side (e.g.
    proxy timeout, half-open TCP, Werkzeug threading edge cases). The WS session
    will also call set_pause(False) in its own finally block once it exits; the
    calls are idempotent.
    """
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    with _live_view_lock:
        session = _live_view_sessions.get(real_ip)
    if session is not None:
        try:
            session.stop()
        except Exception as exc:
            logger.warning(f"[live_view] stop API: session.stop() failed for {real_ip}: {exc}")
    try:
        bot_state.set_pause(real_ip, False)
    except Exception as exc:
        logger.warning(f"[live_view] stop API: set_pause(False) failed for {real_ip}: {exc}")
    return jsonify({"ok": True})


@app.route("/api/web_close/<ip>", methods=["POST"])
def web_close(ip):
    """Request the (web_h5) device thread to close its headless browser now.

    Unlike force-sleep, the device keeps running and cold-restarts the browser on
    its next loop/wake (a browser "restart"). The Flask thread only sets a flag;
    the owning device thread performs the Playwright close on its own thread (the
    Playwright objects are thread-affine). The caller (live-view 關閉瀏覽器 button)
    closes the live view first so automation is no longer paused and the device
    thread reaches the top of its loop to consume the flag.
    """
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    try:
        bot_state.request_web_close(real_ip)
    except Exception as exc:
        logger.warning(f"[web_close] request_web_close failed for {real_ip}: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "action": "web_close", "ip": real_ip})


@app.route("/api/labeler/config", methods=["GET"])
def get_labeler_config():
    try:
        return jsonify(_get_labeler_ui_config())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/labeler/config", methods=["POST"])
def set_labeler_config():
    try:
        payload = request.json or {}
        safe_payload = {}
        for key in DEFAULT_LABELER_UI_CONFIG.keys():
            if key in payload:
                safe_payload[key] = str(payload[key]).strip()
        config_manager.update_ocr_config(safe_payload)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/labeler/run_once", methods=["POST"])
def run_labeler_once():
    with _labeler_lock:
        if _labeler_state["running"]:
            return jsonify(
                {"status": "busy", "message": "labeler is already running"}
            ), 409
    try:
        cfg = _get_labeler_ui_config()
        t = threading.Thread(
            target=_run_labeler_once_worker,
            args=(cfg,),
            daemon=True,
            name="labeler-once-worker",
        )
        t.start()
        return jsonify({"status": "ok", "message": "labeler started"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/labeler/status", methods=["GET"])
def get_labeler_status():
    with _labeler_lock:
        return jsonify(_labeler_state.copy())


@app.route("/api/labeler/pause", methods=["POST"])
def pause_labeler():
    try:
        os.makedirs(_labeler_control_dir, exist_ok=True)
        (Path(_labeler_control_dir) / "pause.flag").write_text("1", encoding="utf-8")
        with _labeler_lock:
            _labeler_state["paused"] = True
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/labeler/resume", methods=["POST"])
def resume_labeler():
    try:
        pause_flag = Path(_labeler_control_dir) / "pause.flag"
        if pause_flag.exists():
            pause_flag.unlink()
        with _labeler_lock:
            _labeler_state["paused"] = False
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/labeler/terminate", methods=["POST"])
def terminate_labeler():
    try:
        os.makedirs(_labeler_control_dir, exist_ok=True)
        (Path(_labeler_control_dir) / "stop.flag").write_text("1", encoding="utf-8")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/trainer/run_once", methods=["POST"])
def run_trainer_once():
    with _trainer_lock:
        if _trainer_state["running"]:
            return jsonify(
                {"status": "busy", "message": "trainer is already running"}
            ), 409
    try:
        cfg = _get_labeler_ui_config()
        t = threading.Thread(
            target=_run_trainer_worker,
            args=(cfg,),
            daemon=True,
            name="trainer-once-worker",
        )
        t.start()
        return jsonify({"status": "ok", "message": "trainer started"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/trainer/status", methods=["GET"])
def get_trainer_status():
    with _trainer_lock:
        return jsonify(_trainer_state.copy())


@app.route("/api/labeler/check_connection", methods=["POST"])
def check_labeler_connection():
    try:
        payload = request.json or {}
        endpoint = str(payload.get("endpoint", "")).strip()
        model = str(payload.get("model", "local-model")).strip()
        ok, detail = _check_llama_endpoint(
            endpoint=endpoint, model=model, timeout_sec=8.0
        )
        return jsonify(
            {
                "status": "ok" if ok else "error",
                "connected": ok,
                "detail": detail,
                "endpoint": endpoint,
                "model": model,
            }
        ), (200 if ok else 400)
    except Exception as e:
        return jsonify({"status": "error", "connected": False, "detail": str(e)}), 500


@app.route("/api/pause/<ip>", methods=["POST"])
def pause_bot(ip):
    queue_command(ip, "paused", True)
    return jsonify({"status": "ok", "action": "paused", "ip": ip})


@app.route("/api/resume/<ip>", methods=["POST"])
def resume_bot(ip):
    queue_command(ip, "paused", False)
    return jsonify({"status": "ok", "action": "resumed", "ip": ip})


@app.route("/api/skip_sleep/<ip>", methods=["POST"])
def skip_sleep(ip):
    queue_command(ip, "skip_sleep", True)
    return jsonify({"status": "ok", "action": "skip_sleep", "ip": ip})


@app.route("/api/manual_release/<ip>", methods=["POST"])
def manual_release(ip):
    queue_command(ip, "manual_release", True)
    return jsonify({"status": "ok", "action": "manual_release", "ip": ip})


@app.route("/api/force_sleep/<ip>", methods=["POST"])
def force_sleep(ip):
    queue_command(ip, "force_sleep", True)
    return jsonify({"status": "ok", "action": "force_sleep", "ip": ip})


@app.route("/api/recover/<ip>", methods=["POST"])
def recover_screen(ip):
    # 遠端設備（本機 TCP 模擬器同樣帶冒號，必須走 _is_local_command_target 判斷）
    if not _is_local_command_target(ip):
        queue_command(ip, "recover", True)
        return jsonify({"status": "ok", "action": "queued_recover", "ip": ip})

    # 本地設備
    try:
        if config_manager.get_flag(ip, "is_real_phone") or "fc65396d" in ip:
            run_adb("shell wm density reset && wm size reset", device_serial=ip)
            return jsonify({"status": "ok", "action": "screen_recovered", "ip": ip})
        return jsonify(
            {"status": "error", "message": "此設備未設定為實體機/特殊機型"}
        ), 403
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _cdp_evaluate(ip, expression, await_promise=False):
    """Execute JS on a web_h5 device via CDP. Returns (result_dict, error_str)."""
    import websocket as _ws
    from runtime_services.live_view_bridge import find_game_page_target

    cfg = config_manager.get_device_config(ip)
    debug_port = cfg.get("web_debug_port")
    if not debug_port:
        return None, "no web_debug_port"

    ws_url = find_game_page_target(
        debug_port, "mushroomh5.acenetgame.com", timeout_sec=5.0
    )
    if not ws_url:
        return None, f"no CDP target on port {debug_port}"

    try:
        ws = _ws.create_connection(ws_url, timeout=15, suppress_origin=True)
        payload = json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        })
        ws.send(payload)
        deadline = time.time() + 15
        while time.time() < deadline:
            raw = ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == 1:
                ws.close()
                return msg.get("result", {}), None
        ws.close()
        return None, "timeout"
    except Exception as exc:
        return None, str(exc)


def _cdp_json_response(ip, expression, await_promise=False, data_key="data"):
    """Helper: evaluate JS, parse JSON string result, return Flask response."""
    result, err = _cdp_evaluate(ip, expression, await_promise=await_promise)
    if err:
        code = 400 if err == "no web_debug_port" else 502 if "no CDP target" in err else 500
        return jsonify({"status": "error", "message": err}), code
    inner = result.get("result", {})
    exc_detail = result.get("exceptionDetails")
    if exc_detail:
        return jsonify({"status": "error", "message": str(exc_detail)}), 500
    if inner.get("type") == "string":
        try:
            parsed = json.loads(inner["value"])
            if isinstance(parsed, dict) and "error" in parsed:
                return jsonify({"status": "error", "message": parsed["error"]}), 500
            return jsonify({"status": "ok", data_key: parsed})
        except Exception:
            return jsonify({"status": "ok", "raw": inner["value"]})
    return jsonify({"status": "ok", data_key: inner.get("value", inner)})


# Shared JS helper for extracting a pet's lock + star flags. Canonical rule:
# scan pet.ext for entry k===2 (lock) / k===1 (star); fall back to pet.lock.
# Single source of truth so fly_pet_list and fly_pet_find_pair stay consistent.
# Returns an object: {lock, star}. Callers read .lock (and .star) as needed.
# Pure literal (no { } interpolation) so it injects safely into both raw-strings
# and f-strings (in f-strings interpolate via a placeholder, not doubled braces).
_FLY_PET_LOCK_JS = (
    "(function(pet){"
    "var lock=0; var star=0; var ext=pet.ext||[];"
    "if(Array.isArray(ext)){"
    "for(var i=0;i<ext.length;i++){var x=ext[i];"
    "if(x && x.k===2) lock=x.v; if(x && x.k===1) star=x.v;}"
    "} else {"
    "for(var ek in ext){var x=ext[ek];"
    "if(x && x.k===2) lock=x.v; if(x && x.k===1) star=x.v;}"
    "}"
    "if(!lock && pet.lock!==undefined) lock=pet.lock;"
    "return {lock:lock, star:star};"
    "})"
)


@app.route("/api/cdp_evaluate/<ip>", methods=["GET", "POST"])
def cdp_evaluate(ip):
    """Execute JS on a web_h5 device via CDP Runtime.evaluate.

    POST: {"expression": "..."}
    GET:  ?expr=...  (URL-encoded JS expression)
    """
    if request.method == "POST":
        expression = (request.json or {}).get("expression", "")
    else:
        expression = request.args.get("expr", "")
    if not expression:
        return jsonify({"status": "error", "message": "no expression"}), 400

    await_promise = False
    if request.method == "POST":
        await_promise = bool((request.json or {}).get("awaitPromise", False))

    return _cdp_json_response(ip, expression, await_promise=await_promise)


@app.route("/api/fly_pet_list/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_list(ip):
    """Dump all fly pets + entries for a device (convenience wrapper)."""
    js = r"""(() => {
        try {
            const __extractLockStar = """ + _FLY_PET_LOCK_JS + r""";
            const cache = IS(ISInclude.FlyPetDataCache);
            const pets = cache.pet_list;
            const result = [];
            for (const key in pets) {
                const pet = pets[key];
                if (!pet) continue;
                const cfg = configFly.getDataByKey(pet.config_id);
                const entries = [];
                for (const e of (pet.entry_list || [])) {
                    try {
                        const ec = configFly_entry.getDataByKeys("id", e.k, "level", e.v);
                        entries.push({
                            id: e.k, level: e.v,
                            name: ec ? ec.name : "?",
                            quality: ec ? ec.quality : 0,
                            desc: ec ? String(ec.desc || "") : "",
                            desc_parm: ec ? ec.desc_parm : [],
                            belong_talent: ec ? ec.belong_talent : 0,
                            special_effect: ec ? (ec.special_effect || 0) : 0
                        });
                    } catch(ex) {
                        entries.push({id: e.k, level: e.v, name: "parse_err", quality: 0});
                    }
                }
                const __ls = __extractLockStar(pet);
                const lock = __ls.lock;
                const star = __ls.star;
                result.push({
                    id: pet.id,
                    config_id: pet.config_id,
                    name: pet.name || "",
                    display_name: cfg ? cfg.name : "?",
                    quality: pet.quality,
                    level: pet.level,
                    fight: pet.fight || 0,
                    generation: pet.generation || 0,
                    growth: pet.growth || 0,
                    step: pet.step || 0,
                    lock: lock,
                    star: star,
                    entries: entries
                });
            }
            result.sort((a,b) => b.quality - a.quality || b.level - a.level);
            return JSON.stringify(result);
        } catch(err) {
            return JSON.stringify({error: err.message, stack: err.stack});
        }
    })()"""
    return _cdp_json_response(ip, js, data_key="pets")


# Real in-game fly-pet icons dumped from the live game by tools/dump_flypet_icons.py
# (one PNG per species config_id). Served to the dashboard card wall.
_FLY_PET_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "flypet_icons")


@app.route("/api/fly_pet_icon/<int:config_id>", methods=["GET"])
@_fly_pet_auth
def fly_pet_icon(config_id):
    """Serve the cached real in-game icon for a fly-pet species.

    Returns the dumped ``static/flypet_icons/<config_id>.png``. A missing icon is a
    404 (not an error) so the dashboard falls back to its placeholder avatar. The
    ``<int:config_id>`` route converter blocks path traversal / arbitrary filenames.
    """
    filename = f"{config_id}.png"
    if not os.path.isfile(os.path.join(_FLY_PET_ICON_DIR, filename)):
        return ("", 404)
    resp = send_from_directory(_FLY_PET_ICON_DIR, filename, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/fly_pet_check_connection/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_check_connection(ip):
    """Check if the game WebSocket is connected."""
    js = r"""(() => {
        try {
            const cnet = netManager._cnet;
            const sock = cnet._socket;
            const connected = sock && sock.readyState === 1;
            return JSON.stringify({connected});
        } catch(e) {
            return JSON.stringify({connected: false, error: e.message});
        }
    })()"""
    return _cdp_json_response(ip, js)


@app.route("/api/fly_pet_browser_status/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_browser_status(ip):
    """Lightweight 'is a browser already launched?' check.

    Hits the device's CDP ``/json/list`` (a local HTTP call via
    ``find_game_page_target``) instead of evaluating JS into the game, so the
    UI can tell 'no browser launched' apart from 'browser up but game still
    loading' without the heavy game-WebSocket probe (``fly_pet_check_connection``).
    """
    from runtime_services.live_view_bridge import find_game_page_target

    cfg = config_manager.get_device_config(ip)
    debug_port = cfg.get("web_debug_port")
    if not debug_port:
        return jsonify({
            "status": "ok",
            "data": {"browser_up": False, "debug_port": None, "no_debug_port": True},
        })
    try:
        ws_url = find_game_page_target(
            int(debug_port), "mushroomh5.acenetgame.com",
            timeout_sec=1.0, poll_interval=0.3,
        )
    except Exception as exc:
        return jsonify({
            "status": "ok",
            "data": {"browser_up": False, "debug_port": int(debug_port), "error": str(exc)},
        })
    return jsonify({
        "status": "ok",
        "data": {"browser_up": bool(ws_url), "debug_port": int(debug_port)},
    })


@app.route("/api/fly_pet_bot_status/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_bot_status(ip):
    """Check if bot script is actively running on this device."""
    states = bot_state.get_all_states()
    st = states.get(ip, {})
    status = st.get("status", "OFFLINE")
    task = st.get("task", "")
    step = st.get("step", "")
    running = status not in ("OFFLINE", "") and task not in ("待命", "初始化", "休眠中", "強制休眠", "手動操作", "")
    return jsonify({
        "status": "ok",
        "bot_running": running,
        "bot_status": status,
        "bot_task": task,
        "bot_step": step,
    })


@app.route("/api/fly_pet_resolve/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_resolve(ip):
    """Sell/decompose selected pets. Waits for server ack via OnInfoUpdate event."""
    data = request.json or {}
    ids = data.get("ids", [])
    if not ids or not isinstance(ids, list):
        return jsonify({"status": "error", "message": "ids must be a non-empty list"}), 400
    ids_js = json.dumps([int(i) for i in ids])
    js = f"""new Promise((resolve) => {{
    let done = false;
    const timeout = setTimeout(() => {{
        if (done) return;
        done = true;
        normalEvent.off('OnInfoUpdate', handler);
        const count = Object.keys(IS(ISInclude.FlyPetDataCache).pet_list).length;
        resolve(JSON.stringify({{ok: false, message: 'timeout', petCount: count}}));
    }}, 8000);
    function handler() {{
        if (done) return;
        done = true;
        clearTimeout(timeout);
        normalEvent.off('OnInfoUpdate', handler);
        const count = Object.keys(IS(ISInclude.FlyPetDataCache).pet_list).length;
        resolve(JSON.stringify({{ok: true, petCount: count}}));
    }}
    normalEvent.on('OnInfoUpdate', handler);
    IS(ISInclude.FlyPetControl).send_66_8({ids_js});
}})"""
    return _cdp_json_response(ip, js, await_promise=True)


@app.route("/api/fly_pet_breed_info/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_breed_info(ip):
    """Get breeding pool status and shelf info."""
    js = r"""(() => {
        const cache = IS(ISInclude.FlyPetDataCache);
        const isRealPet = (p) => p && Number(p.id || 0) > 0;
        const homes = [];
        for (const k in cache.home_list) {
            const h = cache.home_list[k];
            if (!h) continue;
            const raw = {};
            for (const fk in h) {
                const v = h[fk];
                if (v === null || v === undefined) { raw[fk] = v; continue; }
                if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') raw[fk] = v;
            }
            const petInfo = (p) => {
                if (!isRealPet(p)) return null;
                const cfg = configFly.getDataByKey(p.config_id);
                return {id: p.id, config_id: p.config_id, name: p.name || '', display_name: cfg ? cfg.name : '?', quality: p.quality || 0, role_id: p.role_id || 0};
            };
            homes.push({
                id: h.id, name: h.name || '', state: h.state,
                end_time: h.end_time || 0,
                fly_a: petInfo(h.fly_a),
                fly_b: petInfo(h.fly_b),
                fly_pet: petInfo(h.fly_pet),
                _raw: raw
            });
        }
        const shelved = [];
        for (const k in cache.use_pet_list) {
            const s = cache.use_pet_list[k];
            if (!s || !s.info) continue;
            const cfg = configFly.getDataByKey(s.info.config_id);
            shelved.push({id: s.info.id, config_id: s.info.config_id, display_name: cfg ? cfg.name : '?', quality: s.info.quality || 0, state: s.state || 0});
        }
        const egg_list = [];
        if (cache.egg_list) {
            for (const ek in cache.egg_list) {
                const eg = cache.egg_list[ek];
                if (!eg) continue;
                const raw = {};
                for (const fk in eg) {
                    const v = eg[fk];
                    if (v === null || v === undefined) { raw[fk] = v; continue; }
                    if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') raw[fk] = v;
                }
                egg_list.push(raw);
            }
        }
        return JSON.stringify({homes, shelved, egg_list});
    })()"""
    return _cdp_json_response(ip, js)


@app.route("/api/fly_pet_breed_methods/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_breed_methods(ip):
    """Discover FlyPetControl send_* methods for debugging."""
    js = r"""(() => {
        const ctrl = IS(ISInclude.FlyPetControl);
        const proto = Object.getPrototypeOf(ctrl);
        const methods = Object.getOwnPropertyNames(proto).filter(n => n.startsWith('send_66'));
        return JSON.stringify(methods.sort());
    })()"""
    return _cdp_json_response(ip, js)


@app.route("/api/fly_pet_shelve/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_shelve(ip):
    """Put a pet on or remove from the breeding shelf."""
    data = request.json or {}
    pet_id = data.get("pet_id")
    action = data.get("action")
    if pet_id is None or action not in ("place", "remove"):
        return jsonify({"status": "error", "message": "pet_id required, action must be 'place' or 'remove'"}), 400
    type_val = 0 if action == "place" else 1
    js = f"IS(ISInclude.FlyPetControl).send_66_23({int(pet_id)}, {type_val}, 0)"
    result, err = _cdp_evaluate(ip, js)
    if err:
        code = 400 if err == "no web_debug_port" else 502 if "no CDP target" in err else 500
        return jsonify({"status": "error", "message": err}), code
    return jsonify({"status": "ok"})


@app.route("/api/fly_pet_partner/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_partner(ip):
    """Get a 搭檔 (breeding partner)'s shelved pets, by role_id (async).

    The server replies via the ``RolePetListBack`` event whose payload carries
    the partner's shelved pets under ``data.list`` (array of {info, state, ...}).
    """
    role_id = request.args.get("role_id")
    if not role_id:
        return jsonify({"status": "error", "message": "role_id query param required"}), 400
    try:
        role_id_int = int(role_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "role_id must be an integer"}), 400
    js = f"""new Promise((resolve) => {{
    const tmr = setTimeout(() => {{
        normalEvent.off('RolePetListBack', handler);
        resolve(JSON.stringify([]));
    }}, 8000);
    function handler(data) {{
        normalEvent.off('RolePetListBack', handler);
        clearTimeout(tmr);
        const pets = [];
        for (const p of (data.list || [])) {{
            if (!p || !p.info) continue;
            const cfg = configFly.getDataByKey(p.info.config_id);
            const entries = [];
            for (const e of (p.info.entry_list || [])) {{
                try {{
                    const ec = configFly_entry.getDataByKeys("id", e.k, "level", e.v);
                    entries.push({{
                        id: e.k, level: e.v,
                        name: ec ? ec.name : "?",
                        quality: ec ? ec.quality : 0
                    }});
                }} catch(ex) {{
                    entries.push({{id: e.k, level: e.v, name: "?", quality: 0}});
                }}
            }}
            pets.push({{
                id: p.info.id, config_id: p.info.config_id,
                name: p.info.name || '', display_name: cfg ? cfg.name : '?',
                quality: p.info.quality, level: p.info.level,
                generation: p.info.generation || 0,
                growth: p.info.growth || 0,
                state: p.state || 0,
                end_time: p.end_time || 0,
                entries: entries
            }});
        }}
        resolve(JSON.stringify(pets));
    }}
    normalEvent.on('RolePetListBack', handler);
    IS(ISInclude.FlyPetControl).send_66_24({role_id_int});
}})"""
    return _cdp_json_response(ip, js, await_promise=True)


@app.route("/api/fly_pet_breed_start/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_breed_start(ip):
    """Start breeding."""
    data = request.json or {}
    base_id = data.get("base_id")
    fly_a_id = data.get("fly_a_id")
    fly_b_id = data.get("fly_b_id")
    if base_id is None or fly_a_id is None or fly_b_id is None:
        return jsonify({"status": "error", "message": "base_id, fly_a_id, fly_b_id required"}), 400
    base_id_int = int(base_id)
    # Bug 9: no confirming *Back event exists for 66_27, so send the command then
    # poll the breed cache until home_list[base_id].state becomes 1 (breeding),
    # bounded by a ~3000ms timeout. Resolve {ok, state, timed_out}.
    js = f"""new Promise((resolve) => {{
    IS(ISInclude.FlyPetControl).send_66_27({base_id_int}, {int(fly_a_id)}, {int(fly_b_id)});
    const baseId = {base_id_int};
    const deadline = Date.now() + 3000;
    function readState() {{
        try {{
            const home = IS(ISInclude.FlyPetDataCache).home_list[baseId];
            if (!home) return null;
            return home.state;
        }} catch(e) {{ return null; }}
    }}
    const initial = readState();
    if (initial === null) {{
        resolve(JSON.stringify({{ok: true, state: null, timed_out: false}}));
        return;
    }}
    const timer = setInterval(() => {{
        const st = readState();
        if (st === 1) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: false}}));
        }} else if (Date.now() >= deadline) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: true}}));
        }}
    }}, 150);
}})"""
    return _cdp_json_response(ip, js, await_promise=True)


@app.route("/api/fly_pet_breed_collect/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_breed_collect(ip):
    """Collect breeding result."""
    data = request.json or {}
    base_id = data.get("base_id")
    if base_id is None:
        return jsonify({"status": "error", "message": "base_id required"}), 400
    base_id_int = int(base_id)
    # Bug 9: no confirming *Back event exists for 66_28, so send the command then
    # poll the breed cache until home_list[base_id].state moves AWAY from 2
    # (collectable) -> 0 idle / 3 awaiting-hatch, bounded by a ~3000ms timeout.
    js = f"""new Promise((resolve) => {{
    IS(ISInclude.FlyPetControl).send_66_28({base_id_int});
    const baseId = {base_id_int};
    const deadline = Date.now() + 3000;
    function readState() {{
        try {{
            const home = IS(ISInclude.FlyPetDataCache).home_list[baseId];
            if (!home) return null;
            return home.state;
        }} catch(e) {{ return null; }}
    }}
    const initial = readState();
    if (initial === null) {{
        resolve(JSON.stringify({{ok: true, state: null, timed_out: false}}));
        return;
    }}
    const timer = setInterval(() => {{
        const st = readState();
        if (st !== 2) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: false}}));
        }} else if (Date.now() >= deadline) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: true}}));
        }}
    }}, 150);
}})"""
    return _cdp_json_response(ip, js, await_promise=True)


@app.route("/api/fly_pet_hatch/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_hatch(ip):
    """Hatch an egg in a breeding nest."""
    data = request.json or {}
    egg_id = data.get("egg_id")
    if egg_id is None:
        return jsonify({"status": "error", "message": "egg_id required"}), 400
    egg_ids_js = json.dumps([int(egg_id)])
    js = f"""new Promise((resolve) => {{
    let done = false;
    const tmr = setTimeout(() => {{
        if (done) return; done = true;
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({{ok: false, message: 'timeout'}}));
    }}, 8000);
    function handler() {{
        if (done) return; done = true;
        clearTimeout(tmr);
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({{ok: true}}));
    }}
    normalEvent.on('EggListBack', handler);
    IS(ISInclude.FlyPetControl).send_66_3({egg_ids_js});
}})"""
    return _cdp_json_response(ip, js, await_promise=True)


@app.route("/api/fly_pet_partners/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_partners(ip):
    """Get the 搭檔 (breeding-partner) list, capped at 30 by the game.

    Partners live in ``FlyPetDataCache.role_list`` (populated by InitHybridData
    from the server's ``partner_list``), NOT the friend list. Each entry exposes
    role_id + name so the frontend can fetch each partner's shelved pets via
    ``/api/fly_pet_partner`` (send_66_24 -> RolePetListBack). If the cache is not
    yet populated we request hybrid data (send_66_1/21/22) and read it back.
    """
    js = r"""new Promise((resolve) => {
        function collect() {
            const out = [];
            try {
                const rl = IS(ISInclude.FlyPetDataCache).role_list || [];
                for (const k in rl) {
                    const r = rl[k];
                    if (!r) continue;
                    const rid = Number(r.role_id) || 0;
                    if (!rid) continue;
                    out.push({
                        role_id: rid,
                        name: String(r.name || ""),
                        head: Number(r.head) || 0
                    });
                }
            } catch (ex) {}
            return out;
        }
        const cached = collect();
        if (cached.length > 0) { return resolve(JSON.stringify(cached)); }
        let done = false;
        function handler() {
            if (done) return; done = true;
            clearTimeout(tmr);
            normalEvent.off('EggListBack', handler);
            resolve(JSON.stringify(collect()));
        }
        normalEvent.on('EggListBack', handler);
        const c = IS(ISInclude.FlyPetControl);
        c.send_66_1(); c.send_66_21(); c.send_66_22();
        const tmr = setTimeout(() => {
            if (done) return; done = true;
            normalEvent.off('EggListBack', handler);
            resolve(JSON.stringify(collect()));
        }, 3000);
    })"""
    return _cdp_json_response(ip, js, await_promise=True, data_key="partners")


@app.route("/api/fly_pet_refresh_breed/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_refresh_breed(ip):
    """Trigger breed info refresh (egg list + hybrid base + shelves).

    Bug 5: block until the refresh data actually arrives (EggListBack) instead of
    fire-and-forget, so the frontend no longer needs a fixed delay. Bounded by a
    ~1500ms timeout; the listener is always detached on both paths.
    """
    js = """new Promise((resolve) => {
    let done = false;
    function handler() {
        if (done) return; done = true;
        clearTimeout(tmr);
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({ok: true, timed_out: false}));
    }
    const tmr = setTimeout(() => {
        if (done) return; done = true;
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({ok: true, timed_out: true}));
    }, 1500);
    normalEvent.on('EggListBack', handler);
    const c = IS(ISInclude.FlyPetControl);
    c.send_66_1(); c.send_66_21(); c.send_66_22();
})"""
    return _cdp_json_response(ip, js, await_promise=True)


@app.route("/api/fly_pet_find_pair/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_find_pair(ip):
    """Find best breeding pair matching criteria from available pets."""
    data = request.json or {}
    criteria = data.get("criteria", {})
    exclude_ids = data.get("exclude_ids", [])

    mode = criteria.get("mode", "quality_count")
    quality = int(criteria.get("quality", 3))
    min_count = int(criteria.get("min_count", 2))
    min_total = int(criteria.get("min_total_entries", 3))
    prefer_low_gen = bool(criteria.get("prefer_low_gen", True))
    species_whitelist = [int(x) for x in (criteria.get("species_whitelist") or [])]
    entry_whitelist = [int(x) for x in (criteria.get("entry_whitelist") or [])]
    exclude_js = json.dumps([int(i) for i in exclude_ids])
    species_js = json.dumps(species_whitelist)
    entry_js = json.dumps(entry_whitelist)

    js = f"""(() => {{
    try {{
        const __extractLockStar = {_FLY_PET_LOCK_JS};
        const cache = IS(ISInclude.FlyPetDataCache);
        const excludeIds = new Set({exclude_js}.map(String));
        const mode = '{mode}';
        const quality = {quality};
        const minCount = {min_count};
        const minTotal = {min_total};
        const preferLowGen = {str(prefer_low_gen).lower()};
        const speciesWhitelist = new Set({species_js}.map(Number));
        const entryWhitelist = {entry_js}.map(Number);

        const cooldownIds = new Set();
        for (const k in cache.use_pet_list) {{
            const s = cache.use_pet_list[k];
            if (s && s.state > 0 && s.info) cooldownIds.add(String(s.info.id));
        }}

        const breedingIds = new Set();
        for (const k in cache.home_list) {{
            const h = cache.home_list[k];
            if (!h) continue;
            if (h.fly_a) breedingIds.add(String(h.fly_a.id));
            if (h.fly_b) breedingIds.add(String(h.fly_b.id));
        }}

        const candidates = [];
        for (const key in cache.pet_list) {{
            const pet = cache.pet_list[key];
            if (!pet) continue;
            const pid = String(pet.id);
            if (excludeIds.has(pid) || cooldownIds.has(pid) || breedingIds.has(pid)) continue;

            const locked = __extractLockStar(pet).lock;
            if (locked || pet.lock) continue;
            if (pet.fight === 1) continue;

            const entries = [];
            for (const e of (pet.entry_list || [])) {{
                try {{
                    const ec = configFly_entry.getDataByKeys("id", e.k, "level", e.v);
                    entries.push({{id: e.k, level: e.v, name: ec ? ec.name : '?', quality: ec ? ec.quality : 0}});
                }} catch(ex) {{
                    entries.push({{id: e.k, level: e.v, name: '?', quality: 0}});
                }}
            }}

            let matches = false;
            let matchingCount = 0;
            if (mode === 'quality_count') {{
                matchingCount = entries.filter(e => e.quality === quality).length;
                matches = matchingCount >= minCount;
            }} else {{
                matches = entries.length >= minTotal;
                matchingCount = entries.length;
            }}
            if (!matches) continue;

            // 方案: 限制種類 (白名單). 空 = 不限.
            if (speciesWhitelist.size > 0 && !speciesWhitelist.has(Number(pet.config_id))) continue;
            // 方案: 限制詞條 (白名單, AND). 候選須同時包含全部指定 entry id.
            if (entryWhitelist.length > 0) {{
                const entryIds = new Set(entries.map(e => Number(e.id)));
                if (!entryWhitelist.every(id => entryIds.has(id))) continue;
            }}

            const cfg = configFly.getDataByKey(pet.config_id);
            candidates.push({{
                id: pet.id, config_id: pet.config_id,
                name: pet.name || '',
                display_name: cfg ? cfg.name : '?',
                generation: pet.generation || 0,
                quality: pet.quality || 0,
                entry_count: entries.length,
                matching_count: matchingCount,
                entries: entries.map(e => ({{name: e.name, quality: e.quality}}))
            }});
        }}

        if (preferLowGen) {{
            candidates.sort((a, b) => a.generation - b.generation || b.matching_count - a.matching_count || b.entry_count - a.entry_count);
        }} else {{
            candidates.sort((a, b) => b.matching_count - a.matching_count || b.entry_count - a.entry_count || a.generation - b.generation);
        }}

        const pair = candidates.length >= 2
            ? {{fly_a: candidates[0], fly_b: candidates[1]}}
            : null;

        return JSON.stringify({{
            pair: pair,
            candidates_count: candidates.length,
            skipped_cooldown: cooldownIds.size,
            skipped_breeding: breedingIds.size
        }});
    }} catch(err) {{
        return JSON.stringify({{error: err.message, stack: err.stack}});
    }}
}})()"""
    return _cdp_json_response(ip, js)


@app.route("/api/fly_pet_catalog/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_catalog(ip):
    """Full flypet catalog: every species (configFly) + every entry (configFly_entry).

    Used to populate the breeding-preset editor's 種類/詞條 whitelists. Enumerates
    the config tables' `.datas` array (not getDataByKey, which is single-row).
    Entries are deduped by id (one row per 詞條, collapsing levels).
    """
    js = """(() => {
        try {
            const sp = (typeof configFly !== 'undefined' && configFly && configFly.datas)
                ? configFly.datas.map(d => ({id: d.id, name: d.name})) : [];
            const arr = (typeof configFly_entry !== 'undefined' && configFly_entry && configFly_entry.datas)
                ? configFly_entry.datas : [];
            const seen = {};
            const entries = [];
            for (const e of arr) {
                if (!e || seen[e.id]) continue;
                seen[e.id] = 1;
                entries.push({id: e.id, name: e.name, quality: e.quality});
            }
            return JSON.stringify({species: sp, entries: entries});
        } catch(err) {
            return JSON.stringify({species: [], entries: [], error: err.message});
        }
    })()"""
    return _cdp_json_response(ip, js, data_key="catalog")


def run_server(port=5002):

    print(f"🚀 中控台網頁伺服器啟動: http://127.0.0.1:{port}")

    # 在 Thread 中運行時必須關閉 debug 和 reloader，否則會觸發訊號錯誤
    # threaded=True：let WebSocket (live-view) connections coexist with the
    # status-polling endpoints instead of blocking the single WSGI worker.

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)

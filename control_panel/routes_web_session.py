"""Playwright web 登入 / 啟動 / 關閉相關路由（從 control_panel_app.py 拆出，純 code-motion）。

模組級狀態 `_web_login_lock` / `_web_login_state` 與 helpers 逐字搬自 control_panel_app.py。

晚綁定規則：`start_web_login` 起背景 thread 時，target 透過 façade
（`import control_panel_app as _cpa` → `_cpa._run_web_login_worker`）取得，
讓 tests/test_register_device_disabled.py 的 monkeypatch.setattr(cpa, "_run_web_login_worker", ...)
仍能生效。其餘 helper 直接模組內引用。
"""
from flask import Blueprint, jsonify, request
import json
import logging
import os
import shutil
import threading
import time

import bot_state
import config_manager
from utils.web_profile_paths import resolve_profile_dir, resolve_state_file

logger = logging.getLogger(__name__)

bp = Blueprint("web_session", __name__)

_web_login_lock = threading.Lock()
_web_login_state = {}


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


# Thin wrappers over the shared resolver (utils/web_profile_paths) so the
# dashboard and device_wrapper._start always agree on the same normpathed dir.
def _resolve_web_profile_dir(ip: str, profile_dir_raw: str) -> str:
    return resolve_profile_dir(ip, profile_dir_raw)


def _resolve_web_state_file(ip: str, state_file_raw: str) -> str:
    return resolve_state_file(ip, state_file_raw)


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
    import control_panel_app as _cpa
    app = _cpa.app
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


def _start_web_login_thread(ip: str, payload: dict) -> bool:
    """原子保留並啟動 standalone Playwright worker；已在執行時回 False。"""
    with _web_login_lock:
        state = _normalize_web_login_state(ip)
        if state.get("running"):
            return False
        # 必須在 thread.start() 前占位，否則兩個按鈕快速連按會同時通過檢查，
        # 建立兩個 worker 競爭同一個 persistent profile。
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
        # 晚綁定：target 透過 facade 取，保留既有 tests monkeypatch 行為。
        import control_panel_app as _cpa

        t = threading.Thread(
            target=_cpa._run_web_login_worker,
            args=(ip, payload),
            daemon=True,
            name=f"web-login-{ip}",
        )
        t.start()
    except Exception as exc:
        with _web_login_lock:
            state = _normalize_web_login_state(ip)
            state["running"] = False
            state["finished_at"] = time.time()
            state["last_error"] = str(exc)
            state["last_message"] = "failed to start"
        raise
    return True


@bp.route("/api/web_login/<ip>", methods=["POST"])
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

        if not _start_web_login_thread(real_ip, payload):
            return jsonify(
                {"status": "busy", "message": "web login is already running"}
            ), 409
        return jsonify({"status": "ok", "message": "web login started", "ip": real_ip})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/web_login_status/<ip>", methods=["GET"])
def get_web_login_status(ip):
    try:
        real_ip = ip.split(":")[-1] if ":" in ip else ip
        with _web_login_lock:
            state = _normalize_web_login_state(real_ip).copy()
        return jsonify(state)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/web_backup_state/<ip>", methods=["POST"])
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


@bp.route("/api/web_launch/<ip>", methods=["POST"])
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

        cfg = config_manager.get_device_config(real_ip)
        standalone_required = (
            str(cfg.get("backend", "adb")).strip().lower() == "web_h5"
            and bool(cfg.get("special_wanshen_account", False))
            and bool(cfg.get("special_wanshen_enabled", False))
            and not bot_state.has_web_launch_consumer(real_ip)
        )
        if standalone_required:
            # 舊版按鈕可能已留下永遠 pending 的信箱請求；先結案，避免下週 thread
            # 重建後意外消費舊請求。standalone worker 自己擁有 Playwright thread。
            bot_state.complete_web_launch_request(
                real_ip, ok=False, message="superseded by standalone web login worker"
            )
            login_payload = {
                "persist_settings": False,
                "web_headless": not force_headful,
                "web_clear_cookies_on_start": clear_once,
            }
            if not _start_web_login_thread(real_ip, login_payload):
                return jsonify(
                    {
                        "status": "busy",
                        "message": "web login is already running",
                        "mode": "standalone",
                    }
                ), 409
            return jsonify(
                {
                    "status": "ok",
                    "message": "standalone web login started",
                    "ip": real_ip,
                    "mode": "standalone",
                }
            )

        bot_state.request_web_launch(real_ip, payload=req_payload)
        return jsonify(
            {"status": "ok", "message": "web launch requested", "ip": real_ip}
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/web_close/<ip>", methods=["POST"])
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

"""OCR labeler / trainer 路由群（從 control_panel_app.py 純 code-motion 搬出）。

包含 labeler/trainer 的模組級狀態、worker helpers，以及 ``/api/labeler/*`` 與
``/api/trainer/*`` 路由。對外仍由根目錄 ``control_panel_app.py`` façade 註冊本
blueprint 並 re-export 所需符號。
"""

import contextlib
import importlib.util
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import requests
from flask import Blueprint, jsonify, request

import config_manager
from control_panel.shared.auth import require_admin

bp = Blueprint("labeler", __name__)


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


@bp.route("/api/labeler/config", methods=["GET"])
def get_labeler_config():
    try:
        return jsonify(_get_labeler_ui_config())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/labeler/config", methods=["POST"])
@require_admin
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


@bp.route("/api/labeler/run_once", methods=["POST"])
@require_admin
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


@bp.route("/api/labeler/status", methods=["GET"])
def get_labeler_status():
    with _labeler_lock:
        return jsonify(_labeler_state.copy())


@bp.route("/api/labeler/pause", methods=["POST"])
@require_admin
def pause_labeler():
    try:
        os.makedirs(_labeler_control_dir, exist_ok=True)
        (Path(_labeler_control_dir) / "pause.flag").write_text("1", encoding="utf-8")
        with _labeler_lock:
            _labeler_state["paused"] = True
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/labeler/resume", methods=["POST"])
@require_admin
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


@bp.route("/api/labeler/terminate", methods=["POST"])
@require_admin
def terminate_labeler():
    try:
        os.makedirs(_labeler_control_dir, exist_ok=True)
        (Path(_labeler_control_dir) / "stop.flag").write_text("1", encoding="utf-8")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/trainer/run_once", methods=["POST"])
@require_admin
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


@bp.route("/api/trainer/status", methods=["GET"])
def get_trainer_status():
    with _trainer_lock:
        return jsonify(_trainer_state.copy())


@bp.route("/api/labeler/check_connection", methods=["POST"])
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

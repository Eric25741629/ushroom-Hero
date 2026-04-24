"""API service startup and main scan-loop helpers.

Extracted from new_main_v2.__main__ as Phase 10 of the slim-down plan.

  start_all(mode, base_dir):
    Start all runtime services for the given mode.

  scan_loop(main_fn, running_threads, Cnn_model, oralce_cnn_model,
            oralce_classes, ocr, log):
    Run the device scan loop until KeyboardInterrupt.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import bot_state
from runtime_services.device_scan_service import scan_and_start_devices
from runtime_services.push_server_service import ensure_push_server_started
from runtime_services.web_session_service import shutdown_web_devices
from runtime_services.worker_sync_service import ensure_worker_sync_started
from utils.logging_utils import logger


def start_all(mode: str, base_dir: str) -> None:
    """Start all runtime services for master or worker mode."""
    ensure_push_server_started(base_dir=base_dir)
    if mode == "master":
        import control_panel_app
        server_thread = threading.Thread(
            target=control_panel_app.run_server, args=(5002,), daemon=True
        )
        server_thread.start()
    else:
        logger.info("[Info] Worker 模式：不啟動本地網頁伺服器，將回報至 Master。")
        from worker_webhook_api import ensure_worker_webhook_started
        ensure_worker_webhook_started()
        ensure_worker_sync_started()


def scan_loop(
    main_fn: Any,
    running_threads: dict,
    Cnn_model: Any,
    oralce_cnn_model: Any,
    oralce_classes: Any,
    ocr: Any,
    log: Any,
) -> None:
    """Run the ADB device scan loop; exits cleanly on KeyboardInterrupt."""
    try:
        while True:
            scan_and_start_devices(
                main_fn,
                running_threads,
                Cnn_model,
                oralce_cnn_model,
                oralce_classes,
                ocr,
                log,
            )
            for _ in range(300):  # 0.1s × 300 = 30 s
                if bot_state.check_refresh_needed():
                    log.info("[System] 收到立即掃描請求！")
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        log.info("\n[System] 收到退出信號，正在關閉所有執行緒...")
        shutdown_web_devices(log)
        log.info("[System] 程式已結束。")

"""API service startup and main scan-loop helpers.

Extracted from new_main_v2.__main__ as Phase 10 of the slim-down plan.

  start_all(mode, base_dir):
    Start all runtime services for the given mode.

  scan_loop(main_fn, running_threads, Cnn_model, oracle_cnn_model,
            oracle_classes, ocr, log):
    Run the device scan loop until KeyboardInterrupt.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import bot_state
from runtime_services.device_scan_service import scan_and_start_devices
from runtime_services.push_server_service import ensure_push_server_started
from runtime_services.web_session_service import shutdown_web_devices
from runtime_services.worker_sync_service import ensure_worker_sync_started
from utils.logging_utils import logger


DEFAULT_DASHBOARD_PORT = 5002

# 保留背景 thread 引用，讓啟動後可以檢查服務是否仍存活；不要只依賴
# ``Thread.start()`` 沒有例外就當作服務永遠健康。
_service_threads: dict[str, threading.Thread] = {}


def _thread_alive(thread: Any) -> bool:
    """安全讀取 thread 存活狀態，也容納輕量測試替身。"""
    checker = getattr(thread, "is_alive", None)
    if not callable(checker):
        return True
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001 — health check 不能反過來弄垮主程式
        return False


def _safe_start(name: str, fn: Callable[[], Any]) -> bool:
    """啟動一個背景服務；失敗只記錄並讓其他服務繼續啟動。"""
    try:
        result = fn()
    except Exception:  # noqa: BLE001 — 單一背景服務不可阻斷主流程
        logger.warning("[%s] 啟動失敗，略過", name, exc_info=True)
        return False
    if result is False:
        logger.warning("[%s] 啟動回報失敗，略過", name)
        return False
    logger.info("[%s] 已啟動", name)
    return True


def _run_dashboard_server(run_server: Callable[[int], Any], port: int) -> None:
    """包住 Flask thread 的頂層例外，避免服務死掉時只剩 stderr。"""
    try:
        run_server(port)
    except Exception:  # noqa: BLE001 — thread 例外要留下可搜尋的正式 log
        logger.error("[control_panel] 伺服器執行緒已終止", exc_info=True)


def _start_dashboard(port: int) -> bool:
    import control_panel_app

    existing = _service_threads.get("control_panel")
    if existing is not None and _thread_alive(existing):
        return True

    server_thread = threading.Thread(
        target=_run_dashboard_server,
        args=(control_panel_app.run_server, port),
        name="control-panel",
        daemon=True,
    )
    # 先保存引用；即使 start() 失敗，health check 也能顯示它曾被建立但未存活。
    _service_threads["control_panel"] = server_thread
    server_thread.start()
    return _thread_alive(server_thread)


def _start_online_check_service() -> Any:
    from runtime_services.online_check_service import ensure_online_check_service_started

    return ensure_online_check_service_started()


def _start_online_monitor() -> bool:
    from ws_token.online_monitor import ensure_started

    monitor = ensure_started()
    thread = getattr(monitor, "_thread", None)
    return thread is not None and _thread_alive(thread)


def _start_mount_tracker() -> Any:
    from runtime_services.mount_tracker_service import ensure_mount_tracker_started

    return ensure_mount_tracker_started()


def _thread_status(thread: Any) -> dict[str, bool]:
    return {"started": thread is not None, "alive": _thread_alive(thread) if thread else False}


def get_service_status() -> dict[str, dict[str, bool]]:
    """回傳本程序可取得的四個 master 服務 thread 健康狀態。"""
    status = {"control_panel": _thread_status(_service_threads.get("control_panel"))}
    for name, module_name in (
        ("online_check_service", "runtime_services.online_check_service"),
        ("mount_tracker", "runtime_services.mount_tracker_service"),
    ):
        try:
            module = __import__(module_name, fromlist=["*"])
            status[name] = _thread_status(getattr(module, "_thread", None))
        except Exception:  # noqa: BLE001 — dashboard health check 只回報不可用
            status[name] = {"started": False, "alive": False}
    try:
        from ws_token import online_monitor

        monitor = getattr(online_monitor, "_monitor", None)
        status["online_monitor"] = _thread_status(
            getattr(monitor, "_thread", None) if monitor else None
        )
    except Exception:  # noqa: BLE001 — health check 不可阻斷主流程
        status["online_monitor"] = {"started": False, "alive": False}
    return status


def start_all(
    mode: str,
    base_dir: str,
    *,
    dashboard_port: int = DEFAULT_DASHBOARD_PORT,
) -> dict[str, bool]:
    """安全啟動 master/worker 背景服務，回傳各服務的啟動結果。"""
    statuses = {
        "push_server": _safe_start(
            "push_server", lambda: ensure_push_server_started(base_dir=base_dir)
        )
    }
    if mode == "master":
        statuses.update({
            "control_panel": _safe_start(
                "control_panel", lambda: _start_dashboard(int(dashboard_port))
            ),
            "online_check_service": _safe_start(
                "online_check_service", _start_online_check_service
            ),
            "online_monitor": _safe_start("online_monitor", _start_online_monitor),
            "mount_tracker": _safe_start("mount_tracker", _start_mount_tracker),
        })
    else:
        logger.info("[Info] Worker 模式：不啟動本地網頁伺服器，將回報至 Master。")

        def _start_worker_webhook() -> Any:
            from worker_webhook_api import ensure_worker_webhook_started

            return ensure_worker_webhook_started()

        statuses["worker_webhook"] = _safe_start(
            "worker_webhook", _start_worker_webhook
        )
        statuses["worker_sync"] = _safe_start(
            "worker_sync", ensure_worker_sync_started
        )
    return statuses


def scan_loop(
    main_fn: Any,
    running_threads: dict,
    Cnn_model: Any,
    oracle_cnn_model: Any,
    oracle_classes: Any,
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
                oracle_cnn_model,
                oracle_classes,
                ocr,
                log,
            )
            # 掉線判離線：remote (worker) 裝置逾 1 小時未回報 → 標 OFFLINE。
            try:
                bot_state.sweep_stale_remote_devices()
            except Exception as sweep_err:
                log.info(f"[System] 清掃逾時遠端裝置失敗: {sweep_err}")
            for _ in range(300):  # 0.1s × 300 = 30 s
                if bot_state.check_refresh_needed():
                    log.info("[System] 收到立即掃描請求！")
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        log.info("\n[System] 收到退出信號，正在關閉所有執行緒...")
        shutdown_web_devices(log)
        log.info("[System] 程式已結束。")

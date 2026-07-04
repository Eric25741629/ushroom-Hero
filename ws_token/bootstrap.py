"""WS-first ADB token bootstrap helpers.

ADB devices run the WS phase before the app is opened. If no capture exists yet,
the passive scrape in the normal wake flow never gets a chance to seed it, so
this helper cold-starts the app once, lets ``refresh_creds`` write the capture,
then returns the phone to a quiet desktop state before the WS phase continues.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ws_token.creds import AUTH_DIR, load_creds, refresh_creds

logger = logging.getLogger(__name__)

PACKAGE = "com.mxdzz.tw.and"
_ADB_TIMEOUT_SEC = 15


def has_creds(device: str, *, auth_dir: Path | None = None) -> bool:
    """Return True when a readable capture exists for ``device``.

    Invalid/corrupt captures are treated as missing so the caller can try a
    fresh ADB scrape. This is a guard, not validation for WS login freshness.
    """
    try:
        load_creds(device, auth_dir=Path(auth_dir) if auth_dir is not None else AUTH_DIR)
        return True
    except Exception:  # noqa: BLE001 — bootstrap 檢查不可炸 wake loop
        return False


def _adb(device: str, *args: str) -> None:
    subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_ADB_TIMEOUT_SEC,
        check=False,
    )


def _return_to_home(device: str, logger_obj=None) -> None:
    """Best-effort stop of the app session started only to mint WS creds."""
    log = logger_obj or logger
    commands = (
        ("shell", "am", "force-stop", PACKAGE),
        ("shell", "input", "keyevent", "HOME"),
    )
    for args in commands:
        try:
            _adb(device, *args)
        except Exception as exc:  # noqa: BLE001 — cleanup 不可遮蔽 bootstrap 結果
            log.debug("[%s] WS token bootstrap cleanup failed (%s): %s",
                      device, " ".join(args), exc, exc_info=True)


def bootstrap_token(
    device: str,
    logger_obj=None,
    *,
    force: bool = False,
    auth_dir: Path | None = None,
) -> bool:
    """Ensure an ADB device has a WS capture, refreshing once when needed.

    Returns False only when a refresh was needed/forced and failed. The function
    never raises to callers: WS-first is an optimization and must degrade to the
    normal Playwright/ADB pipeline.
    """
    log = logger_obj or logger
    resolved_auth_dir = Path(auth_dir) if auth_dir is not None else AUTH_DIR
    if not force and has_creds(device, auth_dir=resolved_auth_dir):
        return True

    try:
        if auth_dir is None:
            refresh_creds(device)
        else:
            refresh_creds(device, auth_dir=resolved_auth_dir)
        log.info("[%s] WS token bootstrap 已主動重撈 capture", device)
        return True
    except Exception as exc:  # noqa: BLE001 — refresh 失敗只降級，不中斷喚醒
        log.warning("[%s] WS token bootstrap 失敗，降級跑原流程: %s", device, exc)
        return False
    finally:
        _return_to_home(device, log)

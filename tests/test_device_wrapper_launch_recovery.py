"""Regression tests for web_h5 launch profile-in-use recovery.

Root cause (2026-06-13, emulator-5554): a web_h5 restart fired while the previous
Chrome on the same `--user-data-dir` was still tearing down. The new chrome.exe
detected the existing instance, forwarded its command line and exited 21
(Chromium CHROME_RESULT_CODE_NORMAL_EXIT_PROCESS_NOTIFIED). Playwright surfaced
that as a launch failure. The old fallback switched to a SEPARATE, login-less
profile dir on the first failure, which caused a ~90s "未知頁面" loop plus an
extra game restart.

The fix prefers the original (logged-in) profile: on a profile-in-use error it
waits and retries the SAME profile; the different-profile fallback is a genuine
last resort. These tests pin that contract using injected launch_fn / sleep_fn so
no real Playwright/Chrome is needed.

device_wrapper imports cv2 at module load; stub it to keep this a light unit test.
"""
from __future__ import annotations

import sys
import types

if "cv2" not in sys.modules:
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.IMREAD_COLOR = 1
    cv2_stub.COLOR_BGR2RGB = 4
    cv2_stub.imdecode = lambda *args, **kwargs: None
    cv2_stub.cvtColor = lambda img, *args, **kwargs: img
    sys.modules["cv2"] = cv2_stub

import logging

import pytest

import device_wrapper

ORIG = "playwright_profile/emulator-5554"
FALLBACK = "C:/Users/x/AppData/Local/mushroom_playwright_profiles/emulator-5554"

# A realistic Playwright error string for Chrome exit 21.
EXIT21 = (
    "BrowserType.launch_persistent_context: Failed to launch the browser process.\n"
    "[pid=51740] <process did exit: exitCode=21, signal=null>"
)


def _recorder():
    """Return (sleep_fn, sleeps_list) capturing sleep durations without waiting."""
    sleeps: list[float] = []
    return (lambda s: sleeps.append(s)), sleeps


# --------------------------------------------------------------------------- #
# _is_profile_in_use_error
# --------------------------------------------------------------------------- #
def test_is_profile_in_use_error_matches_exit21():
    assert device_wrapper._is_profile_in_use_error(RuntimeError(EXIT21)) is True


def test_is_profile_in_use_error_rejects_unrelated():
    assert (
        device_wrapper._is_profile_in_use_error(
            RuntimeError("Executable doesn't exist at /chrome")
        )
        is False
    )


# --------------------------------------------------------------------------- #
# _launch_with_profile_recovery
# --------------------------------------------------------------------------- #
def test_retries_same_profile_then_succeeds(caplog):
    """Two profile-in-use failures then success must stay on the ORIGINAL profile."""
    calls: list[tuple[str, bool]] = []

    def launch_fn(profile, use_channel):
        calls.append((profile, use_channel))
        if len([c for c in calls if c[0] == ORIG]) <= 2:
            raise RuntimeError(EXIT21)
        return f"ctx::{profile}"

    sleep_fn, sleeps = _recorder()
    ctx, used = device_wrapper._launch_with_profile_recovery(
        profile_dir=ORIG,
        fallback_profile_dir=FALLBACK,
        launch_fn=launch_fn,
        logger_obj=logging.getLogger("t"),
        device_id="emulator-5554",
        sleep_fn=sleep_fn,
    )

    assert used == ORIG
    assert ctx == f"ctx::{ORIG}"
    # Two transient retries -> two waits; fallback profile never attempted.
    assert len(sleeps) == 2
    assert all(c[0] == ORIG for c in calls)


def test_falls_back_to_other_profile_only_as_last_resort(caplog):
    """If the original profile stays in-use, the login-less fallback is used last."""
    calls: list[tuple[str, bool]] = []

    def launch_fn(profile, use_channel):
        calls.append((profile, use_channel))
        if profile == ORIG:
            raise RuntimeError(EXIT21)
        return f"ctx::{profile}"

    sleep_fn, _sleeps = _recorder()
    with caplog.at_level(logging.WARNING):
        ctx, used = device_wrapper._launch_with_profile_recovery(
            profile_dir=ORIG,
            fallback_profile_dir=FALLBACK,
            launch_fn=launch_fn,
            logger_obj=logging.getLogger("t"),
            device_id="emulator-5554",
            sleep_fn=sleep_fn,
        )

    assert used == FALLBACK
    assert ctx == f"ctx::{FALLBACK}"
    # The fallback profile must be the LAST thing tried.
    assert calls[-1][0] == FALLBACK
    assert any("switched profile dir to fallback" in r.message for r in caplog.records)


def test_hard_error_does_not_waste_retry_waits():
    """A non-profile-in-use error must not trigger the wait/retry loop."""
    calls: list[tuple[str, bool]] = []

    def launch_fn(profile, use_channel):
        calls.append((profile, use_channel))
        if profile == ORIG:
            raise RuntimeError("Executable doesn't exist at /chrome")
        return f"ctx::{profile}"

    sleep_fn, sleeps = _recorder()
    ctx, used = device_wrapper._launch_with_profile_recovery(
        profile_dir=ORIG,
        fallback_profile_dir=FALLBACK,
        launch_fn=launch_fn,
        logger_obj=logging.getLogger("t"),
        device_id="emulator-5554",
        sleep_fn=sleep_fn,
    )

    assert used == FALLBACK
    assert sleeps == []  # no transient waits for a hard error


def test_success_first_try_no_sleep_no_fallback(caplog):
    """Clean launch on the original profile: no waits, no fallback warning."""
    def launch_fn(profile, use_channel):
        return f"ctx::{profile}"

    sleep_fn, sleeps = _recorder()
    with caplog.at_level(logging.WARNING):
        ctx, used = device_wrapper._launch_with_profile_recovery(
            profile_dir=ORIG,
            fallback_profile_dir=FALLBACK,
            launch_fn=launch_fn,
            logger_obj=logging.getLogger("t"),
            device_id="emulator-5554",
            sleep_fn=sleep_fn,
        )

    assert used == ORIG
    assert sleeps == []
    assert not any("switched profile dir to fallback" in r.message for r in caplog.records)


def test_all_attempts_fail_raises_last_error():
    """When every attempt fails, the last exception propagates."""
    def launch_fn(profile, use_channel):
        raise RuntimeError(EXIT21)

    sleep_fn, _ = _recorder()
    with pytest.raises(RuntimeError, match="exitCode=21"):
        device_wrapper._launch_with_profile_recovery(
            profile_dir=ORIG,
            fallback_profile_dir=FALLBACK,
            launch_fn=launch_fn,
            logger_obj=logging.getLogger("t"),
            device_id="emulator-5554",
            sleep_fn=sleep_fn,
        )

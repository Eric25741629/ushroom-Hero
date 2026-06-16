"""Regression tests for web_h5 launch profile-in-use recovery.

Root cause (2026-06-16, device 7fe98fc6): normal hourly sleep does NOT close the
web_h5 browser, so a previous Chrome on the same `--user-data-dir` can linger
across the sleep. At the next wake the bot thinks the browser is closed
(`is_alive()` canvas probe false-negatives on a throttled tab) and launches a
NEW Chrome. On Windows that fresh chrome.exe finds the existing instance holding
the profile, forwards its command line and exits cleanly with **exitCode=0**
(NOT 21). The OLD detection only knew exit 21, so it fell straight through to a
SEPARATE, login-less fallback profile dir — which loads the game but can never
authenticate, leaving the page stuck at "未知" for hours (restart_count=105).

The fix (E/F/H):
- E: `_is_profile_in_use_error` also recognises the exitCode=0 hand-off.
- H: on a profile-in-use failure, KILL the stale Chrome process that is holding
  this exact `--user-data-dir`, then retry the SAME (logged-in) profile.
- F: if the original profile stays in-use after retries, RAISE instead of
  switching to the login-less fallback profile (the startup ceiling + 30-minute
  avoidance then lets the lock clear and the next wake reuses the real profile).
  The fallback profile is reserved for genuine NON-in-use launch errors.

These tests pin that contract using injected launch_fn / sleep_fn / kill_orphan_fn
so no real Playwright / Chrome / process kill happens.

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

# Windows hand-off signature (the real 7fe98fc6 failure, 2026-06-16): a freshly
# spawned Chrome that finds an existing instance on the same --user-data-dir
# forwards its command line to it and exits cleanly with exitCode=0 (NOT 21).
EXITCODE0_HANDOFF = (
    "BrowserType.launch_persistent_context: Failed to launch the browser process.\n"
    "[pid=19796] <process did exit: exitCode=0, signal=null>"
)


def _recorder():
    """Return (sleep_fn, sleeps_list) capturing sleep durations without waiting."""
    sleeps: list[float] = []
    return (lambda s: sleeps.append(s)), sleeps


def _kill_recorder(freed_after: int | None = None):
    """Return (kill_fn, killed_list). If `freed_after` is set, the orphan is
    reported gone (returns 1) for the first call and the profile is considered
    freed; otherwise every call returns 0 (no holder found / could not free)."""
    killed: list[str] = []

    def kill_fn(profile_dir: str) -> int:
        killed.append(profile_dir)
        if freed_after is not None and len(killed) >= freed_after:
            return 1
        return 0

    return kill_fn, killed


# --------------------------------------------------------------------------- #
# _is_profile_in_use_error
# --------------------------------------------------------------------------- #
def test_is_profile_in_use_error_matches_exit21():
    assert device_wrapper._is_profile_in_use_error(RuntimeError(EXIT21)) is True


def test_is_profile_in_use_error_matches_exitcode0_handoff():
    """Fix E: the Windows exitCode=0 hand-off must be recognised as profile-in-use
    so the SAME-profile kill+retry guard fires instead of degrading to fallback."""
    assert (
        device_wrapper._is_profile_in_use_error(RuntimeError(EXITCODE0_HANDOFF))
        is True
    )


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
def test_kill_orphan_then_retry_same_profile_succeeds():
    """Fix E+H: a profile-in-use (exitCode=0) failure kills the stale Chrome
    holding the ORIGINAL profile, then retries and succeeds on that SAME
    logged-in profile — never the login-less fallback."""
    calls: list[tuple[str, bool]] = []
    state = {"orphan_alive": True}

    def launch_fn(profile, use_channel):
        calls.append((profile, use_channel))
        if profile == ORIG and state["orphan_alive"]:
            raise RuntimeError(EXITCODE0_HANDOFF)
        return f"ctx::{profile}"

    killed: list[str] = []

    def kill_fn(profile_dir):
        killed.append(profile_dir)
        state["orphan_alive"] = False  # killing the holder frees the profile
        return 1

    sleep_fn, sleeps = _recorder()
    ctx, used = device_wrapper._launch_with_profile_recovery(
        profile_dir=ORIG,
        fallback_profile_dir=FALLBACK,
        launch_fn=launch_fn,
        logger_obj=logging.getLogger("t"),
        device_id="emulator-5554",
        sleep_fn=sleep_fn,
        kill_orphan_fn=kill_fn,
    )

    assert used == ORIG
    assert ctx == f"ctx::{ORIG}"
    assert killed == [ORIG]  # killed the orphan holding the original profile
    assert all(c[0] == ORIG for c in calls)  # fallback never attempted
    assert len(sleeps) == 1


def test_profile_in_use_raises_and_never_uses_loginless_fallback():
    """Fix F: if the original profile stays in-use even after kill attempts, the
    recovery must RAISE — it must NOT switch to the separate, login-less fallback
    profile (the 3-hour 未知 thrash on 7fe98fc6, 2026-06-16)."""
    calls: list[tuple[str, bool]] = []

    def launch_fn(profile, use_channel):
        calls.append((profile, use_channel))
        if profile == ORIG:
            raise RuntimeError(EXITCODE0_HANDOFF)
        return f"ctx::{profile}"  # fallback would succeed IF (wrongly) attempted

    kill_fn, killed = _kill_recorder()  # orphan never dies → every kill returns 0
    sleep_fn, _ = _recorder()
    with pytest.raises(RuntimeError, match="exitCode=0"):
        device_wrapper._launch_with_profile_recovery(
            profile_dir=ORIG,
            fallback_profile_dir=FALLBACK,
            launch_fn=launch_fn,
            logger_obj=logging.getLogger("t"),
            device_id="emulator-5554",
            sleep_fn=sleep_fn,
            kill_orphan_fn=kill_fn,
        )

    assert FALLBACK not in [c[0] for c in calls]  # never degraded to login-less
    assert killed  # it did try to kill the orphan


def test_hard_error_falls_back_without_kill_or_waits():
    """A non-profile-in-use error must not kill processes nor wait/retry; the
    separate fallback profile is still a genuine last resort for hard errors."""
    calls: list[tuple[str, bool]] = []

    def launch_fn(profile, use_channel):
        calls.append((profile, use_channel))
        if profile == ORIG:
            raise RuntimeError("Executable doesn't exist at /chrome")
        return f"ctx::{profile}"

    kill_fn, killed = _kill_recorder()
    sleep_fn, sleeps = _recorder()
    ctx, used = device_wrapper._launch_with_profile_recovery(
        profile_dir=ORIG,
        fallback_profile_dir=FALLBACK,
        launch_fn=launch_fn,
        logger_obj=logging.getLogger("t"),
        device_id="emulator-5554",
        sleep_fn=sleep_fn,
        kill_orphan_fn=kill_fn,
    )

    assert used == FALLBACK
    assert sleeps == []  # no transient waits for a hard error
    assert killed == []  # no orphan-kill for a non-profile-in-use error


def test_success_first_try_no_sleep_no_fallback_no_kill(caplog):
    """Clean launch on the original profile: no kill, no waits, no fallback."""

    def launch_fn(profile, use_channel):
        return f"ctx::{profile}"

    kill_fn, killed = _kill_recorder()
    sleep_fn, sleeps = _recorder()
    with caplog.at_level(logging.WARNING):
        ctx, used = device_wrapper._launch_with_profile_recovery(
            profile_dir=ORIG,
            fallback_profile_dir=FALLBACK,
            launch_fn=launch_fn,
            logger_obj=logging.getLogger("t"),
            device_id="emulator-5554",
            sleep_fn=sleep_fn,
            kill_orphan_fn=kill_fn,
        )

    assert used == ORIG
    assert sleeps == []
    assert killed == []
    assert not any(
        "switched profile dir to fallback" in r.message for r in caplog.records
    )


def test_persistent_exit21_in_use_raises_last_error():
    """When the original profile is permanently in-use (exit 21) and the orphan
    cannot be freed, the last exception propagates (no login-less fallback)."""

    def launch_fn(profile, use_channel):
        if profile == ORIG:
            raise RuntimeError(EXIT21)
        return f"ctx::{profile}"

    kill_fn, _ = _kill_recorder()
    sleep_fn, _ = _recorder()
    with pytest.raises(RuntimeError, match="exitCode=21"):
        device_wrapper._launch_with_profile_recovery(
            profile_dir=ORIG,
            fallback_profile_dir=FALLBACK,
            launch_fn=launch_fn,
            logger_obj=logging.getLogger("t"),
            device_id="emulator-5554",
            sleep_fn=sleep_fn,
            kill_orphan_fn=kill_fn,
        )


# --------------------------------------------------------------------------- #
# _kill_chrome_holding_profile — surgical matching by exact --user-data-dir
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, pid, name, cmdline):
        self.pid = pid
        self.info = {"name": name, "cmdline": cmdline}
        self.killed = False

    def kill(self):
        self.killed = True


def test_kill_chrome_holding_profile_kills_only_exact_user_data_dir():
    import os

    target = "C:/games/playwright_profile/emulator-5554"
    other = "C:/games/playwright_profile/emulator-5560"
    procs = [
        _FakeProc(1, "chrome.exe", ["chrome.exe", f"--user-data-dir={os.path.abspath(target)}", "--x"]),
        _FakeProc(2, "chrome.exe", ["chrome.exe", "--type=renderer", f"--user-data-dir={os.path.abspath(target)}"]),
        _FakeProc(3, "chrome.exe", ["chrome.exe", f"--user-data-dir={os.path.abspath(other)}"]),  # other device
        _FakeProc(4, "explorer.exe", ["explorer.exe"]),  # non-chrome
        _FakeProc(5, "chrome.exe", ["chrome.exe"]),  # personal chrome, no automation profile
    ]

    n = device_wrapper._kill_chrome_holding_profile(
        target,
        logger_obj=logging.getLogger("t"),
        device_id="emulator-5554",
        process_iter=lambda attrs=None: iter(procs),
    )

    assert n == 2
    assert procs[0].killed and procs[1].killed  # both procs on the target profile
    assert not procs[2].killed  # other device's chrome untouched
    assert not procs[3].killed and not procs[4].killed  # explorer + personal chrome safe


def test_kill_chrome_holding_profile_never_raises_on_iter_failure():
    def boom(attrs=None):
        raise RuntimeError("psutil exploded")

    # Must swallow errors and return 0 — orphan kill is strictly best-effort.
    n = device_wrapper._kill_chrome_holding_profile(
        "C:/x/profile",
        logger_obj=logging.getLogger("t"),
        device_id="dev",
        process_iter=boom,
    )
    assert n == 0

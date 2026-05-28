"""Pause-aware guard for tasks that drive Playwright pages directly, bypassing
the device_wrapper's _pause_guard.

device_wrapper.MonitoredDevice gates every click()/shell()/screenshot() through
bot_state.check_pause. But task modules that touch d._page directly
(carpark_auto, sea_v2.session, statue_weekly H5 path) sidestep that guard, so
opening live-view (which calls bot_state.set_pause(ip, True)) doesn't actually
stop those tasks from clicking.

This module provides:
  - bind(ip, page): set thread-local context (call once per task entry).
  - unbind(): clear thread-local context (in finally).
  - check(): if paused, snapshot cocos view stack, block via bot_state.check_pause,
    re-snapshot on resume; raise TaskAborted if the snapshot changed (user
    manually navigated the page during the pause -> any in-progress task state
    is now stale; abort + let the scheduler restart fresh next cycle).
  - TaskAborted: exception raised on state divergence; task entry point should
    catch and end cleanly.

Thread-local follows the same pattern as utils/carpark_click_recorder.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

import bot_state


class TaskAborted(Exception):
    """Raised by check() when the cocos view stack changed during a pause.

    Signals: the page state diverged while paused (user navigated away),
    so any in-progress task assumptions are stale. The caller should let
    this propagate out of the task; the scheduler will run the task again
    from scratch on its next cycle.
    """


_local = threading.local()


def bind(*, ip: str, page: Any) -> None:
    """Bind thread-local ip + page for subsequent check() calls.

    Call once at the top of a task's scheduler entry, paired with unbind()
    in a finally block.
    """
    _local.ip = ip
    _local.page = page


def unbind() -> None:
    """Clear thread-local context. Idempotent."""
    _local.ip = None
    _local.page = None


# Fingerprint: top of UIRoot's active view stack, including one level of
# active children inside each top view. Catches typical "user navigated to
# a different page" cases (e.g., left ParkingMainView, opened MessageView
# dialog, scrolled to a different scene).
_FINGERPRINT_JS = r"""() => {
  try {
    if (typeof cc === 'undefined' || !cc.director) return '';
    const scene = cc.director.getScene();
    const ui = (scene && scene.children || []).find(c => c.name === 'UIRoot');
    if (!ui) return '';
    const parts = [];
    for (const child of ui.children || []) {
      if (!child.active) continue;
      parts.push(child.name);
      const sub = (child.children || []).filter(c => c.active).map(c => c.name);
      if (sub.length) parts.push('[' + sub.join(',') + ']');
    }
    return parts.join('/');
  } catch (e) { return ''; }
}"""


def _snapshot_fingerprint() -> str:
    page = getattr(_local, "page", None)
    if page is None:
        return ""
    try:
        return str(page.evaluate(_FINGERPRINT_JS))
    except Exception:
        return ""


def check() -> None:
    """Pause-aware checkpoint. Cheap when not paused.

    If currently paused:
      1. Snapshot the cocos view fingerprint.
      2. Block via bot_state.check_pause (until unpaused).
      3. Re-snapshot the fingerprint.
      4. If it changed, raise TaskAborted.
    """
    ip = getattr(_local, "ip", None)
    if ip is None:
        return
    # Fast path: not paused -> no work, no page.evaluate, no logging.
    event = bot_state._pause_events.get(ip)  # noqa: SLF001 - private attr by design
    if event is None or event.is_set():
        return

    # Paused: snapshot, block, compare.
    before = _snapshot_fingerprint()
    bot_state.check_pause(ip)
    after = _snapshot_fingerprint()

    # If either snapshot failed (empty), we can't safely compare -> assume
    # diverged and abort (better safe than wrong-click). If both are present
    # and differ, also abort.
    if before != after:
        raise TaskAborted(
            f"view stack changed during pause: '{before}' -> '{after}'"
        )

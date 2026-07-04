"""pause_guard.check(): abort a paused task when the cocos view stack diverged.

Regression guard (item B): an empty/failed fingerprint snapshot must be treated
as "cannot verify -> abort", not silently pass. The old `before != after` check
let `before == after == ""` (both snapshots failed) slip through.
"""
import threading

import pytest

import bot_state
from utils import pause_guard
from utils.pause_guard import TaskAborted


def _force_paused(monkeypatch) -> None:
    """Make check() enter the paused branch and return from check_pause at once."""
    ev = threading.Event()  # not set() => paused (fast-path won't early-return)
    monkeypatch.setattr(bot_state, "get_pause_event", lambda ip: ev)
    monkeypatch.setattr(bot_state, "check_pause", lambda ip: None)


def _snapshots(monkeypatch, values) -> None:
    seq = iter(values)
    monkeypatch.setattr(pause_guard, "_snapshot_fingerprint", lambda: next(seq))


def test_check_aborts_when_view_stack_changed(monkeypatch):
    pause_guard.bind(ip="pg-test", page=object())
    try:
        _force_paused(monkeypatch)
        _snapshots(monkeypatch, ["ParkingMainView", "MessageView"])
        with pytest.raises(TaskAborted):
            pause_guard.check()
    finally:
        pause_guard.unbind()


def test_check_aborts_when_both_snapshots_empty(monkeypatch):
    """before == after == '' means both snapshots failed -> must abort, not pass."""
    pause_guard.bind(ip="pg-test", page=object())
    try:
        _force_paused(monkeypatch)
        _snapshots(monkeypatch, ["", ""])
        with pytest.raises(TaskAborted):
            pause_guard.check()
    finally:
        pause_guard.unbind()


def test_check_aborts_when_before_empty(monkeypatch):
    pause_guard.bind(ip="pg-test", page=object())
    try:
        _force_paused(monkeypatch)
        _snapshots(monkeypatch, ["", "ParkingMainView"])
        with pytest.raises(TaskAborted):
            pause_guard.check()
    finally:
        pause_guard.unbind()


def test_check_passes_when_stable_nonempty(monkeypatch):
    pause_guard.bind(ip="pg-test", page=object())
    try:
        _force_paused(monkeypatch)
        _snapshots(monkeypatch, ["ParkingMainView", "ParkingMainView"])
        pause_guard.check()  # must not raise
    finally:
        pause_guard.unbind()


def test_check_noop_when_not_paused(monkeypatch):
    """No pause event -> cheap fast path, never snapshots, never raises."""
    pause_guard.bind(ip="pg-test", page=object())
    try:
        monkeypatch.setattr(bot_state, "get_pause_event", lambda ip: None)

        def _boom():
            raise AssertionError("must not snapshot on the fast path")

        monkeypatch.setattr(pause_guard, "_snapshot_fingerprint", _boom)
        pause_guard.check()  # returns immediately
    finally:
        pause_guard.unbind()

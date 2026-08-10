"""Tests for the offline WS fallback wait-round helper.

When a phone with ``ws_token.offline_fallback`` enabled can't be reached during
device init, the thread must NOT die (set_offline + return). Instead it runs one
pure-WS round (idle reward / lamp / mining via ws_phase) + aligned sleep, then
retries the ADB connection. The reusable round lives in
``runtime_services.ws_fallback_service`` so it can be tested without importing
the import-heavy ``new_main_v2`` (device_wrapper -> cv2, miner, Skill ...).

Behavior gating (``new_main_v2.main`` init except branch) is verified through
the predicate helper ``should_ws_fallback`` rather than re-importing main.

Host gate: master + worker share the NAS-synced bot_config.json, so BOTH hosts
would otherwise spawn the fallback. Only one host may run it:
``ws_token.fallback_host`` set -> only the matching hostname (case-insensitive);
unset -> only the resolved-mode=="master" host. Otherwise the worker's hourly
WS login kicks the phone session while the master is driving it normally.
"""
import sys
import types

import pytest

import config_manager


def _import_service():
    return __import__(
        "runtime_services.ws_fallback_service", fromlist=["x"]
    )


class _NullLogger:
    def __init__(self):
        self.messages = []

    def warning(self, msg, *a, **k):
        self.messages.append(("warning", msg))

    def info(self, msg, *a, **k):
        self.messages.append(("info", msg))

    def error(self, msg, *a, **k):
        self.messages.append(("error", msg))


def _patch_host(monkeypatch, svc, *, hostname="testhost", mode="master"):
    """Pin hostname + resolved global mode so tests never read the real config."""
    monkeypatch.setattr(svc.config_manager, "get_hostname", lambda: hostname)
    monkeypatch.setattr(svc.config_manager, "get_global_config", lambda: {"mode": mode})


def _patch_device_cfg(monkeypatch, svc, ws_token, backend="adb"):
    monkeypatch.setattr(
        svc.config_manager,
        "get_device_config",
        lambda ip: {"backend": backend, "ws_token": ws_token},
    )


# ── predicate: should we take the WS fallback path? ──────────────────────────

def test_should_ws_fallback_true_for_adb_with_flag(monkeypatch):
    svc = _import_service()
    _patch_host(monkeypatch, svc, mode="master")
    _patch_device_cfg(monkeypatch, svc, {"enabled": True, "offline_fallback": True})
    assert svc.should_ws_fallback("phone", "adb") is True


def test_should_ws_fallback_false_when_flag_off(monkeypatch):
    svc = _import_service()
    _patch_host(monkeypatch, svc, mode="master")
    _patch_device_cfg(monkeypatch, svc, {"enabled": True, "offline_fallback": False})
    assert svc.should_ws_fallback("phone", "adb") is False


def test_should_ws_fallback_false_for_web_h5(monkeypatch):
    svc = _import_service()
    _patch_host(monkeypatch, svc, mode="master")
    _patch_device_cfg(
        monkeypatch, svc, {"enabled": True, "offline_fallback": True}, backend="web_h5"
    )
    # backend_kind argument is authoritative; web_h5 never takes this path.
    assert svc.should_ws_fallback("phone", "web_h5") is False


def test_should_ws_fallback_false_when_ws_disabled(monkeypatch):
    svc = _import_service()
    _patch_host(monkeypatch, svc, mode="master")
    _patch_device_cfg(monkeypatch, svc, {"enabled": False, "offline_fallback": True})
    assert svc.should_ws_fallback("phone", "adb") is False


# ── predicate host gate (mirror of get_ws_fallback_devices's gate) ───────────

def test_should_ws_fallback_false_on_worker_without_fallback_host(monkeypatch):
    svc = _import_service()
    _patch_host(monkeypatch, svc, mode="worker")
    _patch_device_cfg(monkeypatch, svc, {"enabled": True, "offline_fallback": True})
    assert svc.should_ws_fallback("phone", "adb") is False


def test_should_ws_fallback_true_when_fallback_host_matches_case_insensitive(monkeypatch):
    svc = _import_service()
    # even on a worker, an explicit matching fallback_host wins
    _patch_host(monkeypatch, svc, hostname="INFINITE", mode="worker")
    _patch_device_cfg(
        monkeypatch, svc,
        {"enabled": True, "offline_fallback": True, "fallback_host": "infinite"},
    )
    assert svc.should_ws_fallback("phone", "adb") is True


def test_should_ws_fallback_false_when_fallback_host_mismatch_even_on_master(monkeypatch):
    svc = _import_service()
    _patch_host(monkeypatch, svc, hostname="DESKTOP-OV0ASQ4", mode="master")
    _patch_device_cfg(
        monkeypatch, svc,
        {"enabled": True, "offline_fallback": True, "fallback_host": "infinite"},
    )
    assert svc.should_ws_fallback("phone", "adb") is False


# ── round: run WS phase then sleep, swallow exceptions ───────────────────────

def test_round_runs_ws_then_sleeps(monkeypatch):
    svc = _import_service()
    order = []

    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: order.append("state"))
    monkeypatch.setattr(
        svc, "_load_run_sleep_cycle",
        lambda: (lambda ip, lg, **k: order.append(("sleep", k))),
    )

    def ws_fn(ip, lg):
        order.append("ws")
        return frozenset({"idle_reward"})

    svc.run_ws_fallback_wait_round(
        "phone", _NullLogger(), run_ws_phase_fn=ws_fn, enable_dungeon_manager=False
    )

    # state update happens, WS runs, then sleep — in that order.
    assert order[0] == "state"
    assert "ws" in order
    sleep_entry = next(e for e in order if isinstance(e, tuple) and e[0] == "sleep")
    assert order.index("ws") < order.index(sleep_entry)
    # aligned sleep policy/reason for the offline-WS round
    assert sleep_entry[1].get("sleep_policy") == "phone_offline_ws_only"
    assert sleep_entry[1].get("enable_dungeon_manager") is False


def test_round_runs_offline_tasks_between_ws_and_sleep(monkeypatch):
    svc = _import_service()
    order = []
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: order.append("state"))
    monkeypatch.setattr(
        svc, "_load_run_sleep_cycle",
        lambda: (lambda ip, lg, **k: order.append("sleep")),
    )
    svc.run_ws_fallback_wait_round(
        "phone", _NullLogger(),
        run_ws_phase_fn=lambda ip, lg: order.append("ws"),
        run_offline_tasks_fn=lambda ip, lg: order.append("offline"),
        enable_dungeon_manager=False,
    )
    assert order.index("ws") < order.index("offline") < order.index("sleep")


def test_round_sleeps_even_when_ws_phase_raises(monkeypatch):
    svc = _import_service()
    slept = []

    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(
        svc, "_load_run_sleep_cycle",
        lambda: (lambda ip, lg, **k: slept.append(True)),
    )

    def boom(ip, lg):
        raise RuntimeError("ws blew up")

    # must not raise out (thread survives) and must still sleep.
    svc.run_ws_fallback_wait_round(
        "phone", _NullLogger(), run_ws_phase_fn=boom, enable_dungeon_manager=True
    )
    assert slept == [True]


def test_round_propagates_login_conflict_to_runtime_cooldown(monkeypatch):
    """The offline fallback must not swallow the explicit WS control signal."""
    svc = _import_service()
    class LoginConflictError(Exception):
        pass

    stage_guard = types.ModuleType("game_actions.stage_guard")
    stage_guard.LoginConflictError = LoginConflictError
    monkeypatch.setitem(sys.modules, "game_actions.stage_guard", stage_guard)

    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    slept = []
    monkeypatch.setattr(
        svc,
        "_load_run_sleep_cycle",
        lambda: (lambda ip, lg, **k: slept.append(k)),
    )

    with pytest.raises(LoginConflictError, match="cmd=259"):
        svc.run_ws_fallback_wait_round(
            "phone",
            _NullLogger(),
            run_ws_phase_fn=lambda ip, lg: (_ for _ in ()).throw(
                LoginConflictError("cmd=259")
            ),
            enable_dungeon_manager=False,
        )

    assert slept == []


def test_round_swallows_sleep_exception_with_floor_sleep(monkeypatch):
    """A failed run_sleep_cycle must not propagate AND must not hot-spin.

    The caller does `continue` right after the round; if the aligned sleep blew
    up and we returned instantly, the retry loop would hammer WS logins. The
    except branch must apply a floor sleep (>= 60s) before returning.
    """
    svc = _import_service()
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)

    def boom_sleep(ip, lg, **k):
        raise RuntimeError("sleep blew up")

    monkeypatch.setattr(svc, "_load_run_sleep_cycle", lambda: boom_sleep)

    floor_calls = []
    monkeypatch.setattr(svc.time, "sleep", lambda sec: floor_calls.append(sec))

    # even a sleep failure must not propagate (thread survives)...
    svc.run_ws_fallback_wait_round(
        "phone", _NullLogger(), run_ws_phase_fn=lambda ip, lg: frozenset(),
        enable_dungeon_manager=False,
    )
    # ...and the floor sleep must have been applied to avoid a tight retry loop.
    assert floor_calls, "floor sleep not applied after run_sleep_cycle failure"
    assert floor_calls[0] >= 60


def test_round_no_floor_sleep_when_sleep_cycle_succeeds(monkeypatch):
    svc = _import_service()
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_load_run_sleep_cycle", lambda: (lambda ip, lg, **k: None))

    floor_calls = []
    monkeypatch.setattr(svc.time, "sleep", lambda sec: floor_calls.append(sec))

    svc.run_ws_fallback_wait_round(
        "phone", _NullLogger(), run_ws_phase_fn=lambda ip, lg: frozenset(),
        enable_dungeon_manager=False,
    )
    assert floor_calls == []  # normal path never calls the floor sleep


def test_round_passes_dungeon_manager_flag(monkeypatch):
    svc = _import_service()
    captured = {}
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(
        svc, "_load_run_sleep_cycle",
        lambda: (lambda ip, lg, **k: captured.update(k)),
    )
    svc.run_ws_fallback_wait_round(
        "phone", _NullLogger(), run_ws_phase_fn=lambda ip, lg: frozenset(),
        enable_dungeon_manager=True,
    )
    assert captured.get("enable_dungeon_manager") is True

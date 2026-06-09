"""Phone-account flow tests for the ws_token pure-WS backend.

Covers the 2026-06-09 additions to ``runtime_services.ws_runner_service`` that
let a device backed by a *real phone account* run safely:

  - token bootstrap / refresh: mint a fresh ticket when the serial is
    adb-reachable, otherwise reuse the cached one (``_ensure_token``);
  - online protection: never stomp a live human session — skip the wake when the
    protected player is online OR the answer is undetermined
    (``_protected_player_online`` + the cycle's skip path);
  - token-expiry state machine: a ``login_ok=False`` report drops the loop into
    "need refresh" mode (no more run_device) until the phone is adb-reachable and
    a fresh ticket is minted, then it resumes.

Everything external (ADB scan, refresh_creds, run_device, the online-check
mailbox, sleep) is monkeypatched — these are pure offline unit tests, no device,
no WS, no Playwright. The gate for all of this is the presence of
``online_check_target_pid`` in the device config; tests assert that devices
WITHOUT it keep the original S0 behaviour (no protect, no auto-refresh).
"""
import types

import pytest

import config_manager


# ── helpers ──────────────────────────────────────────────────────────────────

class _NullLogger:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


def _cfg(**overrides):
    base = {"use_ws_runner": True}
    base.update(overrides)
    return config_manager.DeviceConfig.from_dict(base)


PHONE_PID = 89565100511322


@pytest.fixture
def svc(monkeypatch):
    """Import the service and silence bot_state.update_state side effects."""
    import runtime_services.ws_runner_service as svc
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    return svc


# ── _ensure_token: adb-reachable mints, offline reuses cache ─────────────────

def test_ensure_token_refreshes_when_adb_reachable(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_load_get_adb_devices", lambda: (lambda: ["ws-phone"]))
    monkeypatch.setattr(svc, "_load_refresh_creds",
                        lambda: (lambda ip: calls.append(ip)))

    ok = svc._ensure_token("ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger())

    assert ok is True
    assert calls == ["ws-phone"]   # fresh ticket minted


def test_ensure_token_uses_cache_when_not_reachable(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_load_get_adb_devices", lambda: (lambda: []))  # unreachable
    monkeypatch.setattr(svc, "_load_refresh_creds",
                        lambda: (lambda ip: calls.append(ip)))

    ok = svc._ensure_token("ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger())

    assert ok is True       # cached token assumed usable
    assert calls == []      # refresh NOT attempted (phone offline)


def test_ensure_token_refresh_failure_returns_false(svc, monkeypatch):
    def boom(ip):
        raise RuntimeError("adb_token_login failed")

    monkeypatch.setattr(svc, "_load_get_adb_devices", lambda: (lambda: ["ws-phone"]))
    monkeypatch.setattr(svc, "_load_refresh_creds", lambda: boom)

    ok = svc._ensure_token("ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger())

    assert ok is False      # reachable but mint failed → caller stays in refresh mode


# ── _protected_player_online: busy / free / undetermined / no-target ─────────

def test_protected_returns_false_without_target_pid(svc, monkeypatch):
    # No mailbox call must happen when there is nothing to protect.
    called = {"submit": False}
    monkeypatch.setattr(svc.bot_state, "submit_online_check_request",
                        lambda *a, **k: called.__setitem__("submit", True) or "id")
    assert svc._protected_player_online("ws-plain", _cfg(), _NullLogger()) is False
    assert called["submit"] is False


def test_protected_true_when_player_online(svc, monkeypatch):
    monkeypatch.setattr(svc.bot_state, "submit_online_check_request",
                        lambda *, requester_ip, target_pid: "req-1")
    monkeypatch.setattr(svc.bot_state, "wait_online_check_result",
                        lambda req_id, timeout_sec: {"status": "done", "result_busy": True})
    result = svc._protected_player_online(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )
    assert result is True


def test_protected_false_when_player_offline(svc, monkeypatch):
    monkeypatch.setattr(svc.bot_state, "submit_online_check_request",
                        lambda *, requester_ip, target_pid: "req-2")
    monkeypatch.setattr(svc.bot_state, "wait_online_check_result",
                        lambda req_id, timeout_sec: {"status": "done", "result_busy": False})
    result = svc._protected_player_online(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )
    assert result is False


def test_protected_true_when_undetermined_timeout(svc, monkeypatch):
    """status != done (timeout / no checker) → conservative online → skip."""
    monkeypatch.setattr(svc.bot_state, "submit_online_check_request",
                        lambda *, requester_ip, target_pid: "req-3")
    monkeypatch.setattr(svc.bot_state, "wait_online_check_result",
                        lambda req_id, timeout_sec: {"status": "pending", "error": ""})
    result = svc._protected_player_online(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )
    assert result is True


def test_protected_true_when_mailbox_raises(svc, monkeypatch):
    def boom(**k):
        raise RuntimeError("mailbox down")

    monkeypatch.setattr(svc.bot_state, "submit_online_check_request", boom)
    result = svc._protected_player_online(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )
    assert result is True   # any error → conservative skip


# ── cycle: protection gates run_device ───────────────────────────────────────

def _stub_run_device(svc, monkeypatch, *, login_ok=True):
    calls = []

    def fake_run_device(ip, *, spend=False, sweep_list=None, open_lamp=False):
        calls.append(ip)
        return types.SimpleNamespace(device=ip, login_ok=login_ok, tasks={}, errors={})

    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    return calls


def test_cycle_skips_run_device_when_player_online(svc, monkeypatch):
    calls = _stub_run_device(svc, monkeypatch)
    monkeypatch.setattr(svc, "_protected_player_online", lambda ip, cfg, lg: True)

    report = svc.run_ws_device_cycle(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )

    assert report is None     # skipped
    assert calls == []        # run_device NOT called — no kick


def test_cycle_runs_run_device_when_player_offline(svc, monkeypatch):
    calls = _stub_run_device(svc, monkeypatch)
    monkeypatch.setattr(svc, "_protected_player_online", lambda ip, cfg, lg: False)

    report = svc.run_ws_device_cycle(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )

    assert report is not None and report.login_ok is True
    assert calls == ["ws-phone"]


def test_cycle_skips_when_undetermined(svc, monkeypatch):
    calls = _stub_run_device(svc, monkeypatch)
    monkeypatch.setattr(svc, "_protected_player_online", lambda ip, cfg, lg: True)  # None folded to True
    report = svc.run_ws_device_cycle(
        "ws-phone", _cfg(online_check_target_pid=PHONE_PID), _NullLogger()
    )
    assert report is None
    assert calls == []


# ── _is_token_invalid: only an explicit login_ok=False report counts ─────────

def test_is_token_invalid_distinguishes_skip_from_failure(svc):
    assert svc._is_token_invalid(None) is False  # skip / exception is not expiry
    assert svc._is_token_invalid(
        types.SimpleNamespace(login_ok=True)) is False
    assert svc._is_token_invalid(
        types.SimpleNamespace(login_ok=False)) is True


# ── loop: bootstrap token on first wake, then run ────────────────────────────

class _Stop(Exception):
    pass


def _patch_loop_scaffolding(svc, monkeypatch, *, cfg, force_sleep=False):
    """Common loop monkeypatches; sleep raises _Stop after each wake."""
    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: force_sleep)
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config", lambda ip: cfg)

    import runtime_services.startup_sleep as ss
    import runtime_services.sleep_service as sl
    monkeypatch.setattr(ss, "_handle_startup_sleep", lambda ip, lg: None)

    sleep_count = {"n": 0}

    def fake_sleep(ip, lg, **k):
        sleep_count["n"] += 1
        raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)
    return sleep_count


def test_loop_bootstraps_token_then_runs(svc, monkeypatch):
    cfg = _cfg(online_check_target_pid=PHONE_PID)
    _patch_loop_scaffolding(svc, monkeypatch, cfg=cfg)

    ensure_calls = []
    cycle_calls = []
    monkeypatch.setattr(svc, "_ensure_token",
                        lambda ip, c, lg, force=False: ensure_calls.append((ip, force)) or True)
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, c, lg: cycle_calls.append(ip)
                        or types.SimpleNamespace(login_ok=True))

    svc.run_ws_device_loop("ws-phone", _NullLogger())

    assert ensure_calls == [("ws-phone", False)]  # bootstrap once, not forced
    assert cycle_calls == ["ws-phone"]            # ran after bootstrap


def test_loop_no_target_pid_does_not_bootstrap_token(svc, monkeypatch):
    """S0 parity: a ws_runner device without target_pid never auto-fetches."""
    cfg = _cfg()  # no online_check_target_pid
    _patch_loop_scaffolding(svc, monkeypatch, cfg=cfg)

    ensure_calls = []
    cycle_calls = []
    monkeypatch.setattr(svc, "_ensure_token",
                        lambda ip, c, lg, force=False: ensure_calls.append(ip) or True)
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, c, lg: cycle_calls.append(ip)
                        or types.SimpleNamespace(login_ok=True))

    svc.run_ws_device_loop("ws-plain", _NullLogger())

    assert ensure_calls == []           # never minted a token
    assert cycle_calls == ["ws-plain"]  # ran cycle as before


# ── loop: token-expiry state machine ─────────────────────────────────────────

def test_loop_enters_refresh_mode_then_recovers(svc, monkeypatch):
    """login_ok=False → refresh mode; once adb-reachable again → resume.

    Wakes (sleep raises _Stop only after a fixed number) so we can observe the
    transition across iterations:
      wake 1: bootstrap + run → login_ok=False → need_refresh=True
      wake 2: refresh mode, adb still unreachable → no run
      wake 3: refresh mode, adb now reachable → re-mint + resume run
    """
    cfg = _cfg(online_check_target_pid=PHONE_PID)

    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: False)
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config", lambda ip: cfg)

    import runtime_services.startup_sleep as ss
    import runtime_services.sleep_service as sl
    monkeypatch.setattr(ss, "_handle_startup_sleep", lambda ip, lg: None)

    # _is_adb_reachable is ONLY consulted inside the refresh branch (wakes 2+).
    # wake1 bootstraps + runs (fails) without checking reachability. So the
    # timeline of reachability probes is: wake2 unreachable, wake3 reachable.
    reachable_seq = iter([False, True])
    monkeypatch.setattr(svc, "_is_adb_reachable", lambda ip: next(reachable_seq))

    # _ensure_token: always "have a token" (True); record force flag.
    ensure_calls = []
    monkeypatch.setattr(svc, "_ensure_token",
                        lambda ip, c, lg, force=False: ensure_calls.append(force) or True)

    # First real run fails login; after recovery the run succeeds.
    run_results = iter([
        types.SimpleNamespace(login_ok=False, tasks={}, errors={"login": "expired"}),
        types.SimpleNamespace(login_ok=True, tasks={}, errors={}),
    ])
    cycle_calls = []

    def fake_cycle(ip, c, lg):
        cycle_calls.append(ip)
        return next(run_results)

    monkeypatch.setattr(svc, "run_ws_device_cycle", fake_cycle)

    wake = {"n": 0}

    def fake_sleep(ip, lg, **k):
        wake["n"] += 1
        if wake["n"] >= 3:
            raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    svc.run_ws_device_loop("ws-phone", _NullLogger())

    # wake1 ran (failed), wake2 skipped run (refresh mode, unreachable),
    # wake3 re-minted + ran (recovered) → 2 cycle calls total.
    assert cycle_calls == ["ws-phone", "ws-phone"]
    # bootstrap (force=False) on wake1; wake2 force=True; wake3 force=True.
    assert ensure_calls == [False, True, True]


def test_loop_stays_in_refresh_mode_while_unreachable(svc, monkeypatch):
    """Once token is dead and phone stays offline, run_device is never retried."""
    cfg = _cfg(online_check_target_pid=PHONE_PID)

    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: False)
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config", lambda ip: cfg)

    import runtime_services.startup_sleep as ss
    import runtime_services.sleep_service as sl
    monkeypatch.setattr(ss, "_handle_startup_sleep", lambda ip, lg: None)

    # wake1 bootstraps + runs (no reachability probe). Wakes 2,3,4 are in the
    # refresh branch and each probes reachability — all offline here.
    monkeypatch.setattr(svc, "_is_adb_reachable", lambda ip: False)
    monkeypatch.setattr(svc, "_ensure_token", lambda ip, c, lg, force=False: True)

    cycle_calls = []

    def fake_cycle(ip, c, lg):
        cycle_calls.append(ip)
        return types.SimpleNamespace(login_ok=False, tasks={}, errors={"login": "expired"})

    monkeypatch.setattr(svc, "run_ws_device_cycle", fake_cycle)

    wake = {"n": 0}

    def fake_sleep(ip, lg, **k):
        wake["n"] += 1
        if wake["n"] >= 4:
            raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    svc.run_ws_device_loop("ws-phone", _NullLogger())

    # Only the first wake runs the cycle (and it fails). Wakes 2-4 stay in
    # refresh mode with the phone offline → no further run_device.
    assert cycle_calls == ["ws-phone"]


def test_loop_force_sleep_skips_everything(svc, monkeypatch):
    """Force-sleep short-circuits before token/protection work, like S0."""
    cfg = _cfg(online_check_target_pid=PHONE_PID)
    seen = {"policy": None}

    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: True)
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config", lambda ip: cfg)

    import runtime_services.startup_sleep as ss
    import runtime_services.sleep_service as sl
    monkeypatch.setattr(ss, "_handle_startup_sleep", lambda ip, lg: None)

    ensure_calls = []
    cycle_calls = []
    monkeypatch.setattr(svc, "_ensure_token",
                        lambda ip, c, lg, force=False: ensure_calls.append(ip) or True)
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, c, lg: cycle_calls.append(ip))

    def fake_sleep(ip, lg, **k):
        seen["policy"] = k.get("sleep_policy")
        raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    svc.run_ws_device_loop("ws-phone", _NullLogger())

    assert ensure_calls == []                # no token work under force-sleep
    assert cycle_calls == []                 # no run
    assert seen["policy"] == "force_sleep"

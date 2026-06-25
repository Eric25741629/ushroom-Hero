"""Online monitor must never connect as a human-played account (異地登入 kick).

Regression for: 真人在手機登入立刻被 WS 彈出. The monitor used to default its
persistent detector to the phone account (fc65396d_u999) and reclaim it on every
disconnect, fighting the human. The detector must default to an emulator bot
account and refuse to log in as any protected (human-played) roleId.
"""
from __future__ import annotations

import json

from ws_token import online_monitor as om


class _FakeCreds:
    def __init__(self, role_id):
        self.role_id = role_id


def test_default_preferred_is_not_the_phone():
    mon = om.OnlineMonitor()
    assert mon._preferred == "emulator-5554"


def test_connect_refuses_protected_account(monkeypatch):
    # load_creds yields the protected (human) roleId; monitor must refuse before
    # ever constructing a WS client (constructing == logging in == kicking human).
    monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds(89565100509472))
    built = []
    monkeypatch.setattr(om, "WSGameClient", lambda *a, **k: built.append(1))
    mon = om.OnlineMonitor(protected_role_ids=frozenset({89565100509472}))

    client = mon._connect("fc65396d_u999")

    assert client is None
    assert built == []


def test_connect_allows_unprotected_account(monkeypatch):
    monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds(89555436834913))

    class _C:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            return None

    monkeypatch.setattr(om, "WSGameClient", _C)
    mon = om.OnlineMonitor(protected_role_ids=frozenset({89565100509472}))

    client = mon._connect("emulator-5554")

    assert client is not None


def _states(monkeypatch, mapping):
    import bot_state
    monkeypatch.setattr(bot_state, "get_all_states", lambda: mapping)


def _allow_creds(monkeypatch):
    """All synthetic devices have loadable (unprotected) creds."""
    monkeypatch.setattr(om, "load_creds", lambda dev: _FakeCreds(1))


def test_select_detector_prefers_idle_preferred(monkeypatch):
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5556": {"task": "休眠中"},
    })
    assert mon._select_detector() == "emulator-5554"


def test_select_detector_hands_off_when_preferred_busy(monkeypatch):
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "喚醒檢查"},  # busy bot → never use as detector
        "emulator-5556": {"task": "休眠中"},     # idle → hand off here
    })
    assert mon._select_detector() == "emulator-5556"


def test_select_detector_none_when_all_busy(monkeypatch):
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "挖礦"},
        "emulator-5556": {"task": "農場"},
    })
    assert mon._select_detector() is None


def test_select_detector_excludes_creds_less_device(monkeypatch):
    """5558 (no creds) is never a detector — only ever a monitored target."""
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5558"}

    def _load(dev):
        if dev == "emulator-5558":
            raise FileNotFoundError
        return _FakeCreds(1)

    monkeypatch.setattr(om, "load_creds", _load)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5558": {"task": "休眠中"},
    })
    assert mon._select_detector() == "emulator-5554"

    mon._role_map = {2: "emulator-5558"}  # only the creds-less device
    assert mon._select_detector() is None


def test_select_detector_sticky_keeps_current(monkeypatch):
    """Once on a detector, stay — never bounce back to preferred when it frees."""
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},  # preferred is free…
        "emulator-5556": {"task": "休眠中"},
    })
    # …but current 5556 is still eligible → keep it (sticky).
    assert mon._select_detector(current="emulator-5556", snapshot=None) == "emulator-5556"


def test_select_detector_reselect_only_picks_snapshot_offline(monkeypatch):
    """Reselect connects only to accounts the snapshot confirms OFFLINE, even
    if that means skipping the preferred (which a human is on)."""
    import config_manager
    mon = om.OnlineMonitor(preferred="emulator-5554")
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中"},
        "emulator-5556": {"task": "休眠中"},
    })
    monkeypatch.setattr(config_manager, "get_device_role_id",
                        lambda dev: {"emulator-5554": 11, "emulator-5556": 22}.get(dev))
    snap = om.Snapshot(detector="x", timestamp=1000.0, entries=(
        om.StatusEntry(11, "a", True, None),    # 5554 online (human) → exclude
        om.StatusEntry(22, "b", False, None),   # 5556 offline → safe
    ))
    assert mon._select_detector(current=None, snapshot=snap) == "emulator-5556"


def test_select_detector_excludes_about_to_wake(monkeypatch):
    """A candidate whose bot wakes within the lead window is skipped."""
    mon = om.OnlineMonitor(preferred="emulator-5554", now=lambda: 1000.0)
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中", "next_wake_at": 1030.0},   # 30s < lead → skip
        "emulator-5556": {"task": "休眠中", "next_wake_at": 9_999_999.0},
    })
    assert mon._select_detector() == "emulator-5556"


def test_select_detector_sticky_breaks_when_current_about_to_wake(monkeypatch):
    """Current detector about to run its own script → hand off before it wakes."""
    mon = om.OnlineMonitor(preferred="emulator-5554", now=lambda: 1000.0)
    mon._role_map = {1: "emulator-5554", 2: "emulator-5556"}
    _allow_creds(monkeypatch)
    _states(monkeypatch, {
        "emulator-5554": {"task": "休眠中", "next_wake_at": 1030.0},  # current, waking soon
        "emulator-5556": {"task": "休眠中"},
    })
    assert mon._select_detector(current="emulator-5554", snapshot=None) == "emulator-5556"


def test_last_switch_records_transition():
    clock = {"t": 100.0}
    mon = om.OnlineMonitor(now=lambda: clock["t"])
    assert mon.last_switch is None

    mon._set_active("emulator-5554")            # None -> 5554
    assert mon.last_switch == {"frm": None, "to": "emulator-5554", "ts": 100.0}

    clock["t"] = 160.0
    mon._set_active("emulator-5556")            # 5554 -> 5556
    sw = mon.last_switch
    assert sw["frm"] == "emulator-5554" and sw["to"] == "emulator-5556"
    assert sw["ts"] == 160.0

    clock["t"] = 200.0
    mon._set_active("emulator-5556")            # no-op (unchanged) → not recorded
    assert mon.last_switch["ts"] == 160.0


def test_switch_cooldown_blocks_rapid_reconnect():
    clock = {"t": 1_000_000.0}
    mon = om.OnlineMonitor(switch_cooldown_sec=300.0, now=lambda: clock["t"])

    # Nothing switched yet → first connect is allowed immediately.
    assert mon._switch_allowed() is True

    # Just switched → blocked until the 5-min cooldown elapses.
    mon._last_switch_ts = clock["t"]
    assert mon._switch_allowed() is False
    clock["t"] += 299
    assert mon._switch_allowed() is False
    clock["t"] += 2  # 301s total
    assert mon._switch_allowed() is True


def test_discover_role_map_excludes_protected(tmp_path, monkeypatch):
    import config_manager
    monkeypatch.setattr(config_manager, "load_config", lambda: {})

    def _write(name, rid):
        (tmp_path / f"_auth_capture_{name}.json").write_text(
            json.dumps({"creds": {"roleId": rid}}), encoding="utf-8")

    _write("emulator-5554", 89555436834913)
    _write("fc65396d_u999", 89565100509472)

    mapping = om.discover_role_map(
        auth_dir=tmp_path, protected_role_ids=frozenset({89565100509472}))

    assert 89555436834913 in mapping
    assert 89565100509472 not in mapping

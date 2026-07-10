"""Task 1: /api/status lease 欄位 + ws_session precheck 的純邏輯測試（不起 Flask）。"""
from runtime_services.session_registry import Channel, Lease, Owner


def _lease(device, owner, label=""):
    return Lease(device=device, owner=owner, channel=Channel.WS,
                 label=label, acquired_at=0.0, role_id=None)


# --- routes_status._lease_fields -------------------------------------------

def test_lease_fields_none_when_no_lease():
    from control_panel.routes_status import _lease_fields
    assert _lease_fields({}, "emulator-5554", "emulator-5554") == {
        "lease_owner": None, "lease_label": ""}


def test_lease_fields_maps_owner_value_and_label():
    from control_panel.routes_status import _lease_fields
    leases = {"emulator-5554": _lease("emulator-5554", Owner.TOOL, "工具")}
    assert _lease_fields(leases, "emulator-5554", "emulator-5554") == {
        "lease_owner": "tool", "lease_label": "工具"}


def test_lease_fields_falls_back_to_real_ip_key():
    from control_panel.routes_status import _lease_fields
    leases = {"5554": _lease("5554", Owner.MOUNT_TRACKER)}
    assert _lease_fields(leases, "127.0.0.1:5554", "5554")["lease_owner"] == "mount_tracker"


# --- ws_session.precheck -----------------------------------------------------

def test_precheck_reports_lease_and_online(monkeypatch):
    from control_panel import ws_session
    monkeypatch.setattr(ws_session.registry, "peek",
                        lambda d: _lease(d, Owner.ONLINE_MONITOR, "偵測"))
    monkeypatch.setattr(ws_session, "_precheck_account_online", lambda d: True)
    out = ws_session.precheck("emulator-5554")
    assert out == {"lease": {"owner": "online_monitor", "label": "偵測"},
                   "account_online": True}


def test_precheck_empty_when_idle(monkeypatch):
    from control_panel import ws_session
    monkeypatch.setattr(ws_session.registry, "peek", lambda d: None)
    monkeypatch.setattr(ws_session, "_precheck_account_online", lambda d: None)
    assert ws_session.precheck("emulator-5554") == {
        "lease": None, "account_online": None}

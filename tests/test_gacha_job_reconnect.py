"""_run_gacha_job robustness: reconnect-retry on WS loss + true draw counts.

The drain path fires hundreds of back-to-back 0x0902 draws over ~10+ minutes;
a single transport drop must not abort the whole job (the decoration executor
already reconnects — live 2026-07-08 the same drop killed a drain run).

Also: the 0x0902 reply aggregates rewards into stacks (a 999 draw came back as
24 stacks live on 5554), so ``total`` must count draws (bundle sizes), not
reply stacks.
"""
import control_panel.routes_gacha_tools as routes
from control_panel import tools_optimize_jobs as jobs

_CONN_ERR = "WebSocketConnectionClosedException: socket is already closed."


class _FakeClient:
    pass


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _ok(drawn=24):
    return {"ok": True, "drawn": drawn, "remaining": None,
            "rejected": False, "error_code": None}, None


def _reject(code=6):
    return {"ok": False, "drawn": 0, "remaining": None,
            "rejected": True, "error_code": code}, None


def _setup(monkeypatch, script, *, probes=None, clock=None,
           get_client_after_reconnect=True):
    """Wire fakes: scripted _draw_once + ws_session ensure/get_client."""
    calls = {"draws": [], "draw_started": [], "ensure": 0}
    clock = clock or _FakeClock()
    probe_script = list(probes) if probes is not None else []

    def fake_draw_once(client, draw_type, count):
        calls["draws"].append((draw_type, count))
        calls["draw_started"].append(clock.monotonic())
        return script.pop(0)

    def fake_read_probe(client, draw_type, *, timeout=None):
        if probe_script:
            return probe_script.pop(0)
        return routes.gacha_logic.GachaProbe(10_000_000, 1000)

    def fake_ensure(ip):
        calls["ensure"] += 1
        return {"status": "ok", "connected": True}

    def fake_get_client(ip):
        return _FakeClient() if get_client_after_reconnect else None

    monkeypatch.setattr(routes, "_draw_once", fake_draw_once)
    monkeypatch.setattr(routes.gacha_logic, "read_probe", fake_read_probe)
    monkeypatch.setattr(routes.ws_session, "ensure", fake_ensure)
    monkeypatch.setattr(routes.ws_session, "get_client", fake_get_client)
    monkeypatch.setattr(routes, "time", clock, raising=False)
    return calls


def test_drain_uses_probe_balance_and_stops_before_unaffordable_draw(monkeypatch):
    calls = _setup(
        monkeypatch,
        [_ok()],
        probes=[routes.gacha_logic.GachaProbe(800, 1000)],
    )
    jid = jobs._new_job()

    routes._run_gacha_job(jid, "emulator-5554", 1, "drain", 0, 1)

    assert calls["draws"] == [(1, 999)]
    assert jobs._jobs[jid]["result"]["stopped_reason"] == "exhausted"


def test_draw_starts_are_at_least_one_second_apart(monkeypatch):
    clock = _FakeClock()
    calls = _setup(
        monkeypatch,
        [_ok(), _ok()],
        probes=[routes.gacha_logic.GachaProbe(1600, 1000)],
        clock=clock,
    )
    jid = jobs._new_job()

    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 2)

    assert calls["draw_started"] == [0.0, 1.0]
    assert clock.sleeps == [1.0]


def test_drain_reconnects_and_retries_on_conn_lost(monkeypatch):
    script = [
        (None, _CONN_ERR),   # 999 draw: transport dies
        _ok(),               # retry after reconnect: lands
        _reject(),           # next 999: insufficient -> step down
        _reject(),           # 35 rejected
        _reject(),           # 15 rejected -> exhausted
    ]
    calls = _setup(monkeypatch, script)
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "drain", 0, 1)
    job = jobs._jobs[jid]
    assert job["status"] == "done", job.get("error")
    assert calls["ensure"] == 1, "conn loss must trigger exactly one reconnect"
    res = job["result"]
    assert res["stopped_reason"] == "exhausted"
    assert res["total"] == 999, "total must count draws, not reply stacks"
    assert res["batches_done"] == 1


def test_drain_conn_lost_twice_stops_cleanly(monkeypatch):
    script = [
        (None, _CONN_ERR),   # draw dies
        (None, _CONN_ERR),   # retry dies too -> stop, no infinite loop
    ]
    calls = _setup(monkeypatch, script)
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "drain", 0, 1)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert calls["ensure"] == 1
    assert job["result"]["stopped_reason"].startswith("error:")
    assert job["result"]["total"] == 0


def test_fixed_mode_reconnects_and_counts_draws(monkeypatch):
    script = [
        _ok(),               # batch 1 ok
        (None, _CONN_ERR),   # batch 2: transport dies
        _ok(),               # retry lands
    ]
    calls = _setup(monkeypatch, script)
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 2, "fixed", 999, 2)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert calls["ensure"] == 1
    res = job["result"]
    assert res["stopped_reason"] is None
    assert res["total"] == 1998, "999×2 batches = 1998 draws (not 48 stacks)"
    assert res["batches_done"] == 2


def test_non_conn_transport_error_stops_without_reconnect(monkeypatch):
    script = [
        (None, "WSTimeoutError: no response for cmd=2306"),
    ]
    calls = _setup(monkeypatch, script)
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "drain", 0, 1)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert calls["ensure"] == 0, "a plain timeout must not trigger reconnect"
    assert job["result"]["stopped_reason"].startswith("error:")

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
    calls = {"draws": [], "draw_started": [], "ensure": 0, "disconnect": 0,
             "order": [], "draw_clients": []}
    clock = clock or _FakeClock()
    probe_script = list(probes) if probes is not None else []
    reconnected_client = _FakeClient()

    def fake_draw_once(client, draw_type, count):
        calls["draws"].append((draw_type, count))
        calls["draw_started"].append(clock.monotonic())
        calls["draw_clients"].append(client)
        calls["order"].append("draw")
        return script.pop(0)

    def fake_read_probe(client, draw_type, *, timeout=None):
        calls["order"].append("probe")
        if probe_script:
            return probe_script.pop(0)
        return routes.gacha_logic.GachaProbe(10_000_000, 1000)

    def fake_ensure(ip):
        calls["ensure"] += 1
        calls["order"].append("ensure")
        return {"status": "ok", "connected": True}

    def fake_disconnect(ip):
        calls["disconnect"] += 1
        calls["order"].append("disconnect")
        return {"status": "ok"}

    def fake_get_client(ip):
        return reconnected_client if get_client_after_reconnect else None

    monkeypatch.setattr(routes, "_draw_once", fake_draw_once)
    monkeypatch.setattr(routes.gacha_logic, "read_probe", fake_read_probe)
    monkeypatch.setattr(routes.ws_session, "ensure", fake_ensure)
    monkeypatch.setattr(routes.ws_session, "disconnect", fake_disconnect)
    monkeypatch.setattr(routes.ws_session, "get_client", fake_get_client)
    monkeypatch.setattr(routes, "time", clock, raising=False)
    calls["reconnected_client"] = reconnected_client
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
    ]
    probe = routes.gacha_logic.GachaProbe(800, 1000)
    calls = _setup(monkeypatch, script, probes=[probe, probe])
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
    probe = routes.gacha_logic.GachaProbe(800, 1000)
    calls = _setup(monkeypatch, script, probes=[probe, probe, probe])
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "drain", 0, 1)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert calls["ensure"] == 2  # retry socket也失敗，需再重連只讀 probe 判定
    assert job["result"]["stopped_reason"] == "unconfirmed"
    assert job["result"]["total"] == 0


def test_fixed_mode_reconnects_and_counts_draws(monkeypatch):
    script = [
        _ok(),               # batch 1 ok
        (None, _CONN_ERR),   # batch 2: transport dies
        _ok(),               # retry lands
    ]
    calls = _setup(
        monkeypatch,
        script,
        probes=[
            routes.gacha_logic.GachaProbe(1600, 1000),
            routes.gacha_logic.GachaProbe(800, 1999),
        ],
    )
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 2, "fixed", 999, 2)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert calls["ensure"] == 1
    res = job["result"]
    assert res["stopped_reason"] is None
    assert res["total"] == 1998, "999×2 batches = 1998 draws (not 48 stacks)"
    assert res["batches_done"] == 2


def test_timeout_confirmed_landed_counts_without_retry(monkeypatch):
    calls = _setup(
        monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306")],
        probes=[
            routes.gacha_logic.GachaProbe(800, 1000),
            routes.gacha_logic.GachaProbe(0, 1999),
        ],
    )
    jid = jobs._new_job()

    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)

    job = jobs._jobs[jid]
    assert calls["draws"] == [(1, 999)]
    # timeout must force-reconnect (drop old socket) before probing, even when
    # the draw landed — a late 0x0902 reply otherwise misroutes to a later waiter.
    assert calls["disconnect"] == 1
    assert calls["ensure"] == 1
    assert job["result"]["total"] == 999
    assert job["result"]["stopped_reason"] is None
    assert any("狀態探測確認成功" in line for line in job["log"])


def test_timeout_confirmed_not_landed_retries_once(monkeypatch):
    calls = _setup(
        monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306"), _ok()],
        probes=[
            routes.gacha_logic.GachaProbe(800, 1000),
            routes.gacha_logic.GachaProbe(800, 1000),
        ],
    )
    jid = jobs._new_job()

    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)

    job = jobs._jobs[jid]
    assert calls["draws"] == [(1, 999), (1, 999)]
    assert calls["draw_started"] == [0.0, 1.0]  # reconnect adds no clock time
    assert calls["disconnect"] == 1
    assert calls["ensure"] == 1
    assert job["result"]["total"] == 999
    assert job["result"]["stopped_reason"] is None
    assert any("安全重試一次" in line for line in job["log"])


def test_timeout_conflicting_probe_stops_unconfirmed(monkeypatch):
    calls = _setup(
        monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306")],
        probes=[
            routes.gacha_logic.GachaProbe(800, 1000),
            routes.gacha_logic.GachaProbe(0, 1000),
        ],
    )
    jid = jobs._new_job()

    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)

    job = jobs._jobs[jid]
    assert calls["draws"] == [(1, 999)]
    assert calls["disconnect"] == 1
    assert calls["ensure"] == 1
    assert job["status"] == "done"
    assert job["result"]["stopped_reason"] == "unconfirmed"
    assert job["result"]["total"] == 0


def test_timeout_disconnects_before_probe_and_retries_on_new_client(monkeypatch):
    """逾時後,必須先斷開舊連線並重連,才讀券數/重送。

    這是修好與 buggy 的分水嶺:舊行為在同一條 socket 上直接探針/重送,遲到的
    0x0902 回包會錯配給下一抽的 waiter(client.py 只用 cmd 關聯);強制重連丟棄
    舊 socket 後,遲到回包無處錯配。
    """
    calls = _setup(
        monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306"), _ok()],
        probes=[
            routes.gacha_logic.GachaProbe(800, 1000),   # initial
            routes.gacha_logic.GachaProbe(800, 1000),   # confirm: not landed
        ],
    )
    jid = jobs._new_job()

    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)

    job = jobs._jobs[jid]
    # order: initial probe, timed-out draw, THEN disconnect before confirm probe,
    # then ensure, confirm probe, then the retry draw.
    assert calls["order"] == [
        "probe", "draw", "disconnect", "ensure", "probe", "draw"]
    # the disconnect must precede both the confirm probe and the retry draw.
    d_idx = calls["order"].index("disconnect")
    assert d_idx < calls["order"].index("ensure")
    assert "probe" in calls["order"][d_idx:]
    # retry draw runs on the reconnected client, not the original.
    assert calls["draw_clients"][1] is calls["reconnected_client"]
    assert job["result"]["total"] == 999
    assert job["result"]["stopped_reason"] is None

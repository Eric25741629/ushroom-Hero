import sys
from types import SimpleNamespace

from flask import Flask

import bot_state
from control_panel import routes_web_session


def _client():
    app = Flask(__name__)
    app.register_blueprint(routes_web_session.bp)
    return app.test_client()


def _web_cfg(*, special=True):
    return {
        "backend": "web_h5",
        "special_wanshen_account": special,
        "special_wanshen_enabled": special,
    }


def _stub_launch_dependencies(monkeypatch, cfg):
    monkeypatch.setattr(
        routes_web_session.config_manager, "get_device_config", lambda _ip: cfg
    )
    monkeypatch.setattr(
        routes_web_session.config_manager,
        "get_global_config",
        lambda: {"manual_launch_force_headful": True},
    )


def test_dead_wanshen_thread_uses_standalone_worker(monkeypatch):
    _stub_launch_dependencies(monkeypatch, _web_cfg())
    calls = []
    monkeypatch.setattr(bot_state, "has_web_launch_consumer", lambda _ip: False)
    monkeypatch.setattr(
        bot_state,
        "complete_web_launch_request",
        lambda ip, **kwargs: calls.append(("complete", ip, kwargs)),
    )
    monkeypatch.setattr(
        bot_state,
        "request_web_launch",
        lambda *args, **kwargs: calls.append(("mailbox", args, kwargs)),
    )
    monkeypatch.setattr(
        routes_web_session,
        "_start_web_login_thread",
        lambda ip, payload: calls.append(("standalone", ip, payload)) or True,
    )

    response = _client().post(
        "/api/web_launch/web-002",
        json={"manual_hold_until_closed": False},
    )

    assert response.status_code == 200
    assert response.get_json()["mode"] == "standalone"
    assert any(call[0] == "complete" for call in calls)
    assert any(call[0] == "standalone" for call in calls)
    assert not any(call[0] == "mailbox" for call in calls)


def test_live_wanshen_thread_keeps_mailbox_path(monkeypatch):
    _stub_launch_dependencies(monkeypatch, _web_cfg())
    calls = []
    monkeypatch.setattr(bot_state, "has_web_launch_consumer", lambda _ip: True)
    monkeypatch.setattr(
        bot_state,
        "request_web_launch",
        lambda ip, payload=None: calls.append((ip, payload)),
    )
    monkeypatch.setattr(
        routes_web_session,
        "_start_web_login_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("存活中的 consumer 不得啟動 standalone worker")
        ),
    )

    response = _client().post("/api/web_launch/web-002", json={})

    assert response.status_code == 200
    assert response.get_json().get("mode") is None
    assert len(calls) == 1


def test_dead_normal_web_device_is_not_broadly_rerouted(monkeypatch):
    _stub_launch_dependencies(monkeypatch, _web_cfg(special=False))
    calls = []
    monkeypatch.setattr(bot_state, "has_web_launch_consumer", lambda _ip: False)
    monkeypatch.setattr(
        bot_state,
        "request_web_launch",
        lambda ip, payload=None: calls.append((ip, payload)),
    )
    monkeypatch.setattr(
        routes_web_session,
        "_start_web_login_thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("一般 web_h5 不得因 OFFLINE 被前端推測成 standalone")
        ),
    )

    response = _client().post("/api/web_launch/web-003", json={})

    assert response.status_code == 200
    assert len(calls) == 1


def test_web_login_worker_reservation_is_atomic(monkeypatch):
    ip = "web-atomic-test"
    routes_web_session._web_login_state.pop(ip, None)

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(
        routes_web_session, "threading", SimpleNamespace(Thread=FakeThread)
    )
    monkeypatch.setitem(
        sys.modules,
        "control_panel_app",
        SimpleNamespace(_run_web_login_worker=lambda *_args, **_kwargs: None),
    )

    try:
        assert routes_web_session._start_web_login_thread(ip, {}) is True
        assert routes_web_session._start_web_login_thread(ip, {}) is False
        assert routes_web_session._web_login_state[ip]["running"] is True
    finally:
        routes_web_session._web_login_state.pop(ip, None)


def test_web_launch_consumer_lifecycle_state():
    ip = "web-consumer-test"
    bot_state.set_web_launch_consumer_active(ip, False)
    assert bot_state.has_web_launch_consumer(ip) is False

    bot_state.set_web_launch_consumer_active(ip, True)
    assert bot_state.has_web_launch_consumer(ip) is True

    bot_state.set_web_launch_consumer_active(ip, False)
    assert bot_state.has_web_launch_consumer(ip) is False

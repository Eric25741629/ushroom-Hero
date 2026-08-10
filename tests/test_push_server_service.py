from types import SimpleNamespace


def test_ensure_push_server_started_returns_true_when_port_is_open(monkeypatch):
    from runtime_services import push_server_service as service

    monkeypatch.setattr(service, "is_tcp_port_open", lambda *args, **kwargs: True)

    assert service.ensure_push_server_started("C:/missing") is True


def test_ensure_push_server_started_returns_false_when_app_is_missing(monkeypatch):
    from runtime_services import push_server_service as service

    monkeypatch.setattr(service, "is_tcp_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(service.os.path, "exists", lambda path: False)

    assert service.ensure_push_server_started("C:/missing") is False


def test_ensure_push_server_started_returns_false_when_popen_fails(monkeypatch):
    from runtime_services import push_server_service as service

    monkeypatch.setattr(service, "is_tcp_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(service.os.path, "exists", lambda path: True)

    def fail_popen(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(service.subprocess, "Popen", fail_popen)

    assert service.ensure_push_server_started("C:/base") is False


def test_ensure_push_server_started_returns_true_after_popen(monkeypatch):
    from runtime_services import push_server_service as service

    monkeypatch.setattr(service, "is_tcp_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(service.os.path, "exists", lambda path: True)
    monkeypatch.setattr(
        service.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=1234),
    )

    assert service.ensure_push_server_started("C:/base") is True

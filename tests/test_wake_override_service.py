from __future__ import annotations

import logging
import types


def test_apply_manual_wake_override_noop_without_override(monkeypatch):
    from runtime_services import wake_override_service as svc

    updates = []
    monkeypatch.setattr(
        svc,
        "bot_state",
        types.SimpleNamespace(
            consume_wake_override=lambda ip: None,
            update_state=lambda ip, **kw: updates.append({"ip": ip, **kw}),
        ),
    )

    wake_ts, should_wake = svc.apply_manual_wake_override(
        "emu-1",
        200.0,
        logging.getLogger("t"),
        task="休眠中",
    )

    assert wake_ts == 200.0
    assert should_wake is False
    assert updates == []


def test_apply_manual_wake_override_updates_state_for_future_override(monkeypatch):
    from runtime_services import wake_override_service as svc

    updates = []
    monkeypatch.setattr(svc.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        svc,
        "bot_state",
        types.SimpleNamespace(
            consume_wake_override=lambda ip: 130.0,
            update_state=lambda ip, **kw: updates.append({"ip": ip, **kw}),
        ),
    )

    wake_ts, should_wake = svc.apply_manual_wake_override(
        "emu-1",
        200.0,
        logging.getLogger("t"),
        task="休眠中",
    )

    assert wake_ts == 130.0
    assert should_wake is False
    assert updates == [
        {
            "ip": "emu-1",
            "task": "休眠中",
            "step": "手動調整喚醒：30 秒後開始",
            "next_wake_at": 130.0,
        }
    ]


def test_apply_manual_wake_override_reports_immediate_override(monkeypatch):
    from runtime_services import wake_override_service as svc

    updates = []
    monkeypatch.setattr(svc.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        svc,
        "bot_state",
        types.SimpleNamespace(
            consume_wake_override=lambda ip: 99.0,
            update_state=lambda ip, **kw: updates.append({"ip": ip, **kw}),
        ),
    )

    wake_ts, should_wake = svc.apply_manual_wake_override(
        "emu-1",
        200.0,
        logging.getLogger("t"),
        task="啟動後休眠",
    )

    assert wake_ts == 99.0
    assert should_wake is True
    assert updates == [
        {
            "ip": "emu-1",
            "task": "啟動後休眠",
            "step": "手動調整喚醒：立即開始",
            "next_wake_at": 99.0,
        }
    ]

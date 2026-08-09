"""共用 scheduler policy 的最小 contract 測試。"""
from __future__ import annotations

import datetime

from game_actions.scheduler_policy import SchedulerPolicy


def test_record_policy_preserves_standard_enabled_due_and_mark_flow():
    calls: list[tuple] = []
    cfg = {"enabled": True, "backend": "web_h5"}

    def get_config(ip):
        calls.append(("config", ip))
        return cfg

    def get_record(ip, *, name):
        calls.append(("record", ip, name))
        return None

    def expired(record, cooldown):
        calls.append(("expired", record, cooldown))
        return True

    def record_done(ip, *, name):
        calls.append(("done", ip, name))

    policy = SchedulerPolicy(
        enabled_key="enabled",
        backend="web_h5",
        record_key="demo_last_run",
        cooldown_seconds=20 * 3600,
    )

    assert policy.is_enabled("dev", get_device_config=get_config) is True
    assert policy.is_due(
        "dev",
        datetime.datetime(2026, 8, 10, 12),
        return_time=get_record,
        is_record_expired=expired,
    ) is True
    policy.mark_done("dev", time_recording=record_done)

    assert calls == [
        ("config", "dev"),
        ("record", "dev", "demo_last_run"),
        ("expired", None, 20 * 3600),
        ("done", "dev", "demo_last_run"),
    ]


def test_hook_policy_keeps_special_due_and_mark_semantics():
    marked: list[str] = []
    policy = SchedulerPolicy(
        enabled_hook=lambda ip: ip == "web-device",
        due_hook=lambda ip, now: now is not None and now.hour == 20,
        mark_done_hook=marked.append,
    )

    assert policy.is_enabled("web-device", get_device_config=lambda _ip: {}) is True
    assert policy.is_due("web-device", datetime.datetime(2026, 8, 10, 20)) is True
    assert policy.is_due("web-device", datetime.datetime(2026, 8, 10, 19)) is False
    policy.mark_done("web-device")
    assert marked == ["web-device"]

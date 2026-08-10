import sys
import types
from types import SimpleNamespace


def _import_scheduler(monkeypatch):
    monkeypatch.setitem(sys.modules, "new_battle", types.ModuleType("new_battle"))
    from game_actions import dungeon_scheduler

    return dungeon_scheduler


def _stub_weekly_runner(monkeypatch, runner):
    battle_pkg = types.ModuleType("battle")
    battle_pkg.__path__ = []
    weekly = types.ModuleType("battle.weekly_trials")
    weekly._run_pure_ws_wanshen = runner
    monkeypatch.setitem(sys.modules, "battle", battle_pkg)
    monkeypatch.setitem(sys.modules, "battle.weekly_trials", weekly)


def test_offline_wanshen_runs_due_pure_ws_and_marks_done(monkeypatch):
    scheduler = _import_scheduler(monkeypatch)

    cfg = {
        "enable_wanshen": True,
        "wanshen_battle_mode": "pure_ws",
        "wanshen_rounds": 3,
        "wanshen_until_cap": True,
    }
    monkeypatch.setattr(scheduler.config_manager, "get_device_config", lambda ip: cfg)
    marked = []
    monkeypatch.setattr(
        scheduler,
        "_WEEKLY_POLICY",
        SimpleNamespace(
            is_due=lambda ip, now: True,
            mark_done=lambda ip, time_recording: marked.append(ip),
        ),
    )
    calls = []
    _stub_weekly_runner(
        monkeypatch,
        lambda d, ip, rounds, cfg, until_cap: calls.append(
            (d, ip, rounds, until_cap)
        ) or SimpleNamespace(success=True),
    )

    assert scheduler.run_offline_wanshen_if_due("phone") is True
    assert calls == [(None, "phone", 3, True)]
    assert marked == ["phone"]


def test_offline_wanshen_skips_when_not_due(monkeypatch):
    scheduler = _import_scheduler(monkeypatch)

    monkeypatch.setattr(
        scheduler.config_manager,
        "get_device_config",
        lambda ip: {"enable_wanshen": True, "wanshen_battle_mode": "pure_ws"},
    )
    monkeypatch.setattr(
        scheduler,
        "_WEEKLY_POLICY",
        SimpleNamespace(is_due=lambda ip, now: False),
    )
    calls = []
    _stub_weekly_runner(
        monkeypatch,
        lambda *args, **kwargs: calls.append(True),
    )

    assert scheduler.run_offline_wanshen_if_due("phone") is False
    assert calls == []

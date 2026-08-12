import datetime
from types import SimpleNamespace

from game_actions import ws_phase
from ws_token import runner
from ws_token import state as ws_state


WEEKEND_CONFIG = {
    "enabled": True,
    "types": [1, 2],
    "mode": "fixed",
    "count": 35,
    "batches": 3,
    "weekend_only": True,
}

SKILL_SPRINT_CONFIG = {
    "enabled": True,
    "types": [1],
    "mode": "target",
    "count": 35,
    "batches": 3,
    "weekend_only": False,
    "target_draws": 8000,
    "interval_days": 28,
    "skill_sprint_weekdays": [1, 2],
    "skill_sprint_end_hour": 22,
}


def _report(draw_type):
    return SimpleNamespace(
        total_drawn=105,
        bundles=[35, 35, 35],
        stopped_reason=f"type-{draw_type}-done",
    )


def test_paid_gacha_runs_each_type_once_per_weekend_day(monkeypatch, tmp_path):
    calls = []

    def fake_run(client, tracker, **kwargs):
        calls.append(kwargs["draw_type"])
        return _report(kwargs["draw_type"])

    monkeypatch.setattr(runner.gacha, "run_gacha", fake_run)
    saturday = datetime.datetime(2026, 8, 1, 9, 0)

    first = runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=saturday,
    )
    second = runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=saturday.replace(hour=11),
    )

    assert calls == [1, 2]
    assert first["技能"]["drawn"] == 105
    assert first["同伴"]["drawn"] == 105
    assert second == {"already_attempted": True, "last_date": "2026-08-01"}
    saved = ws_state.load_state("phone", state_dir=tmp_path)["gacha_paid"]
    assert saved["last_date"] == "2026-08-01"
    assert saved["attempted_types"] == [1, 2]


def test_paid_gacha_runs_again_on_sunday(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        runner.gacha,
        "run_gacha",
        lambda client, tracker, **kwargs: (
            calls.append(kwargs["draw_type"]) or _report(kwargs["draw_type"])
        ),
    )

    runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=datetime.datetime(2026, 8, 1, 9, 0),
    )
    runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=datetime.datetime(2026, 8, 2, 9, 0),
    )

    assert calls == [1, 2, 1, 2]


def test_paid_gacha_skips_friday_without_writing_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner.gacha,
        "run_gacha",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不應抽卡")),
    )

    result = runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=datetime.datetime(2026, 7, 31, 9, 0),
    )

    assert result == {"skipped": "weekend_only: not Sat/Sun"}
    assert ws_state.load_state("phone", state_dir=tmp_path) == {}


def test_skill_sprint_mode_change_ignores_old_weekend_gate(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        runner.skill_sprint,
        "read_progress",
        lambda client: {"open": True, "act_type": 270, "draws": 0},
    )

    def fake_run(client, tracker, **kwargs):
        calls.append(kwargs)
        return _report(kwargs["draw_type"])

    monkeypatch.setattr(runner.gacha, "run_gacha", fake_run)

    # 舊週末抽卡已留下 gacha_paid；切到技能衝刺時必須開新週期，不能被舊紀錄擋掉。
    runner._run_gacha(
        object(), object(), gacha_config=WEEKEND_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 8, 9, 0),
    )
    result = runner._run_gacha(
        object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 11, 9, 0),
    )

    assert len(calls) == 3  # 週末技能/同伴兩次 + 週二技能衝刺一次
    assert calls[-1]["draw_type"] == 1
    assert calls[-1]["mode"] == "target"
    assert calls[-1]["target_draws"] == 8000
    assert result["技能"]["drawn"] == 105


def test_skill_sprint_uses_server_progress_as_remaining_target(monkeypatch, tmp_path):
    calls = []
    progress = iter((210, 210, 7000, 7000))

    monkeypatch.setattr(
        runner.skill_sprint,
        "read_progress",
        lambda client: {"open": True, "act_type": 270, "draws": next(progress)},
    )

    def fake_run(client, tracker, **kwargs):
        calls.append(kwargs)
        return _report(kwargs["draw_type"])

    monkeypatch.setattr(runner.gacha, "run_gacha", fake_run)

    first = runner._run_gacha(
        object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 12, 9, 0),
    )
    second = runner._run_gacha(
        object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 12, 10, 0),
    )

    assert [call["target_draws"] for call in calls] == [7790]
    assert first["技能"]["drawn"] == 105
    assert second["already_attempted"] is True
    assert second["window"] == "2026-08-11"
    assert second["progress"] == 7000


def test_skill_sprint_runs_once_between_tuesday_and_wednesday_22(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        runner.skill_sprint,
        "read_progress",
        lambda client: {"open": True, "act_type": 270, "draws": 0},
    )
    monkeypatch.setattr(
        runner.gacha,
        "run_gacha",
        lambda client, tracker, **kwargs: (
            calls.append(kwargs["draw_type"]) or _report(kwargs["draw_type"])
        ),
    )

    first = runner._run_gacha(
        object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 11, 0, 0),
    )
    second = runner._run_gacha(
        object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 12, 21, 59),
    )

    assert calls == [1]
    assert first["技能"]["drawn"] == 105
    assert second["already_attempted"] is True
    assert second["window"] == "2026-08-11"


def test_skill_sprint_skips_outside_tuesday_wednesday_window(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner.gacha,
        "run_gacha",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("技能衝刺不應在窗口外抽卡")
        ),
    )

    for current in (
        datetime.datetime(2026, 8, 10, 23, 59),  # Monday
        datetime.datetime(2026, 8, 12, 22, 0),   # Wednesday cutoff
        datetime.datetime(2026, 8, 13, 9, 0),    # Thursday
    ):
        result = runner._run_gacha(
            object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
            state_dir=tmp_path, now=current,
        )
        assert result == {"skipped": "skill_sprint: outside Tue-Wed 22:00 window"}

    assert ws_state.load_state("phone", state_dir=tmp_path) == {}


def test_skill_sprint_new_window_resets_local_gate_without_server_precheck(
    monkeypatch, tmp_path
):
    calls = []
    config = {**SKILL_SPRINT_CONFIG, "check_activity": False}
    monkeypatch.setattr(
        runner.gacha,
        "run_gacha",
        lambda client, tracker, **kwargs: (
            calls.append(kwargs["draw_type"]) or _report(kwargs["draw_type"])
        ),
    )

    runner._run_gacha(
        object(), object(), gacha_config=config, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 11, 9, 0),
    )
    runner._run_gacha(
        object(), object(), gacha_config=config, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 18, 9, 0),
    )

    assert calls == [1, 1]


def test_skill_sprint_complete_skips_without_spending(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner.skill_sprint,
        "read_progress",
        lambda client: {"open": True, "act_type": 270, "draws": 8000},
    )
    monkeypatch.setattr(
        runner.gacha,
        "run_gacha",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("技能衝刺已達標，不應抽卡")
        ),
    )

    result = runner._run_gacha(
        object(), object(), gacha_config=SKILL_SPRINT_CONFIG, device="phone",
        state_dir=tmp_path, now=datetime.datetime(2026, 8, 11, 9, 0),
    )

    assert result == {
        "skipped": "skill_sprint: already complete",
        "progress": 8000,
        "target": 8000,
        "act_type": 270,
        "claimed_rounds": [],
    }
    assert ws_state.load_state("phone", state_dir=tmp_path) == {}


def test_paid_gacha_error_is_recorded_and_other_type_continues(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(client, tracker, **kwargs):
        draw_type = kwargs["draw_type"]
        calls.append(draw_type)
        if draw_type == 1:
            raise RuntimeError("socket lost")
        return _report(draw_type)

    monkeypatch.setattr(runner.gacha, "run_gacha", fake_run)
    saturday = datetime.datetime(2026, 8, 1, 9, 0)

    first = runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=saturday,
    )
    second = runner._run_gacha(
        object(),
        object(),
        gacha_config=WEEKEND_CONFIG,
        device="phone",
        state_dir=tmp_path,
        now=saturday.replace(hour=10),
    )

    assert calls == [1, 2]
    assert first["技能"]["error"] == "RuntimeError: socket lost"
    assert first["同伴"]["drawn"] == 105
    assert second == {"already_attempted": True, "last_date": "2026-08-01"}
    saved = ws_state.load_state("phone", state_dir=tmp_path)["gacha_paid"]
    assert saved["attempted_types"] == [1, 2]
    assert saved["results"]["1"]["error"] == "RuntimeError: socket lost"


def test_already_attempted_still_skips_adb_paid_fallback():
    report = SimpleNamespace(
        tasks={"gacha": {"already_attempted": True, "last_date": "2026-08-01"}}
    )

    assert "gacha" in ws_phase._substantive_done(report)

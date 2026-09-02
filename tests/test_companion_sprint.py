from __future__ import annotations
import datetime
from ws_token import runner


def test_companion_sprint_target_uses_companion_ticket(monkeypatch, tmp_path):
    config = {
        "enabled": True, "types": [1, 2], "mode": "target", "count": 35,
        "batches": 1, "target_draws": 8000, "check_activity": True,
        "skill_sprint_weekdays": [1, 2], "skill_sprint_end_hour": 22,
    }
    monkeypatch.setattr(runner.skill_sprint, "read_progress", lambda client: {"open": False})
    monkeypatch.setattr(runner.companion_sprint, "read_progress", lambda client: {
        "open": True, "act_type": 268, "draws": 0,
        "rounds": [], "claimable_rounds": [], "tasks": [],
    })
    calls = []
    monkeypatch.setattr(runner.gacha, "run_gacha", lambda *a, **kw: (
        calls.append(kw["draw_type"]), type("R", (), {"total_drawn": 35, "bundles": 1, "stopped_reason": "done"})
    )[1])
    out = runner._run_gacha(object(), object(), gacha_config=config, device="dev",
                            state_dir=tmp_path,
                            now=datetime.datetime(2026, 9, 1, 10, 0))
    assert calls == [2]
    assert out["同伴"]["drawn"] == 35

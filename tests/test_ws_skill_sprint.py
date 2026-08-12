from ws_token import skill_sprint


def test_read_progress_probes_both_skill_activity_types(monkeypatch):
    calls = []

    def fake_read(client, act_type, *, timeout=None):
        calls.append(act_type)
        if act_type == 8:
            return {"open": False, "act_type": act_type}
        return {"open": True, "act_type": act_type, "accrued": 210}

    monkeypatch.setattr(skill_sprint.relic_sprint, "read_sprint", fake_read)

    result = skill_sprint.read_progress(object())

    assert calls == [8, 270]
    assert result == {"open": True, "act_type": 270, "draws": 210}


def test_read_progress_returns_closed_when_no_type_is_active(monkeypatch):
    monkeypatch.setattr(
        skill_sprint.relic_sprint,
        "read_sprint",
        lambda client, act_type, *, timeout=None: {"open": False},
    )

    assert skill_sprint.read_progress(object()) == {"open": False}

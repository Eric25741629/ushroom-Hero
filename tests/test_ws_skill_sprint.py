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
    assert result == {
        "open": True,
        "act_type": 270,
        "draws": 210,
        "rounds": [],
        "claimable_rounds": [],
        "tasks": [],
    }


def test_read_progress_returns_closed_when_no_type_is_active(monkeypatch):
    monkeypatch.setattr(
        skill_sprint.relic_sprint,
        "read_sprint",
        lambda client, act_type, *, timeout=None: {"open": False},
    )

    assert skill_sprint.read_progress(object()) == {"open": False}


def test_claim_completed_rounds_checks_all_four_and_claims_ready_groups(monkeypatch):
    calls = []
    tasks = [
        {"task_id": task_id, "status": 1 if task_id in (1, 22) else 0, "count": 1}
        for task_id in range(1, 29)
    ]
    tasks.extend(
        {"task_id": task_id, "status": 1 if task_id == 31 else 0, "count": 7}
        for task_id in range(29, 33)
    )

    def fake_claim(client, act_type, group_id, *, timeout=None):
        calls.append((act_type, group_id))
        return type("Result", (), {"success": True})()

    monkeypatch.setattr(skill_sprint.relic_sprint, "claim_round", fake_claim)

    claimed = skill_sprint.claim_completed_rounds(
        object(),
        {
            "open": True,
            "act_type": 270,
            "rounds": [{"small_group_id": n} for n in range(1, 5)],
            "claimable_rounds": [3],
            "tasks": tasks,
        },
    )

    assert calls == [(270, 1), (270, 3), (270, 4)]
    assert claimed == [1, 3, 4]

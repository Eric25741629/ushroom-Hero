from dragon_realm.state import DragonState, DragonEvent
from dragon_realm import constants as C


def _raw(**over):
    base = {
        "activity_open": True,
        "ceng": 1,
        "hp": 30,
        "server_time": 1000,
        "help_hp": 0,
        "event_id": 0,
        "event_type": 0,
        "event_uid": 0,
        "event_data": {},          # k(EventDataKey)->v
        "event_list": [],
        "help_events": [],
        "bag": {},
    }
    base.update(over)
    return base


def test_no_event_state():
    s = DragonState.from_raw(_raw(), my_role_id=777)
    assert s.activity_open and s.ceng == 1 and s.event_id == 0


def test_monster_challenge_flags_extracted_from_event_data():
    raw = _raw(
        event_id=5001, event_type=C.PVE, event_uid=42,
        event_data={C.K_IS_CHALLENGE: 1, C.K_IS_ASK_HELP: 0, C.K_BACK_KILL_TIME: 900},
    )
    s = DragonState.from_raw(raw, my_role_id=777)
    assert s.is_challenge is True
    assert s.is_ask_help is False
    assert s.back_kill_time == 900


def test_event_list_marks_mine_vs_teammate():
    raw = _raw(event_list=[
        {"role_id": 777, "event_id": 5001, "id": 1, "event_type": C.PVE, "back_kill_time": 0},
        {"role_id": 888, "event_id": 5002, "id": 2, "event_type": C.PVE, "back_kill_time": 0},
    ])
    s = DragonState.from_raw(raw, my_role_id=777)
    assert [e.is_mine for e in s.event_list] == [True, False]
    assert s.event_list[0].uid == 1


def test_help_events_only_include_own_completed_events():
    raw = _raw(event_list=[
        {"role_id": 777, "event_id": 321, "id": 1, "status": 1},
        {"role_id": 888, "event_id": 322, "id": 2, "status": 1},
        {"role_id": 777, "event_id": 323, "id": 3, "status": 0},
    ])

    s = DragonState.from_raw(raw, my_role_id=777)

    assert s.help_events == (321,)
    assert [e.status for e in s.event_list] == [1, 1, 0]


def test_bag_lookup_helper():
    s = DragonState.from_raw(_raw(bag={1518: 3}), my_role_id=777)
    assert s.bag_count(1518) == 3
    assert s.bag_count(9999) == 0


def test_missing_fields_default_safely():
    s = DragonState.from_raw({"activity_open": True}, my_role_id=777)
    assert s.ceng == 1 and s.hp == 0 and s.event_id == 0 and s.event_list == ()

from runtime_services.mount_tracker_service import MountTrackerStore


def test_add_remove_target_roundtrip(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.add_target({"role_id": 1, "name": "A", "uid": "X"})
    assert s.get_targets()[0]["role_id"] == 1
    s2 = MountTrackerStore(state_dir=str(tmp_path))   # reload from disk
    assert s2.get_targets()[0]["name"] == "A"
    s2.remove_target(1)
    assert s2.get_targets() == []


def test_add_target_dedupes_by_role_id(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.add_target({"role_id": 1, "name": "A"})
    s.add_target({"role_id": 1, "name": "A2"})
    assert len(s.get_targets()) == 1 and s.get_targets()[0]["name"] == "A2"


def test_upsert_known_merges_fields(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.upsert_known(9, name="曇花一現")
    s.upsert_known(9, guild="羽皇居", coin=145)
    k = s.get_known()["9"]
    assert k["name"] == "曇花一現" and k["guild"] == "羽皇居" and k["coin"] == 145


def test_upsert_known_ignores_none_fields(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.upsert_known(9, name="X", guild="G")
    s.upsert_known(9, guild=None)          # None must NOT wipe existing
    assert s.get_known()["9"]["guild"] == "G"


def test_results_and_last_run(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.set_results({"1": [{"owner_role_id": 2, "pos": 3}]})
    s.set_last_run({"ts": 123, "found": 1})
    assert s.get_results()["1"][0]["pos"] == 3


def test_snapshot_shape(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.add_target({"role_id": 1, "name": "A"})
    s.upsert_known(9, name="X")
    snap = s.snapshot()
    assert set(snap) >= {"targets", "results", "known_count", "last_run", "running"}
    assert snap["known_count"] == 1

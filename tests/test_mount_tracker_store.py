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


def test_bulk_upsert_known_merges_and_skips_none(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.upsert_known(9, name="X", guild="G")
    s.bulk_upsert_known({
        9: {"guild": None, "coin": 145},        # None 不覆蓋既有 guild
        7: {"name": "曇花一現", "host_hits": 3},
    })
    k = s.get_known()
    assert k["9"] == {"name": "X", "guild": "G", "coin": 145}
    assert k["7"] == {"name": "曇花一現", "host_hits": 3}


def test_bulk_upsert_known_empty_is_noop(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.bulk_upsert_known({})
    assert s.get_known() == {}


def test_mark_attacked_set_and_get_roundtrip(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.mark_attacked(100, 5, 111)                     # on 預設 True
    s2 = MountTrackerStore(state_dir=str(tmp_path))  # reload from disk
    atk = s2.get_attacked()
    assert set(atk) == {"100"}
    assert "5:111" in atk["100"] and atk["100"]["5:111"] > 0


def test_mark_attacked_off_removes_key_and_empty_target(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.mark_attacked(100, 5, 111)
    s.mark_attacked(100, 5, 111, on=False)           # 取消 → 鍵移除、空 target 一併移除
    assert s.get_attacked() == {}


def test_set_attacked_overwrites(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.mark_attacked(100, 5, 111)
    s.set_attacked({"200": {"9:9": 1.0}})            # 整批覆寫
    assert s.get_attacked() == {"200": {"9:9": 1.0}}


def test_bootstrap_done_roundtrip(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    assert s.get_bootstrap_done() is None            # 尚未跑過
    s.set_bootstrap_done(1699999999.0)
    s2 = MountTrackerStore(state_dir=str(tmp_path))   # reload from disk
    assert s2.get_bootstrap_done() == 1699999999.0
    s2.set_bootstrap_done(None)                       # None 清除（觸發重建）
    assert s2.get_bootstrap_done() is None
    assert MountTrackerStore(state_dir=str(tmp_path)).get_bootstrap_done() is None


def test_snapshot_has_bootstrap_done(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    snap = s.snapshot()
    assert "bootstrap_done" in snap and snap["bootstrap_done"] is None
    s.set_bootstrap_done(123.0)
    assert s.snapshot()["bootstrap_done"] == 123.0


def test_bulk_upsert_known_single_write(tmp_path, monkeypatch):
    s = MountTrackerStore(state_dir=str(tmp_path))
    calls = {"n": 0}
    orig = s._save

    def counting_save(data):
        calls["n"] += 1
        orig(data)

    monkeypatch.setattr(s, "_save", counting_save)
    s.bulk_upsert_known({1: {"name": "A"}, 2: {"name": "B"}, 3: {"name": "C"}})
    assert calls["n"] == 1                       # 三筆合併只寫一次
    assert set(s.get_known()) == {"1", "2", "3"}

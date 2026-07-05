"""Task 5：scan_cycle 核心測試（全依賴注入，不碰真 socket / sleep / 時鐘）。

reader / idle_picker / sleeper / now_fn 皆為 fake；store 用真 MountTrackerStore
搭 tmp_path，順帶驗證持久化。
"""
from runtime_services.mount_tracker_service import MountTrackerStore, scan_cycle


def _target_lot(target_id=100):
    """一個停著追蹤目標的車位。"""
    return {
        "skin_list": [],
        "spaces": [{"pos": 1, "role_id": target_id, "start_time": 111, "name": "T"}],
    }


def test_returns_skip_when_no_targets(tmp_path):
    store = MountTrackerStore(state_dir=str(tmp_path))
    out = scan_cycle(store, lambda d, o: None, lambda: "d", lambda s: None, lambda: 1.0)
    assert out == {"skipped": "no_targets"}


def test_finds_target_and_early_exits_at_5(tmp_path):
    store = MountTrackerStore(state_dir=str(tmp_path))
    store.add_target({"role_id": 100, "name": "T"})
    for oid in range(1, 7):                       # 6 個房東入 known → queue 長 6(+目標)
        store.upsert_known(oid, name=f"owner{oid}")

    calls = []

    def reader(dev, owner):
        calls.append(owner)
        return _target_lot(100)                   # 每個房東都停著目標

    tick = [0.0]

    def now_fn():
        tick[0] += 1.0
        return tick[0]

    scan_cycle(store, reader, lambda: "d", lambda s: None, now_fn,
               budget_s=10_000, max_per_target=5)

    results = store.get_results()
    assert len(results["100"]) == 5              # 只收 5 台
    assert len(calls) == 5                       # 收滿後 all-found 早停，第 6 個房東不再讀


def test_snowball_adds_new_occupants(tmp_path):
    store = MountTrackerStore(state_dir=str(tmp_path))
    store.add_target({"role_id": 100, "name": "T"})
    store.upsert_known(1, name="owner1")

    def reader(dev, owner):
        # 車位裡是一個「未知」佔用者 999（非目標）
        return {"skin_list": [],
                "spaces": [{"pos": 2, "role_id": 999, "start_time": 5, "name": "U"}]}

    scan_cycle(store, reader, lambda: "d", lambda s: None, lambda: 1.0, budget_s=10_000)

    known = store.get_known()
    assert "999" in known and known["999"]["name"] == "U"   # 雪球納入
    assert store.get_results()["100"] == []                 # 目標未出現


def test_respects_budget(tmp_path):
    store = MountTrackerStore(state_dir=str(tmp_path))
    store.add_target({"role_id": 100, "name": "T"})
    for oid in range(1, 11):                      # 10 個房東
        store.upsert_known(oid, name=f"o{oid}")

    calls = []

    def reader(dev, owner):
        calls.append(owner)
        return {"skin_list": [], "spaces": []}

    tick = [0.0]

    def now_fn():
        tick[0] += 1000.0                         # 時間飛快前進
        return tick[0]

    scan_cycle(store, reader, lambda: "d", lambda s: None, now_fn,
               top_n=1600, budget_s=1500, max_per_target=5)

    assert len(calls) < 10                        # 預算截斷，沒掃完整個 queue


def test_attacked_annotate_and_prune(tmp_path):
    """預先標記的實例本輪回來帶 attacked=True；本輪缺席的舊標記被剪掉。"""
    store = MountTrackerStore(state_dir=str(tmp_path))
    store.add_target({"role_id": 100, "name": "T"})
    store.upsert_known(1, name="owner1")
    # 兩個預標記：1:111 本輪會出現、2:222 本輪不會出現（應被剪枝）。
    store.set_attacked({"100": {"1:111": 1000.0, "2:222": 2000.0}})

    def reader(dev, owner):
        # 只有房東 1 的車位停著目標；目標自身車位（owner=100）為空。
        if owner == 1:
            return {"skin_list": [],
                    "spaces": [{"pos": 1, "role_id": 100, "start_time": 111, "name": "T"}]}
        return {"skin_list": [], "spaces": []}

    scan_cycle(store, reader, lambda: "d", lambda s: None, lambda: 1.0, budget_s=10_000)

    entry = store.get_results()["100"][0]
    assert entry["attacked"] is True                       # 命中預標記
    # 剪枝：僅保留本輪 found 仍出現的 1:111，缺席的 2:222 被移除。
    assert store.get_attacked() == {"100": {"1:111": 1000.0}}


def test_unmarked_instance_annotated_false(tmp_path):
    """未被標記的實例 attacked=False，且不會誤增標記。"""
    store = MountTrackerStore(state_dir=str(tmp_path))
    store.add_target({"role_id": 100, "name": "T"})
    store.upsert_known(1, name="owner1")

    def reader(dev, owner):
        if owner == 1:
            return {"skin_list": [],
                    "spaces": [{"pos": 1, "role_id": 100, "start_time": 111, "name": "T"}]}
        return {"skin_list": [], "spaces": []}

    scan_cycle(store, reader, lambda: "d", lambda s: None, lambda: 1.0, budget_s=10_000)

    assert store.get_results()["100"][0]["attacked"] is False
    assert store.get_attacked() == {}


def test_skips_when_no_idle_device(tmp_path):
    store = MountTrackerStore(state_dir=str(tmp_path))
    store.add_target({"role_id": 100, "name": "T"})
    for oid in range(1, 6):
        store.upsert_known(oid, name=f"o{oid}")

    picks = [None, None] + ["d"] * 100            # 前兩次沒有 idle 裝置，之後才有
    idx = [0]

    def idle_picker():
        val = picks[idx[0]]
        idx[0] += 1
        return val

    sleeps = []

    scan_cycle(store, reader=lambda d, o: _target_lot(100),
               idle_picker=idle_picker, sleeper=lambda s: sleeps.append(s),
               now_fn=lambda: 1.0, budget_s=10_000, max_per_target=5)

    results = store.get_results()
    assert len(results["100"]) >= 1              # 不崩、最終仍掃到
    assert sleeps                                # 無裝置的空檔有冷卻

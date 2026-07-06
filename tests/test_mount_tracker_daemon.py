"""Task 6：daemon + toggle + 真 reader 測試。

- toggle：monkeypatch dashboard_settings 檔案路徑到 tmp，set/get 一致。
- ensure_started：連叫兩次只起一條 thread（fake Thread 記次數）。
- 真 reader：monkeypatch _get_ws_client 回傳 fake client（.call 回 canned
  car_park_info bytes，以 ws_token.codec 自組），驗證走 parse_lot_occupants。
"""
import runtime_services.mount_tracker_service as mt
from utils import dashboard_settings as ds
from ws_token import codec


def test_toggle_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "_settings_path", str(tmp_path / "dashboard_settings.json"))
    assert ds.get_mount_tracker_enabled() is False   # 預設關
    ds.set_mount_tracker_enabled(True)
    assert ds.get_mount_tracker_enabled() is True
    ds.set_mount_tracker_enabled(False)
    assert ds.get_mount_tracker_enabled() is False


def test_ensure_started_idempotent(monkeypatch):
    created = []

    class FakeThread:
        def __init__(self, *a, **k):
            created.append(1)

        def start(self):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(mt, "_thread", None)
    monkeypatch.setattr(mt, "_started", False)
    monkeypatch.setattr(mt.threading, "Thread", FakeThread)

    mt.ensure_mount_tracker_started()
    mt.ensure_mount_tracker_started()

    assert len(created) == 1                          # 只起一條 thread


def test_real_reader_uses_carpark_parser(monkeypatch):
    # 用 codec 自組 car_park_info body：一個佔用車位 + 一個外觀 skin。
    space = (codec.pb_uint(1, 3) + codec.pb_uint(2, 555)
             + codec.pb_uint(5, 1783267198) + codec.pb_str(9, "曇花一現"))
    skin = codec.pb_uint(1, 10002) + codec.pb_uint(2, 10)
    body = codec.pb_msg(7, space) + codec.pb_msg(8, skin)

    class FakeClient:
        def __init__(self):
            self.calls = []

        def call(self, cmd, req_body=b"", **kwargs):
            self.calls.append((cmd, req_body))
            return body

    fake = FakeClient()
    monkeypatch.setattr(mt, "_get_ws_client", lambda dev: fake)

    lot = mt._read_lot("d", 555)

    assert lot["spaces"][0] == {"pos": 3, "role_id": 555,
                                "start_time": 1783267198, "name": "曇花一現"}
    assert (10002, 10) in lot["skin_list"]
    assert fake.calls[0][0] == mt.mount_scan.CMD_LOT_INFO   # 用車位查詢 cmd


def test_real_reader_none_on_no_client(monkeypatch):
    monkeypatch.setattr(mt, "_get_ws_client", lambda dev: None)
    assert mt._read_lot("d", 555) is None


def test_get_store_singleton():
    a = mt.get_store()
    b = mt.get_store()
    assert a is b


def test_read_lot_records_borrow_at_ensure_even_if_read_kicked(monkeypatch):
    """FIX 7：ensure 成功但 call 立刻被踢 → 回 None，但 on_ensure 已登記借用（收尾會歸還）。"""
    class KickClient:
        def call(self, *a, **k):
            raise RuntimeError("kicked")

    monkeypatch.setattr(mt, "_get_ws_client", lambda dev: KickClient())
    seen = []
    out = mt._read_lot("devX", 5, on_ensure=lambda d: seen.append(d))
    assert out is None                 # 讀取被踢 → None
    assert seen == ["devX"]            # 連線已建立 → 已登記借用（即使讀取失敗）


def test_scan_borrows_all_safe_idle_and_releases(monkeypatch):
    """並行掃描：借「所有」安全 idle 且連得上的裝置當 worker、收尾一律歸還；
    still_idle 反映 idle 且未即將自我喚醒（進入喚醒窗 → False，讓位給自身 bot loop）。"""
    captured = {}

    def fake_scan_cycle(store, reader, worker_devices, still_idle, sleeper, now_fn, **kw):
        captured["worker_devices"] = list(worker_devices)
        captured["still_idle"] = still_idle
        return {}

    class FakeStore:
        def get_bootstrap_done(self):
            return 1.0                     # 已完成 → 跳過 bootstrap，直接驗 scan 路徑
        def set_running(self, running):
            pass

    monkeypatch.setattr(mt, "scan_cycle", fake_scan_cycle)
    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    monkeypatch.setattr(mt, "CANDIDATES", ["devA", "devB", "devC", "devD"])
    # devA/devC/devD 安全可借；devD 連不上（client None）→ 只借 devA/devC。
    monkeypatch.setattr(mt, "is_safe_to_borrow", lambda ip: ip in {"devA", "devC", "devD"})
    monkeypatch.setattr(mt, "_get_ws_client", lambda dev: None if dev == "devD" else object())

    released = []
    monkeypatch.setattr(mt, "_release_dev", lambda dev: released.append(dev))

    # still_idle 判定：devA 仍 idle；devC 即將自我喚醒（1060-1000=60s < 120s lead）。
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    states = {"devA": {"task": "休眠中", "next_wake_at": 9_999_999.0},
              "devC": {"task": "休眠中", "next_wake_at": 1060.0}}
    monkeypatch.setattr(mt, "_states", lambda: states)

    mt._run_one_cycle()

    assert captured["worker_devices"] == ["devA", "devC"]   # devB 不安全、devD 連不上
    # 收尾歸還所有登記過借用的裝置；devD 連不上仍列入 borrowed（防洩漏）→ 一併歸還。
    assert set(released) == {"devA", "devC", "devD"}
    still_idle = captured["still_idle"]
    assert still_idle("devA") is True                       # 仍 idle
    assert still_idle("devC") is False                      # 進入喚醒窗 → 收掉該 worker


def test_run_one_cycle_runs_bootstrap_when_not_done(monkeypatch):
    """bootstrap_done is None → 借 idle 裝置跑 bootstrap、灌 known、標記完成、催掃、跳過 scan。"""
    events = {"scan": 0, "wake": 0, "seed": None, "done": None}

    class FakeStore:
        def get_bootstrap_done(self):
            return None                         # 尚未跑過 → 觸發 bootstrap
        def set_bootstrap_done(self, ts):
            events["done"] = ts
        def bulk_upsert_known(self, seed):
            events["seed"] = seed
        def set_running(self, running):
            pass

    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    monkeypatch.setattr(mt, "pick_idle_device", lambda: "devA")
    monkeypatch.setattr(mt, "_get_ws_client", lambda dev: object())   # 非 None → 進 bootstrap
    monkeypatch.setattr(mt, "_states", lambda: {})
    monkeypatch.setattr(mt, "_about_to_wake", lambda dev, states: False)
    monkeypatch.setattr(mt, "bootstrap_known",
                        lambda *a, **k: {555: {"name": "X", "guild": "G", "level": 9}})
    monkeypatch.setattr(mt, "wake_scan", lambda: events.__setitem__("wake", events["wake"] + 1))
    monkeypatch.setattr(mt, "scan_cycle",
                        lambda *a, **k: events.__setitem__("scan", events["scan"] + 1))
    released = []
    monkeypatch.setattr(mt, "_release_dev", lambda dev: released.append(dev))

    mt._run_one_cycle()

    assert events["seed"] == {555: {"name": "X", "guild": "G", "level": 9}}  # 一次批次灌
    assert events["done"] is not None            # 標記完成（即使部分）
    assert events["wake"] == 1                    # 催下一輪
    assert events["scan"] == 0                    # 本輪跳過正式掃描
    assert released == ["devA"]                   # 借用裝置收尾歸還


def test_run_one_cycle_skips_bootstrap_when_done(monkeypatch):
    """bootstrap_done 已設 → 不跑 bootstrap，直接走正式 scan_cycle。"""
    events = {"scan": 0, "bootstrap": 0}

    class FakeStore:
        def get_bootstrap_done(self):
            return 12345.0                       # 已完成
        def set_running(self, running):
            pass

    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    monkeypatch.setattr(
        mt, "bootstrap_known",
        lambda *a, **k: (events.__setitem__("bootstrap", events["bootstrap"] + 1) or {}))
    monkeypatch.setattr(mt, "scan_cycle",
                        lambda *a, **k: events.__setitem__("scan", events["scan"] + 1))
    monkeypatch.setattr(mt, "pick_idle_device", lambda: None)
    # scan 路徑會借「所有」安全 idle 裝置；此測試只驗 scan 有跑，關掉借用避免真連線。
    monkeypatch.setattr(mt, "is_safe_to_borrow", lambda ip: False)

    mt._run_one_cycle()

    assert events["bootstrap"] == 0              # 未跑 bootstrap
    assert events["scan"] == 1                   # 正常掃描照跑


def test_progress_roundtrip_and_copy_isolation():
    """get_progress / _set_progress / _reset_progress 往返 + 回傳複本隔離。"""
    mt._reset_progress("bootstrap")
    p = mt.get_progress()
    assert p["phase"] == "bootstrap"
    assert p["scanned"] == 0 and p["found"] == 0 and p["known"] == 0
    assert p["guilds"] == 0 and p["members"] == 0
    assert p["started_ts"] is not None

    mt._set_progress(scanned=7, found=3, known=42)     # 只合併傳入鍵
    p2 = mt.get_progress()
    assert (p2["scanned"], p2["found"], p2["known"]) == (7, 3, 42)
    assert p2["phase"] == "bootstrap"                   # 未傳 phase → 保留

    # 複本隔離：改動回傳的 dict 不會回寫內部狀態。
    p2["phase"] = "TAMPERED"
    p2["scanned"] = 999
    assert mt.get_progress()["phase"] == "bootstrap"
    assert mt.get_progress()["scanned"] == 7

    mt._reset_progress("idle")                          # 收尾歸零，避免污染其他測試
    assert mt.get_progress()["phase"] == "idle"
    assert mt.get_progress()["scanned"] == 0


def test_next_interval_slows_when_satisfied(monkeypatch):
    """有目標且上輪 found_total 達門檻（2 目標 → 門檻 2*5-2=8）→ 放慢到 _IDLE_INTERVAL_SEC。"""
    class FakeStore:
        def get_targets(self):
            return [{"role_id": 1}, {"role_id": 2}]
        def get_last_run(self):
            return {"found_total": 8}

    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    assert mt._next_interval() == mt._IDLE_INTERVAL_SEC


def test_next_interval_fast_when_not_satisfied(monkeypatch):
    """found_total 未達門檻（5 < 8）→ 維持快掃間隔 _CYCLE_INTERVAL_SEC。"""
    class FakeStore:
        def get_targets(self):
            return [{"role_id": 1}, {"role_id": 2}]
        def get_last_run(self):
            return {"found_total": 5}

    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    assert mt._next_interval() == mt._CYCLE_INTERVAL_SEC


def test_next_interval_fast_when_no_targets(monkeypatch):
    """無目標 → 一律快掃（即使 found_total 很高也不放慢）。"""
    class FakeStore:
        def get_targets(self):
            return []
        def get_last_run(self):
            return {"found_total": 999}

    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    assert mt._next_interval() == mt._CYCLE_INTERVAL_SEC


def test_next_interval_fast_when_no_last_run(monkeypatch):
    """last_run 為 None（尚未掃過）→ found_total 視為 0 → 快掃。"""
    class FakeStore:
        def get_targets(self):
            return [{"role_id": 1}]
        def get_last_run(self):
            return None

    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    assert mt._next_interval() == mt._CYCLE_INTERVAL_SEC


def test_cycle_sleeper_is_cooldown_not_wake(monkeypatch):
    # 關鍵安全性：_run_one_cycle 給 scan_cycle 的 sleeper 必須是 _cooldown（真 sleep），
    # 不可是 _wake.wait——否則「立即掃描」催醒 (_wake.set) 會讓冷卻瞬間失效。
    captured = {}

    def fake_scan_cycle(store, reader, worker_devices, still_idle, sleeper, now_fn, **kwargs):
        captured["sleeper"] = sleeper
        return {}

    class FakeStore:
        def get_bootstrap_done(self):
            return 1.0                     # 已完成 → 跳過 bootstrap，直接驗 scan_cycle 路徑
        def set_running(self, running):
            pass

    monkeypatch.setattr(mt, "scan_cycle", fake_scan_cycle)
    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())
    monkeypatch.setattr(mt, "is_safe_to_borrow", lambda ip: False)  # 不借用，只驗 sleeper 身分

    mt._run_one_cycle()

    sleeper = captured["sleeper"]
    assert sleeper is mt._cooldown
    assert sleeper is not mt._wake.wait

    # _wake 被 set 後，冷卻仍實際延遲（time.sleep 被呼叫），不會瞬間返回。
    slept = []
    monkeypatch.setattr(mt.time, "sleep", lambda s: slept.append(s))
    mt._wake.set()
    try:
        sleeper(3.0)
        assert slept == [3.0]
    finally:
        mt._wake.clear()

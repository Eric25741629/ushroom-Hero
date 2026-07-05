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


def test_cycle_sleeper_is_cooldown_not_wake(monkeypatch):
    # 關鍵安全性：_run_one_cycle 給 scan_cycle 的 sleeper 必須是 _cooldown（真 sleep），
    # 不可是 _wake.wait——否則「立即掃描」催醒 (_wake.set) 會讓冷卻瞬間失效。
    captured = {}

    def fake_scan_cycle(store, reader, idle_picker, sleeper, now_fn, **kwargs):
        captured["sleeper"] = sleeper
        return {}

    class FakeStore:
        def set_running(self, running):
            pass

    monkeypatch.setattr(mt, "scan_cycle", fake_scan_cycle)
    monkeypatch.setattr(mt, "get_store", lambda: FakeStore())

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

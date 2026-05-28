"""Daily-flow orchestration for sea_v2.tasks.

The orchestration is driven through an injected ``SeaSession`` so the decision logic
(target selection, ordering, reward claim, graceful abort) is unit-testable without a
live game. The garrison/attack/repair *actions* are session methods filled in during the
daytime window (階段 B); here we only assert the orchestration calls them correctly.
"""
import pytest

from sea_v2 import tasks
from sea_v2 import map_cache as mc


def _raw(name, x, y):
    return {"name": name, "wp": {"x": x, "y": y}}


HOME = (-31910, -1867)
RAW_OBJS = [
    _raw("resource_1", -30954, -1630),
    _raw("resource_1", -31364, -1709),
    _raw("resource_1", -31773, -1314),
    _raw("base", *HOME),
    _raw("base", -28088, -1867),
    _raw("remain", -29999, -1709),
]


class FakeSession:
    """Records orchestration calls; stages object reads and on-screen results."""

    def __init__(self, objs, cam_wp=(-31733, -1440), on_screen=True):
        self._objs = objs
        self._cam = cam_wp
        self._on_screen = on_screen
        self.calls = []
        self.garrisoned = []
        self.attacked = []

    def enter_season(self):
        self.calls.append("enter")

    def locate(self):
        self.calls.append("locate")
        return self._cam

    def read_objects(self):
        return list(self._objs)

    def bring_on_screen(self, tile):
        self.calls.append(("bring", tile.name, tile.wx, tile.wy))
        return self._on_screen

    def garrison(self, tile):
        self.garrisoned.append((tile.wx, tile.wy))
        return True

    def attack(self, tile):
        self.attacked.append((tile.wx, tile.wy))
        return True

    def use_repair_kit(self):
        self.calls.append("repair")
        return True

    def upgrade_repair_station(self):
        self.calls.append("station")
        return True

    def claim_rewards(self):
        self.calls.append("claim")
        return 3

    def exit_season(self):
        self.calls.append("exit")


def test_run_daily_locates_before_reading_targets():
    s = FakeSession(RAW_OBJS)
    tasks.run_daily(s)
    # 定位 must happen before any tile interaction (establishes the home origin)
    assert s.calls[0] == "enter"
    assert s.calls[1] == "locate"
    assert s.calls.index("locate") < next(i for i, c in enumerate(s.calls) if isinstance(c, tuple))


def test_run_daily_garrisons_two_resources_and_attacks_relic():
    s = FakeSession(RAW_OBJS)
    report = tasks.run_daily(s)
    assert len(s.garrisoned) == 2
    assert s.attacked == [(-29999, -1709)]
    assert report.home.wp == HOME


def test_run_daily_claims_rewards_and_exits():
    s = FakeSession(RAW_OBJS)
    report = tasks.run_daily(s)
    assert "claim" in s.calls and "exit" in s.calls
    assert s.calls.index("claim") < s.calls.index("exit")
    assert report.rewards_claimed == 3


def test_run_daily_station_upgrade_is_last_before_exit():
    # Repair runs FIRST (ship at 大本營 from yesterday); 一鍵修築 station-upgrade dives
    # into 港口 and closing 港口 exits the season, so it must be the LAST action -- its
    # close doubles as leaving the season. Order: repair < claim < station < exit.
    s = FakeSession(RAW_OBJS)
    tasks.run_daily(s)
    assert s.calls.index("repair") < s.calls.index("claim") < s.calls.index("station")
    # station is the last real action before exit
    assert s.calls.index("station") < s.calls.index("exit")
    assert s.calls[-1] == "exit"


def test_run_daily_skips_action_when_tile_cannot_be_brought_on_screen():
    s = FakeSession(RAW_OBJS, on_screen=False)
    tasks.run_daily(s)
    assert s.garrisoned == []
    assert s.attacked == []  # never clicked an off-screen tile (no blind action)


def test_run_daily_aborts_cleanly_when_no_home_base():
    objs = [_raw("remain", -29999, -1709)]  # no base at all
    s = FakeSession(objs)
    report = tasks.run_daily(s)
    assert report.aborted_reason is not None
    assert "exit" in s.calls          # still leaves the view tidy
    assert s.garrisoned == []


def test_run_daily_writes_shared_map_cache(tmp_path):
    s = FakeSession(RAW_OBJS)
    p = tmp_path / "shared_map.json"
    tasks.run_daily(s, account_id="emulator-5554", cache_path=p)
    cache = mc.load(p)
    assert mc.get_account_base(cache, "emulator-5554") == HOME
    assert (-29999, -1709) in mc.get_targets(cache, "remain")

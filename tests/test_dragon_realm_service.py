from dragon_realm.service import run_loop, LoopLimits, _resolve_role_id
from dragon_realm.planner import Action, Prefs
from dragon_realm.state import DragonState
from dragon_realm import constants as C


class FakeClient:
    """回放預設 state 序列；記錄 dispatch。"""
    def __init__(self, states):
        self._states = list(states)
        self._i = 0
        self.dispatched = []

    def read_state(self):
        return self._states[min(self._i, len(self._states) - 1)]

    def dispatch(self, action):
        self.dispatched.append(action)
        self._i += 1   # 每次動作推進到下一個 state


def _s(**over):
    base = dict(activity_open=True, ceng=1, hp=30, server_time=0, help_hp=0,
                event_id=0, event_type=0, event_uid=0, is_challenge=False,
                is_ask_help=False, back_kill_time=0, event_list=(), help_events=(), bag={})
    base.update(over)
    return DragonState(**base)


class Cfg:
    tier_two_require = (1518, 1)
    tier_three_require = (1519, 1)
    stamina_tier = (5, 8, 10)
    back_kill_cooldown = 600
    chest_key = {}
    stamina_item_gtid = 0


PREFS = Prefs(my_role_id=1)


def test_resolve_role_id_falls_back_to_captured_credentials():
    assert _resolve_role_id({}, detected_role_id=89555436834913) == 89555436834913
    assert _resolve_role_id({"dragon_realm_role_id": 777}, detected_role_id=888) == 777


def test_loop_stops_on_out_of_stamina():
    client = FakeClient([_s(hp=4)])
    report = run_loop(client, Cfg, PREFS, LoopLimits())
    assert report.stop_reason == "out_of_stamina"
    assert report.actions == 0


def test_loop_runs_then_stops_on_tier3_gate():
    client = FakeClient([_s(ceng=1, hp=30), _s(ceng=2, bag={1519: 1})])
    report = run_loop(client, Cfg, PREFS, LoopLimits())
    assert report.stop_reason == "reached_tier_three_gate"
    assert client.dispatched[0].kind == C.A_EXPLORE


def test_loop_aborts_on_deadloop():
    stuck = _s(event_id=5001, event_type=C.PVE, event_uid=9,
               is_challenge=True, is_ask_help=True, back_kill_time=10**9, server_time=0)
    client = FakeClient([stuck])
    report = run_loop(client, Cfg, PREFS, LoopLimits(max_wait_iters=3, sleep_fn=lambda s: None))
    assert report.stop_reason == "deadloop"


def test_loop_aborts_on_budget():
    client = FakeClient([_s(ceng=2, hp=30, bag={})])
    report = run_loop(client, Cfg, PREFS,
                      LoopLimits(max_actions=5, sleep_fn=lambda s: None))
    assert report.stop_reason == "budget_exhausted"
    assert report.actions == 5

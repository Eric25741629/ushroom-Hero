"""Tests for ws_token.dragon_realm run() — explore loop + dead-loop guard.

Field numbers (live-verified on 5554 via CDP info_s2c, 2026-06-26):
  dragon_realm info: ceng#2, hp#3, event_id#7, event_uid#8, event_data#9(repeated)
A stuck CAVE (event_data empty, choice has no effect on the ws_token socket) must
abort via dead-loop detection instead of burning the full action budget.
"""
import sys
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token import dragon_realm  # noqa: E402
from ws_token import dragon_sos  # noqa: E402
from ws_token.mining import InventoryTracker  # noqa: E402


def _info_body(ceng, hp, eid, euid, event_data=None):
    """Build a dragon_realm info_s2c body."""
    body = (codec.pb_uint(2, ceng) + codec.pb_uint(3, hp)
            + codec.pb_uint(7, eid) + codec.pb_uint(8, euid))
    for k, v in (event_data or []):
        body += codec.pb_msg(9, codec.pb_uint(1, k) + codec.pb_uint(2, v))
    return body


class StuckClient:
    """Always returns the same CAVE info (eid never changes); records sends.

    Models the observed bug: choice sent over the ws_token socket has no effect,
    so every read returns the identical stuck state.
    """

    def __init__(self, info_body):
        self._info = info_body
        self.sent = []

    def call(self, cmd, body=b"", **kw):
        return self._info  # never changes -> stuck

    def send(self, cmd, body=b""):
        self.sent.append((cmd, body))


class FakeServer:
    """Minimal server: explore spawns a monster + costs stamina, choice resolves it."""

    def __init__(self, *, ceng=1, hp=5):
        self.ceng = ceng
        self.hp = hp
        self.eid = 0
        self.euid = 0
        self.sent = []
        self._seq = 0

    def call(self, cmd, body=b"", **kw):
        return _info_body(self.ceng, self.hp, self.eid, self.euid)

    def send(self, cmd, body=b""):
        self.sent.append((cmd, body))
        if cmd == dragon_realm.CMD_EXPLORE:
            self.hp -= 1
            self._seq += 1
            self.eid = 4
            self.euid = 40004000000 + self._seq
        elif cmd == dragon_realm.CMD_CHOICE:
            self.eid = 0
            self.euid = 0


class ClaimingServer(FakeServer):
    """Fake WS server with one own completed help event."""

    def __init__(self):
        super().__init__(ceng=1, hp=0)
        self._creds = SimpleNamespace(role_id=777)
        event = (codec.pb_uint(1, 200)
                 + codec.pb_uint(2, 7)
                 + codec.pb_uint(3, 777)
                 + codec.pb_uint(4, 1))
        self._help_lists = [codec.pb_msg(1, event), b""]

    def call(self, cmd, body=b"", **kw):
        if cmd == dragon_sos.CMD_HELP_LIST:
            return self._help_lists.pop(0)
        return super().call(cmd, body, **kw)


def test_run_aborts_on_stuck_cave_instead_of_budget_exhausted():
    # CAVE: event_data empty, choice has no effect -> info frozen
    client = StuckClient(_info_body(2, 24, 16, 40016000002))
    reason = dragon_realm.run(client, InventoryTracker(), pace=0, max_actions=200)

    assert reason == "deadloop"
    # must bail out fast, not hammer the socket 200 times
    assert len(client.sent) < 20


def test_unknown_noncombat_event_detour_first_then_advance():
    """未知非戰鬥 eid（不在 CHEST_EIDS）：先 DETOUR（寶箱安全，不會誤開箱耗鑰），
    同一事件凍住不動才升級 ADVANCE(1) 結束山洞類事件。

    LIVE 實證 2026-07-16：寶箱不只一種、各有自己的 eid（12/13 不在舊
    CHEST_EIDS={11}），被直接 ADVANCE 開箱遭 server 拒絕而 deadloop。
    山洞(eid=14) 則相反：DETOUR 無效、只吃 ADVANCE。
    fallback 順序 DETOUR->ADVANCE 兩者都能收斂。
    """
    client = StuckClient(_info_body(1, 29, 15, 20015000000))

    dragon_realm.run(client, InventoryTracker(), pace=0, max_actions=2)

    assert [codec.walk_dict(b) for c, b in client.sent
            if c == dragon_realm.CMD_CHOICE] == [
        {1: dragon_realm.CHOICE_DETOUR, 2: 0},
        {1: dragon_realm.CHOICE_ADVANCE, 2: 0},
    ]


def test_chest_event_uses_detour_not_open():
    """寶箱(eid=11=秘銀龍骸箱)送 DETOUR(2)「繼續探索」，永不開箱、不耗秘銀龍鑰。

    對齊 LIVE 黃金樣本 frame 8（0x4F12 body=08021000={1:2,2:0}）。
    """
    client = StuckClient(_info_body(1, 29, 11, 20011000002))

    dragon_realm.run(client, InventoryTracker(), pace=0, max_actions=1)

    choice_cmd, choice_body = client.sent[0]
    assert choice_cmd == dragon_realm.CMD_CHOICE
    assert codec.walk_dict(choice_body) == {1: dragon_realm.CHOICE_DETOUR, 2: 0}


def test_run_normal_progress_does_not_false_trigger_deadloop():
    server = FakeServer(ceng=1, hp=5)  # tier1 stamina cost = 1 -> 5 explores
    reason = dragon_realm.run(server, InventoryTracker(), pace=0, max_actions=200)

    assert reason == "out_of_stamina"
    # explore was actually driven (stamina was spent), no premature abort
    assert server.hp <= 0
    assert any(cmd == dragon_realm.CMD_EXPLORE for cmd, _ in server.sent)


def test_run_claims_own_completed_help_reward_before_loop():
    server = ClaimingServer()

    reason = dragon_realm.run(server, InventoryTracker(), pace=0, max_actions=1)

    assert reason == "out_of_stamina"
    assert (dragon_sos.CMD_RECEIVE_HELP, codec.pb_uint(1, 7)) in server.sent

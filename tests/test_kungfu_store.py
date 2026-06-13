"""Unit tests for ws_token.kungfu_store (競猜商店 競猜幣 採購, pure WS).

No real device/socket: a FakeClient scripts shop_buy accept/reject per shop_id so
the buy-to-cap loop, idempotent stop-on-error, and report totals are covered.
"""
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from ws_token import codec
from ws_token.client import WSTimeoutError
from ws_token import kungfu_store as ks


class FakeClient:
    """Scripts shop_buy outcomes per shop_id.

    ``accept`` maps shop_id -> how many successful buys to grant before replying
    on the 0x0201 error channel. ``raise_on`` maps shop_id -> a WSTimeoutError on
    that shop's Nth (1-based) call.
    """

    def __init__(self, accept, *, error_code=159, raise_on=None):
        self.accept = dict(accept)
        self.error_code = error_code
        self.raise_on = dict(raise_on or {})
        self._seen: dict[int, int] = {}
        self.calls: list[int] = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        assert cmd == ks.CMD_SHOP_BUY
        d = codec.walk_dict(body)
        assert int(d.get(1)) == ks.SHOP_TYPE
        shop_id = int(d.get(2))
        assert int(d.get(3)) == 1            # always num=1
        self.calls.append(shop_id)
        n = self._seen.get(shop_id, 0) + 1
        self._seen[shop_id] = n
        if self.raise_on.get(shop_id) == n:
            raise WSTimeoutError(f"forced timeout shop_id={shop_id}")
        if n <= self.accept.get(shop_id, 0):
            return ks.CMD_SHOP_BUY, b""       # success
        return ks.CMD_ERR, codec.pb_uint(1, self.error_code)   # rejected


def test_buys_every_tier_to_cap_when_server_accepts():
    client = FakeClient(accept={15001: 9, 15002: 9, 15003: 9, 15004: 9})
    rep = ks.claim_guess_coins(client)
    assert rep.bought == {15001: 1, 15002: 1, 15003: 2, 15004: 3}
    # 100*1 + 200*1 + 300*2 + 500*3
    assert rep.coins == 100 + 200 + 600 + 1500
    assert rep.coins == 2400
    # 0 + 600*1 + 1500*2 + 3000*3
    assert rep.diamonds_spent == 600 + 3000 + 9000
    assert rep.diamonds_spent == 12600
    assert rep.stopped == {}            # every tier hit its cap cleanly
    assert rep.any_bought


def test_idempotent_all_rejected_buys_nothing():
    # Already maxed / event window closed -> first attempt of each errors.
    client = FakeClient(accept={}, error_code=159)
    rep = ks.claim_guess_coins(client)
    assert rep.bought == {}
    assert rep.coins == 0
    assert rep.diamonds_spent == 0
    assert not rep.any_bought
    # one probe per tier, then stop
    assert client.calls == [15001, 15002, 15003, 15004]
    assert set(rep.stopped) == {15001, 15002, 15003, 15004}


def test_partial_stop_on_insufficient_diamonds():
    # 15004 accepts 1 then rejects (粉鑽 ran out) before its cap of 3.
    client = FakeClient(accept={15001: 1, 15002: 1, 15003: 2, 15004: 1})
    rep = ks.claim_guess_coins(client)
    assert rep.bought == {15001: 1, 15002: 1, 15003: 2, 15004: 1}
    assert rep.coins == 100 + 200 + 600 + 500
    assert rep.diamonds_spent == 600 + 3000 + 3000
    assert rep.stopped == {15004: 159}        # only the under-cap tier records a stop


def test_timeout_mid_tier_records_zero_code_and_continues():
    # 15003 times out on its 2nd buy; later tiers still attempted.
    client = FakeClient(accept={15001: 1, 15002: 1, 15003: 9, 15004: 9},
                        raise_on={15003: 2})
    rep = ks.claim_guess_coins(client)
    assert rep.bought[15001] == 1
    assert rep.bought[15002] == 1
    assert rep.bought[15003] == 1            # one success before the timeout
    assert rep.bought[15004] == 3            # unaffected; bought to cap
    assert rep.stopped[15003] == 0           # timeout -> code 0


def test_buy_body_shape():
    body = ks.build_shop_buy_body(14, 15003, 1)
    d = codec.walk_dict(body)
    assert int(d.get(1)) == 14
    assert int(d.get(2)) == 15003
    assert int(d.get(3)) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

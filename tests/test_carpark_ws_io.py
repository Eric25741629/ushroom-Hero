"""Contract tests for pure-WS car-park decoration read + write.

Validates that routes_tools_optimize plumbing correctly carries shop_id,
plans with the WS-shaped state dict, and delegates exec to the WS module.
"""
import control_panel.routes_tools_optimize as routes
from ws_token import carpark_decoration_ws as deco_ws
from ws_token import codec


# --- 菇車幣 read via role_info 0x0301 (numeric attr 201) --------------------

def _num_attr(aid, val):
    """Encode one numeric role attr {1:id, 2:int} as a repeated f1 entry."""
    return codec.pb_msg(1, codec.pb_uint(1, aid) + codec.pb_uint(2, val))


def _str_attr(aid, s):
    """Encode one string role attr {1:id, 2:bytes} as a repeated f2 entry."""
    return codec.pb_msg(2, codec.pb_uint(1, aid) + codec.pb_str(2, s))


def _role_info_body(num_attrs, str_attrs=()):
    """Build a 0x0301 body: {1:{1:repeated id, 2:{1:num[], 2:str[]}}}.

    Mirrors the live 7fe98fc6 structure so the parser is tested against the
    real nesting (numeric table at 1.2.1[], string table at 1.2.2[]).
    """
    table = b"".join(_num_attr(a, v) for a, v in num_attrs.items())
    table += b"".join(_str_attr(a, s) for a, s in str_attrs)
    id_list = b"".join(codec.pb_uint(1, a) for a in num_attrs)  # repeated f1 ids
    container = id_list + codec.pb_msg(2, table)
    return codec.pb_msg(1, container)


class _FakeRoleClient:
    """Minimal WSGameClient stand-in: call_for(769) -> a canned 0x0301 reply."""

    def __init__(self, reply=None, raises=None):
        self._reply = reply
        self._raises = raises
        self.sent = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        self.sent.append((cmd, body, expect_cmds))
        if self._raises is not None:
            raise self._raises
        return cmd, self._reply


def test_parse_role_num_attrs_extracts_car_coin_and_ignores_strings():
    body = _role_info_body(
        {201: 92_983_699, 301: 47_900_256, 203: 4_527},
        str_attrs=[(1003, "nickname")],
    )
    attrs = deco_ws.parse_role_num_attrs(body)
    assert attrs[deco_ws.ROLE_ATTR_CAR_COIN] == 92_983_699
    assert attrs[301] == 47_900_256
    assert 1003 not in attrs  # string attrs excluded from the numeric table


def test_read_car_coin_returns_latest_attr_201():
    client = _FakeRoleClient(reply=_role_info_body({201: 92_983_699}))
    coin, err = deco_ws.read_car_coin(client)
    assert coin == 92_983_699
    assert err is None
    # on-demand read: sends an empty c2s 769 to force a fresh snapshot
    assert client.sent[0][0] == deco_ws.CMD_ROLE_INFO
    assert client.sent[0][1] == b""


def test_read_car_coin_missing_attr_is_explicit_unknown():
    client = _FakeRoleClient(reply=_role_info_body({301: 1}))  # no 201
    coin, err = deco_ws.read_car_coin(client)
    assert coin is None
    assert err and "201" in err


def test_read_car_coin_timeout_is_graceful():
    from ws_token.client import WSTimeoutError
    client = _FakeRoleClient(raises=WSTimeoutError("no role_info"))
    coin, err = deco_ws.read_car_coin(client)
    assert coin is None
    assert err is not None


def _ws_state():
    """A WS-read-shaped device state: decos carry shop_id (not cat/cell)."""
    return {
        "coin": 20_000_000,
        "decos": [
            {"id": 10003, "name": "中式庭院大門", "level": 9, "shop_id": 1705,
             "price": 300000, "limit_remaining": 95,
             "steps": [[10, 5, 48000], [11, 10, 48000]]},
        ],
    }


def test_build_decos_carries_shop_id_for_executor():
    decos, meta = routes._build_decos(_ws_state())
    assert len(decos) == 1
    assert decos[0].id == 10003
    assert meta[10003]["shop_id"] == 1705


def test_plan_steps_expose_shop_id():
    plan = routes._plan(_ws_state(), budget=0, max_steps=5)
    assert plan["steps"], "expected at least one affordable planned step"
    assert all("shop_id" in s for s in plan["steps"])
    assert plan["steps"][0]["shop_id"] == 1705
    assert plan["steps"][0]["id"] == 10003


def test_plan_with_user_budget_when_coin_unknown():
    state = _ws_state()
    state["coin"] = None
    plan = routes._plan(state, budget=5_000_000, max_steps=5)
    assert plan["coin"] is None
    assert plan["budget"] == 5_000_000


def test_plan_requires_manual_budget_when_coin_unknown_and_budget_blank():
    state = _ws_state()
    state["coin"] = None
    state["coin_error"] = "attr_201_absent_in_role_info"
    plan = routes._plan(state, budget=0, max_steps=5)
    assert plan["coin"] is None
    assert plan["coin_error"] == "attr_201_absent_in_role_info"
    assert plan["budget"] == 0
    assert plan["steps"] == []
    assert plan["skipped_reason"] == "coin_unknown_need_budget"


def test_plan_carries_coin_source_when_coin_known():
    state = _ws_state()
    state["coin_source"] = "role_info_0x0301"
    plan = routes._plan(state, budget=0, max_steps=5)
    assert plan["coin"] == 20_000_000
    assert plan["coin_source"] == "role_info_0x0301"
    assert plan["coin_error"] is None


def test_exec_step_delegates_to_ws_module(monkeypatch):
    captured = {}

    def fake_ws_client(ip):
        return "fake_client", None

    def fake_exec(client, shop_id, skin_id, frags, timeout=10):
        captured.update(client=client, shop_id=shop_id, skin_id=skin_id, frags=frags)
        return {"ok": True, "bought": True, "after_level": 10}, None

    monkeypatch.setattr(routes, "_ws_client", fake_ws_client)
    monkeypatch.setattr(routes.deco_ws, "exec_buy_and_upgrade", fake_exec)

    step = {"id": 10003, "shop_id": 1705, "frags": 5, "name": "中式庭院大門",
            "from_level": 9, "to_level": 10, "coin": 1_500_000}
    res, err = routes._exec_step("emulator-5554", step)
    assert res["ok"]
    assert captured["client"] == "fake_client"
    assert captured["shop_id"] == 1705
    assert captured["skin_id"] == 10003
    assert captured["frags"] == 5

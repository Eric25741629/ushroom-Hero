"""Contract tests for pure-WS car-park decoration read + write.

Validates that routes_carpark_decorate_tools plumbing correctly carries shop_id,
plans with the WS-shaped state dict, and delegates exec to the WS module.
"""
import control_panel.routes_carpark_decorate_tools as routes
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

    def fake_exec(client, shop_id, skin_id, frags, timeout=10, target_level=None,
                  skin_up_gap=0.0):
        captured.update(client=client, shop_id=shop_id, skin_id=skin_id,
                        frags=frags, target_level=target_level,
                        skin_up_gap=skin_up_gap)
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
    assert captured["target_level"] == 10, "exec must know the planned star cap"
    assert captured["skin_up_gap"] == routes._STEP_GAP_S


# --- exec_buy_and_upgrade robustness (live 2026-07-05 7fe98fc6) --------------
#
# Live fact: a raw-protobuf 12817 skin_up is EXECUTED by the server but its
# reply frame is unreliable (observed: 1 reply / 1 silence out of 2 upgrades;
# the silent one still levelled up server-side). The executor must treat reply
# frames as fast-path only and ground-truth every mutation by re-reading
# 12801 (levels) / 6913 (buy counts), and must decode 0x0201 error codes.

from types import SimpleNamespace

from ws_token.client import WSTimeoutError


def _skin_list_body(skins):
    """12801 s2c: field#8 repeated p_car_park_skin {1:skin_id, 2:skin_lev}."""
    return b"".join(
        codec.pb_msg(8, codec.pb_uint(1, sid) + codec.pb_uint(2, lev))
        for sid, lev in skins)


def _buy_info_body(counts):
    """6913 s2c: field#2 repeated p_key_value {1:shop_id, 2:buy_count}."""
    return b"".join(
        codec.pb_msg(2, codec.pb_uint(1, k) + codec.pb_uint(2, v))
        for k, v in counts.items())


def _skin_up_ok_body(sid, lev):
    """12817 s2c: {1:code=0, 2:{1:skin_id, 2:skin_lev}}."""
    return codec.pb_uint(1, 0) + codec.pb_msg(
        2, codec.pb_uint(1, sid) + codec.pb_uint(2, lev))


def _inventory_body(bag):
    """0x0401 reply: flat repeated {1:item_id, 3:count} under field 1."""
    return b"".join(
        codec.pb_msg(1, codec.pb_uint(1, iid) + codec.pb_uint(3, cnt))
        for iid, cnt in (bag or {}).items())


def _frag_item(skin_id):
    """The bag item_id of a decoration's fragment (from the real catalog)."""
    return deco_ws._frag_goods_of(deco_ws._load_catalog(), skin_id)


class _FakeExecClient:
    """Scripted stand-in: pops one canned action per call/call_for.

    Each action is ("reply", cmd, body) or ("raise", exc). The 0x0401 bag query
    (held-frags source) is answered out-of-band from ``bag`` WITHOUT consuming a
    script action, so held is set by ``bag`` and the scripts stay call-ordered.
    """

    def __init__(self, script, bag=None):
        self._creds = SimpleNamespace(role_id=42)
        self.script = list(script)
        self.sent = []
        self.bodies = []
        self.bag = bag or {}

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        if cmd == deco_ws.CMD_INVENTORY_QUERY:
            return cmd, _inventory_body(self.bag)  # held frags — no script pop
        self.sent.append(cmd)
        self.bodies.append((cmd, body))
        action = self.script.pop(0)
        if action[0] == "raise":
            raise action[1]
        return action[1], action[2]

    def call(self, cmd, body=b"", *, expect_cmd=None, timeout=None):
        _cmd, reply = self.call_for(
            cmd, body, expect_cmds=(expect_cmd or cmd,), timeout=timeout)
        return reply


def test_exec_buy_rejected_decodes_error_code():
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # before level
        ("reply", 6913, _buy_info_body({1753: 0})),        # pre-buy count
        ("reply", 513, codec.pb_uint(1, 3)),               # buy rejected code=3
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is False
    assert res["bought"] is False
    assert res["err"] == "buy_rejected_code_3"


def test_exec_buy_reply_lost_but_bought_continues_to_upgrade():
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # before level
        ("reply", 6913, _buy_info_body({1753: 0})),        # pre-buy count
        ("raise", WSTimeoutError("no response for cmd=6914")),
        ("reply", 6913, _buy_info_body({1753: 1})),        # verify: count +1
        ("reply", 12817, _skin_up_ok_body(40097, 2)),      # upgrade ok
        ("reply", 12801, _skin_list_body([(40097, 2)])),   # verify level
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["bought"] is True
    assert res["after_level"] == 2


def test_exec_buy_reply_lost_and_not_bought_aborts():
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("raise", WSTimeoutError("no response for cmd=6914")),
        ("reply", 6913, _buy_info_body({1753: 0})),        # verify: unchanged
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is False
    assert res["bought"] is False
    assert res["err"] == "buy_unconfirmed_no_reply"


def test_exec_upgrade_reply_lost_but_levelled_is_success():
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("raise", WSTimeoutError("no response for cmd=12817")),
        ("reply", 12801, _skin_list_body([(40097, 2)])),   # verify: level +1
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["bought"] is True
    assert res["after_level"] == 2


def test_exec_upgrade_reply_lost_and_no_levelup_fails(monkeypatch):
    monkeypatch.setattr(deco_ws, "_COOLDOWN_WAIT_S", 0.0)
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("raise", WSTimeoutError("no response for cmd=12817")),
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # verify: unchanged
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # post-cooldown re-verify
        ("raise", WSTimeoutError("no response for cmd=12817")),  # retry silent
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # final verify
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is False
    assert res["bought"] is True                            # coin WAS spent
    assert res["err"] == "upgrade_no_levelup"


def test_exec_upgrade_dropped_by_cooldown_retried_once(monkeypatch):
    """Server silently drops a skin_up sent too soon after the previous one
    (live 2026-07-05: ~1s gap dropped, 10s gap ok). After the cooldown wait the
    executor must re-verify and re-send once."""
    monkeypatch.setattr(deco_ws, "_COOLDOWN_WAIT_S", 0.0)
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("raise", WSTimeoutError("no response for cmd=12817")),  # dropped
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # verify: unchanged
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # post-cooldown re-verify
        ("reply", 12817, _skin_up_ok_body(40097, 2)),      # retry replies
        ("reply", 12801, _skin_list_body([(40097, 2)])),   # final verify
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["after_level"] == 2


def test_exec_upgrade_late_execution_caught_by_reverify(monkeypatch):
    """A dropped-looking upgrade that lands late must be caught by the
    post-cooldown re-verify WITHOUT re-sending (no double upgrade)."""
    monkeypatch.setattr(deco_ws, "_COOLDOWN_WAIT_S", 0.0)
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("raise", WSTimeoutError("no response for cmd=12817")),
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # verify: unchanged
        ("reply", 12801, _skin_list_body([(40097, 2)])),   # re-verify: landed!
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["after_level"] == 2
    assert client.sent.count(14337) == 1, "must not re-send after late landing"


def test_exec_resent_flag_marks_cooldown_resend(monkeypatch):
    """A step that had to RE-SEND skin_up (cooldown drop) must report
    resent=True so the job loop can back off to the safe gap."""
    monkeypatch.setattr(deco_ws, "_COOLDOWN_WAIT_S", 0.0)
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("raise", WSTimeoutError("no response for cmd=12817")),  # dropped
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["resent"] is True


def test_exec_skin_up_gap_waits_only_remaining_cooldown(monkeypatch):
    """skin_up_gap counts from the PREVIOUS skin_up send: time already spent
    on reads/buys is credited, only the remainder is slept."""
    import time as _time
    slept = []
    monkeypatch.setattr(deco_ws.time, "sleep", lambda s: slept.append(s))
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ])
    client._last_skin_up_ts = _time.monotonic() - 2.0   # last send was 2s ago
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1, skin_up_gap=5.0)
    assert err is None
    assert res["ok"] is True
    assert not res.get("resent")
    assert len(slept) == 1
    assert 2.5 < slept[0] <= 3.0, "only the remaining ~3s should be slept"
    assert client._last_skin_up_ts > _time.monotonic() - 1.0, \
        "send timestamp must be re-stamped for the next step"


def test_exec_skin_up_gap_first_send_no_wait(monkeypatch):
    """No previous skin_up on this connection: send immediately, no sleep."""
    slept = []
    monkeypatch.setattr(deco_ws.time, "sleep", lambda s: slept.append(s))
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1, skin_up_gap=5.0)
    assert err is None
    assert res["ok"] is True
    assert not slept
    assert hasattr(client, "_last_skin_up_ts")


def test_exec_retry_rejected_but_first_send_landed_is_success(monkeypatch):
    """First send lands after the re-verify; the retry gets rejected (frags
    already consumed). The level re-read must win over the reject."""
    monkeypatch.setattr(deco_ws, "_COOLDOWN_WAIT_S", 0.0)
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("raise", WSTimeoutError("no response for cmd=12817")),
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # verify: unchanged
        ("reply", 12801, _skin_list_body([(40097, 1)])),   # re-verify: unchanged
        ("reply", 513, codec.pb_uint(1, 3)),               # retry rejected
        ("reply", 12801, _skin_list_body([(40097, 2)])),   # but level landed!
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["after_level"] == 2


def test_exec_upgrade_rejected_decodes_error_code():
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 513, codec.pb_uint(1, 3)),                # upgrade rejected
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is False
    assert res["bought"] is True
    assert res["err"] == "upgrade_rejected_code_3"


def test_exec_upgrade_is_sent_as_json_proto_envelope():
    """The real client sends skin_up via json_proto (netManager.send ..., true).

    Raw-protobuf 12817 is handled erratically by the server (live 2026-07-05:
    executed+replied / executed silently / silently ignored). The upgrade MUST
    go out as 14337 json_proto_c2s {1:proto_id=12817, 2:msg=json}.
    """
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None and res["ok"] is True
    assert 14337 in client.sent, "upgrade must use the json_proto envelope"
    assert 12817 not in client.sent, "raw-protobuf 12817 must not be sent"


# --- off-shelf (open_time window) filtering ----------------------------------
#
# Live 2026-07-05: buying frags of a seasonal decoration whose configMall
# open_time window has passed is rejected with 0x0201 code=283. The static
# mall dump must carry the window and read_state must zero the purchase quota
# outside it, so the planner never schedules an unbuyable decoration.

import datetime


_WINDOW = [[[2024, 6, 28], [0, 0, 0]], [[2024, 7, 21], [23, 59, 55]]]


def test_is_on_sale_inside_window():
    now = datetime.datetime(2024, 7, 1, 12, 0, 0)
    assert deco_ws._is_on_sale(_WINDOW, now=now) is True


def test_is_on_sale_outside_window():
    now = datetime.datetime(2026, 7, 5, 4, 0, 0)
    assert deco_ws._is_on_sale(_WINDOW, now=now) is False


def test_is_on_sale_no_window_is_always_on():
    assert deco_ws._is_on_sale(None) is True


def test_mall_dump_carries_open_time_for_seasonal_frags():
    # 60109 = 10009's frag, live-verified rejected (code 283) on 2026-07-05
    mall = deco_ws._load_mall()
    assert mall[60109].get("open_time"), "seasonal frag must carry its window"
    assert not mall[60412].get("open_time"), "evergreen frag must have none"


def test_read_state_zeroes_quota_for_off_shelf_deco():
    # 10009's frag shop entry (1739) expired 2024-07-21 -> not plannable.
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(10009, 1), (40097, 1)])),
        ("reply", 6913, _buy_info_body({1739: 1, 1753: 1})),
        ("reply", 769, _role_info_body({201: 1_000_000})),
    ])
    state, err = deco_ws.read_state(client)
    assert err is None
    by_id = {d["id"]: d for d in state["decos"]}
    assert by_id[10009]["limit_remaining"] == 0
    assert by_id[10009]["off_shelf"] is True
    assert by_id[40097]["limit_remaining"] > 0
    assert by_id[40097]["off_shelf"] is False


# --- idempotent buy + target-level cap (WS 斷線續跑, live 2026-07-05) ---------
#
# A run died mid-step with WebSocketConnectionClosedException: the step's buy
# may have landed (coin spent) while the upgrade did not. Retrying/re-running
# must never double-buy frags nor upgrade past the planned star. held frags =
# shop bought count (6913) minus the catalog ladder's consumption for the
# current level (row 0 excluded — decorations come from the free picker).


def test_exec_target_level_reached_skips_all_mutations():
    """Reconnect retry: the interrupted upgrade landed late — never re-send."""
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 2)])),   # already at target
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1, target_level=2)
    assert err is None
    assert res["ok"] is True
    assert res["after_level"] == 2
    assert 6914 not in client.sent and 14337 not in client.sent


def test_exec_skips_buy_when_frags_already_held():
    """Held frags (bag ground truth) cover the step -> no buy, no double spend:
    bag holds 1, step needs 1 -> upgrade directly."""
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),      # upgrade directly
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ], bag={_frag_item(40097): 1})                          # held 1 >= frags 1
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert 6914 not in client.sent, "held frags must not be re-bought"
    assert res["frags_bought"] == 0


def test_exec_buys_only_the_shortfall():
    """Bag holds 1; the step needs 2 frags so only the shortfall (1) is bought."""
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 2)])),
        ("reply", 6913, _buy_info_body({1753: 2})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 12817, _skin_up_ok_body(40097, 3)),
        ("reply", 12801, _skin_list_body([(40097, 3)])),
    ], bag={_frag_item(40097): 1})                          # holds 1 of 2 needed
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=2)
    assert err is None
    assert res["ok"] is True
    buy_bodies = [b for c, b in client.bodies if c == 6914]
    assert codec.walk_dict(buy_bodies[0])[3] == 1, "must buy only the shortfall"
    assert res["frags_bought"] == 1


def test_exec_held_overestimate_selfheals_on_reject():
    """Safety net: if held (bag) looked sufficient but the upgrade is rejected
    for 次數不足, buy the shortfall once and resend (total bought <= frags)."""
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 513, codec.pb_uint(1, 159)),             # skin_up rejected 次數不足
        ("reply", 6913, _buy_info_body({1753: 1})),        # shortfall-buy pre read
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),      # resend lands
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ], bag={_frag_item(40097): 1})                          # held looked enough
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is True
    assert res["after_level"] == 2
    assert 6914 in client.sent
    assert res["frags_bought"] == 1


def test_exec_full_buy_reject_does_not_selfheal():
    """A reject after buying the FULL planned frags is a real reject — no
    shortfall loop, no extra spend."""
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),        # nothing held
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 513, codec.pb_uint(1, 3)),               # upgrade rejected
    ])
    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1)
    assert err is None
    assert res["ok"] is False
    assert res["err"] == "upgrade_rejected_code_3"
    assert client.sent.count(6914) == 1, "must not buy again after a real reject"


# --- execute job: reconnect + resume on connection loss ----------------------


from control_panel import tools_optimize_jobs as jobs


def test_execute_job_reconnects_and_retries_on_conn_lost(monkeypatch):
    monkeypatch.setattr(routes, "_STEP_GAP_S", 0)
    monkeypatch.setattr(routes, "_read_state", lambda ip: (_ws_state(), None))
    calls = {"exec": 0, "ensure": 0}

    def fake_exec(ip, step, gap=None):
        calls["exec"] += 1
        if calls["exec"] == 1:
            return None, ("WebSocketConnectionClosedException: "
                          "socket is already closed.")
        return {"ok": True, "bought": True, "after_level": step["to_level"],
                "frags_bought": step["frags"]}, None

    def fake_ensure(ip):
        calls["ensure"] += 1
        return {"status": "ok", "connected": True}

    monkeypatch.setattr(routes, "_exec_step", fake_exec)
    monkeypatch.setattr(routes.ws_session, "ensure", fake_ensure)
    monkeypatch.setattr(routes.ws_session, "get_client", lambda ip: None)

    jid = jobs._new_job()
    routes._run_execute_job(jid, "emulator-5554", 0, 5)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert calls["ensure"] == 1, "connection loss must trigger one reconnect"
    result = job["result"]
    assert result["stopped_reason"] is None
    assert len(result["executed"]) == 2
    assert all(s["ok"] for s in result["executed"])


def test_execute_job_non_conn_error_stops_without_retry(monkeypatch):
    monkeypatch.setattr(routes, "_STEP_GAP_S", 0)
    monkeypatch.setattr(routes, "_read_state", lambda ip: (_ws_state(), None))
    ensure_calls = []
    exec_calls = []

    def fake_exec(ip, step, gap=None):
        exec_calls.append(step["to_level"])
        return {"ok": False, "bought": True, "frags_bought": step["frags"],
                "err": "upgrade_rejected_code_3"}, None

    def fake_ensure(ip):
        ensure_calls.append(ip)
        return {"status": "ok"}

    monkeypatch.setattr(routes, "_exec_step", fake_exec)
    monkeypatch.setattr(routes.ws_session, "ensure", fake_ensure)

    jid = jobs._new_job()
    routes._run_execute_job(jid, "emulator-5554", 0, 5)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert not ensure_calls, "logic rejects must not trigger a reconnect"
    assert len(exec_calls) == 1
    assert job["result"]["stopped_reason"] == "step_failed:upgrade_rejected_code_3"


def test_execute_job_starts_fast_and_backs_off_after_resend(monkeypatch):
    """Steps start at the optimistic gap; the first cooldown re-send flips the
    remaining steps to the proven-safe gap."""
    monkeypatch.setattr(routes, "_read_state", lambda ip: (_ws_state(), None))
    gaps = []

    def fake_exec(ip, step, gap):
        gaps.append(gap)
        return {"ok": True, "bought": True, "after_level": step["to_level"],
                "frags_bought": step["frags"],
                "resent": len(gaps) == 1}, None

    monkeypatch.setattr(routes, "_exec_step", fake_exec)

    jid = jobs._new_job()
    routes._run_execute_job(jid, "emulator-5554", 0, 5)
    job = jobs._jobs[jid]
    assert job["status"] == "done"
    assert len(gaps) == 2
    assert gaps[0] == routes._FAST_GAP_S
    assert gaps[1] == routes._STEP_GAP_S, "a re-send must back off the gap"

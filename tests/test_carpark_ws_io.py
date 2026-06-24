"""Contract tests for pure-WS car-park decoration read + write.

Validates that routes_tools_optimize plumbing correctly carries shop_id,
plans with the WS-shaped state dict, and delegates exec to the WS module.
"""
import control_panel.routes_tools_optimize as routes


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
    assert plan["budget"] == 5_000_000


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

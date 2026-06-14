"""TDD contract for the pure-WS car-park decoration read + write.

Defines the behaviour OpenCode must implement when converting the dashboard
車位裝飾 tool from the slow cocos scene-tree walk to pure WebSocket I/O. These
tests are written FIRST and fail until the implementation lands.

Protocol (live-verified 2026-06-15, docs/protocol/CARPARK_DECORATION_SHOP.md §10):
  read  : car_park.car_park_info_c2s (12801, {type:0, master_id, ceng:0}) -> skin_list
          + shop.shop_info_c2s (6913, {shop_type:11}) -> per-shop_id bought count
          + 菇車幣 = role attribute 201
          item_id/price/cap come from the client config configMall.
  write : shop.shop_buy_c2s (6914, {shop_type:11, shop_id, num}) -> buy fragments
          + car_park.car_park_skin_up_c2s (12817, {type:0, skin_id}) -> upgrade 1 star

The WS read replaces the cocos ``cat``/``cell`` grid coordinates with the mall
``shop_id``; the executor buys + upgrades over WS using that shop_id + skin id.
"""
import json

import control_panel.carpark_tools_js as js
import control_panel.routes_tools_optimize as routes


# --- injected-JS payload structural contract --------------------------------
# The payloads run in-browser (can't execute in pytest), so we assert they are
# the WS path (right cmd names) and NOT the slow cocos-navigation path.

def test_read_state_ws_payload_uses_ws_reads_not_cocos_walk():
    src = js.READ_STATE_WS_JS
    assert isinstance(src, str) and src.strip(), "READ_STATE_WS_JS must be a JS string"
    assert "car_park.car_park_info_c2s" in src      # skin_list (owned + levels)
    assert "shop.shop_info_c2s" in src              # per-shop_id bought count
    assert "201" in src                             # 菇車幣 role attribute
    # must not fall back to the ~90s cocos scene-tree walk
    assert "ParkingDecorateView" not in src
    assert "btnSkin" not in src


def test_exec_step_ws_payload_uses_ws_buy_and_upgrade_not_cocos():
    src = js.EXEC_STEP_WS_JS
    assert isinstance(src, str) and src.strip(), "EXEC_STEP_WS_JS must be a JS string"
    assert "shop.shop_buy_c2s" in src              # buy fragments
    assert "car_park.car_park_skin_up_c2s" in src  # upgrade one star
    # must not drive the cocos buy/upgrade UI
    assert "MallTipsView" not in src
    assert "btnUnlock" not in src


# --- routes plumbing: cat/cell -> shop_id -----------------------------------

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


def test_exec_step_invokes_ws_payload_with_shop_and_skin_args(monkeypatch):
    captured = {}

    def fake_cdp(ip, expression, timeout):
        captured["ip"] = ip
        captured["expr"] = expression
        return {"ok": True, "bought": True, "after_level": 10}, None

    monkeypatch.setattr(routes, "_cdp_json", fake_cdp)
    step = {"id": 10003, "shop_id": 1705, "frags": 5, "name": "中式庭院大門",
            "from_level": 9, "to_level": 10, "coin": 1_500_000}
    routes._exec_step("emulator-5554", step)

    expr = captured["expr"]
    assert "shop.shop_buy_c2s" in expr, "executor must run the WS exec payload"
    # WS exec args = [shop_id, skin_id(=deco id), frags, do_upgrade=True]
    assert json.dumps([1705, 10003, 5, True]) in expr

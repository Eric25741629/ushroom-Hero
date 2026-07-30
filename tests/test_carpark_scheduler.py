"""H5 車位排程與 WS 倉庫領取的分流測試。"""

from types import SimpleNamespace

import pytest

import game_actions.carpark_scheduler as scheduler
from game_actions.carpark_scheduler import _ws_owns_warehouse_claim


def test_ws_enabled_carpark_plan_owns_warehouse_claim():
    cfg = {
        "ws_token": {
            "enabled": True,
            "carpark_plan": {"enabled": True},
        },
    }

    assert _ws_owns_warehouse_claim(cfg) is True


@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"ws_token": {"enabled": False, "carpark_plan": {"enabled": True}}},
        {"ws_token": {"enabled": True, "carpark_plan": {"enabled": False}}},
        {"ws_token": {"enabled": True}},
    ],
)
def test_h5_keeps_warehouse_claim_without_active_ws_carpark_plan(cfg):
    assert _ws_owns_warehouse_claim(cfg) is False


def test_scheduler_tells_h5_reconcile_to_skip_ws_owned_warehouse(monkeypatch):
    from utils import carpark_auto, carpark_click_recorder, cocos_navigator, pause_guard

    cfg = {
        "backend": "web_h5",
        "carpark": {"enabled": True},
        "ws_token": {
            "enabled": True,
            "carpark_plan": {"enabled": True},
        },
    }
    seen = {}

    class _Recorder:
        def close(self):
            pass

    def fake_reconcile(page, carpark_cfg, *, claim_warehouse_rewards):
        seen["page"] = page
        seen["cfg"] = carpark_cfg
        seen["claim_warehouse_rewards"] = claim_warehouse_rewards
        return {"snapshot": {}, "target": {}, "actions": []}

    monkeypatch.setattr(cocos_navigator, "_device_flag_enabled", lambda _ip: True)
    monkeypatch.setattr(scheduler.config_manager, "get_device_config", lambda _ip: cfg)
    monkeypatch.setattr(carpark_auto, "reconcile", fake_reconcile)
    monkeypatch.setattr(
        carpark_click_recorder,
        "CarparkClickRecorder",
        lambda *_args, **_kwargs: _Recorder(),
    )
    monkeypatch.setattr(carpark_click_recorder, "set_recorder", lambda _rec: None)
    monkeypatch.setattr(carpark_click_recorder, "clear_recorder", lambda: None)
    monkeypatch.setattr(pause_guard, "bind", lambda **_kwargs: None)
    monkeypatch.setattr(pause_guard, "unbind", lambda: None)
    monkeypatch.setattr(scheduler.bot_state, "update_state", lambda *_args, **_kwargs: None)

    page = object()
    scheduler.run_carpark_check_if_due(SimpleNamespace(_page=page), "web-device")

    assert seen == {
        "page": page,
        "cfg": cfg["carpark"],
        "claim_warehouse_rewards": False,
    }

"""5558 H5/CDP 原生方案切換：不走 OCR、不另開純 WS session。"""
from __future__ import annotations

import json

from game_actions import skill_manager
from control_panel.shared import cdp


def _cdp_ok(expression, **_kwargs):
    assert "roleControl.send_role_choose_plan_c2s(9)" in expression
    return {
        "result": {
            "type": "string",
            "value": json.dumps({"ok": True, "plan_id": 9}),
        }
    }, None


def test_switch_skill_h5_calls_native_role_controller(monkeypatch):
    monkeypatch.setattr(cdp, "_cdp_evaluate", lambda _ip, expression, **kwargs: _cdp_ok(
        expression, **kwargs
    ))
    monkeypatch.setattr(skill_manager.time, "sleep", lambda _seconds: None)

    assert skill_manager.switch_skill_h5("emulator-5558", "戰士推圖") is True


def test_switch_skill_h5_uses_live_verified_sleep_plan_id(monkeypatch):
    seen = {}

    def fake_eval(_ip, expression, **_kwargs):
        seen["expression"] = expression
        return {
            "result": {
                "type": "string",
                "value": json.dumps({"ok": True, "plan_id": 10}),
            }
        }, None

    monkeypatch.setattr(cdp, "_cdp_evaluate", fake_eval)
    monkeypatch.setattr(skill_manager.time, "sleep", lambda _seconds: None)

    assert skill_manager.switch_skill_h5("emulator-5558", "騙人用") is True
    assert "roleControl.send_role_choose_plan_c2s(10)" in seen["expression"]


def test_switch_skill_h5_fails_closed_on_cdp_error(monkeypatch):
    monkeypatch.setattr(
        cdp,
        "_cdp_evaluate",
        lambda *_args, **_kwargs: (None, "no CDP target"),
    )

    assert skill_manager.switch_skill_h5("emulator-5558", "戰士推圖") is False


def test_switch_skill_h5_rejects_unknown_name_without_cdp(monkeypatch):
    called = []
    monkeypatch.setattr(
        cdp,
        "_cdp_evaluate",
        lambda *_args, **_kwargs: called.append(True),
    )

    assert skill_manager.switch_skill_h5("emulator-5558", "不存在") is False
    assert called == []

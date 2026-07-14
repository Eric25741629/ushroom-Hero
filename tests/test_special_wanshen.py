import json

import config_manager


def test_special_wanshen_config_defaults():
    cfg = config_manager.DeviceConfig.from_dict({})

    assert cfg.get("special_wanshen_account") is False
    assert cfg.get("special_wanshen_enabled") is False
    assert cfg.get("special_wanshen_rounds") == 10


def test_special_wanshen_rounds_are_clamped(tmp_path, monkeypatch):
    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps({"devices": {"web-x": {}}}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(path))

    config_manager.update_device_config("web-x", {
        "special_wanshen_account": True,
        "special_wanshen_enabled": True,
        "special_wanshen_rounds": 999,
    })

    cfg = config_manager.get_device_config("web-x")
    assert cfg.get("special_wanshen_account") is True
    assert cfg.get("special_wanshen_enabled") is True
    assert cfg.get("special_wanshen_rounds") == 50

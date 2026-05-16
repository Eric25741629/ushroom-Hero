import pytest


def test_device_config_has_expected_defaults():
    from config_manager import DeviceConfig
    cfg = DeviceConfig()
    # Spot check a few critical defaults
    assert cfg.enable_farm is True
    assert cfg.enable_mining is True


def test_device_config_from_dict():
    from config_manager import DeviceConfig
    raw = {"enable_farm": False, "lamp_check_interval": 4}
    cfg = DeviceConfig.from_dict(raw)
    assert cfg.enable_farm is False
    assert cfg.lamp_check_interval == 4
    # Non-overridden fields keep defaults
    assert cfg.enable_mining is True


def test_device_config_unknown_keys_preserved_in_extra():
    from config_manager import DeviceConfig
    raw = {"enable_farm": True, "some_future_key": "future_value"}
    cfg = DeviceConfig.from_dict(raw)
    # Unknown keys should be accessible via .get() (backward compat)
    assert cfg.get("some_future_key") == "future_value"


def test_device_config_get_returns_default_for_missing_key():
    from config_manager import DeviceConfig
    cfg = DeviceConfig()
    assert cfg.get("nonexistent_key", "fallback") == "fallback"
    assert cfg.get("nonexistent_key") is None


def test_get_device_config_returns_dataclass():
    """get_device_config() must return DeviceConfig, not plain dict."""
    from config_manager import get_device_config, DeviceConfig
    # Use any test device ID — it should at least return defaults
    cfg = get_device_config("test-device-fake-ip")
    assert isinstance(cfg, DeviceConfig)


def test_legacy_dict_access_still_works():
    """Backward compat: existing callers use .get() — must still work."""
    from config_manager import get_device_config
    cfg = get_device_config("test-device-fake-ip")
    # These should not raise
    enable_farm = cfg.get("enable_farm", True)
    lamp_interval = cfg.get("lamp_check_interval", 2)
    assert isinstance(enable_farm, bool)
    assert isinstance(lamp_interval, int)


def test_device_config_known_field_via_get():
    """Known fields are returned correctly via .get()."""
    from config_manager import DeviceConfig
    cfg = DeviceConfig(backend="web_h5", lamp_check_interval=3)
    assert cfg.get("backend") == "web_h5"
    assert cfg.get("lamp_check_interval") == 3


def test_device_config_all_defaults_match_default_dict():
    """DeviceConfig defaults must match DEFAULT_DEVICE_CONFIG for the fields it covers."""
    from config_manager import DeviceConfig, DEFAULT_DEVICE_CONFIG
    cfg = DeviceConfig()
    for key in [
        "backend", "web_url", "web_canvas_selector", "web_profile_dir",
        "web_state_file", "web_channel", "web_headless", "web_clear_cookies_on_start",
        "web_viewport_width", "web_viewport_height", "web_stop_mode",
        "web_screenshot_method", "enable_farm", "enable_arena", "enable_mining",
        "enable_dungeon", "is_real_phone", "keep_screen_on", "screenshot_debug",
        "online_check_interval_sec", "lamp_check_interval", "lamp_duration_sec",
        "mining_duration_min", "mining_planner_version", "mining_save_samples",
        "sleep_min_hours", "sleep_max_hours",
    ]:
        assert cfg.get(key) == DEFAULT_DEVICE_CONFIG[key], (
            f"Default mismatch for {key!r}: DeviceConfig={cfg.get(key)!r}, "
            f"DEFAULT_DEVICE_CONFIG={DEFAULT_DEVICE_CONFIG[key]!r}"
        )

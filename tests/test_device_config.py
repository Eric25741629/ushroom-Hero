import pytest


def test_device_config_has_expected_defaults():
    from config_manager import DeviceConfig
    cfg = DeviceConfig()
    # Spot check a few critical defaults
    assert cfg.enable_farm is True
    assert cfg.enable_mining is True


def test_device_config_granular_dungeon_flags_default_true():
    """New granular 副本/任務 toggles default True in both dict and dataclass."""
    from config_manager import DEFAULT_DEVICE_CONFIG, DeviceConfig
    cfg = DeviceConfig()
    for key in ("enable_hellgate", "enable_wanshen", "enable_cloud_battle", "enable_biweekly"):
        assert DEFAULT_DEVICE_CONFIG[key] is True, f"DEFAULT_DEVICE_CONFIG[{key!r}] should be True"
        assert key in DeviceConfig.__dataclass_fields__, f"{key} missing from DeviceConfig"
        assert cfg.get(key) is True, f"DeviceConfig().{key} should default True"


def _raw_config_for(monkeypatch, user_config):
    """monkeypatch load_config 餵假 config，回傳 _get_raw_device_config 的 merge 結果。"""
    import config_manager
    monkeypatch.setattr(
        config_manager, "load_config",
        lambda: {"devices": {"test-ip": user_config}},
    )
    return config_manager._get_raw_device_config("test-ip")


def test_legacy_enable_dungeon_false_migrates_to_hellgate_false(monkeypatch):
    """舊 config 只有 enable_dungeon: false → 地獄之門 granular flag 推導為 False。"""
    merged = _raw_config_for(monkeypatch, {"enable_dungeon": False})
    assert merged["enable_hellgate"] is False
    # enable_dungeon_manager 未設 → _dm fallback 到 enable_dungeon=False → 其餘三個也 False
    assert merged["enable_wanshen"] is False
    assert merged["enable_cloud_battle"] is False
    assert merged["enable_biweekly"] is False


def test_legacy_dungeon_manager_false_migrates_but_hellgate_stays_true(monkeypatch):
    """只有 enable_dungeon_manager: false → wanshen/cloud/biweekly False，hellgate 仍 True。"""
    merged = _raw_config_for(monkeypatch, {"enable_dungeon_manager": False})
    assert merged["enable_hellgate"] is True
    assert merged["enable_wanshen"] is False
    assert merged["enable_cloud_battle"] is False
    assert merged["enable_biweekly"] is False


def test_explicit_granular_keys_win_over_legacy(monkeypatch):
    """user_config 有明確 granular key 時尊重 granular，不做 legacy 推導。"""
    merged = _raw_config_for(monkeypatch, {
        "enable_dungeon": False,
        "enable_dungeon_manager": False,
        "enable_hellgate": True,
        "enable_wanshen": True,
    })
    assert merged["enable_hellgate"] is True
    assert merged["enable_wanshen"] is True
    # 未明確給的仍走 legacy 推導 (_dm=False)
    assert merged["enable_cloud_battle"] is False
    assert merged["enable_biweekly"] is False


def test_no_legacy_keys_granular_defaults_true(monkeypatch):
    """全新 config（無 legacy 也無 granular）→ 四個 granular 皆預設 True。"""
    merged = _raw_config_for(monkeypatch, {})
    for key in ("enable_hellgate", "enable_wanshen", "enable_cloud_battle", "enable_biweekly"):
        assert merged[key] is True, f"{key} should default True"


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


def test_default_device_config_has_jpeg_quality_none():
    """web_screenshot_jpeg_quality defaults to None (PNG) so OCR/CNN input is unchanged."""
    from config_manager import DEFAULT_DEVICE_CONFIG
    assert DEFAULT_DEVICE_CONFIG["web_screenshot_jpeg_quality"] is None


def test_device_config_jpeg_quality_is_typed_field():
    """The flag must be a first-class DeviceConfig field, not just an _extra key."""
    from config_manager import DeviceConfig
    assert "web_screenshot_jpeg_quality" in DeviceConfig.__dataclass_fields__
    assert DeviceConfig().web_screenshot_jpeg_quality is None
    assert DeviceConfig(web_screenshot_jpeg_quality=85).get("web_screenshot_jpeg_quality") == 85


def test_device_config_web_reload_after_goto_defaults_false():
    """H5 navigation should load once by default; forced reload is opt-in."""
    from config_manager import DEFAULT_DEVICE_CONFIG, DeviceConfig

    assert DEFAULT_DEVICE_CONFIG["web_reload_after_goto"] is False
    assert "web_reload_after_goto" in DeviceConfig.__dataclass_fields__
    assert DeviceConfig().web_reload_after_goto is False
    assert DeviceConfig(web_reload_after_goto=True).get("web_reload_after_goto") is True


def test_device_config_skip_browser_when_all_done_defaults_false():
    """Phase D2: opt-in 純 WS 掛機旗標，預設 False → 行為與現況相同（一定會喚醒瀏覽器）。"""
    from config_manager import DEFAULT_DEVICE_CONFIG, DeviceConfig

    assert DEFAULT_DEVICE_CONFIG["skip_browser_when_all_done"] is False
    assert "skip_browser_when_all_done" in DeviceConfig.__dataclass_fields__
    assert DeviceConfig().skip_browser_when_all_done is False
    assert DeviceConfig(skip_browser_when_all_done=True).get("skip_browser_when_all_done") is True


def test_device_config_all_defaults_match_default_dict():
    """DeviceConfig defaults must match DEFAULT_DEVICE_CONFIG for the fields it covers."""
    from config_manager import DeviceConfig, DEFAULT_DEVICE_CONFIG
    cfg = DeviceConfig()
    for key in [
        "backend", "web_url", "web_canvas_selector", "web_profile_dir",
        "web_state_file", "web_channel", "web_headless", "web_clear_cookies_on_start",
        "web_viewport_width", "web_viewport_height", "web_stop_mode",
        "web_screenshot_method", "web_reload_after_goto", "enable_farm", "enable_arena", "enable_mining",
        "enable_dungeon", "is_real_phone", "keep_screen_on", "screenshot_debug",
        "online_check_interval_sec", "lamp_check_interval", "lamp_duration_sec",
        "mining_duration_min", "mining_planner_version", "mining_save_samples",
        "sleep_min_hours", "sleep_max_hours",
    ]:
        assert cfg.get(key) == DEFAULT_DEVICE_CONFIG[key], (
            f"Default mismatch for {key!r}: DeviceConfig={cfg.get(key)!r}, "
            f"DEFAULT_DEVICE_CONFIG={DEFAULT_DEVICE_CONFIG[key]!r}"
        )

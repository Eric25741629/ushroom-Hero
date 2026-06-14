"""ws_token nested device-config defaults (config_manager.DEFAULT_DEVICE_CONFIG)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_manager  # noqa: E402


def test_default_device_config_has_ws_token_disabled():
    # 2026-06-12 使用者指示：enabled 開了就代表全要 → 子功能預設全開，
    # 只有總開關 enabled 與破壞性的 forge_ring 維持 off。
    ws = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]
    assert ws["enabled"] is False
    assert ws["spend"] is True
    assert ws["open_lamp"] is True
    assert ws["bootstrap_token"] is True
    assert ws["couple_gifts"] is True
    assert ws["forge_ring"] is False
    assert ws["dungeon_sweeps"] == []
    assert ws["farm"] is None
    assert ws["carpark_target"] is None
    assert ws["workshop_rotate"] is True
    # 開神燈百分比 / 最低保留：預設 0（= 不依百分比 / 無下限），維持舊行為（開到沒燈）。
    assert ws["lamp_percent"] == 0
    assert ws["lamp_min_keep"] == 0


def test_merge_ws_token_fills_lamp_percent_min_keep_when_absent():
    # 裝置沒設 lamp_percent / lamp_min_keep → 合併後仍帶回預設 0。
    merged = config_manager._merge_ws_token_phase_config({"enabled": True})
    assert merged["lamp_percent"] == 0
    assert merged["lamp_min_keep"] == 0


def test_get_device_config_yields_lamp_defaults_for_bare_device(tmp_path, monkeypatch):
    # get_device_config 對沒設這兩鍵的裝置，巢狀 ws_token 仍補上預設 0。
    import json

    cfg_path = tmp_path / "bot_config.json"
    cfg_path.write_text(
        json.dumps({"devices": {"bare": {"backend": "adb"}},
                    "global": {"mode": "master"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr(config_manager, "_config_cache", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_mtime_ns", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_path", None, raising=False)

    cfg = config_manager.get_device_config("bare")
    ws = cfg.get("ws_token") or {}
    assert ws.get("lamp_percent") == 0
    assert ws.get("lamp_min_keep") == 0


def test_default_carpark_plan_disabled_with_day_night_quota():
    plan = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]["carpark_plan"]
    assert plan["enabled"] is False
    assert plan["silver_levels"] == [9, 10]
    # 跨界只開放台灣 10:00-22:00；夜窗 cross=0
    assert plan["day"] == {"window": ["10:00", "22:00"], "cross": 1}
    assert plan["night"] == {"window": ["22:00", "10:00"], "cross": 0}
    assert config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]["carpark_auto"] is False


def test_merge_carpark_plan_sanitizes_malformed_input():
    merged = config_manager._merge_ws_token_phase_config({
        "carpark_plan": {
            "enabled": 1,
            "day": {"window": ["8am", "20:00"], "cross": 99},
            "night": "garbage",
            "silver_levels": [0, 9, "x", 31],
        },
        "carpark_auto": "true",
    })
    plan = merged["carpark_plan"]
    assert plan["enabled"] is True
    assert plan["day"]["window"] == ["10:00", "22:00"]  # bad HH:MM -> default
    assert plan["day"]["cross"] == 10                   # clamped to 0..10
    assert plan["night"] == {"window": ["22:00", "10:00"], "cross": 0}
    assert plan["silver_levels"] == [9]                 # only valid 1..30 ints
    assert merged["carpark_auto"] is True

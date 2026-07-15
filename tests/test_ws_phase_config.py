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
    assert ws["lamp_daily_min"] == 0


def test_merge_ws_token_fills_lamp_percent_min_keep_when_absent():
    # 裝置沒設 lamp_percent / lamp_min_keep / lamp_daily_min → 合併後仍帶回預設 0。
    merged = config_manager._merge_ws_token_phase_config({"enabled": True})
    assert merged["lamp_percent"] == 0
    assert merged["lamp_min_keep"] == 0
    assert merged["lamp_daily_min"] == 0


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
    assert ws.get("lamp_daily_min") == 0


def test_default_relic_sprint_disabled_with_full_target():
    # 遺物碎片衝刺 (衝刺榜) 預設關 (SPENDS 遺物碎片)，target_spend = 全衝刺門檻 900000。
    rs = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]["relic_sprint"]
    assert rs == {"enabled": False, "target_spend": 900000}


def test_merge_relic_sprint_fills_default_when_absent():
    # 裝置沒設 relic_sprint → 合併後補回預設 (disabled, 900000)。
    merged = config_manager._merge_ws_token_phase_config({"enabled": True})
    assert merged["relic_sprint"] == {"enabled": False, "target_spend": 900000}


def test_merge_relic_sprint_coerces_enabled_and_target():
    # enabled 強轉 bool；target_spend 取正整數。
    merged = config_manager._merge_ws_token_phase_config({
        "relic_sprint": {"enabled": 1, "target_spend": "450000"},
    })
    assert merged["relic_sprint"] == {"enabled": True, "target_spend": 450000}


def test_merge_relic_sprint_bad_target_falls_back_to_default():
    # 非法 / 非正的 target_spend 退回預設 900000；壞 dict 整段退預設。
    bad_target = config_manager._merge_ws_token_phase_config({
        "relic_sprint": {"enabled": True, "target_spend": -5},
    })
    assert bad_target["relic_sprint"] == {"enabled": True, "target_spend": 900000}
    not_dict = config_manager._merge_ws_token_phase_config({
        "relic_sprint": "garbage",
    })
    assert not_dict["relic_sprint"] == {"enabled": False, "target_spend": 900000}


def test_default_carpark_plan_disabled_with_day_night_quota():
    plan = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]["carpark_plan"]
    assert plan["enabled"] is False
    assert plan["silver_levels"] == [9, 10]
    # 跨界只開放台灣 10:00-22:00；夜窗 cross=0
    assert plan["day"] == {"window": ["10:00", "22:00"], "cross": 1}
    assert plan["night"] == {"window": ["22:00", "10:00"], "cross": 0}
    # 搶位分層 + 每秒重試預設 (2026-06-15)
    assert plan["cluster_min"] == 5
    assert plan["grab_window_seconds"] == 60
    assert plan["grab_poll_seconds"] == 1.0
    assert plan["grab_attempts"] == 8
    assert plan["allow_low_noncluster"] is False
    assert config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]["carpark_auto"] is False


def test_merge_carpark_plan_preserves_strict_cluster_scan():
    merged = config_manager._merge_ws_token_phase_config({
        "carpark_plan": {
            "enabled": True,
            "cluster_scan": {
                "enabled": True,
                "levels": [1, 2, 3, 4, 9],
                "priority_levels": [1, 4, 9],
                "excluded_levels": [2],
                "min_allies": 2,
            },
        },
    })["carpark_plan"]

    scan = merged["cluster_scan"]
    assert scan["enabled"] is True
    assert scan["levels"] == [4, 9]
    assert scan["priority_levels"] == [4, 9]
    assert scan["excluded_levels"] == [1, 2, 3]
    assert scan["min_allies"] == 5


def test_merge_carpark_plan_sanitizes_malformed_input():
    merged = config_manager._merge_ws_token_phase_config({
        "carpark_plan": {
            "enabled": 1,
            "day": {"window": ["8am", "20:00"], "cross": 99},
            "night": "garbage",
            "silver_levels": [0, 9, "x", 31],
            "cluster_min": -5,            # non-positive -> strict default 5
            "grab_window_seconds": "x",   # unparsable -> default 60
            "grab_poll_seconds": 0,       # non-positive -> default 1.0
            "grab_attempts": 0,           # non-positive -> default 8
            "allow_low_noncluster": 0,    # coerced to bool False
        },
        "carpark_auto": "true",
    })
    plan = merged["carpark_plan"]
    assert plan["enabled"] is True
    assert plan["day"]["window"] == ["10:00", "22:00"]  # bad HH:MM -> default
    assert plan["day"]["cross"] == 10                   # clamped to 0..10
    assert plan["night"] == {"window": ["22:00", "10:00"], "cross": 0}
    assert plan["silver_levels"] == [9]                 # only valid 1..30 ints
    assert plan["cluster_min"] == 5
    assert plan["grab_window_seconds"] == 60
    assert plan["grab_poll_seconds"] == 1.0
    assert plan["grab_attempts"] == 8
    assert plan["allow_low_noncluster"] is False
    assert merged["carpark_auto"] is True

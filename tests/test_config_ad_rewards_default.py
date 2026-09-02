"""ad_rewards config_ids 預設須含挖礦鎬子/鑽頭/炸彈 (1/2/3)，與
ws_token/ad_reward.py 的 DEFAULT_CONFIG_IDS 一致。"""

import config_manager as cm
from ws_token.ad_reward import DEFAULT_CONFIG_IDS


def test_default_device_config_ad_rewards_includes_mining_ids():
    ids = cm.DEFAULT_DEVICE_CONFIG["ws_token"]["ad_rewards"]["config_ids"]
    assert ids == [1, 2, 3, 12, 14, 15]
    assert ids == DEFAULT_CONFIG_IDS


def test_sanitize_ad_rewards_falls_back_to_full_default_ids():
    default = cm.DEFAULT_DEVICE_CONFIG["ws_token"]["ad_rewards"]
    # 空 config_ids 應退回完整預設（含 1/2/3），而非殘缺清單。
    out = cm._sanitize_ad_rewards_config({"enabled": True, "config_ids": []}, default)
    assert out["config_ids"] == [1, 2, 3, 12, 14, 15]


def test_emulator_5558_enables_ws_ad_rewards():
    """5558 走 H5 的 WS-first 階段時應自動領取廣告收益。"""
    import json
    from pathlib import Path

    config_path = Path(cm.__file__).with_name("bot_config.json")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    ad_rewards = config["devices"]["emulator-5558"]["ws_token"]["ad_rewards"]
    assert ad_rewards["enabled"] is True
    assert {12, 14, 15}.issubset(ad_rewards["config_ids"])

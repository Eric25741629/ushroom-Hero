"""ws_token nested device-config defaults (config_manager.DEFAULT_DEVICE_CONFIG)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_manager  # noqa: E402


def test_default_device_config_has_ws_token_disabled():
    ws = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]
    assert ws["enabled"] is False
    assert ws["spend"] is False
    assert ws["open_lamp"] is False
    assert ws["couple_gifts"] is True
    assert ws["forge_ring"] is False
    assert ws["dungeon_sweeps"] == []
    assert ws["farm"] is None
    assert ws["carpark_target"] is None

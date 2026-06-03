from dragon_realm import use_dragon_realm


def test_flag_defaults_off_when_unset():
    assert use_dragon_realm("emulator-5560", {}) is False


def test_global_flag_enables():
    cfg = {"global": {"dragon_realm_enabled": True}}
    assert use_dragon_realm("emulator-5560", cfg) is True


def test_per_device_overrides_global():
    cfg = {
        "global": {"dragon_realm_enabled": True},
        "devices": {"emulator-5560": {"dragon_realm_enabled": False}},
    }
    assert use_dragon_realm("emulator-5560", cfg) is False

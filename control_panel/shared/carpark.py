"""中控板車位手動操作的共用設定。"""

# 這些裝置都使用 web_h5，可由 dashboard 透過 CDP 送出車位搶佔/駐守。
CARPARK_ROB_DEVICES = frozenset(
    {
        "emulator-5554",
        "emulator-5556",
        "emulator-5560",
        "7fe98fc6",
        "web-003",
        "web-004",
    }
)


def carpark_rob_enabled(device_id: str) -> bool:
    """判斷裝置是否啟用 dashboard 車位搶佔/駐守操作。"""
    return device_id in CARPARK_ROB_DEVICES

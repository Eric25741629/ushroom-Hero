from utils.emulator_watchdog import HangDetector, WatchdogSample


def _sample(ts, heartbeat_ts, screenshot_hash, adb_ok):
    return WatchdogSample(
        timestamp=ts,
        heartbeat_ts=heartbeat_ts,
        screenshot_hash=screenshot_hash,
        adb_ok=adb_ok,
    )


def test_hang_detection_rules():
    detector = HangDetector(
        heartbeat_timeout_sec=90,
        frozen_frame_window_sec=90,
        adb_timeout_strikes=2,
    )

    # Baseline healthy sample
    assert detector.is_hung(_sample(1000, 980, "h1", True)) is False

    # Heartbeat stale alone should not trigger
    assert detector.is_hung(_sample(1100, 900, "h2", True)) is False

    # Heartbeat stale + frozen frame should trigger
    assert detector.is_hung(_sample(1200, 1000, "same", True)) is False
    assert detector.is_hung(_sample(1295, 1000, "same", True)) is True

    # New detector: heartbeat stale + repeated adb failure should trigger
    detector2 = HangDetector(
        heartbeat_timeout_sec=90,
        frozen_frame_window_sec=90,
        adb_timeout_strikes=2,
    )
    assert detector2.is_hung(_sample(1000, 1000, "h1", True)) is False
    assert detector2.is_hung(_sample(1100, 900, "h2", False)) is False
    assert detector2.is_hung(_sample(1200, 900, "h3", False)) is True

    # adb recovers, should clear strike count and stop triggering on healthy heartbeat
    assert detector2.is_hung(_sample(1210, 1210, "h4", True)) is False

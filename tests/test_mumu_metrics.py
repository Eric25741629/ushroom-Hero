import bot_state


def test_metrics_exposed():
    ip = "emulator-5554"
    bot_state.init_device(ip)
    bot_state.record_emulator_restart(ip, "hung_detected")
    bot_state.update_watchdog_probe(ip, level="L2", adb_failures=2)

    states = bot_state.get_all_states()
    assert ip in states
    assert states[ip]["restart_count"] >= 1
    assert states[ip]["last_restart_reason"] == "hung_detected"
    assert states[ip]["watchdog_level"] == "L2"
    assert states[ip]["adb_consecutive_failures"] == 2

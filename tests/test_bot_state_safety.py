import threading
import time
import bot_state


def test_request_force_sleep_sets_pause_event():
    """request_force_sleep() must unblock a thread waiting in check_pause()."""
    ip = "test-safety-5554"
    bot_state.init_device(ip)
    bot_state.set_pause(ip, True)

    unblocked = threading.Event()
    waiter_started = threading.Event()

    def waiter():
        waiter_started.set()   # signal: we are about to call check_pause
        bot_state.check_pause(ip)
        unblocked.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()

    # Wait until waiter thread has started, then give check_pause a moment to enter its loop
    assert waiter_started.wait(timeout=2.0), "Waiter thread did not start in time"
    time.sleep(0.05)  # brief wait for check_pause to reach event.wait()

    bot_state.request_force_sleep(ip)

    assert unblocked.wait(timeout=3.0), "check_pause() did not unblock after request_force_sleep()"
    t.join(timeout=1.0)

    # Teardown: remove test device from module state
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._force_sleep_flags.pop(ip, None)
        bot_state._skip_sleep_flags.pop(ip, None)
        bot_state._locks.pop(ip, None)


def test_check_pause_safe_after_device_cleared():
    """check_pause() must not KeyError if device is cleared concurrently."""
    import bot_state as bs
    ip = "test-safety-clear-5556"
    bot_state.init_device(ip)
    bot_state.set_pause(ip, True)

    errors = []

    def pauser():
        try:
            bot_state.check_pause(ip)
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=pauser, daemon=True)
    t.start()
    time.sleep(0.05)  # let check_pause enter its loop

    # Force device offline and clear it while check_pause is waiting
    with bs._global_lock:
        if ip in bs._states:
            bs._states[ip]["status"] = "OFFLINE"
    bot_state.clear_offline_devices()

    t.join(timeout=3.0)
    assert not errors, f"check_pause() raised exception(s): {errors}"

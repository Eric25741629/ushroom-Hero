import threading
import time
import bot_state


def test_request_force_sleep_sets_pause_event():
    """request_force_sleep() must unblock a thread waiting in check_pause()."""
    ip = "test-safety-5554"
    bot_state.init_device(ip)
    bot_state.set_pause(ip, True)

    unblocked = threading.Event()

    def waiter():
        bot_state.check_pause(ip)
        unblocked.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.1)

    bot_state.request_force_sleep(ip)

    assert unblocked.wait(timeout=3.0), "check_pause() did not unblock after request_force_sleep()"
    t.join(timeout=1.0)

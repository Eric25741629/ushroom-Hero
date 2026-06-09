"""Phase 2 tests for bot_state: paused-as-derived-value + single refresh entry.

Part A — `paused` is derived from the per-device pause Event (single source of
truth) for LOCAL devices only. Remote/worker devices (worker_id:ip) keep the
value reported via update_state(paused=...).

Part B — the local refresh flag has a single lock-free write helper
(_set_refresh_needed_locked) routed by set_refresh_needed / request_web_launch /
submit_online_check_request, with no self-deadlock on the plain _global_lock.

Teardown mirrors tests/test_bot_state_signals.py but ALSO pops _local_device_ids
(so the remote-device test truly sees the ip as non-local) and resets
_refresh_needed.
"""
import bot_state


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._locks.pop(ip, None)
        bot_state._local_device_ids.discard(ip)
        bot_state._web_launch_requests.pop(ip, None)
        bot_state._refresh_needed = False


# --------------------------------------------------------------------------
# Part A — paused derives from the Event for LOCAL devices
# --------------------------------------------------------------------------


def test_local_paused_derives_from_event():
    ip = "test-p2-derive-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        # init sets the event -> not paused
        assert bot_state.get_all_states()[ip]["paused"] is False

        bot_state.set_pause(ip, True)
        assert bot_state.get_all_states()[ip]["paused"] is True

        bot_state.set_pause(ip, False)
        assert bot_state.get_all_states()[ip]["paused"] is False
    finally:
        _cleanup(ip)


def test_set_pause_does_not_write_stored_bool():
    """Characterization: the Event, not the stored bool, is the source of truth.

    After init + set_pause(True) the RAW stored bool stays at the init literal
    (False, untouched) while the pause Event is cleared (is_set() False).
    """
    ip = "test-p2-storedbool-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_pause(ip, True)

        # stored bool is the untouched init literal
        with bot_state.get_device_lock(ip):
            assert bot_state._states[ip].get("paused") is False
        # but the real source of truth (Event) is cleared
        ev = bot_state.get_pause_event(ip)
        assert ev is not None
        assert ev.is_set() is False
    finally:
        _cleanup(ip)


def test_force_sleep_makes_derived_paused_false():
    ip = "test-p2-force-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_pause(ip, True)
        assert bot_state.get_all_states()[ip]["paused"] is True

        bot_state.request_force_sleep(ip)

        assert bot_state.get_all_states()[ip]["paused"] is False
        ev = bot_state.get_pause_event(ip)
        assert ev is not None
        assert ev.is_set() is True
    finally:
        _cleanup(ip)


def test_remote_paused_is_preserved_not_clobbered():
    """Critical regression guard for the local-only derive.

    A remote device (worker_id:ip) is NOT registered local, so its reported
    paused value must survive get_all_states() untouched.
    """
    rid = "w1:emulator-5554"
    _cleanup(rid)
    try:
        # do NOT init_device -> not registered local
        bot_state.update_state(rid, paused=True)
        assert bot_state.get_all_states()[rid]["paused"] is True

        bot_state.update_state(rid, paused=False)
        assert bot_state.get_all_states()[rid]["paused"] is False
    finally:
        _cleanup(rid)


# --------------------------------------------------------------------------
# Part B — refresh flag: single write entry, no deadlock
# --------------------------------------------------------------------------


def test_refresh_flag_round_trip_one_shot():
    _cleanup("test-p2-refresh-noop")  # also resets _refresh_needed
    try:
        bot_state.set_refresh_needed()
        assert bot_state.check_refresh_needed() is True
        # one-shot consume
        assert bot_state.check_refresh_needed() is False
    finally:
        _cleanup("test-p2-refresh-noop")


def test_request_web_launch_sets_refresh_without_deadlock():
    ip = "test-p2-weblaunch-5554"
    _cleanup(ip)
    try:
        # If the helper wrongly re-acquired the plain _global_lock this would hang.
        bot_state.request_web_launch(ip)
        assert bot_state.check_refresh_needed() is True
    finally:
        _cleanup(ip)


def test_submit_online_check_sets_refresh_without_deadlock():
    requester = "test-p2-onlinecheck-req"
    checker = "test-p2-onlinecheck-chk"
    _cleanup(requester)
    _cleanup(checker)
    try:
        req_id = bot_state.submit_online_check_request(requester, checker)
        assert isinstance(req_id, str)
        assert bot_state.check_refresh_needed() is True
    finally:
        # online-check leaves request + pending + signal entries; clear thoroughly
        with bot_state._global_lock:
            bot_state._online_check_requests.pop(req_id, None)
            if req_id in bot_state._online_check_pending:
                bot_state._online_check_pending.remove(req_id)
        _cleanup(requester)
        _cleanup(checker)

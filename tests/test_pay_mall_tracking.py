from datetime import datetime, timedelta
from pathlib import Path
from shutil import rmtree

from ws_token import pay_mall, runner, state as ws_state


class _Result:
    def __init__(self, success=True, error_code=0):
        self.success = success
        self.error_code = error_code


def test_pay_mall_is_sent_once_per_device_per_day(monkeypatch):
    state_dir = Path(".pay-mall-test-state")
    rmtree(state_dir, ignore_errors=True)
    calls = []

    def fake_claim(client):
        calls.append(client)
        return _Result()

    monkeypatch.setattr(pay_mall, "claim_free_gift", fake_claim)
    now = datetime(2026, 9, 2, 8, 0)
    try:
        first = runner._run_pay_mall(object(), device="dev", state_dir=state_dir, now=now)
        second = runner._run_pay_mall(object(), device="dev", state_dir=state_dir,
                                       now=now + timedelta(hours=1))
        next_day = runner._run_pay_mall(object(), device="dev", state_dir=state_dir,
                                        now=now + timedelta(days=1))

        assert first["claimed_run"] is True
        assert second["claimed_run"] is False
        assert next_day["claimed_run"] is True
        assert len(calls) == 2
        assert ws_state.load_state("dev", state_dir=state_dir)["pay_mall"]["attempt_date"] == "2026-09-03"
    finally:
        rmtree(state_dir, ignore_errors=True)


def test_pay_mall_failure_is_still_at_most_once_that_day(monkeypatch):
    state_dir = Path(".pay-mall-test-state-failure")
    rmtree(state_dir, ignore_errors=True)
    calls = []

    def fake_claim(client):
        calls.append(client)
        return _Result(success=False, error_code=173)

    monkeypatch.setattr(pay_mall, "claim_free_gift", fake_claim)
    now = datetime(2026, 9, 2, 8, 0)
    try:
        first = runner._run_pay_mall(object(), device="dev", state_dir=state_dir, now=now)
        second = runner._run_pay_mall(object(), device="dev", state_dir=state_dir, now=now)

        assert first["claimed_run"] is True
        assert first["error_code"] == 173
        assert second["claimed_run"] is False
        assert len(calls) == 1
    finally:
        rmtree(state_dir, ignore_errors=True)

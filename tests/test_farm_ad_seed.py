"""Unit tests for farm_v2.operations.ad_seed.claim_ad_seeds.

Covers the orchestration logic only (the H5 page primitives are live-verified
on 7fe98fc6, not unit-tested): the 8am gate, the persisted daily quota
("看過廣告就不要再看了"), the ADB honest-stub, and the watch loop's stop
conditions (inactive / remaining==0 / no reward popup) + record-keeping.
"""
from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace

from unittest.mock import patch


# --- Hermetic imports (real module if present, else minimal stub) -------------
def _ensure(name, **attrs):
    try:
        return importlib.import_module(name)
    except Exception:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m


_ensure("opencc", OpenCC=lambda *a, **k: types.SimpleNamespace(convert=lambda s: s))
for _n in ("paddleocr", "img_tools", "easyocr", "cv2"):
    _ensure(_n)
_ensure("uiautomator2", Device=object)
_ensure("new_cnn")
_ensure("new_cnn.cnn_model", predict_image=lambda *a, **k: None)
sys.modules["new_cnn"].cnn_model = sys.modules["new_cnn.cnn_model"]

import farm_v2.operations.ad_seed as ad_seed  # noqa: E402


# --- Fakes --------------------------------------------------------------------
class _FakeTM:
    def __init__(self, same_day=True, count=0):
        self._same_day = same_day
        self._count = count
        self.recorded = []

    def is_same_day(self, name):
        return self._same_day

    def get_numeric_value(self, name, key, default=0):
        return self._count

    def record_timestamp(self, name, extra=None):
        self.recorded.append((name, extra))


class _FakeWF:
    """Scriptable web_farm replacement. `statuses` is the queue seed_ad_status
    returns; `reward` is whether GoodsGetView opens after a tap."""

    def __init__(self, statuses, reward=True, open_ok=True):
        self._statuses = list(statuses)
        self.reward = reward
        self.open_ok = open_ok
        self.taps = 0
        self.closed_reward = 0
        self.closed_seed = 0
        self._reward_state = False

    def open_seed_select(self, page):
        return self.open_ok

    def seed_ad_status(self, page):
        return self._statuses.pop(0) if self._statuses else {"open": True, "ad": None}

    def tap_seed_ad(self, page, ad):
        self.taps += 1
        self._reward_state = self.reward
        return True

    def reward_open(self, page):
        return self._reward_state

    def close_reward(self, page):
        self.closed_reward += 1
        self._reward_state = False
        return True

    def close_seed_select(self, page):
        self.closed_seed += 1
        return True


_FAKE_TIME = SimpleNamespace(
    localtime=lambda *a, **k: SimpleNamespace(tm_hour=10),
    sleep=lambda *a, **k: None,
)


def _ad(active=True, remaining=1):
    return {"open": True, "ad": {"active": active, "remaining": remaining, "wx": 1, "wy": 1}}


def _run(d, tm, wf=None, hour=10):
    t = SimpleNamespace(localtime=lambda *a, **k: SimpleNamespace(tm_hour=hour),
                        sleep=lambda *a, **k: None)
    ctx = [patch.object(ad_seed, "time", t)]
    if wf is not None:
        ctx.append(patch.object(ad_seed, "web_farm", wf))
    for c in ctx:
        c.start()
    try:
        return ad_seed.claim_ad_seeds(d, "7fe98fc6", tm)
    finally:
        for c in ctx:
            c.stop()


H5 = SimpleNamespace(_page=object())   # web_h5 backend
ADB = SimpleNamespace()                # no _page -> adb backend


def test_skips_before_8am():
    wf = _FakeWF([_ad()])
    assert _run(H5, _FakeTM(), wf, hour=5) == 0
    assert wf.open_ok and wf.taps == 0  # never touched the dialog


def test_skips_when_daily_quota_spent():
    wf = _FakeWF([_ad()])
    assert _run(H5, _FakeTM(same_day=True, count=2), wf) == 0
    assert wf.taps == 0


def test_adb_is_honest_stub():
    tm = _FakeTM()
    assert _run(ADB, tm) == 0
    assert tm.recorded == []  # no fake success recorded


def test_h5_watches_once_then_stops_when_seed_available():
    # ad available, then after the grant the seed is in stock (ad inactive).
    wf = _FakeWF([_ad(active=True, remaining=1), _ad(active=False, remaining=1)])
    tm = _FakeTM(count=0)
    assert _run(H5, tm, wf) == 1
    assert wf.taps == 1 and wf.closed_reward == 1 and wf.closed_seed == 1
    assert tm.recorded == [("farm_ad_seed", {"count": 1})]


def test_h5_stops_when_remaining_zero_even_if_active():
    # btnSeedAd stays active at (0/2) — must stop on remaining, not active.
    wf = _FakeWF([_ad(active=True, remaining=0)])
    tm = _FakeTM(count=0)
    assert _run(H5, tm, wf) == 0
    assert wf.taps == 0 and wf.closed_seed == 1
    assert tm.recorded == []


def test_h5_stops_when_no_reward_popup():
    # tap but GoodsGetView never appears -> don't count it, don't record.
    wf = _FakeWF([_ad(active=True, remaining=2)], reward=False)
    tm = _FakeTM(count=0)
    assert _run(H5, tm, wf) == 0
    assert wf.taps == 1 and wf.closed_reward == 0
    assert tm.recorded == []


def test_h5_allowance_capped_by_prior_count():
    # used 1 already today, limit 2 -> only 1 more even if ad stays available.
    wf = _FakeWF([_ad(active=True, remaining=1), _ad(active=True, remaining=1)])
    tm = _FakeTM(same_day=True, count=1)
    assert _run(H5, tm, wf) == 1
    assert wf.taps == 1
    assert tm.recorded == [("farm_ad_seed", {"count": 2})]


def test_h5_open_fail_returns_zero():
    wf = _FakeWF([_ad()], open_ok=False)
    tm = _FakeTM()
    assert _run(H5, tm, wf) == 0
    assert wf.taps == 0 and tm.recorded == []

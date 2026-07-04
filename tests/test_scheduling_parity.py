"""Tests for the shared 分流 (load-distribution) config parsers.

`runtime_services.wake_parity` is the single source of truth for mapping a
device-config ``wake_hour_parity`` / ``wake_minute_offset`` value to its
canonical constraint:

  parse_hour_parity(value) -> Optional[int]
    'even'/0 -> 0, 'odd'/1 -> 1, anything else (incl. None / bool) -> None
  parse_minute_offset(value) -> Optional[int]
    a non-negative int -> that int, anything else (incl. None / bool) -> None

Both ``sleep_service`` (wake alignment) and ``startup_sleep`` (launch
stagger order) previously duplicated this parsing. These tests pin the
canonical behavior AND assert both consumers stay byte-identical to their
pre-extraction semantics:

  - sleep_service keeps None as "no constraint"
  - startup_sleep maps None -> its own sort sentinel (parity 2 = last,
    offset 0 = first), preserving the launch order.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def wake_parity():
    return importlib.import_module("runtime_services.wake_parity")


# ---------------------------------------------------------------------------
# parse_hour_parity — canonical Optional[int]
# ---------------------------------------------------------------------------

def test_parse_hour_parity_even_strings(wake_parity):
    assert wake_parity.parse_hour_parity("even") == 0
    assert wake_parity.parse_hour_parity("EVEN") == 0
    assert wake_parity.parse_hour_parity(" Even ") == 0


def test_parse_hour_parity_odd_strings(wake_parity):
    assert wake_parity.parse_hour_parity("odd") == 1
    assert wake_parity.parse_hour_parity("ODD") == 1


def test_parse_hour_parity_ints(wake_parity):
    assert wake_parity.parse_hour_parity(0) == 0
    assert wake_parity.parse_hour_parity(1) == 1


def test_parse_hour_parity_unset_and_invalid_is_none(wake_parity):
    assert wake_parity.parse_hour_parity(None) is None
    assert wake_parity.parse_hour_parity("whatever") is None
    assert wake_parity.parse_hour_parity(5) is None
    assert wake_parity.parse_hour_parity(-1) is None


def test_parse_hour_parity_rejects_bool(wake_parity):
    # bool is a subclass of int; True/False must NOT pose as 1/0.
    assert wake_parity.parse_hour_parity(True) is None
    assert wake_parity.parse_hour_parity(False) is None


# ---------------------------------------------------------------------------
# parse_minute_offset — canonical Optional[int]
# ---------------------------------------------------------------------------

def test_parse_minute_offset_non_negative_int(wake_parity):
    assert wake_parity.parse_minute_offset(0) == 0
    assert wake_parity.parse_minute_offset(5) == 5
    assert wake_parity.parse_minute_offset(15) == 15
    assert wake_parity.parse_minute_offset(99) == 99


def test_parse_minute_offset_unset_and_invalid_is_none(wake_parity):
    assert wake_parity.parse_minute_offset(None) is None
    assert wake_parity.parse_minute_offset(-1) is None
    assert wake_parity.parse_minute_offset("5") is None


def test_parse_minute_offset_rejects_bool(wake_parity):
    assert wake_parity.parse_minute_offset(True) is None
    assert wake_parity.parse_minute_offset(False) is None


# ---------------------------------------------------------------------------
# Consumers stay byte-identical to their original behavior.
# ---------------------------------------------------------------------------

def test_sleep_service_parsers_match_canonical(wake_parity):
    sleep_mod = importlib.import_module("runtime_services.sleep_service")
    # sleep_service surfaces the canonical Optional[int] directly.
    for v in ("even", "odd", "EVEN", 0, 1, None, "whatever", 5, True, False, -1):
        assert sleep_mod._parse_hour_parity(v) == wake_parity.parse_hour_parity(v)
    for v in (0, 5, 15, 99, None, -1, True, False, "5"):
        assert sleep_mod._parse_minute_offset(v) == wake_parity.parse_minute_offset(v)


def test_startup_sleep_ranks_preserve_sentinels(wake_parity):
    startup_mod = importlib.import_module("runtime_services.startup_sleep")
    # parity rank: even=0, odd=1, unset/other=2 (sorts last)
    assert startup_mod._parity_rank("even") == 0
    assert startup_mod._parity_rank("odd") == 1
    assert startup_mod._parity_rank(None) == 2
    assert startup_mod._parity_rank("whatever") == 2
    assert startup_mod._parity_rank(5) == 2
    assert startup_mod._parity_rank(True) == 2
    assert startup_mod._parity_rank(False) == 2
    # offset rank: a non-negative int, else 0 (sorts first)
    assert startup_mod._offset_rank(0) == 0
    assert startup_mod._offset_rank(5) == 5
    assert startup_mod._offset_rank(15) == 15
    assert startup_mod._offset_rank(None) == 0
    assert startup_mod._offset_rank(-1) == 0
    assert startup_mod._offset_rank(True) == 0
    assert startup_mod._offset_rank(False) == 0

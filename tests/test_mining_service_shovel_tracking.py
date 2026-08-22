"""Mining-service internal pickaxe counter + OCR drift reconciliation.

Background — previously `mining_service.run` did:

  count = check_pickaxe_count(d)             # init
  while count >= 1:
      ...
      if iterations % 3 == 0:
          count = check_pickaxe_count(d)     # full override every 3 iters

i.e. OCR was the *source of truth* and there was no incremental
tracking at all. Worse, OCR failures silently returned 20, so a broken
OCR pipeline kept the bot mining indefinitely.

The new contract:

  1. `count` is the authoritative internal value, decremented after
     each plan execution by `ExecutionResult.shovels_used`.
  2. OCR is run periodically as **validation**. If `allow_none` returns
     None, the internal value is kept (OCR failure shouldn't override).
  3. When OCR and internal drift more than `tolerance`, the OCR wins
     and a warning is logged.

The reconciliation logic is extracted into a pure helper so we can
unit-test it without bringing up the whole mining loop.
"""
from __future__ import annotations

import sys
import types


# Light stubs so importing mining_service doesn't drag in heavy modules.
def _noop(*_a, **_kw):
    return None


if "uiautomator2" not in sys.modules:
    sys.modules["uiautomator2"] = types.SimpleNamespace(Device=object)
if "img_tools" not in sys.modules:
    sys.modules["img_tools"] = types.SimpleNamespace(get_all_text=lambda _frame, **_kw: [])
if "tools" not in sys.modules:
    sys.modules["tools"] = types.SimpleNamespace(click_white=lambda _d: None)
if "miner.models.classifier" not in sys.modules:
    sys.modules["miner.models.classifier"] = types.SimpleNamespace(
        ClassifierCNN=object,
        load_cnn_model=lambda: (None, None, None),
    )
if "miner.planning.executor" not in sys.modules:
    class _StubNBCErr(Exception):
        pass
    class _StubOOIErr(Exception):
        pass
    class _StubExecutionResult:
        def __init__(self, shovels_used=0, drills_used=0, bombs_used=0,
                     steps_completed=0, terminated_reason=None,
                     pickaxe_count_after=None):
            self.shovels_used = shovels_used
            self.drills_used = drills_used
            self.bombs_used = bombs_used
            self.steps_completed = steps_completed
            self.terminated_reason = terminated_reason
            self.pickaxe_count_after = pickaxe_count_after
    sys.modules["miner.planning.executor"] = types.SimpleNamespace(
        execute_plan_steps=lambda *a, **kw: _StubExecutionResult(),
        NoBoardChangeError=_StubNBCErr,
        OutOfItemError=_StubOOIErr,
        ExecutionResult=_StubExecutionResult,
    )
if "miner.core.ocr_utils" not in sys.modules:
    sys.modules["miner.core.ocr_utils"] = types.SimpleNamespace(
        check_pickaxe_count=lambda *a, **kw: 20,
        check_drill_num=lambda *a, **kw: 0,
        check_boom_num=lambda *a, **kw: 0,
    )
if "miner.planning.smart_planner" not in sys.modules:
    sys.modules["miner.planning.smart_planner"] = types.SimpleNamespace(
        plan_smart=lambda *a, **kw: {"ok": False, "steps": []},
    )
if "miner.rl.rl_recorder" not in sys.modules:
    sys.modules["miner.rl.rl_recorder"] = types.SimpleNamespace(RLRecorder=object)
if "miner.core.vision_utils" not in sys.modules:
    sys.modules["miner.core.vision_utils"] = types.SimpleNamespace(
        check_points=lambda *a, **kw: (False, None),
    )
if "utils.logging_utils" not in sys.modules:
    sys.modules["utils.logging_utils"] = types.SimpleNamespace(
        logger=None,
        setup_miner_logger=lambda _ip: types.SimpleNamespace(
            info=_noop, debug=_noop, warning=_noop, error=_noop,
        ),
    )
if "config.paths" not in sys.modules:
    sys.modules["config.paths"] = types.SimpleNamespace(DATASET_LOW_CONFIDENCE_DIR_STR="")


import miner.mining_service as service  # noqa: E402
from miner.mining_service import (  # noqa: E402
    _apply_partial,
    _is_h5_board_unavailable,
    _record_item_failure,
    _reconcile_shovel_count,
)
from miner.planning.executor import ExecutionResult  # noqa: E402


def test_h5_board_unavailable_is_classified_as_nonrecoverable_preflight():
    exc = types.SimpleNamespace(
        diagnostics={"phase": "h5_preflight", "validation": "board_unavailable"}
    )
    assert _is_h5_board_unavailable(exc) is True

    transient = types.SimpleNamespace(
        diagnostics={"phase": "h5_server_response", "validation": "board_unavailable"}
    )
    assert _is_h5_board_unavailable(transient) is False


def test_run_stops_after_h5_board_unavailable_instead_of_spinning(monkeypatch):
    class _Recorder:
        def start(self, **_kwargs):
            pass

        def round(self, **_kwargs):
            pass

        def end(self, **_kwargs):
            pass

    logger = types.SimpleNamespace(
        info=lambda *_a, **_kw: None,
        debug=lambda *_a, **_kw: None,
        warning=lambda *_a, **_kw: None,
        error=lambda *_a, **_kw: None,
        exception=lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(service, "setup_miner_logger", lambda _ip: logger)
    monkeypatch.setattr(
        service.config_manager,
        "get_device_config",
        lambda _ip: {"backend": "web_h5", "mining_planner_version": "v1"},
    )
    monkeypatch.setattr(service, "check_pickaxe_count", lambda *_a, **_kw: 10)
    monkeypatch.setattr(service, "read_ws_prop_counts", lambda _d: None)
    monkeypatch.setattr(service, "check_drill_num", lambda *_a, **_kw: 0)
    monkeypatch.setattr(service, "check_boom_num", lambda *_a, **_kw: 0)
    monkeypatch.setattr(service, "web_page", lambda _d: object())
    monkeypatch.setattr(service, "_dismiss_mining_overlay_if_needed", lambda *_a, **_kw: False)
    monkeypatch.setattr(service, "_log_inventory_validation", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_log_board_validation", lambda *_a, **_kw: None)
    monkeypatch.setattr(service, "_check_force_sleep", lambda _ip: None)
    monkeypatch.setattr(
        service,
        "MiningMapRecorder",
        types.SimpleNamespace(for_device=lambda *_a, **_kw: _Recorder()),
    )
    monkeypatch.setattr(
        service,
        "_dispatch_planner",
        lambda *_a, **_kw: (
            {"ok": True, "steps": [{"type": "dig", "pos": (1, 0), "target": (1, 0), "action": "dig"}]},
            "test",
        ),
    )

    exc = service.NoBoardChangeError()
    exc.reason = "H5 authoritative board unavailable"
    exc.diagnostics = {"phase": "h5_preflight", "validation": "board_unavailable"}
    exc.step = {"type": "dig", "pos": (1, 0), "target": (1, 0), "action": "dig"}
    exc.partial_result = service.ExecutionResult()
    monkeypatch.setattr(service, "execute_plan_steps", lambda *_a, **_kw: (_ for _ in ()).throw(exc))

    class _Device:
        def screenshot(self, **_kwargs):
            return object()

    clf = types.SimpleNamespace(
        classify_board=lambda *_a, **_kw: ([
            ["empty"] * 6,
            ["dirt"] * 6,
            *[["unreachable_dirt"] * 6 for _ in range(5)],
        ], []),
    )
    result = service.run(_Device(), "h5-board-missing", clf)

    assert result.success is False
    assert result.stopped_reason == "board_unavailable"
    assert result.rounds == 1


# ---------------------------------------------------------------------------
# Reconciliation helper — pure function, easy to unit test
# ---------------------------------------------------------------------------
def test_reconcile_returns_internal_when_ocr_is_none():
    """OCR failure must never override the internal counter."""
    new, kind = _reconcile_shovel_count(internal=50, ocr=None, tolerance=2)
    assert new == 50
    assert kind == "ocr_unavailable"


def test_reconcile_within_tolerance_keeps_internal():
    """Small drift (±tolerance) is treated as OCR jitter — internal wins."""
    new, kind = _reconcile_shovel_count(internal=50, ocr=49, tolerance=2)
    assert new == 50
    assert kind == "ok"

    new, kind = _reconcile_shovel_count(internal=50, ocr=52, tolerance=2)
    assert new == 50
    assert kind == "ok"


def test_reconcile_exact_match_is_ok():
    new, kind = _reconcile_shovel_count(internal=42, ocr=42, tolerance=2)
    assert new == 42
    assert kind == "ok"


def test_reconcile_large_drift_snaps_to_ocr():
    """When drift > tolerance, OCR is the authority — internal resets to OCR."""
    new, kind = _reconcile_shovel_count(internal=50, ocr=30, tolerance=2)
    assert new == 30
    assert kind == "drift"

    new, kind = _reconcile_shovel_count(internal=10, ocr=100, tolerance=2)
    assert new == 100
    assert kind == "drift"


def test_reconcile_zero_tolerance_strict():
    """tolerance=0 means OCR wins on any mismatch."""
    new, kind = _reconcile_shovel_count(internal=50, ocr=49, tolerance=0)
    assert new == 49
    assert kind == "drift"


def test_apply_partial_prefers_authoritative_ws_pickaxe_count():
    logger = types.SimpleNamespace(info=lambda *_a, **_kw: None)
    result = ExecutionResult(shovels_used=2, pickaxe_count_after=9)
    items = {"drill": 3, "bomb": 4}

    count = _apply_partial(result, 12, items, logger)

    assert count == 9


def test_item_verification_failure_blocks_once_then_blacklists_on_second():
    """WS refresh_failed/非 WS 暫時驗證失敗共用兩次門檻。"""
    streaks = {}

    first, first_blacklist = _record_item_failure("bomb", streaks)
    second, second_blacklist = _record_item_failure("bomb", streaks)

    assert (first, first_blacklist) == (1, False)
    assert (second, second_blacklist) == (2, True)

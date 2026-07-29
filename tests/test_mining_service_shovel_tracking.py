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
                     steps_completed=0, terminated_reason=None):
            self.shovels_used = shovels_used
            self.drills_used = drills_used
            self.bombs_used = bombs_used
            self.steps_completed = steps_completed
            self.terminated_reason = terminated_reason
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


from miner.mining_service import _apply_partial, _reconcile_shovel_count  # noqa: E402
from miner.planning.executor import ExecutionResult  # noqa: E402


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

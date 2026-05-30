"""Compute load-distribution primitives for the shared CNN model.

All device threads share ONE CNN model instance with no lock. When
several devices wake in the same :00-:20 window they can pile forward
passes onto the GPU simultaneously and oversubscribe CPU intra-op
threads. These primitives spread that load:

  * `configure_torch_runtime(n)` caps torch intra-op threads so N device
    threads don't each spin up the default (= core count) pool.
  * `InferenceGate` serializes (or bounds) concurrent forward passes on
    the shared model so they queue instead of cramming together.
"""
from __future__ import annotations

import threading

import torch

from utils.torch_runtime import InferenceGate, configure_torch_runtime


def test_configure_torch_runtime_caps_intraop_threads():
    original = torch.get_num_threads()
    try:
        resolved = configure_torch_runtime(num_threads=2)
        assert resolved == 2
        assert torch.get_num_threads() == 2
    finally:
        torch.set_num_threads(original)


def test_configure_torch_runtime_ignores_non_positive():
    original = torch.get_num_threads()
    try:
        resolved = configure_torch_runtime(num_threads=0)
        # 非正數視為不調整，回傳目前值且不改動
        assert resolved == original
        assert torch.get_num_threads() == original
    finally:
        torch.set_num_threads(original)


def test_inference_gate_serializes_when_concurrency_one():
    gate = InferenceGate(max_concurrency=1)
    order: list[str] = []
    first_holds = threading.Event()
    release_first = threading.Event()

    def worker_a() -> None:
        with gate.slot():
            order.append("a_enter")
            first_holds.set()
            release_first.wait(timeout=2)
            order.append("a_exit")

    def worker_b() -> None:
        first_holds.wait(timeout=2)  # ensure A is inside the slot first
        with gate.slot():            # must block until A releases
            order.append("b_enter")

    ta = threading.Thread(target=worker_a)
    tb = threading.Thread(target=worker_b)
    ta.start()
    tb.start()

    first_holds.wait(timeout=2)
    # While A holds the only slot, B must not have entered.
    assert "b_enter" not in order

    release_first.set()
    ta.join(timeout=2)
    tb.join(timeout=2)

    assert order == ["a_enter", "a_exit", "b_enter"]


def test_inference_gate_allows_bounded_concurrency():
    gate = InferenceGate(max_concurrency=2)
    # Two slots available -> two non-blocking acquires succeed, third fails.
    assert gate._semaphore.acquire(blocking=False) is True
    assert gate._semaphore.acquire(blocking=False) is True
    assert gate._semaphore.acquire(blocking=False) is False
    gate._semaphore.release()
    gate._semaphore.release()

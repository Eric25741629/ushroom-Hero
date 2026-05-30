"""Compute load-distribution for the shared CNN model across device threads.

All device threads share ONE CNN model instance (loaded once in
``new_main_v2`` and passed into every thread). When several devices wake
in the same hourly :00-:20 window they can pile forward passes onto the
GPU at once and each spin up torch's default intra-op thread pool,
oversubscribing the CPU. The primitives here spread that load:

  * :func:`configure_torch_runtime` caps torch intra-op threads.
  * :class:`InferenceGate` bounds concurrent forward passes on the shared
    model so they queue instead of cramming together.

See ``tests/test_torch_runtime.py`` for the contract.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import torch


def configure_torch_runtime(num_threads: Optional[int] = None) -> int:
    """Cap the torch intra-op thread pool.

    ``num_threads`` of ``None`` or a non-positive value leaves the current
    setting untouched. Returns the resulting ``torch.get_num_threads()``.
    """
    if num_threads is not None and num_threads > 0:
        torch.set_num_threads(int(num_threads))
    return torch.get_num_threads()


class InferenceGate:
    """Bounds concurrent forward passes on the shared model.

    Acquiring a slot before the batched forward keeps simultaneous GPU
    work from cramming together when multiple device threads infer at once.
    """

    def __init__(self, max_concurrency: int = 1) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = int(max_concurrency)
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)

    @contextmanager
    def slot(self) -> Iterator[None]:
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()


# Process-wide gate for the shared mining classifier. Default serializes
# (concurrency 1): the model is tiny and a 42-cell batch is fast, so
# queuing avoids GPU spikes when several devices wake together.
_inference_gate = InferenceGate(max_concurrency=1)


def set_inference_concurrency(max_concurrency: int) -> None:
    """Resize the process-wide inference gate. Call once at startup."""
    global _inference_gate
    _inference_gate = InferenceGate(max_concurrency=max_concurrency)


def inference_slot():
    """Acquire a slot on the process-wide inference gate (context manager)."""
    return _inference_gate.slot()


__all__ = [
    "configure_torch_runtime",
    "InferenceGate",
    "set_inference_concurrency",
    "inference_slot",
]

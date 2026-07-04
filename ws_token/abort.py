"""Shared abort signal for the ws_token daily run.

``WSRunAborted`` is raised — either at a task boundary in ``runner.run_device``
or from inside a long-running task loop (開神燈 / 挖礦) — when an external caller
asks the WS run to stop ASAP (e.g. a pending「開啟瀏覽器」request so the user can
take over the browser). The runner records which tasks were already done, leaves
the rest pending, and returns a ``RunReport`` with ``aborted=True``.

It lives in its own dependency-free module so ``runner`` can import it AND the
long tasks (``lamp`` / ``mining_supervised``) can raise it without creating a
``runner`` <-> task circular import (``runner`` imports those tasks at module
top-level).
"""
from __future__ import annotations


class WSRunAborted(Exception):
    """Raised to stop the ws_token daily run early on an external abort request.

    Distinct from task failures: the runner catches this separately so the
    in-progress task is left *pending* (not recorded as done or errored).
    """

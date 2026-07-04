"""Shared in-memory job registry for the 「工具 優化類」 tool blueprints.

Three sibling blueprints — carpark decorate (``routes_carpark_decorate_tools``),
gacha (``routes_gacha_tools``) and dragon realm (``routes_dragon_tools``) — all
run long tasks in background threads tracked by a single process-wide registry.
The frontend polls ``/api/carpark/job/<job_id>`` (owned by the carpark
blueprint) for any job regardless of which tool spawned it, so the registry has
to be shared rather than duplicated per blueprint.
"""
import threading
import uuid

# In-memory job registry: job_id -> dict(status, phase, log, result, error).
_jobs: dict = {}
_jobs_lock = threading.Lock()


def _new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {"status": "running", "phase": "starting", "log": [],
                      "result": None, "error": None}
    return jid


def _job_update(jid: str, **kw) -> None:
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kw)


def _job_log(jid: str, msg: str) -> None:
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].setdefault("log", []).append(msg)


def _spawn(target, *args) -> str:
    jid = _new_job()
    threading.Thread(target=target, args=(jid, *args), daemon=True).start()
    return jid

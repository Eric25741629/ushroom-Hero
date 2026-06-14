"""game_actions.ws_phase — 中斷後持久化續做 ledger (interruptible WS phase).

Covers the resume ledger added for「WS 階段被開啟瀏覽器中斷 → 記錄進度 → 重新上線
續做未完成」:
  - abort writes ws_resume (substantive-done only, date/ts),
  - a valid ledger -> skip_tasks = done - EXEMPT (carpark/idle_reward always re-run),
  - a full (non-aborted) completion clears the ledger; pipeline-skip uses
    effective_done (prior ledger ∪ this run),
  - stale (TTL passed / next day) -> ignored, full run,
  - a ledger read failure degrades to a full run (never crashes the phase).

State is sandboxed via ``state_dir=tmp_path`` and time via ``now=`` injection.
"""
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("cv2", types.SimpleNamespace())

import config_manager  # noqa: E402
from game_actions import ws_phase  # noqa: E402
from ws_token import state as ws_state  # noqa: E402
from ws_token.runner import RunReport  # noqa: E402

NOW = 1_700_000_000.0  # fixed epoch (mid-range, +60s never crosses local midnight)


def _cfg(monkeypatch, ws, *, backend="adb"):
    merged = {"bootstrap_token": False}
    merged.update(ws)
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  {"ws_token": merged, "backend": backend}.get(k, d)})())


def _report(tasks, errors=None, login_ok=True, aborted=False):
    return RunReport(device="dev", login_ok=login_ok, spend=False,
                     tasks=tasks, errors=errors or {}, aborted=aborted)


def _date(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _seed_ledger(tmp_path, done, *, now=NOW):
    ws_state.save_state("dev", {ws_phase._RESUME_KEY: {
        "date": _date(now), "ts": now, "done": list(done)}}, state_dir=tmp_path)


# --- abort writes the ledger ------------------------------------------------

def test_abort_writes_substantive_done_ledger(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    captured = {}

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        captured["skip_tasks"] = set(skip_tasks or ())
        return _report({"main_tasks": {}, "league_solo": {},
                        "couple": {"skipped": "no partner"}}, aborted=True)

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    ws_phase.run_ws_phase("dev", now=NOW, state_dir=tmp_path)

    led = ws_state.load_state("dev", state_dir=tmp_path)[ws_phase._RESUME_KEY]
    assert led["date"] == _date(NOW)
    assert led["ts"] == NOW
    # {"skipped"} task (couple) is NOT substantive -> excluded from the ledger
    assert set(led["done"]) == {"main_tasks", "league_solo"}
    assert captured["skip_tasks"] == set()      # no prior ledger -> full run


def test_abort_accumulates_prior_done(tmp_path, monkeypatch):
    """A resume run that aborts again merges prior + new done into the ledger."""
    _cfg(monkeypatch, {"enabled": True})
    _seed_ledger(tmp_path, ["main_tasks"], now=NOW)

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        # main_tasks is skipped (resume); this run does league_solo, then aborts.
        return _report({"league_solo": {}}, aborted=True)

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    ws_phase.run_ws_phase("dev", now=NOW + 60, state_dir=tmp_path)

    led = ws_state.load_state("dev", state_dir=tmp_path)[ws_phase._RESUME_KEY]
    assert set(led["done"]) == {"main_tasks", "league_solo"}   # accumulated
    assert led["ts"] == NOW + 60                                # ts refreshed


# --- a valid ledger drives skip_tasks (minus EXEMPT) ------------------------

def test_valid_ledger_skips_done_minus_exempt(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    _seed_ledger(tmp_path,
                 ["main_tasks", "league_solo", "carpark", "idle_reward"], now=NOW)
    captured = {}

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        captured["skip_tasks"] = set(skip_tasks or ())
        return _report({"main_tasks": {}, "league_solo": {},
                        "carpark": {}, "idle_reward": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    ws_phase.run_ws_phase("dev", now=NOW, state_dir=tmp_path)

    # carpark + idle_reward are EXEMPT -> still run; everything else is skipped.
    assert captured["skip_tasks"] == {"main_tasks", "league_solo"}


# --- completion clears the ledger; pipeline-skip uses effective_done --------

def test_completion_clears_ledger_and_uses_effective_done(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    _seed_ledger(tmp_path, ["main_tasks", "redpack"], now=NOW)

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        # main_tasks/redpack skipped (resume); this run completes steward.
        return _report({"steward": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    skips = ws_phase.run_ws_phase("dev", now=NOW + 120, state_dir=tmp_path)

    # pipeline-skip must reflect prior-done (所有日常任務 / 紅包檢查) + this run (商店購買)
    assert {"所有日常任務", "紅包檢查", "商店購買"} <= skips
    # full completion clears the ledger -> next wake starts fresh
    assert ws_phase._RESUME_KEY not in ws_state.load_state("dev", state_dir=tmp_path)


def test_no_ledger_plain_completion_writes_nothing(tmp_path, monkeypatch):
    """A normal (no prior ledger, not aborted) run leaves no ws_resume behind."""
    _cfg(monkeypatch, {"enabled": True})

    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw: _report({"redpack": {}}))

    ws_phase.run_ws_phase("dev", now=NOW, state_dir=tmp_path)

    assert ws_phase._RESUME_KEY not in ws_state.load_state("dev", state_dir=tmp_path)


# --- stale ledger is ignored ------------------------------------------------

def test_ledger_ttl_expired_is_ignored(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    _seed_ledger(tmp_path, ["main_tasks"], now=NOW)
    captured = {}

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        captured["skip_tasks"] = set(skip_tasks or ())
        return _report({"main_tasks": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    ws_phase.run_ws_phase("dev", now=NOW + ws_phase._RESUME_TTL_SEC + 1,
                          state_dir=tmp_path)

    assert captured["skip_tasks"] == set()      # TTL passed -> stale -> full run


def test_ledger_next_day_is_ignored(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    _seed_ledger(tmp_path, ["main_tasks"], now=NOW)
    captured = {}

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        captured["skip_tasks"] = set(skip_tasks or ())
        return _report({"main_tasks": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    ws_phase.run_ws_phase("dev", now=NOW + 86400, state_dir=tmp_path)

    assert captured["skip_tasks"] == set()      # next day -> stale -> full run


# --- ledger read failure degrades to a full run -----------------------------

def test_ledger_read_failure_degrades_to_full_run(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_state, "load_state",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    captured = {}

    def fake_run(ip, cfg, progress=None, *, should_abort=None, skip_tasks=None):
        captured["skip_tasks"] = set(skip_tasks or ())
        return _report({"redpack": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    skips = ws_phase.run_ws_phase("dev", now=NOW)

    assert captured["skip_tasks"] == set()      # unreadable ledger -> full run
    assert "紅包檢查" in skips                   # phase still works

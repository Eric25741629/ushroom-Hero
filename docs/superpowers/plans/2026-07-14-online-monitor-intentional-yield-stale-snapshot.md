# Online Monitor Intentional-Yield Stale Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓手機喚醒閘門只在 online monitor 因 `no idle detector` 主動斷線時，接受 600 秒內、讓路前最後快照明確顯示的 offline，同時保留一般 60 秒 presence 規則。

**Architecture:** `OnlineMonitor` 以 thread-safe 的 `_IntentionalYield` 記憶體標記鎖定讓路原因與當時 snapshot timestamp；一般 `account_online()` 不變，另提供 gate-only 查詢。只有 live detector 走 no-idle 分支時建立標記；成功連線、stop、thread finally 清除，acquire/connect failure 不清除。

**Tech Stack:** Python 3.10、pytest、threading、既有 `ws_token.online_monitor` / `game_actions.ws_phase`。

---

## File map

- Modify: `ws_token/online_monitor.py` — 保存/清除 intentional-yield，提供閘門專用 presence API，接上 monitor loop 生命周期。
- Modify: `game_actions/ws_phase.py` — `_account_online()` 改用閘門專用 API；等待迴圈本身不變。
- Modify: `tests/test_ws_human_offline_gate.py` — 60 秒正常路徑、600 秒 no-idle stale-offline、安全拒絕條件與 ws_phase 接線。
- Modify: `tests/test_online_monitor.py` — no-idle 唯一路徑、preempt/poll/connect failure、失敗保留與重連/stop/finally 清除。

### Task 1: Intentional-yield state and gate-only presence lookup

**Files:**
- Modify: `tests/test_ws_human_offline_gate.py`
- Modify: `tests/test_online_monitor.py`
- Modify: `ws_token/online_monitor.py`

- [ ] **Step 1: Write failing presence-policy tests**

Append to `tests/test_ws_human_offline_gate.py` after the existing `account_online` tests:

```python
def _monitor_state(monkeypatch, *, snap, active=None, yield_ts=None):
    mon = online_monitor.OnlineMonitor()
    with mon._lock:
        mon._snapshot = snap
        mon._active_detector = active
        mon._intentional_yield = (
            online_monitor._IntentionalYield(
                reason="no_idle_detector", snapshot_timestamp=yield_ts)
            if yield_ts is not None else None
        )
    monkeypatch.setattr(online_monitor, "_monitor", mon)
    return mon


def test_wake_gate_uses_normal_fresh_snapshot(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True), (456, False)]),
        active="emulator-5554",
    )
    assert online_monitor.account_online_for_wake_gate(123, now=1030.0) is True
    assert online_monitor.account_online_for_wake_gate(456, now=1030.0) is False


def test_wake_gate_accepts_recent_no_idle_stale_offline(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1599.0) is False


def test_wake_gate_rejects_expired_no_idle_snapshot(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1600.1) is None


def test_wake_gate_never_accepts_stale_online(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(123, True)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(123, now=1100.0) is None


def test_wake_gate_requires_matching_yield_snapshot(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1001.0, [(456, False)]),
        active=None,
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1100.0) is None


def test_wake_gate_requires_monitor_to_remain_disconnected(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False)]),
        active="emulator-5556",
        yield_ts=1000.0,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1100.0) is None


def test_wake_gate_rejects_stale_offline_without_yield_marker(monkeypatch):
    _monitor_state(
        monkeypatch,
        snap=_snap(1000.0, [(456, False)]),
        active=None,
        yield_ts=None,
    )
    assert online_monitor.account_online_for_wake_gate(456, now=1100.0) is None
```

- [ ] **Step 2: Write failing marker transition tests**

Append to `tests/test_online_monitor.py` near `test_last_switch_records_transition`:

```python
def _seed_yield_snapshot(mon, *, ts=1000.0, online=False):
    with mon._lock:
        mon._snapshot = om.Snapshot(
            "emulator-5554", ts,
            (om.StatusEntry(123, "phone", online, None),),
        )
    mon._mark_no_idle_yield()


def test_mark_no_idle_yield_locks_current_snapshot_timestamp():
    mon = om.OnlineMonitor()
    _seed_yield_snapshot(mon, ts=1000.0)
    assert mon._intentional_yield == om._IntentionalYield(
        reason="no_idle_detector", snapshot_timestamp=1000.0)


def test_set_active_none_preserves_existing_yield_marker():
    mon = om.OnlineMonitor()
    _seed_yield_snapshot(mon)
    mon._set_active(None)
    assert mon._intentional_yield is not None


def test_successful_connection_state_clears_yield_marker():
    mon = om.OnlineMonitor()
    _seed_yield_snapshot(mon)
    mon._set_active("emulator-5556")
    assert mon._intentional_yield is None


def test_stop_clears_yield_marker():
    mon = om.OnlineMonitor()
    _seed_yield_snapshot(mon)
    mon.stop()
    assert mon._intentional_yield is None
```

- [ ] **Step 3: Run Task 1 tests and verify RED**

Run:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests\test_ws_human_offline_gate.py tests\test_online_monitor.py -q --basetemp=.pytest_basetemp_online_monitor
```

Expected: FAIL because `_IntentionalYield`, `_mark_no_idle_yield`, and `account_online_for_wake_gate` do not exist yet.

- [ ] **Step 4: Implement the marker and gate-only lookup**

In `ws_token/online_monitor.py`, add after `Snapshot`:

```python
@dataclass(frozen=True)
class _IntentionalYield:
    reason: str
    snapshot_timestamp: float
```

In `OnlineMonitor.__init__`, beside `_active_detector`, add:

```python
self._intentional_yield: Optional[_IntentionalYield] = None
```

Add these methods near `_set_active`:

```python
def _mark_no_idle_yield(self) -> None:
    """鎖定 no-idle 讓路前最後一份 snapshot；無 snapshot 時不建立窗口。"""
    with self._lock:
        if self._snapshot is not None:
            self._intentional_yield = _IntentionalYield(
                reason="no_idle_detector",
                snapshot_timestamp=float(self._snapshot.timestamp),
            )

def _clear_intentional_yield(self) -> None:
    with self._lock:
        self._intentional_yield = None
```

Modify `_set_active()` so a non-`None` detector clears the marker under the same lock before the unchanged-transition early return; `_set_active(None)` must not clear it:

```python
with self._lock:
    if detector is not None:
        self._intentional_yield = None
    prev = self._active_detector
    if prev == detector:
        return
```

Modify `stop()`:

```python
def stop(self) -> None:
    self._running = False
    self._clear_intentional_yield()
    self._wake.set()
```

Add an `OnlineMonitor.account_online_for_wake_gate()` method near `snapshot`:

```python
def account_online_for_wake_gate(
        self, role_id: int, *, max_age_sec: float = 60.0,
        yielded_offline_max_age_sec: float = 600.0,
        now: Optional[float] = None) -> Optional[bool]:
    """喚醒閘門專用：只接受 no-idle 讓路窗口內的 stale-offline。"""
    t = time.time() if now is None else now
    with self._lock:
        snap = self._snapshot
        active = self._active_detector
        marker = self._intentional_yield
        if snap is None:
            return None
        snap_ts = float(snap.timestamp)
        fresh = (t - snap_ts) <= max_age_sec
        yielded_stale_offline = (
            not fresh
            and active is None
            and marker is not None
            and marker.reason == "no_idle_detector"
            and marker.snapshot_timestamp == snap_ts
            and (t - marker.snapshot_timestamp) <= yielded_offline_max_age_sec
        )
        for entry in snap.entries:
            if int(entry.role_id) != int(role_id):
                continue
            if fresh:
                return bool(entry.online)
            if yielded_stale_offline and not entry.online:
                return False
            return None
        return None
```

Add the module-level wrapper beside `account_online()` without changing `account_online()`:

```python
def account_online_for_wake_gate(
        role_id: int, *, max_age_sec: float = 60.0,
        yielded_offline_max_age_sec: float = 600.0,
        now: Optional[float] = None) -> Optional[bool]:
    """Presence for the pre-login human-offline gate only."""
    mon = _monitor
    if mon is None:
        return None
    return mon.account_online_for_wake_gate(
        role_id,
        max_age_sec=max_age_sec,
        yielded_offline_max_age_sec=yielded_offline_max_age_sec,
        now=now,
    )
```

- [ ] **Step 5: Run Task 1 tests and verify GREEN**

Run the Step 3 command again.

Expected: all existing and Task 1 tests PASS.

### Task 2: Wire lifecycle routes and ws_phase

**Files:**
- Modify: `tests/test_online_monitor.py`
- Modify: `tests/test_ws_human_offline_gate.py`
- Modify: `ws_token/online_monitor.py`
- Modify: `game_actions/ws_phase.py`

- [ ] **Step 1: Write failing loop and wiring tests**

Add compact loop helpers and tests to `tests/test_online_monitor.py`:

```python
class _LoopClient:
    def close(self):
        pass


def _ok_acquire(*args, **kwargs):
    return reg.AcquireResult(ok=True)


def test_loop_marks_only_live_no_idle_disconnect(monkeypatch):
    mon = om.OnlineMonitor(now=lambda: 1000.0)
    mon._running = True
    desired = iter(["emulator-5554", None])
    monkeypatch.setattr(mon, "_select_detector", lambda *a: next(desired))
    monkeypatch.setattr(reg, "acquire", _ok_acquire)
    monkeypatch.setattr(mon, "_connect", lambda dev: _LoopClient())
    monkeypatch.setattr(mon, "_release_detector", lambda dev: None)
    monkeypatch.setattr(
        om, "poll_friends",
        lambda client, threshold: [om.StatusEntry(123, "phone", False, None)],
    )
    marked = []
    original = mon._mark_no_idle_yield

    def _mark():
        original()
        marked.append(mon._intentional_yield.snapshot_timestamp)

    monkeypatch.setattr(mon, "_mark_no_idle_yield", _mark)
    sleeps = {"count": 0}

    def _sleep():
        sleeps["count"] += 1
        if sleeps["count"] == 2:
            mon._running = False

    monkeypatch.setattr(mon, "_sleep", _sleep)
    mon._loop()
    assert marked == [1000.0]


@pytest.mark.parametrize("failure", ["acquire", "connect"])
def test_retry_failure_preserves_existing_yield_until_loop_exit(monkeypatch, failure):
    mon = om.OnlineMonitor(now=lambda: 1100.0)
    with mon._lock:
        mon._snapshot = om.Snapshot(
            "emulator-5554", 1000.0,
            (om.StatusEntry(123, "phone", False, None),),
        )
    mon._mark_no_idle_yield()
    mon._running = True
    monkeypatch.setattr(mon, "_select_detector", lambda *a: "emulator-5556")
    monkeypatch.setattr(mon, "_release_detector", lambda dev: None)
    if failure == "acquire":
        monkeypatch.setattr(
            reg, "acquire", lambda *a, **k: reg.AcquireResult(ok=False, reason="busy"))
    else:
        monkeypatch.setattr(reg, "acquire", _ok_acquire)
        monkeypatch.setattr(mon, "_connect", lambda dev: None)
    marked = []
    monkeypatch.setattr(mon, "_mark_no_idle_yield", lambda: marked.append(True))
    observed = []

    def _sleep():
        observed.append(mon._intentional_yield)
        mon._running = False

    monkeypatch.setattr(mon, "_sleep", _sleep)
    mon._loop()
    assert marked == []
    assert observed[0].snapshot_timestamp == 1000.0
    assert mon._intentional_yield is None  # finally 清除


def test_poll_failure_does_not_establish_yield(monkeypatch):
    mon = om.OnlineMonitor()
    mon._running = True
    monkeypatch.setattr(mon, "_select_detector", lambda *a: "emulator-5554")
    monkeypatch.setattr(reg, "acquire", _ok_acquire)
    monkeypatch.setattr(mon, "_connect", lambda dev: _LoopClient())
    monkeypatch.setattr(mon, "_release_detector", lambda dev: None)
    marked = []
    monkeypatch.setattr(mon, "_mark_no_idle_yield", lambda: marked.append(True))

    def _poll(*args):
        mon._running = False
        raise RuntimeError("poll failed")

    monkeypatch.setattr(om, "poll_friends", _poll)
    mon._loop()
    assert marked == []
    assert mon._intentional_yield is None


def test_preempt_does_not_establish_yield(monkeypatch):
    mon = om.OnlineMonitor(now=lambda: 1000.0)
    mon._running = True
    preempted = iter([False, True])
    monkeypatch.setattr(mon, "_preempted", lambda current: next(preempted))
    monkeypatch.setattr(mon, "_select_detector", lambda *a: "emulator-5554")
    monkeypatch.setattr(reg, "acquire", _ok_acquire)
    monkeypatch.setattr(mon, "_connect", lambda dev: _LoopClient())
    monkeypatch.setattr(mon, "_release_detector", lambda dev: None)
    monkeypatch.setattr(om, "poll_friends", lambda *a: [])
    marked = []
    monkeypatch.setattr(mon, "_mark_no_idle_yield", lambda: marked.append(True))
    sleeps = {"count": 0}

    def _sleep():
        sleeps["count"] += 1
        if sleeps["count"] == 2:
            mon._running = False

    monkeypatch.setattr(mon, "_sleep", _sleep)
    mon._loop()
    assert marked == []


def test_successful_reconnect_clears_yield_before_next_poll(monkeypatch):
    mon = om.OnlineMonitor(now=lambda: 1100.0)
    with mon._lock:
        mon._snapshot = om.Snapshot("emulator-5554", 1000.0, ())
    mon._mark_no_idle_yield()
    mon._running = True
    monkeypatch.setattr(mon, "_select_detector", lambda *a: "emulator-5556")
    monkeypatch.setattr(reg, "acquire", _ok_acquire)
    monkeypatch.setattr(mon, "_connect", lambda dev: _LoopClient())
    monkeypatch.setattr(mon, "_release_detector", lambda dev: None)
    monkeypatch.setattr(om, "poll_friends", lambda *a: [])
    observed = []

    def _sleep():
        observed.append(mon._intentional_yield)
        mon._running = False

    monkeypatch.setattr(mon, "_sleep", _sleep)
    mon._loop()
    assert observed == [None]


def test_loop_exception_clears_existing_yield(monkeypatch):
    mon = om.OnlineMonitor()
    with mon._lock:
        mon._snapshot = om.Snapshot("emulator-5554", 1000.0, ())
    mon._mark_no_idle_yield()
    mon._running = True
    monkeypatch.setattr(
        mon, "_select_detector",
        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    mon._loop()
    assert mon._intentional_yield is None
```

Add to `tests/test_ws_human_offline_gate.py`:

```python
def test_ws_phase_account_lookup_uses_wake_gate_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        online_monitor, "account_online_for_wake_gate",
        lambda rid: calls.append(rid) or False,
    )
    assert ws_phase._account_online(123) is False
    assert calls == [123]
```

- [ ] **Step 2: Run Task 2 tests and verify RED**

Run:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests\test_online_monitor.py::test_loop_marks_only_live_no_idle_disconnect tests\test_online_monitor.py::test_retry_failure_preserves_existing_yield_until_loop_exit tests\test_online_monitor.py::test_poll_failure_does_not_establish_yield tests\test_online_monitor.py::test_preempt_does_not_establish_yield tests\test_online_monitor.py::test_successful_reconnect_clears_yield_before_next_poll tests\test_online_monitor.py::test_loop_exception_clears_existing_yield tests\test_ws_human_offline_gate.py::test_ws_phase_account_lookup_uses_wake_gate_policy -q --basetemp=.pytest_basetemp_online_monitor
```

Expected: no-idle test FAILS because `_loop()` has not called `_mark_no_idle_yield`; exception cleanup test FAILS because `finally` has not cleared; ws_phase wiring test FAILS because it still imports normal `account_online`.

- [ ] **Step 3: Wire the exact lifecycle points**

In `OnlineMonitor._loop()`:

```python
if desired is None:
    if client is not None:
        logger.info(
            "online-monitor: no idle detector; disconnecting %s",
            current)
        self._mark_no_idle_yield()
        self._close(client)
        self._release_detector(current)
        client, current = None, None
```

Do not add marker changes to `_preempted`, registry conflict, connect failure, or poll failure branches. The existing `_set_active(None)` calls preserve a marker; successful connect already calls `_set_active(desired)`, which clears it.

In `_loop()` `finally`, add cleanup before `_set_active(None)`:

```python
self._clear_intentional_yield()
self._set_active(None)
```

In `game_actions/ws_phase.py`, change only the imported lookup:

```python
def _account_online(role_id):
    """間接層：喚醒閘門可採信 no-idle 讓路前 10 分鐘內的明確離線快照。"""
    try:
        from ws_token.online_monitor import account_online_for_wake_gate
        return account_online_for_wake_gate(role_id)
    except Exception:  # noqa: BLE001 — 讀快照失敗 → None（視為可能在線）
        return None
```

- [ ] **Step 4: Run full related tests and verify GREEN**

Run:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests\test_online_monitor.py tests\test_ws_human_offline_gate.py -q --basetemp=.pytest_basetemp_online_monitor
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the implementation**

```powershell
git add ws_token\online_monitor.py game_actions\ws_phase.py tests\test_online_monitor.py tests\test_ws_human_offline_gate.py
git commit -m "fix(online-monitor): 讓搶位閘門採信刻意讓路離線快照"
```

### Task 3: Verification and diff audit

**Files:**
- Verify: `ws_token/online_monitor.py`
- Verify: `game_actions/ws_phase.py`
- Verify: `tests/test_online_monitor.py`
- Verify: `tests/test_ws_human_offline_gate.py`

- [ ] **Step 1: Run syntax checks**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m py_compile ws_token\online_monitor.py game_actions\ws_phase.py tests\test_online_monitor.py tests\test_ws_human_offline_gate.py
```

Expected: exit code 0 and no output.

- [ ] **Step 2: Run the complete relevant suite fresh**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests\test_online_monitor.py tests\test_ws_human_offline_gate.py -q --basetemp=.pytest_basetemp_online_monitor
```

Expected: all tests PASS with zero failures/errors.

- [ ] **Step 3: Audit the diff against the spec**

```powershell
git diff HEAD^ --check
git diff HEAD^ -- ws_token\online_monitor.py game_actions\ws_phase.py tests\test_online_monitor.py tests\test_ws_human_offline_gate.py
git status --short
```

Verify:

- only no-idle with a live client calls `_mark_no_idle_yield()`;
- `_set_active(None)` does not clear;
- `_set_active(non-None)`, `stop()`, and `_loop()` finally clear;
- acquire/connect failure have no clear calls;
- normal `account_online()` remains unchanged;
- ws_phase uses only the gate-specific lookup;
- comments added by this change are Chinese where they describe repo policy.

- [ ] **Step 4: Remove only the generated local pytest basetemp after verifying its resolved path is inside this worktree**

First run:

```powershell
Get-Item -LiteralPath .pytest_basetemp_online_monitor | Select-Object FullName,PSIsContainer
```

If it resolves under this worktree, remove exactly that directory:

```powershell
Remove-Item -LiteralPath .pytest_basetemp_online_monitor -Recurse -Force
```

- [ ] **Step 5: Finish the branch using `finishing-a-development-branch`**

Follow that skill to present safe integration options; do not merge into the dirty main worktree without an explicit integration choice.

# Carpark Spend Packet Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every resource-spending carpark decoration WS packet starts at least one second after the previous spending packet without weakening the existing 5/10-second skin-up protection.

**Architecture:** Add one pacing helper in `ws_token/carpark_decoration_ws.py` that computes the maximum remaining wait for the shared spend interval and, for upgrades, the existing skin-up interval. Stamp both timestamps immediately before the mutation send so buys, upgrades, self-heal buys, and retries all share the same protection.

**Tech Stack:** Python 3.10, monotonic clock, pytest, existing pure-WS carpark executor.

---

## File map

- Modify `ws_token/carpark_decoration_ws.py`: define the one-second shared spend interval and apply it immediately before `CMD_SHOP_BUY` and `CMD_JSON_PROTO` mutation sends.
- Modify `tests/test_carpark_ws_io.py`: add deterministic pacing tests using a fake monotonic clock and update the existing skin-up pacing assertions to cover the shared timestamp.

### Task 1: Specify shared carpark spend pacing

**Files:**
- Modify: `tests/test_carpark_ws_io.py`
- Test: `tests/test_carpark_ws_io.py`

- [ ] **Step 1: Add a deterministic fake clock and a failing buy-to-upgrade test**

Add this helper near the existing executor test helpers:

```python
class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
```

Add a test that calls the wished-for pacing helper twice, first as a buy and then as an upgrade:

```python
def test_spend_pacing_waits_between_buy_and_first_upgrade(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(deco_ws.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(deco_ws.time, "sleep", clock.sleep)
    client = object.__new__(_FakeExecClient)

    deco_ws._pace_spend_send(client)
    first_send = client._last_carpark_spend_ts
    clock.now += 0.4
    deco_ws._pace_spend_send(client, skin_up=True, skin_up_gap=0.0)

    assert clock.sleeps == [pytest.approx(0.6)]
    assert client._last_carpark_spend_ts - first_send == pytest.approx(1.0)
    assert client._last_skin_up_ts == client._last_carpark_spend_ts
```

- [ ] **Step 2: Add a failing test proving the existing longer skin-up gap wins**

```python
def test_spend_pacing_keeps_longer_skin_up_gap(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(deco_ws.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(deco_ws.time, "sleep", clock.sleep)
    client = object.__new__(_FakeExecClient)
    client._last_carpark_spend_ts = 0.0
    client._last_skin_up_ts = 0.0
    clock.now = 2.0

    deco_ws._pace_spend_send(client, skin_up=True, skin_up_gap=5.0)

    assert clock.sleeps == [pytest.approx(3.0)]
    assert client._last_carpark_spend_ts == pytest.approx(5.0)
    assert client._last_skin_up_ts == pytest.approx(5.0)
```

- [ ] **Step 3: Add a failing integration test proving both mutation call sites use the pacer**

```python
def test_exec_routes_buy_and_upgrade_through_shared_spend_pacer(monkeypatch):
    paced = []
    monkeypatch.setattr(
        deco_ws, "_pace_spend_send",
        lambda client, *, skin_up=False, skin_up_gap=0.0:
            paced.append((skin_up, skin_up_gap)))
    client = _FakeExecClient([
        ("reply", 12801, _skin_list_body([(40097, 1)])),
        ("reply", 6913, _buy_info_body({1753: 0})),
        ("reply", 6914, codec.pb_uint(1, 1753) + codec.pb_uint(2, 1)),
        ("reply", 12817, _skin_up_ok_body(40097, 2)),
        ("reply", 12801, _skin_list_body([(40097, 2)])),
    ])

    res, err = deco_ws.exec_buy_and_upgrade(
        client, shop_id=1753, skin_id=40097, frags=1,
        skin_up_gap=5.0)

    assert err is None and res["ok"] is True
    assert paced == [(False, 0.0), (True, 5.0)]
```

- [ ] **Step 4: Run the three tests and verify RED**

Run:

```powershell
conda run --no-capture-output -n mushroom1 python -m pytest tests/test_carpark_ws_io.py::test_spend_pacing_waits_between_buy_and_first_upgrade tests/test_carpark_ws_io.py::test_spend_pacing_keeps_longer_skin_up_gap tests/test_carpark_ws_io.py::test_exec_routes_buy_and_upgrade_through_shared_spend_pacer -q
```

Expected: all three tests fail with `AttributeError` because `_pace_spend_send` does not exist.

### Task 2: Implement shared mutation pacing

**Files:**
- Modify: `ws_token/carpark_decoration_ws.py`
- Test: `tests/test_carpark_ws_io.py`

- [ ] **Step 1: Add the shared interval and pacing helper**

Place the constant beside `_REPLY_WAIT_S` and `_COOLDOWN_WAIT_S`:

```python
_SPEND_GAP_S = 1.0
```

Add this helper before `exec_buy_and_upgrade`:

```python
def _pace_spend_send(client: WSGameClient, *, skin_up: bool = False,
                     skin_up_gap: float = 0.0) -> None:
    """等待花費封包的剩餘冷卻，並在實際送出前記錄時間。"""
    now = time.monotonic()
    waits = []
    last_spend = getattr(client, "_last_carpark_spend_ts", None)
    if last_spend is not None:
        waits.append(_SPEND_GAP_S - (now - last_spend))
    if skin_up and skin_up_gap > 0:
        last_skin_up = getattr(client, "_last_skin_up_ts", None)
        if last_skin_up is not None:
            waits.append(skin_up_gap - (now - last_skin_up))
    wait = max(waits, default=0.0)
    if wait > 0:
        time.sleep(wait)
    sent_at = time.monotonic()
    client._last_carpark_spend_ts = sent_at
    if skin_up:
        client._last_skin_up_ts = sent_at
```

- [ ] **Step 2: Pace every shop buy immediately before its send**

In `_buy_frags`, immediately before `client.call_for(CMD_SHOP_BUY, ...)`, add:

```python
_pace_spend_send(client)
```

This location also covers the held-fragment self-heal buy and any buy retry that re-enters `_buy_frags`.

- [ ] **Step 3: Replace the upgrade-only wait with the shared helper**

In `_send_upgrade`, remove the existing manual `skin_up_gap` wait and timestamp block:

```python
if skin_up_gap > 0:
    last = getattr(client, "_last_skin_up_ts", None)
    if last is not None:
        wait = skin_up_gap - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
client._last_skin_up_ts = time.monotonic()
```

Replace it with:

```python
_pace_spend_send(client, skin_up=True, skin_up_gap=skin_up_gap)
```

- [ ] **Step 4: Run the new tests and verify GREEN**

Run:

```powershell
conda run --no-capture-output -n mushroom1 python -m pytest tests/test_carpark_ws_io.py::test_spend_pacing_waits_between_buy_and_first_upgrade tests/test_carpark_ws_io.py::test_spend_pacing_keeps_longer_skin_up_gap tests/test_carpark_ws_io.py::test_exec_routes_buy_and_upgrade_through_shared_spend_pacer -q
```

Expected: `3 passed`.

- [ ] **Step 5: Run the existing direct pacing regressions**

Run:

```powershell
conda run --no-capture-output -n mushroom1 python -m pytest tests/test_carpark_ws_io.py::test_exec_skin_up_gap_waits_only_remaining_cooldown tests/test_carpark_ws_io.py::test_exec_skin_up_gap_first_send_no_wait tests/test_carpark_ws_io.py::test_exec_upgrade_dropped_by_cooldown_retried_once -q
```

Expected before updating the legacy assertion: the two cooldown/retry tests pass and `test_exec_skin_up_gap_first_send_no_wait` fails because its fragment buy is now the first spending mutation. Rename that test to `test_exec_first_upgrade_waits_after_fragment_buy`, then replace `assert not slept` with:

```python
assert len(slept) == 1
assert 0.5 < slept[0] <= 1.0
```

Rerun with the renamed node:

```powershell
conda run --no-capture-output -n mushroom1 python -m pytest tests/test_carpark_ws_io.py::test_exec_skin_up_gap_waits_only_remaining_cooldown tests/test_carpark_ws_io.py::test_exec_first_upgrade_waits_after_fragment_buy tests/test_carpark_ws_io.py::test_exec_upgrade_dropped_by_cooldown_retried_once -q
```

Expected: `3 passed`. The new helper test from Task 1 retains the explicit guarantee that a new connection's first spending mutation does not wait.

- [ ] **Step 6: Commit the behavior change**

```powershell
git add ws_token/carpark_decoration_ws.py tests/test_carpark_ws_io.py
git commit -m "fix(carpark): pace resource spending packets"
```

### Task 3: Verify and integrate

**Files:**
- Verify: `ws_token/carpark_decoration_ws.py`
- Verify: `tests/test_carpark_ws_io.py`

- [ ] **Step 1: Run all executor-level tests that do not depend on the three known job isolation failures**

Run:

```powershell
conda run --no-capture-output -n mushroom1 python -m pytest tests/test_carpark_ws_io.py -q --deselect=tests/test_carpark_ws_io.py::test_execute_job_reconnects_and_retries_on_conn_lost --deselect=tests/test_carpark_ws_io.py::test_execute_job_non_conn_error_stops_without_retry --deselect=tests/test_carpark_ws_io.py::test_execute_job_starts_fast_and_backs_off_after_resend
```

Expected: all selected tests pass. Explicitly report that the three deselected baseline failures do not mock `_prebuy_group` and remain outside this task.

- [ ] **Step 2: Run syntax and diff checks**

```powershell
conda run --no-capture-output -n mushroom1 python -m py_compile ws_token/carpark_decoration_ws.py tests/test_carpark_ws_io.py
git diff --check 8a6fa447..HEAD
```

Expected: both commands exit 0.

- [ ] **Step 3: Merge locally according to repository delivery preference**

After using `verification-before-completion` and `finishing-a-development-branch`, fast-forward `fix/carpark-spend-pacing` into `main`, rerun the focused passing tests on `main`, delete the feature branch, and remove the isolated worktree. Preserve every unrelated dirty file in the main worktree.

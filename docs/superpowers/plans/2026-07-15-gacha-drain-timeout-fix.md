# Pure WS Gacha Drain Timeout Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard drain draws respect a live-verified one-second request interval and resolve timeouts from authoritative ticket and pool-count probes.

**Architecture:** `ws_token.gacha` will own read-only `0x0401`/`0x0901` parsing and probe comparison. `control_panel.routes_gacha_tools` will own pacing, reconnecting, one safe retry, job logs, and totals; every mutation starts from a known `(ticket_count, pool_count)` baseline.

**Tech Stack:** Python 3.10, Flask background jobs, `WSGameClient`, `ws_token.codec`, pytest.

---

## File structure

- Modify `ws_token/gacha.py`: typed state probe and landed/not-landed comparison.
- Modify `control_panel/routes_gacha_tools.py`: one-second limiter and timeout resolution.
- Modify `tests/test_ws_token_gacha.py`: probe parser/comparison tests.
- Modify `tests/test_gacha_job_reconnect.py`: pacing, timeout, reconnect, and fixed-mode tests.

### Task 1: Add authoritative gacha state probes

**Files:**
- Modify: `ws_token/gacha.py:25-130`
- Test: `tests/test_ws_token_gacha.py`

- [ ] **Step 1: Write the failing tests**

Add the following to `tests/test_ws_token_gacha.py`:

```python
def _bag_reply(*pairs):
    return b"".join(codec.pb_msg(1, codec.pb_uint(1, iid) + codec.pb_uint(3, count))
                    for iid, count in pairs)


def _draw_info_reply(*pools):
    return b"".join(codec.pb_msg(1, codec.pb_uint(1, pool_id)
                                  + codec.pb_uint(2, level)
                                  + codec.pb_uint(3, count))
                    for pool_id, level, count in pools)


class ProbeClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def call_for(self, cmd, body, *, expect_cmds, timeout=None):
        return self.replies.pop(0)


def test_read_probe_returns_ticket_and_pool_count():
    client = ProbeClient([
        (gacha.CMD_INVENTORY_QUERY, _bag_reply((1012, 375651), (1013, 374148))),
        (gacha.CMD_DRAW_INFO, _draw_info_reply((1, 15, 488834), (2, 20, 429926))),
    ])
    assert gacha.read_probe(client, 1, timeout=4) == gacha.GachaProbe(375651, 488834)


def test_read_probe_requires_both_values():
    client = ProbeClient([
        (gacha.CMD_INVENTORY_QUERY, _bag_reply((1013, 10))),
        (gacha.CMD_DRAW_INFO, _draw_info_reply((2, 20, 100))),
    ])
    assert gacha.read_probe(client, 1) is None


def test_compare_probe_classifies_outcome():
    before = gacha.GachaProbe(1000, 5000)
    assert gacha.compare_probe(before, gacha.GachaProbe(200, 5999), 999) == "landed"
    assert gacha.compare_probe(before, gacha.GachaProbe(1000, 5000), 999) == "not_landed"
    assert gacha.compare_probe(before, gacha.GachaProbe(200, 5000), 999) == "conflict"
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests/test_ws_token_gacha.py -q
```

Expected: FAIL because the probe API is missing.

- [ ] **Step 3: Implement the probe API**

Add to `ws_token/gacha.py`:

```python
CMD_INVENTORY_QUERY = 0x0401
CMD_DRAW_INFO = 0x0901


@dataclass(frozen=True)
class GachaProbe:
    ticket_count: int
    pool_count: int


def _parse_probe_value(body: bytes, key: int, *, top_field: int = 1) -> int | None:
    for fnum, val in codec.walk(body):
        if fnum == top_field and isinstance(val, (bytes, bytearray)):
            data = codec.walk_dict(bytes(val))
            if data.get(1) == key and isinstance(data.get(3), int):
                return int(data[3])
    return None


def read_probe(client: WSGameClient, draw_type: int, *,
               timeout: float | None = None) -> GachaProbe | None:
    ticket_id = TICKET_ITEM.get(draw_type)
    if ticket_id is None:
        return None
    bag_cmd, bag = client.call_for(
        CMD_INVENTORY_QUERY, b"", expect_cmds=(CMD_INVENTORY_QUERY,), timeout=timeout)
    info_cmd, info = client.call_for(
        CMD_DRAW_INFO, b"", expect_cmds=(CMD_DRAW_INFO,), timeout=timeout)
    if bag_cmd != CMD_INVENTORY_QUERY or info_cmd != CMD_DRAW_INFO:
        return None
    tickets = _parse_probe_value(bag, ticket_id)
    pool_count = _parse_probe_value(info, draw_type)
    if tickets is None or pool_count is None:
        return None
    return GachaProbe(tickets, pool_count)


def compare_probe(before: GachaProbe, after: GachaProbe, count: int) -> str:
    ticket_delta = before.ticket_count - after.ticket_count
    pool_delta = after.pool_count - before.pool_count
    if ticket_delta == BUNDLE_COST[count] and pool_delta == count:
        return "landed"
    if ticket_delta == 0 and pool_delta == 0:
        return "not_landed"
    return "conflict"
```

- [ ] **Step 4: Run the test file and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add ws_token/gacha.py tests/test_ws_token_gacha.py
git commit -m "feat(gacha): add authoritative draw probes"
```

### Task 2: Enforce one-second send-start pacing

**Files:**
- Modify: `control_panel/routes_gacha_tools.py:1-160`
- Test: `tests/test_gacha_job_reconnect.py`

- [ ] **Step 1: Write failing pacing tests**

Extend `_setup` with scripted probes and a fake clock. Add:

```python
class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_drain_uses_probe_balance_and_stops_before_unaffordable_draw(monkeypatch):
    calls = _setup(monkeypatch, [_ok()], probes=[routes.gacha_logic.GachaProbe(800, 1000)])
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "drain", 0, 1)
    assert calls["draws"] == [(1, 999)]
    assert jobs._jobs[jid]["result"]["stopped_reason"] == "exhausted"


def test_draw_starts_are_at_least_one_second_apart(monkeypatch):
    clock = FakeClock()
    calls = _setup(monkeypatch, [_ok(), _ok()],
                   probes=[routes.gacha_logic.GachaProbe(1600, 1000)], clock=clock)
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 2)
    assert calls["draw_started"] == [0.0, 1.0]
    assert clock.sleeps == [1.0]
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests/test_gacha_job_reconnect.py -q
```

Expected: FAIL because the job does not read a probe or pace requests.

- [ ] **Step 3: Implement probe seeding and pacing**

Import `time`; add `_DRAW_MIN_INTERVAL_S = 1.0` and `_PROBE_TIMEOUT = 10`. Read the initial probe before drawing and refuse to mutate if it is missing. Wrap each mutation with:

```python
last_draw_started = None


def _paced_draw(cnt):
    nonlocal last_draw_started
    if last_draw_started is not None:
        wait = _DRAW_MIN_INTERVAL_S - (time.monotonic() - last_draw_started)
        if wait > 0:
            time.sleep(wait)
    last_draw_started = time.monotonic()
    return _draw_once(client, draw_type, cnt)
```

Initialize drain `remaining` from `probe.ticket_count`. On each normal success update:

```python
probe = gacha_logic.GachaProbe(
    probe.ticket_count - _BUNDLE_COST[rung],
    probe.pool_count + rung,
)
remaining = probe.ticket_count
```

Use `count` instead of `rung` in fixed mode.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: pacing and existing reconnect tests pass.

- [ ] **Step 5: Commit**

```powershell
git add control_panel/routes_gacha_tools.py tests/test_gacha_job_reconnect.py
git commit -m "fix(gacha): pace paid draw requests"
```

### Task 3: Resolve ambiguous draw failures with probes

**Files:**
- Modify: `control_panel/routes_gacha_tools.py:90-190`
- Test: `tests/test_gacha_job_reconnect.py`

- [ ] **Step 1: Write failing timeout tests**

Replace the obsolete test that expects plain timeout to stop immediately. Add:

```python
def test_timeout_confirmed_landed_counts_without_retry(monkeypatch):
    calls = _setup(monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306")],
        probes=[GachaProbe(800, 1000), GachaProbe(0, 1999)])
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)
    assert calls["draws"] == [(1, 999)]
    assert jobs._jobs[jid]["result"]["total"] == 999


def test_timeout_confirmed_not_landed_retries_once(monkeypatch):
    calls = _setup(monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306"), _ok()],
        probes=[GachaProbe(800, 1000), GachaProbe(800, 1000)])
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)
    assert calls["draws"] == [(1, 999), (1, 999)]
    assert jobs._jobs[jid]["result"]["total"] == 999


def test_timeout_conflicting_probe_stops_unconfirmed(monkeypatch):
    calls = _setup(monkeypatch,
        [(None, "WSTimeoutError: no response for cmd=2306")],
        probes=[GachaProbe(800, 1000), GachaProbe(0, 1000)])
    jid = jobs._new_job()
    routes._run_gacha_job(jid, "emulator-5554", 1, "fixed", 999, 1)
    assert calls["draws"] == [(1, 999)]
    assert jobs._jobs[jid]["result"]["stopped_reason"] == "unconfirmed"
    assert jobs._jobs[jid]["result"]["total"] == 0
```

Keep explicit connection-loss coverage, but require a post-reconnect probe before retrying.

- [ ] **Step 2: Run tests and verify RED**

Run Task 2 Step 2. Expected: FAIL because timeout remains terminal and reconnect retries blindly.

- [ ] **Step 3: Implement shared error resolution**

Add `_probe_now()` and make the draw wrapper return a result plus outcome. Its core must be:

```python
after = _probe_now()
if after is None:
    return None, "unconfirmed", err
state = gacha_logic.compare_probe(before, after, cnt)
if state == "landed":
    probe = after
    return {"ok": True, "drawn": cnt, "remaining": after.ticket_count,
            "rejected": False, "error_code": None}, "confirmed", None
if state != "not_landed" or not allow_retry:
    return None, "unconfirmed", err
probe = after
retry, retry_err = _paced_draw(cnt)
if retry_err:
    return _resolve_after_error(after, cnt, retry_err, allow_retry=False)
return retry, "success", None
```

For an explicit dead socket, reconnect first and then run the same comparison. Log `狀態探測確認成功`, `伺服器未執行，安全重試一次`, or `狀態無法確認，停止`. Ordinary `0x0201` reject results must not probe or retry.

- [ ] **Step 4: Run all focused tests and verify GREEN**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m pytest tests/test_ws_token_gacha.py tests/test_gacha_job_reconnect.py tests/test_tools_optimize_template.py -q
```

Expected: zero failures.

- [ ] **Step 5: Commit**

```powershell
git add control_panel/routes_gacha_tools.py tests/test_gacha_job_reconnect.py
git commit -m "fix(gacha): verify ambiguous draw timeouts"
```

### Task 4: Final verification

**Files:**
- Verify: `ws_token/gacha.py`
- Verify: `control_panel/routes_gacha_tools.py`
- Verify: `tests/test_ws_token_gacha.py`
- Verify: `tests/test_gacha_job_reconnect.py`

- [ ] **Step 1: Run focused pytest freshly**

Run Task 3 Step 4. Expected: zero failures.

- [ ] **Step 2: Run syntax checks**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n mushroom1 python -m py_compile ws_token/gacha.py control_panel/routes_gacha_tools.py tests/test_ws_token_gacha.py tests/test_gacha_job_reconnect.py
```

Expected: exit 0 and no output.

- [ ] **Step 3: Inspect the exact diff**

```powershell
git diff --check
git diff --stat
git status --short
```

Expected: no whitespace errors; only planned files are changed by this implementation. Existing unrelated dirty files remain untouched.

- [ ] **Step 4: Respect the live-test boundary**

Do not spend more tickets merely to prove the implementation. The design already records 5554 live evidence: send gaps through `707.0ms` were dropped, while `804.1ms` succeeded. Report that evidence separately from automated verification.

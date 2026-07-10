# 在線標示明確化 + 工具頁手動連線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dashboard 在線徽章分五態（腳本執行/在線觀察/被借走/玩家在線/離線），工具頁改手動連線 + 連線前佔用確認，喚醒路徑登記 SCHEDULER lease。

**Architecture:** `session_registry`（已存在）的 lease 經 `/api/status` 曝露給前端；「腳本執行」另以 bot_state task/status fallback 推導（涵蓋 worker 與 H5 階段）。工具頁移除 init 自動 `connectSession()`，連線前打新 `precheck` 端點彈確認 modal。喚醒路徑以 `acquire_scheduler_lease` 取代 `wait_for_dashboard_ws_release`：搶回背景借用者、等待 TOOL、入睡時釋放。

**Tech Stack:** Python 3 / Flask blueprints / vanilla JS templates / pytest。

**Spec:** `docs/superpowers/specs/2026-07-10-presence-clarity-manual-connect-design.md`（本 plan 與 spec 衝突時以 spec 為準）。

## Global Constraints

- 不加新套件。
- JSON / 檔案讀取一律 `encoding="utf-8-sig"`（本 repo 檔案多帶 BOM）。
- pytest 必指定測試檔（hook 會擋裸 `pytest`）；跑法 `python -m pytest tests/<file>.py -q`。
- 只 stage 有動到的檔案；**絕不 `git add -A`**；不 push；commit 不加 attribution footer。
- 改 `.py` 後 PostToolUse hook 會跑 `py_compile`，語法錯會被擋下。
- 前端模板遵守設計系統：色彩/間距用 `static/lib/tokens.css` 的 CSS variables，modal 走各頁既有 modal manager helper。
- 不改 `session_registry.py` 本體（API 已齊備：`acquire/release/peek/peek_all/Owner/Channel/YIELDING_BORROWERS`）。

---

### Task 1: 後端 — `/api/status` 注入 lease + `precheck` 端點

**Files:**
- Modify: `control_panel/routes_status.py`（`get_status` 迴圈，約 :473-492；新 helper 放 `_account_presence` 附近）
- Modify: `control_panel/ws_session.py`（blueprint 區段 :316 之後加 precheck）
- Test: `tests/test_presence_lease_fields.py`（新檔）

**Interfaces:**
- Consumes: `runtime_services.session_registry.peek_all() -> dict[str, Lease]`、`Lease.owner: Owner`（`.value` 為 `"scheduler"|"online_monitor"|"online_check"|"mount_tracker"|"tool"`）、`Lease.label: str`。
- Produces:
  - `/api/status` 每 device dict 新欄位 `lease_owner: str|None`、`lease_label: str`（Task 2 前端消費）。
  - `routes_status._lease_fields(leases: dict, ip: str, real_ip: str) -> dict`。
  - `ws_session.precheck(device: str) -> dict`（keys: `lease`, `account_online`）與 `GET /api/ws_session/<ip>/precheck`（Task 3 前端消費）。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_presence_lease_fields.py
"""Task 1: /api/status lease 欄位 + ws_session precheck 的純邏輯測試（不起 Flask）。"""
import types

from runtime_services.session_registry import Channel, Lease, Owner


def _lease(device, owner, label=""):
    return Lease(device=device, owner=owner, channel=Channel.WS,
                 label=label, acquired_at=0.0, role_id=None)


# --- routes_status._lease_fields -------------------------------------------

def test_lease_fields_none_when_no_lease():
    from control_panel.routes_status import _lease_fields
    assert _lease_fields({}, "emulator-5554", "emulator-5554") == {
        "lease_owner": None, "lease_label": ""}


def test_lease_fields_maps_owner_value_and_label():
    from control_panel.routes_status import _lease_fields
    leases = {"emulator-5554": _lease("emulator-5554", Owner.TOOL, "工具")}
    assert _lease_fields(leases, "emulator-5554", "emulator-5554") == {
        "lease_owner": "tool", "lease_label": "工具"}


def test_lease_fields_falls_back_to_real_ip_key():
    from control_panel.routes_status import _lease_fields
    leases = {"5554": _lease("5554", Owner.MOUNT_TRACKER)}
    assert _lease_fields(leases, "127.0.0.1:5554", "5554")["lease_owner"] == "mount_tracker"


# --- ws_session.precheck -----------------------------------------------------

def test_precheck_reports_lease_and_online(monkeypatch):
    from control_panel import ws_session
    monkeypatch.setattr(ws_session.registry, "peek",
                        lambda d: _lease(d, Owner.ONLINE_MONITOR, "偵測"))
    monkeypatch.setattr(ws_session, "_precheck_account_online", lambda d: True)
    out = ws_session.precheck("emulator-5554")
    assert out == {"lease": {"owner": "online_monitor", "label": "偵測"},
                   "account_online": True}


def test_precheck_empty_when_idle(monkeypatch):
    from control_panel import ws_session
    monkeypatch.setattr(ws_session.registry, "peek", lambda d: None)
    monkeypatch.setattr(ws_session, "_precheck_account_online", lambda d: None)
    assert ws_session.precheck("emulator-5554") == {
        "lease": None, "account_online": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_presence_lease_fields.py -q`
Expected: FAIL（`ImportError: cannot import name '_lease_fields'` 等）。
若 import `control_panel.ws_session` 因重依賴失敗，允許在測試檔頂部以 `pytest.importorskip` 以外的最小 stub 處理，但先直接試——ws_session 只 import flask/ws_token.client，應可載入。

- [ ] **Step 3: Implement**

`control_panel/routes_status.py` — 在 `_account_presence()` 之後新增：

```python
def _lease_fields(leases, ip, real_ip):
    """session_registry lease → /api/status 顯示欄位。key 依序試完整 ip 與 real_ip。"""
    lease = leases.get(ip) or leases.get(real_ip)
    if lease is None:
        return {"lease_owner": None, "lease_label": ""}
    return {"lease_owner": lease.owner.value, "lease_label": lease.label or ""}
```

`get_status()` 內、`presence = _account_presence()` 之後加：

```python
    try:
        from runtime_services import session_registry
        leases = session_registry.peek_all()
    except Exception:
        leases = {}
```

迴圈內（`info["account_online"] = ...` 下一行）加：

```python
        info.update(_lease_fields(leases, ip, real_ip))
```

`control_panel/ws_session.py` — blueprint 區段新增：

```python
def _precheck_account_online(device: str):
    """好友 presence 的帳號在線判定（讀不到回 None）。測試 seam。"""
    try:
        import config_manager
        from ws_token.online_monitor import account_online
        rid = config_manager.get_device_role_id(device)
        return account_online(int(rid)) if rid is not None else None
    except Exception:  # noqa: BLE001 — presence 讀取失敗不可擋 precheck
        return None


def precheck(device: str) -> dict:
    """連線前檢查：帳號是否已被佔用（registry lease）/ 帳號是否在線（好友 presence）。"""
    lease = registry.peek(device)
    return {
        "lease": ({"owner": lease.owner.value, "label": lease.label or ""}
                  if lease is not None else None),
        "account_online": _precheck_account_online(device),
    }


@bp.route("/api/ws_session/<ip>/precheck")
@_fly_pet_auth
def precheck_endpoint(ip: str):
    """工具頁連線前的佔用/在線確認資料。"""
    return jsonify(precheck(ip))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_presence_lease_fields.py -q`
Expected: 5 passed。
再跑 `python -m py_compile control_panel/routes_status.py control_panel/ws_session.py`。

- [ ] **Step 5: Commit**

```bash
git add control_panel/routes_status.py control_panel/ws_session.py tests/test_presence_lease_fields.py
git commit -m "feat(status): /api/status 注入 session lease + ws_session precheck 端點"
```

---

### Task 2: 前端 — dashboard 在線徽章五態

**Files:**
- Modify: `templates/dashboard.html`（badge 渲染 :3055-3067、span title :3015、CSS `.acct-presence` :511-523）

**Interfaces:**
- Consumes: `/api/status` 的 `info.lease_owner`、`info.lease_label`（Task 1）、既有 `info.account_online`、`info.task`、`statusClass`（:2974-2981 已算好）。
- Produces: 無（純顯示）。

- [ ] **Step 1: 改 badge 渲染邏輯**

把 :3055-3067 的 `presenceEl` 區塊整段換成：

```js
        const presenceEl = document.getElementById(`presence-${ip}`);
        if (presenceEl) {
          const OBSERVER_ZH = { online_monitor: '上線偵測', online_check: '上線檢查', mount_tracker: '坐騎追蹤' };
          const idleTasks = ['休眠中', '啟動後休眠'];
          const awake = (statusClass === 'ONLINE' || statusClass === 'DEGRADED' || statusClass === 'PAUSED')
            && !idleTasks.includes(info.task || '')
            && (info.task || '') !== '等待真人下線';
          if (info.lease_owner === 'scheduler') {
            presenceEl.textContent = '腳本執行';
            presenceEl.className = 'acct-presence bot';
          } else if (OBSERVER_ZH[info.lease_owner]) {
            presenceEl.textContent = `在線觀察（${OBSERVER_ZH[info.lease_owner]}）`;
            presenceEl.className = 'acct-presence watch';
          } else if (info.lease_owner === 'tool') {
            presenceEl.textContent = `被借走：工具${info.lease_label ? `（${info.lease_label}）` : ''}`;
            presenceEl.className = 'acct-presence borrowed';
          } else if (awake) {
            // fallback：Phase 5 未登記 / worker 裝置 / H5 瀏覽器階段，bot 醒著就是在跑腳本
            presenceEl.textContent = '腳本執行';
            presenceEl.className = 'acct-presence bot';
          } else if (info.account_online === true) {
            presenceEl.textContent = '玩家在線';
            presenceEl.className = 'acct-presence player';
          } else if (info.account_online === false) {
            presenceEl.textContent = '當前離線';
            presenceEl.className = 'acct-presence off';
          } else {
            presenceEl.textContent = '';
            presenceEl.className = 'acct-presence';
          }
        }
```

- [ ] **Step 2: 改 span title 與 CSS**

:3015 的 title 換成 `title="帳號連線歸屬：腳本執行 / 在線觀察 / 被借走 / 玩家在線 / 離線"`。

:511-523 `.acct-presence` CSS：保留既有 `.on/.off` 樣式規則（`.off` 續用），刪除錯誤註解「real player online, from online-monitor」，新增三類（顏色用 tokens.css 既有變數，實作時以該檔實際存在的變數為準——優先 `--ok`/`--warn`/`--info`/`--danger` 系，無則沿用 `.on/.off` 用的變數配色邏輯）：

```css
        .acct-presence.bot     { color: var(--info, #4da3ff); border-color: var(--info, #4da3ff); }
        .acct-presence.watch   { color: var(--text-secondary); border-color: var(--text-secondary); }
        .acct-presence.borrowed{ color: var(--warn, #e0a636); border-color: var(--warn, #e0a636); }
        .acct-presence.player  { color: var(--danger, #ff6b6b); border-color: var(--danger, #ff6b6b); font-weight: 600; }
```

（若既有 `.acct-presence.on` 是背景色徽章而非 border 樣式，比照其結構寫四類，不要自創新結構。`.on` 規則若無其他引用可刪。）

- [ ] **Step 3: 語法/煙霧驗證**

Run: `python -c "print(open('templates/dashboard.html', encoding='utf-8-sig').read().count('acct-presence'))"`
Expected: 數字輸出（檔案可讀、無編碼炸裂）。瀏覽器實測留到最終 live 驗證。

- [ ] **Step 4: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(dashboard): 在線徽章五態（腳本執行/在線觀察/被借走/玩家在線/離線）"
```

---

### Task 3: 前端 — 倉庫/工具最佳化/飛寵改手動 + 連線前確認 modal

**Files:**
- Modify: `templates/inventory.html`（init IIFE :564-569、`connectSession` :483、新增 modal 元素）
- Modify: `templates/tools_optimize.html`（init IIFE :715、`connectSession` :529、新增 modal 元素）
- Modify: `templates/fly_pet.html`（`initAutoLoad` :1675、`showNotConnectedHint` :1659-1672）

**Interfaces:**
- Consumes: `GET /api/ws_session/<ip>/precheck`（Task 1）→ `{lease: {owner,label}|null, account_online: bool|null}`；兩頁既有 `showModal(id)/hideModal(id)` helper。
- Produces: 無。

- [ ] **Step 1: inventory.html — 移除自動連線 + 加確認**

init IIFE（:564-569）改為：

```js
(async function init(){
  wireSortableHeaders();
  syncConnGating();                    // disable 讀取 buttons + show idle prompt until connected
  await loadDevices();
  // 2026-07-10 使用者定案：開頁不自動連線（避免無感 ticket 登入踢人），按「連線」才連。
})();
```

`connectSession()` 開頭（`setConn('connecting');` 之前）插入：

```js
  if(!(await confirmOccupied(d))){ setConn('off'); return false; }
```

新增（放在 `connectSession` 前面）：

```js
const OWNER_ZH = { tool:'工具', scheduler:'腳本排程', online_monitor:'上線偵測',
                   online_check:'上線檢查', mount_tracker:'坐騎追蹤' };
// 連線前佔用/在線確認：有佔用者或帳號在線 → modal 確認；precheck 失敗 fail-open。
async function confirmOccupied(d){
  let why = '';
  try{
    const r = await fetch(`/api/ws_session/${encodeURIComponent(d)}/precheck`);
    const j = await r.json();
    if(j.lease) why = `帳號目前被「${OWNER_ZH[j.lease.owner]||j.lease.owner}${j.lease.label?'：'+j.lease.label:''}」佔用`;
    else if(j.account_online === true) why = '帳號目前在線（可能有真人正在遊玩）';
  }catch(e){ return true; }
  if(!why) return true;
  return await new Promise(resolve=>{
    $('connConfirmWhy').textContent = `${why}，連線會把對方踢下線。`;
    const m = $('connConfirmModal');
    m._uiConfirmCancel = ()=>resolve(false);          // Esc = 安全預設（取消）
    m._resolve = resolve;
    showModal('connConfirmModal');
  });
}
function connConfirmGo(ok){
  const m = $('connConfirmModal');
  hideModal('connConfirmModal');
  if(m._resolve){ m._resolve(ok); m._resolve = null; }
}
```

modal 元素：仿照該頁既有 `kickModal` 的 DOM 結構（class/包裹層完全一致，只換 id 與文案）插在 `kickModal` 旁：

```html
<!-- 連線前佔用確認 -->
<div class="modal-overlay" id="connConfirmModal" role="dialog" aria-modal="true" aria-labelledby="connConfirmTitle">
  <div class="modal">
    <h3 id="connConfirmTitle">確定要連線嗎？</h3>
    <p id="connConfirmWhy"></p>
    <div class="modal-actions">
      <button class="btn" onclick="connConfirmGo(false)">取消</button>
      <button class="btn btn-danger" onclick="connConfirmGo(true)">仍要連線</button>
    </div>
  </div>
</div>
```

（實作時以 kickModal 的實際 class 名為準改寫上面骨架；重點：id 三個 —— `connConfirmModal`/`connConfirmTitle`/`connConfirmWhy`，按鈕呼叫 `connConfirmGo`。）

- [ ] **Step 2: tools_optimize.html — 同樣處理**

:715 改為：

```js
(async function init(){ await loadDevices(); /* 2026-07-10：開頁不自動連線，按「連線」才連 */ })();
```

`connectSession()`（:529）開頭同樣插入 `if(!(await confirmOccupied(d))){ setConn('off'); return false; }`；複製 Step 1 的 `OWNER_ZH`/`confirmOccupied`/`connConfirmGo` 與 modal 元素（仿該頁 kickModal 結構）。

- [ ] **Step 3: fly_pet.html — 取消自動載入**

`initAutoLoad`（:1675-1679）改為：

```js
async function initAutoLoad() {
  var br = await checkBrowserUp();
  if (br.browser_up) { showNotConnectedHint('ready'); return; }  // 2026-07-10：不自動載入
  showNotConnectedHint('launch');
}
```

`showNotConnectedHint`（:1659-1672）在 `if (kind === 'loading')` 前加一個分支：

```js
  if (kind === 'ready') {
    if (msg) { msg.textContent = '瀏覽器已開 — 點「載入」讀取資料'; msg.style.color = 'var(--text2)'; }
    if (launchBtn) { launchBtn.style.display = 'none'; launchBtn.classList.remove('btn-attention'); }
    if (initMsg) { initMsg.textContent = '瀏覽器已開 — 點「載入」讀取資料'; initMsg.style.display = ''; }
    return;
  }
```

- [ ] **Step 4: 驗證**

三檔各跑一次可讀性煙霧檢查（如 Task 2 Step 3 的 python one-liner，關鍵字 `connConfirmModal` / `initAutoLoad`）。行為驗證留到最終 live（開頁不連線、按連線先彈確認）。

- [ ] **Step 5: Commit**

```bash
git add templates/inventory.html templates/tools_optimize.html templates/fly_pet.html
git commit -m "feat(tools): 倉庫/工具/飛寵頁改手動連線 + 連線前佔用確認"
```

---

### Task 4: Phase 5 — 喚醒登記 SCHEDULER lease

**Files:**
- Modify: `game_actions/ws_phase.py`（:411-453 區塊重寫：`wait_for_dashboard_ws_release` → `acquire_scheduler_lease`；`_dashboard_ws_active` 一併刪除）
- Modify: `new_main_v2.py`（:94 import、:105 呼叫點）
- Modify: `runtime_services/ws_runner_service.py`（:523-524）
- Modify: `runtime_services/sleep_service.py`（`run_sleep_cycle` :181 入口加釋放）
- Test: `tests/test_ws_human_offline_gate.py`（:200-225 舊 wait 測試改寫）、`tests/test_scheduler_lease.py`（新檔）

**Interfaces:**
- Consumes: `session_registry.acquire/release/Owner/Channel/YIELDING_BORROWERS`、既有 `_web_launch_pending(ip)`、`_DASHBOARD_WS_POLL_SEC`。
- Produces: `ws_phase.acquire_scheduler_lease(ip: str, log) -> None`、`sleep_service._release_scheduler_lease(ip: str) -> None`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scheduler_lease.py
"""Task 4: 喚醒路徑 SCHEDULER lease 取得/等待/搶回/釋放。"""
import pytest

from runtime_services import session_registry as registry
from runtime_services.session_registry import Channel, Owner


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """每測隔離 registry 狀態 + 關掉外部 seam。"""
    monkeypatch.setattr(registry, "_leases", {})
    monkeypatch.setattr(registry, "_protected_role_ids", lambda: frozenset())
    monkeypatch.setattr(registry, "_is_human_played_device", lambda d: False)
    monkeypatch.setattr(registry, "_safe_set_pause", lambda d, p: None)
    yield


@pytest.fixture()
def ws_phase(monkeypatch):
    from game_actions import ws_phase as mod
    monkeypatch.setattr(mod, "_web_launch_pending", lambda ip: False)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def test_acquire_idle_registers_scheduler(ws_phase):
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)
    lease = registry.peek("dev")
    assert lease is not None and lease.owner is Owner.SCHEDULER


def test_acquire_preempts_yielding_borrower(ws_phase):
    registry.acquire("dev", Owner.MOUNT_TRACKER, Channel.WS, label="追蹤")
    borrower = registry.peek("dev")
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)
    assert registry.peek("dev").owner is Owner.SCHEDULER
    assert borrower.preempted.is_set()


def test_acquire_waits_for_tool_release(ws_phase, monkeypatch):
    registry.acquire("dev", Owner.TOOL, Channel.WS, label="工具")
    polls = {"n": 0}

    def fake_sleep(sec):
        polls["n"] += 1
        if polls["n"] >= 2:
            registry.release("dev", Owner.TOOL)  # 第二輪 poll 後工具釋放

    monkeypatch.setattr(ws_phase.time, "sleep", fake_sleep)
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)
    assert registry.peek("dev").owner is Owner.SCHEDULER
    assert polls["n"] >= 2


def test_acquire_web_launch_interrupts_tool_wait(ws_phase, monkeypatch):
    registry.acquire("dev", Owner.TOOL, Channel.WS, label="工具")
    monkeypatch.setattr(ws_phase, "_web_launch_pending", lambda ip: True)
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)  # 不可 hang
    assert registry.peek("dev").owner is Owner.TOOL  # 放行但未搶佔


def test_acquire_protected_passes_without_lease(ws_phase, monkeypatch):
    monkeypatch.setattr(registry, "_is_human_played_device", lambda d: True)
    ws_phase.acquire_scheduler_lease("dev", ws_phase.logger)  # 不可 hang / 不可 raise
    assert registry.peek("dev") is None


def test_sleep_entry_releases_scheduler_lease():
    from runtime_services.sleep_service import _release_scheduler_lease
    registry.acquire("dev", Owner.SCHEDULER, Channel.WS, label="喚醒週期")
    _release_scheduler_lease("dev")
    assert registry.peek("dev") is None
    _release_scheduler_lease("dev")  # 冪等，不 raise
```

同檔或 `tests/test_ws_human_offline_gate.py`：把 :200-225 針對 `wait_for_dashboard_ws_release` 的三個測試刪除（函式將移除），保留其餘 `_wait_until_human_offline` 測試不動。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scheduler_lease.py -q`
Expected: FAIL（`acquire_scheduler_lease` / `_release_scheduler_lease` 不存在）。

- [ ] **Step 3: Implement — ws_phase.py**

刪除 `_dashboard_ws_active`（:421-427）與 `wait_for_dashboard_ws_release`（:430-453），原位置改為：

```python
def acquire_scheduler_lease(ip: str, log) -> None:
    """喚醒週期開跑前取得 SCHEDULER lease（Phase 5，取代舊 wait_for_dashboard_ws_release）。

    - 背景借用者（上線偵測/檢查/坐騎追蹤）→ preempt 直接搶回（它們 poll preempted 讓位）。
    - dashboard TOOL（人手動操作）→ 尊重人，15s poll 等待釋放（2026-07-10 使用者定案）。
    - protected（human_played 裝置）→ 不登記 lease 放行，交由既有觀察者閘門保護。
    - 使用者按「開啟網頁」→ 立即放行（明確接管意圖，可能未取得 lease）。
    """
    from runtime_services import session_registry as registry
    waited = 0
    while True:
        result = registry.acquire(ip, registry.Owner.SCHEDULER,
                                  registry.Channel.WS, label="喚醒週期")
        if result.ok:
            if waited:
                log.info("[%s] 佔用已釋放，取得 SCHEDULER lease（等了約 %ds）", ip, waited)
            return
        if result.reason == "protected":
            log.info("[%s] human_played 保護帳號，不登記 SCHEDULER lease", ip)
            return
        conflict = result.conflict
        if conflict is not None and conflict.owner in registry.YIELDING_BORROWERS:
            result = registry.acquire(ip, registry.Owner.SCHEDULER,
                                      registry.Channel.WS, label="喚醒週期",
                                      preempt=True)
            if result.ok:
                log.info("[%s] 已搶回被 %s 借用的帳號", ip, conflict.owner.value)
                return
        if _web_launch_pending(ip):
            log.info("[%s] 偵測到開啟網頁請求，放行 SCHEDULER 閘門（未取得 lease）", ip)
            return
        holder = conflict.owner.value if conflict is not None else "未知"
        if waited == 0:
            log.info("[%s] 帳號被 %s 佔用，喚醒週期等待釋放", ip, holder)
        try:
            import bot_state
            bot_state.update_state(
                ip, task="等待 dashboard 連線釋放",
                step=f"帳號被 {holder} 佔用，{_DASHBOARD_WS_POLL_SEC}s 後重查")
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響等待
            log.debug("[%s] 等待佔用釋放狀態回報失敗", ip, exc_info=True)
        time.sleep(_DASHBOARD_WS_POLL_SEC)
        waited += _DASHBOARD_WS_POLL_SEC
```

模組 docstring/註解（:411-417）同步改寫：「唯一真相來源是 session_registry —— 喚醒週期開跑前 acquire SCHEDULER lease」。

- [ ] **Step 4: Implement — 呼叫端**

`new_main_v2.py:94`：`from game_actions.ws_phase import run_ws_phase, acquire_scheduler_lease`
`new_main_v2.py:105`：`acquire_scheduler_lease(ip, logger_obj)`（上方註解同步改為「先取得 SCHEDULER lease：搶回背景借用者、等待 dashboard 工具釋放」）。

`runtime_services/ws_runner_service.py:523-524`：

```python
                from game_actions.ws_phase import acquire_scheduler_lease
                acquire_scheduler_lease(ip, logger_obj)
```

`runtime_services/sleep_service.py` — module level 新增 + `run_sleep_cycle` 開頭（`cur_ts = time.time()` 之前）呼叫：

```python
def _release_scheduler_lease(ip: str) -> None:
    """入睡即釋放 SCHEDULER lease（冪等；未持有/失敗皆不影響睡眠流程）。"""
    try:
        from runtime_services import session_registry
        session_registry.release(ip, session_registry.Owner.SCHEDULER)
    except Exception:  # noqa: BLE001
        pass
```

```python
    _release_scheduler_lease(ip)
```

檢查 `ws_runner_service` 的入睡是否經 `run_sleep_cycle`（grep `run_sleep_cycle` 於該檔）；若否，在其入睡點補一行 `_release_scheduler_lease(ip)`（from sleep_service import）。

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_scheduler_lease.py tests/test_ws_human_offline_gate.py tests/test_session_registry.py -q`
（`test_session_registry.py` 若檔名不同，以 `ls tests/test_session_registry*` 實名為準；registry 迴歸必跑。）
Expected: all passed。
再跑 `python -m py_compile game_actions/ws_phase.py new_main_v2.py runtime_services/ws_runner_service.py runtime_services/sleep_service.py`。

- [ ] **Step 6: Commit**

```bash
git add game_actions/ws_phase.py new_main_v2.py runtime_services/ws_runner_service.py runtime_services/sleep_service.py tests/test_scheduler_lease.py tests/test_ws_human_offline_gate.py
git commit -m "feat(ws-phase): 喚醒登記 SCHEDULER lease（搶回借用者、等待工具、入睡釋放）"
```

---

### Task 5: 審查與 live 驗證（流程步驟，非 code task）

- [ ] dashboard-ui-review skill 過 Task 2/3 的 UI 改動（5003 live + 對比度）。
- [ ] Live 驗證（重啟 `new_main_v2.py` 後）：
  - 卡片徽章五態：借一台裝置開倉庫工具 → 「被借走：工具」；上線偵測 detector 裝置 → 「在線觀察（上線偵測）」；bot 執行任務中 → 「腳本執行」；真人手機在線且 bot 睡 → 「玩家在線」；全離線 → 「當前離線」。
  - 倉庫/工具最佳化/飛寵：開頁不自動連線/載入；按「連線」在帳號被佔用時先彈確認。
  - 喚醒 vs 工具：工具連線中手動觸發喚醒 → bot 進入「等待 dashboard 連線釋放」，斷線後取得 lease 繼續。
- [ ] 全分支 final review（review-package）→ merge → 主樹複跑 Task 1/4 測試。

# Dashboard 總後台與帳號系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 整個 dashboard 改為登入制，加入總後台（系統設定頁）：帳號申請/審核、每帳號裝置可見性、本機主機角色覆寫。

**Architecture:** 新增 `utils/dashboard_settings.py` 作為唯一儲存層（gitignored JSON、lock + tmp/rename 原子寫入、env 遷移）。`control_panel/shared/auth.py` 重寫為統一 session 認證 + 豁免清單 + 可見性 helpers，`control_panel_app.py` 掛全域 `before_request`。新 blueprint `routes_auth.py`（登入/申請/登出）與 `routes_admin.py`（總後台 API + `/admin` 頁）。`config_manager.get_global_config()` 插入 host_role 覆寫。

**Tech Stack:** Flask（現有）、`werkzeug.security`（Flask 既有依賴）、pytest + Flask test client（照 `tests/test_worker_routes_integration.py` 的 stub 模式）。

**Spec:** `docs/superpowers/specs/2026-07-04-dashboard-admin-console-design.md`

## Global Constraints

- 不加任何新第三方套件。
- 所有 JSON 讀取用 `encoding="utf-8-sig"`（本 repo 檔案常帶 BOM）。
- 測試指令一律指定檔案（repo hook 會擋裸 `pytest`）：`python -m pytest tests/test_dashboard_auth.py -q`。
- 每個 task 結束 commit：只 stage 該 task 動到的檔案，**絕不 `git add -A`**；不 push、不加 attribution footer。
- `dashboard_settings.json` 絕不進版控（Task 1 加 .gitignore）。
- 設定檔損毀 fail-closed：認證層回 503，不得 fallback 成無密碼。
- 保底規則：任何操作後至少要留一名 `status=active` 且 `is_admin=true` 的帳號。
- 帳號名驗證：3–32 字元、`[A-Za-z0-9_]`。
- 裝置 ID 正規化：狀態 key 可能是 `worker_id:emulator-5554` 複合形式，可見性比對一律用 `key.split(":")[-1]`（真實裝置 id）。
- UI 走設計系統：`{% include '_assets_head.html' %}` + `static/lib/tokens.css`/`components.css` tokens。

---

### Task 1: 設定儲存層 `utils/dashboard_settings.py`

**Files:**
- Create: `utils/dashboard_settings.py`
- Modify: `.gitignore`（在第 162 行 `auth_state/` secrets 區塊旁加一行）
- Test: `tests/test_dashboard_auth.py`（新檔，本 task 只寫 settings 部分）

**Interfaces:**
- Produces（後續 task 依賴的精確簽名）：
  - `settings_path() -> str`、`set_settings_path(path: str) -> None`（測試沙箱用）
  - `load_settings() -> dict`（檔案不存在→自動遷移建立；損毀→raise `SettingsCorruptError`）
  - `verify_login(username: str, password: str) -> dict | None`（回傳 account dict，僅 `status=="active"`）
  - `create_application(username: str, password: str) -> str | None`（回傳錯誤訊息或 None=成功，建立 `status="pending"`）
  - `approve_account(username: str, visible_devices: list[str]) -> None`
  - `reject_account(username: str) -> None`
  - `list_accounts() -> list[dict]`（不含 `password_hash`）
  - `create_account(username, password, is_admin: bool, visible_devices: list[str]) -> str | None`
  - `delete_account(username: str) -> str | None`（違反最後管理員保底→回錯誤訊息）
  - `set_password(username: str, password: str) -> str | None`
  - `set_visible_devices(username: str, devices: list[str]) -> str | None`
  - `set_admin(username: str, is_admin: bool) -> str | None`（降權最後管理員→錯誤）
  - `get_host_role() -> dict | None`、`set_host_role(mode: str | None, master_url: str | None) -> None`（兩者皆 None 視為清除覆寫）
  - `pending_count() -> int`
  - `VALID_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,32}$")`
  - class `SettingsCorruptError(Exception)`

**實作要點（完整行為規格）：**

```python
"""dashboard_settings.py — dashboard 帳號/主機角色設定儲存層。

檔案: <project_root>/dashboard_settings.json (gitignored)
結構: {"accounts": [{username, password_hash, is_admin, status, visible_devices}],
       "host_role": {"mode":..., "master_url":...} | None}
"""
import json
import os
import re
import threading

from werkzeug.security import check_password_hash, generate_password_hash

_LOCK = threading.RLock()
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_settings_path = os.path.join(_PROJECT_ROOT, "dashboard_settings.json")

VALID_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,32}$")


class SettingsCorruptError(Exception):
    pass


def set_settings_path(path):
    global _settings_path
    _settings_path = path


def settings_path():
    return _settings_path


def _migrate_initial():
    """檔案不存在時：由 env（或 legacy fallback）生成第一組管理員。"""
    user = os.environ.get("MUSHROOM_DASHBOARD_USER") or "infinite"
    pw = os.environ.get("MUSHROOM_DASHBOARD_PASS") or "infiniteroot"
    return {
        "accounts": [{
            "username": user,
            "password_hash": generate_password_hash(pw),
            "is_admin": True,
            "status": "active",
            "visible_devices": [],
        }],
        "host_role": None,
    }


def load_settings():
    with _LOCK:
        if not os.path.exists(_settings_path):
            data = _migrate_initial()
            _save(data)
            return data
        try:
            with open(_settings_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data.get("accounts"), list):
                raise ValueError("accounts missing")
            return data
        except (ValueError, OSError) as e:
            raise SettingsCorruptError(str(e)) from e


def _save(data):
    tmp = _settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _settings_path)
```

其餘函式全部走 `with _LOCK: data = load_settings(); ...mutate...; _save(data)` 模式。`_find(data, username)` 內部 helper 回傳 account dict or None。最後管理員保底：mutation 前計算 `sum(1 for a in accounts if a["is_admin"] and a["status"]=="active")`，若操作會使其歸零回傳錯誤字串 `"至少需保留一名管理員"`。`verify_login` 用 `check_password_hash`，只接受 `status=="active"`。`list_accounts` 回傳去掉 `password_hash` 的複本。

- [ ] **Step 1: 寫失敗測試** — `tests/test_dashboard_auth.py`：

```python
"""dashboard 帳號/認證/可見性測試。不碰真實裝置。"""
import os
import sys
import unittest
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import dashboard_settings as ds


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ds.set_settings_path(os.path.join(self.tmp.name, "dashboard_settings.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_creates_admin_from_env(self):
        os.environ["MUSHROOM_DASHBOARD_USER"] = "bossman"
        os.environ["MUSHROOM_DASHBOARD_PASS"] = "pw123456"
        try:
            data = ds.load_settings()
        finally:
            del os.environ["MUSHROOM_DASHBOARD_USER"]
            del os.environ["MUSHROOM_DASHBOARD_PASS"]
        self.assertEqual(data["accounts"][0]["username"], "bossman")
        self.assertTrue(data["accounts"][0]["is_admin"])
        self.assertIsNotNone(ds.verify_login("bossman", "pw123456"))
        self.assertIsNone(ds.verify_login("bossman", "wrong"))

    def test_application_flow(self):
        ds.load_settings()
        self.assertIsNone(ds.create_application("newguy", "secret12"))
        self.assertIsNotNone(ds.create_application("newguy", "x"))  # 重名拒絕
        self.assertIsNotNone(ds.create_application("a", "x"))  # 名稱太短
        self.assertIsNone(ds.verify_login("newguy", "secret12"))  # pending 不可登入
        self.assertEqual(ds.pending_count(), 1)
        ds.approve_account("newguy", ["emulator-5554"])
        acct = ds.verify_login("newguy", "secret12")
        self.assertEqual(acct["visible_devices"], ["emulator-5554"])
        self.assertEqual(ds.pending_count(), 0)

    def test_reject_deletes_pending(self):
        ds.load_settings()
        ds.create_application("newguy", "secret12")
        ds.reject_account("newguy")
        self.assertEqual(ds.pending_count(), 0)
        self.assertIsNone(ds.create_application("newguy", "secret12"))  # 名字釋出

    def test_last_admin_guard(self):
        data = ds.load_settings()
        admin = data["accounts"][0]["username"]
        self.assertIsNotNone(ds.delete_account(admin))
        self.assertIsNotNone(ds.set_admin(admin, False))

    def test_corrupt_file_raises(self):
        with open(ds.settings_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        with self.assertRaises(ds.SettingsCorruptError):
            ds.load_settings()

    def test_host_role_roundtrip(self):
        ds.load_settings()
        self.assertIsNone(ds.get_host_role())
        ds.set_host_role("worker", "http://10.0.0.1:5002")
        self.assertEqual(ds.get_host_role()["mode"], "worker")
        ds.set_host_role(None, None)
        self.assertIsNone(ds.get_host_role())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗** — `python -m pytest tests/test_dashboard_auth.py -q`，預期 `ModuleNotFoundError: utils.dashboard_settings` 或全紅。
- [ ] **Step 3: 實作 `utils/dashboard_settings.py`**（依上方規格，全部函式）。
- [ ] **Step 4: 跑測試轉綠** — `python -m pytest tests/test_dashboard_auth.py -q` 全過。
- [ ] **Step 5: `.gitignore` 加 `dashboard_settings.json`**（緊接 `auth_state/` 那行後，含註解 `# dashboard 帳號設定（含密碼雜湊，勿進版控）`），並確認 `git check-ignore dashboard_settings.json` 命中。
- [ ] **Step 6: Commit** — `git add utils/dashboard_settings.py tests/test_dashboard_auth.py .gitignore && git commit -m "feat(dashboard): 帳號/主機角色設定儲存層（原子寫入+env遷移+最後管理員保底）"`

---

### Task 2: 統一認證 — auth.py 重寫 + 登入/申請/登出 + 全域 before_request

**Files:**
- Modify: `control_panel/shared/auth.py`（重寫，保留 `_fly_pet_auth` 名稱相容）
- Create: `control_panel/routes_auth.py`（blueprint `auth_pages`）
- Create: `templates/login.html`、`templates/apply.html`（clone `templates/fly_pet_login.html` 改造）
- Modify: `control_panel_app.py`（註冊 blueprint + 掛 `@app.before_request`）
- Modify: `control_panel/routes_pages.py:126-146`（`/fly-pet/login` GET 改 redirect `/login`；`/fly-pet/logout` 改 redirect `/logout`）
- Test: `tests/test_dashboard_auth.py`（新增 `TestAuthGuard` class）

**Interfaces:**
- Consumes: Task 1 全部（`verify_login`, `create_application`, `SettingsCorruptError`, `pending_count`）
- Produces（後續 task 依賴）：
  - `auth.py`：`check_request_auth() -> Response | None`（給 before_request）、`require_admin(f)` decorator（非管理員 API 回 403 JSON、頁面 redirect `/`）、`is_admin() -> bool`、`current_user() -> str | None`、`current_visible_devices() -> list[str] | None`（管理員回 None=全可見）、`filter_visible_states(states: dict) -> dict`、`require_device_access(ip: str) -> None`（不可見 `abort(403)`）、`_fly_pet_auth`（相容 shim：改查 `session.get("dash_user")`）
  - session keys：`dash_user`（str）、`dash_admin`（bool）
  - 豁免定義（`auth.py` 頂部集中）：
    ```python
    EXEMPT_PATHS = {"/login", "/apply", "/logout", "/favicon.ico"}
    EXEMPT_PREFIXES = ("/static/",)
    MACHINE_EXEMPT_PATHS = {"/api/poll_commands", "/api/refresh_devices", "/api/report_status"}
    ```
    （注意：`/api/devices/register` 是瀏覽器觸發，**不豁免**。push server 是獨立 port 5000 的另一個 Flask app，不受影響、無需豁免。）

**`check_request_auth` 行為：**

```python
def check_request_auth():
    p = request.path
    if p in EXEMPT_PATHS or p in MACHINE_EXEMPT_PATHS:
        return None
    if any(p.startswith(pre) for pre in EXEMPT_PREFIXES):
        return None
    if session.get("dash_user"):
        return None
    try:
        dashboard_settings.load_settings()
    except dashboard_settings.SettingsCorruptError:
        return jsonify({"status": "error", "message": "settings corrupted"}), 503
    if request.is_json or p.startswith("/api/") or p.startswith("/ws/"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    return redirect("/login")
```

**`routes_auth.py`：** `GET/POST /login`（成功→`session["dash_user"]`+`session["dash_admin"]`、redirect `/`；失敗→render login.html with error）、`GET/POST /apply`（`create_application`；同 IP 簡單 rate limit：module-level dict `{ip: [timestamps]}`，60 秒內 >5 次回 429；成功→render 顯示「已送出，待管理員審核」）、`GET /logout`（`session.clear()`→redirect `/login`）。

**templates：** `login.html` 以 `fly_pet_login.html` 為底，`<h1>` 改「菇勇者控制台」，form action `/login`，底部加 `<a href="/apply">申請帳號</a>`。`apply.html` 同款式，form action `/apply`，帳號欄位加 `pattern="[A-Za-z0-9_]{3,32}"`，附成功/錯誤訊息區塊。

**`control_panel_app.py` 接線：** import `routes_auth` 加進 blueprint 註冊 loop；在 `add_no_cache_headers`（line 145 附近）旁加：

```python
from control_panel.shared.auth import check_request_auth

@app.before_request
def _global_auth_guard():
    return check_request_auth()
```

- [ ] **Step 1: 寫失敗測試** — `TestAuthGuard`（照 `tests/test_worker_routes_integration.py` 的 `_install_lightweight_stubs` 模式 stub 掉重依賴後 import `control_panel_app`）：

```python
class TestAuthGuard(unittest.TestCase):
    # setUp: 沙箱 settings path + 建 admin("boss","pw123456") + 一般帳號("viewer","pw123456", visible=["emulator-5554"])
    # + self.client = control_panel_app.app.test_client()

    def test_page_redirects_to_login_when_anonymous(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_api_returns_401_when_anonymous(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 401)

    def test_worker_endpoints_exempt(self):
        r = self.client.post("/api/report_status", json={})
        self.assertNotEqual(r.status_code, 401)

    def test_login_logout_cycle(self):
        r = self.client.post("/login", data={"username": "boss", "password": "pw123456"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.client.get("/logout")
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_pending_cannot_login(self):
        ds.create_application("waiting", "pw123456")
        r = self.client.post("/login", data={"username": "waiting", "password": "pw123456"})
        self.assertEqual(r.status_code, 200)  # 留在登入頁帶錯誤訊息
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_apply_rate_limit(self):
        for i in range(5):
            self.client.post("/apply", data={"username": f"user_{i}", "password": "pw123456"})
        r = self.client.post("/apply", data={"username": "user_x", "password": "pw123456"})
        self.assertEqual(r.status_code, 429)

    def test_corrupt_settings_fail_closed(self):
        with open(ds.settings_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 503)
```

- [ ] **Step 2: 跑測試確認失敗** — `python -m pytest tests/test_dashboard_auth.py::TestAuthGuard -q`
- [ ] **Step 3: 實作**（auth.py 重寫、routes_auth.py、兩個 template、app 接線、fly-pet login/logout redirect）。`_fly_pet_auth` shim 保留原 decorator 介面（30+ 處 import 不用改）。
- [ ] **Step 4: 跑測試轉綠** — `python -m pytest tests/test_dashboard_auth.py -q` 全過，另跑既有 `python -m pytest tests/test_worker_routes_integration.py tests/test_smoke_config_api.py -q` 確認沒破壞（這些測試未登入 → 需在其 setUp 沙箱先登入或本 task 在測試 stub 中提供已登入 session；若既有測試大量 401，在 conftest 不可行時允許用 `client.session_transaction()` 補登入，逐檔最小修改）。
- [ ] **Step 5: Commit** — `git add control_panel/shared/auth.py control_panel/routes_auth.py control_panel_app.py control_panel/routes_pages.py templates/login.html templates/apply.html tests/test_dashboard_auth.py <被最小修改的既有測試> && git commit -m "feat(dashboard): 全站登入制（before_request 豁免清單）+ 申請帳號頁 + 飛寵登入整併"`

---

### Task 3: 裝置可見性過濾

**Files:**
- Modify: `control_panel/routes_status.py`（`/api/status` line 461 過濾 states；`/api/device_data/<ip>` line 181、`/api/carpark/<ip>` line 210、`/api/daily_progress/<ip>` line 383 加 `require_device_access(ip)`）
- Modify: `control_panel/routes_control.py`（全部 7 個 `<ip>` 路由開頭加 `require_device_access(ip)`：pause/resume/skip_sleep/wake_delay/manual_release/force_sleep/recover）
- Test: `tests/test_dashboard_auth.py`（新增 `TestVisibility`）

**Interfaces:**
- Consumes: Task 2 的 `filter_visible_states(states)`、`require_device_access(ip)`。
- 過濾規則：管理員不過濾；一般帳號以 `key.split(":")[-1] in visible_devices` 過濾 `/api/status` 的 `bots` dict（含 disabled 回填的部分，在 jsonify 前統一過一次）；`require_device_access` 對不可見裝置 `abort(403)`（不洩漏存在與否，403 訊息固定 `"forbidden"`）。

- [ ] **Step 1: 寫失敗測試**：

```python
class TestVisibility(unittest.TestCase):
    # setUp 同 TestAuthGuard；並向 bot_state 塞兩台裝置狀態：
    # emulator-5554 與 emulator-5556（或 stub get_all_states 回傳兩 key）

    def test_admin_sees_all(self):
        self._login("boss")
        bots = self.client.get("/api/status").get_json()["bots"]
        self.assertIn("emulator-5554", bots)
        self.assertIn("emulator-5556", bots)

    def test_viewer_sees_only_assigned(self):
        self._login("viewer")  # visible=["emulator-5554"]
        bots = self.client.get("/api/status").get_json()["bots"]
        self.assertIn("emulator-5554", bots)
        self.assertNotIn("emulator-5556", bots)

    def test_viewer_control_forbidden_on_hidden_device(self):
        self._login("viewer")
        r = self.client.post("/api/pause/emulator-5556")
        self.assertEqual(r.status_code, 403)

    def test_viewer_control_allowed_on_visible_device(self):
        self._login("viewer")
        r = self.client.post("/api/pause/emulator-5554")
        self.assertNotEqual(r.status_code, 403)

    def test_composite_worker_key_matches_real_id(self):
        self._login("viewer")
        r = self.client.post("/api/pause/laptop_worker:emulator-5554")
        self.assertNotEqual(r.status_code, 403)
```

- [ ] **Step 2: 跑測試確認失敗** — `python -m pytest tests/test_dashboard_auth.py::TestVisibility -q`
- [ ] **Step 3: 實作**（routes_status 出口過濾 + 兩檔共 10 個 `<ip>` 端點加 guard，一行 `require_device_access(ip)` 各自路由函式第一行）。
- [ ] **Step 4: 跑測試轉綠** — `python -m pytest tests/test_dashboard_auth.py -q`
- [ ] **Step 5: Commit** — `git add control_panel/routes_status.py control_panel/routes_control.py tests/test_dashboard_auth.py && git commit -m "feat(dashboard): 裝置可見性過濾（/api/status 出口過濾 + 控制端點 403）"`

---

### Task 4: 總後台 API blueprint `routes_admin.py`

**Files:**
- Create: `control_panel/routes_admin.py`（blueprint `admin_console`，全部掛 `@require_admin`）
- Modify: `control_panel_app.py`（註冊 blueprint）
- Test: `tests/test_dashboard_auth.py`（新增 `TestAdminApi`）

**Interfaces:**
- Consumes: Task 1 store 全部、Task 2 `require_admin`。
- Produces（Task 6 UI 依賴的 API contract，所有回應 `{"status":"ok", ...}` / `{"status":"error","message":...}`）：
  - `GET /api/admin/accounts` → `{"status":"ok","accounts":[...]}`（不含 hash）
  - `POST /api/admin/accounts` body `{username,password,is_admin,visible_devices}` → 建 active 帳號
  - `POST /api/admin/accounts/<username>/approve` body `{visible_devices}`
  - `POST /api/admin/accounts/<username>/reject`
  - `DELETE /api/admin/accounts/<username>`
  - `POST /api/admin/accounts/<username>/password` body `{password}`
  - `POST /api/admin/accounts/<username>/visible_devices` body `{visible_devices}`
  - `POST /api/admin/accounts/<username>/admin` body `{is_admin}`
  - `GET /api/admin/pending_count` → `{"status":"ok","count":N}`
  - `GET /api/admin/host_role` → `{"status":"ok","hostname":...,"effective":{"mode":...,"master_url":...,"source":"override|host_settings|default"},"override":{...}|null}`（effective 由 `config_manager.get_global_config()` + `get_hostname()` 組出）
  - `POST /api/admin/host_role` body `{mode,master_url}`（空值=清除覆寫）→ 回應附 `"note":"重啟 new_main_v2.py 後生效"`
  - store 層回傳錯誤字串時 → HTTP 400 帶該訊息。

- [ ] **Step 1: 寫失敗測試** — `TestAdminApi`：管理員可 CRUD/approve/reject/pending_count/host_role roundtrip；一般帳號打任一 `/api/admin/*` 回 403；刪最後管理員回 400。
- [ ] **Step 2: 跑測試確認失敗** — `python -m pytest tests/test_dashboard_auth.py::TestAdminApi -q`
- [ ] **Step 3: 實作 `routes_admin.py` + 註冊**。
- [ ] **Step 4: 跑測試轉綠** — `python -m pytest tests/test_dashboard_auth.py -q`
- [ ] **Step 5: Commit** — `git add control_panel/routes_admin.py control_panel_app.py tests/test_dashboard_auth.py && git commit -m "feat(dashboard): 總後台 API（帳號審核/CRUD/可見裝置/主機角色）"`

---

### Task 5: `config_manager` host_role 覆寫

**Files:**
- Modify: `config_manager.py`（`get_global_config()` lines 751-779：host_settings merge 之後、return 之前插入覆寫）
- Test: `tests/test_dashboard_auth.py`（新增 `TestHostRoleOverride`）

**Interfaces:**
- Consumes: Task 1 `get_host_role()`（import 放函式內 local import，避免啟動順序問題；`utils.dashboard_settings` 只依賴 stdlib+werkzeug，無循環風險）。
- 行為：`get_host_role()` 回傳 dict 時，`mode`/`master_url` 兩鍵有值才覆寫（部分覆寫允許）；`SettingsCorruptError` 或任何例外 → log warning 並沿用原邏輯（**主程式啟動不能因 dashboard 設定檔壞掉而掛**，fail-closed 只適用於 dashboard 認證層）。

```python
# get_global_config() 尾端插入：
try:
    from utils import dashboard_settings as _ds
    _role = _ds.get_host_role()
    if _role:
        for _k in ("mode", "master_url"):
            if _role.get(_k):
                final_cfg[_k] = _role[_k]
except Exception as e:  # noqa: BLE001 — 設定檔問題不可擋主程式啟動
    logging.warning("dashboard host_role override skipped: %s", e)
```

- [ ] **Step 1: 寫失敗測試** — `TestHostRoleOverride`：沙箱 settings 設 `set_host_role("worker","http://x:5002")` 後 `config_manager.get_global_config()["mode"] == "worker"`；清除覆寫後回到原值；settings 檔損毀時 `get_global_config()` 不 raise。
- [ ] **Step 2: 跑測試確認失敗** — `python -m pytest tests/test_dashboard_auth.py::TestHostRoleOverride -q`
- [ ] **Step 3: 實作**。
- [ ] **Step 4: 跑測試轉綠** + 既有 config 測試不破：`python -m pytest tests/test_dashboard_auth.py tests/test_smoke_config_api.py -q`
- [ ] **Step 5: Commit** — `git add config_manager.py tests/test_dashboard_auth.py && git commit -m "feat(config): dashboard host_role 覆寫優先於 host_settings（fail-open 保護主程式啟動）"`

---

### Task 6: 總後台 UI + 導覽入口 + 待審核紅點

**Files:**
- Create: `templates/admin_settings.html`（獨立完整頁，`{% include '_assets_head.html' %}`，非 dashboard.html 內嵌 pane）
- Modify: `control_panel/routes_admin.py`（加 `GET /admin` → `render_template("admin_settings.html")`，掛 `@require_admin`）
- Modify: `templates/dashboard.html`（nav `side-rail` line ~1250 加「系統設定」按鈕 + 紅點 badge；管理員才顯示）
- Modify: `control_panel/routes_pages.py`（主頁 render 傳入 `is_admin=session.get("dash_admin", False)`）

**Interfaces:**
- Consumes: Task 4 全部 API contract。
- UI 規格：
  - `admin_settings.html` 三區塊（單檔、vanilla JS fetch，比照 fly-pet 頁模式）：
    1. **待審核申請**：載入 `GET /api/admin/accounts` 過濾 `status=="pending"`；每列帳號名 + 裝置 checkbox 清單（裝置清單來自 `GET /api/status` 的 `bots` keys，正規化 `split(":")[-1]` 去重）+ 「核准」「拒絕」按鈕。無 pending 時整區隱藏。
    2. **帳號管理**：表格列出帳號（名稱/角色/狀態/可見裝置數），操作：新增、刪除、改密碼（prompt 對話）、編輯可見裝置（同 checkbox 清單）、設/撤管理員。錯誤訊息（如最後管理員保底）顯示在頁頂 toast。
    3. **主機角色**：顯示 hostname、effective mode/master_url 與 source；表單編輯覆寫（mode select: master/worker/清除覆寫、master_url input），儲存後顯著顯示「重啟 new_main_v2.py 後生效」。
  - `dashboard.html`：nav 按鈕 `{% if is_admin %}<button class="nav-btn" onclick="window.location='/admin'">系統設定<span id="pendingBadge" class="badge" hidden></span></button>{% endif %}`；頁面載入時（管理員才）fetch `/api/admin/pending_count`，`count>0` 時 badge 顯示數字。badge 樣式用現有 tokens（`var(--color-danger)` 類）。
- [ ] **Step 1: 實作 `/admin` 頁 + templates**（此 task UI 為主，無單元測試；`GET /admin` 的權限已被 Task 4 `require_admin` 測試涵蓋，補一個 route 存在性 assert 到 `TestAdminApi`）。
- [ ] **Step 2: 語法/煙霧驗證** — `python -m py_compile control_panel/routes_admin.py control_panel/routes_pages.py` + `python -m pytest tests/test_dashboard_auth.py -q`。
- [ ] **Step 3: Commit** — `git add templates/admin_settings.html templates/dashboard.html control_panel/routes_admin.py control_panel/routes_pages.py tests/test_dashboard_auth.py && git commit -m "feat(dashboard): 系統設定頁 UI + 導覽入口 + 待審核紅點"`

---

### Task 7: 收尾 — 文件 + 全量相關測試

**Files:**
- Modify: `CLAUDE.md`（Key Modules 表加一行 dashboard auth；Common Operations 提 `/admin`）
- Modify: `docs/INDEX.md`（若有對應段落，加入 auth/admin 條目）

- [ ] **Step 1: 文件更新**（兩檔各加 1-3 行，不長篇）。
- [ ] **Step 2: 跑相關測試全量** — `python -m pytest tests/test_dashboard_auth.py tests/test_worker_routes_integration.py tests/test_smoke_config_api.py tests/test_inventory_routes.py tests/test_ad_reward_routes.py tests/test_relic_sprint_routes.py -q`（覆蓋所有被 `_fly_pet_auth` shim 影響的路由測試）。
- [ ] **Step 3: Commit** — `git add CLAUDE.md docs/INDEX.md && git commit -m "docs: dashboard 總後台/登入制文件補充"`

---

## Self-Review 紀錄

- Spec coverage：兩層帳號(T1/T2/T4)、申請審核(T1/T2/T4/T6)、紅點(T6)、儲存+遷移+原子寫入(T1)、全站登入+豁免(T2)、飛寵整併(T2)、可見性+403(T3)、主機角色+重啟提示(T4/T5/T6)、gitignore(T1)、fail-closed(T2)/fail-open 主程式(T5)、rate limit(T2)、username 驗證(T1)、最後管理員保底(T1/T4)、測試(各 task)。無缺口。
- 型別/命名一致性：`filter_visible_states`/`require_device_access`/`require_admin`/`dash_user`/`dash_admin` 各 task 引用一致。
- 已知風險已標註：Task 2 Step 4 既有測試可能大量 401，處理方式已寫明（session_transaction 最小修補）。

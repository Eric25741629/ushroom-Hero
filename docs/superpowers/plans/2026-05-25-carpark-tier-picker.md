# Carpark Tier Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 啟用跨服車位 checkbox + 跨服車座等級 select to the per-device settings modal so the user can configure `carpark.enabled` / `carpark.cross_tier` without editing `bot_config.json`.

**Architecture:** Pure frontend change inside `templates/dashboard.html`. Existing `POST /api/config/<ip>` already accepts arbitrary partial config. Frontend stashes the existing `carpark` dict on load and spreads it on save, so the backend's shallow `current.update()` merge can't wipe unmanaged keys (`avoid_lots`, `cluster`, daytime/nighttime totals).

**Tech Stack:** Vanilla JS + plain HTML inside the existing Flask-served `dashboard.html`. No build step. No new dependencies. No backend changes.

**Spec:** `docs/superpowers/specs/2026-05-25-carpark-tier-picker-design.md`

**Testing approach:** The dashboard has no unit/integration test harness. Validation is **manual** via the spec's test plan (Tasks 5a–5e). Each prior task ends with a syntax check (`python -m py_compile` not applicable to HTML; we use a lightweight HTML parse via Python's `html.parser` to catch unclosed tags).

---

### Task 1: Add HTML controls inside `#webBackendGroup`

**Files:**
- Modify: `templates/dashboard.html` — insert between line 1103 (closing `</div>` of the screenshot-method block) and line 1105 (opening `<div>` of the Viewport Width block).

**Why here:** Carpark is H5-only (uses cocos scene tree via `_page.evaluate`). `#webBackendGroup` already hides on `backend == adb` via `toggleBackendInputs()`. Placing the controls inside this group means they auto-hide for ADB devices with zero extra logic.

- [ ] **Step 1: Verify insertion anchor**

Run: `grep -n "editWebScreenshotMethod\|editWebViewportWidth" templates/dashboard.html`

Expected output (line numbers may have drifted a few lines from spec-time — check the structure, not exact numbers):
```
1093:            <select id="editWebScreenshotMethod" class="form-control">
1109:            <input type="number" id="editWebViewportWidth" class="form-control" placeholder="540">
```

The `screenshot method <div>` ends with `</div>` (currently line 1103). The `Viewport Width <div>` opens right after (currently line 1105). Insert between them.

- [ ] **Step 2: Insert the carpark control block**

Use the Edit tool. Match the existing two-blank-line-separated `<div>` … `</div>` block style. The insertion replaces the gap between the screenshot-method block and the viewport block.

`old_string` (preserve exact whitespace — this is the closing of the screenshot-method block followed by the opening of the viewport block):
```html
            <div style="font-size:0.72em; color:#8ea6c8; margin-top:4px;">Canvas Capture 適用於某些 Playwright 截圖異常的情境</div>

          </div>

          <div>

            <label style="font-size:0.8em;">Viewport Width</label>
```

`new_string`:
```html
            <div style="font-size:0.72em; color:#8ea6c8; margin-top:4px;">Canvas Capture 適用於某些 Playwright 截圖異常的情境</div>

          </div>

          <div>

            <label style="font-size:0.8em; display:flex; align-items:center; gap:6px;">
              <input type="checkbox" id="chkCarparkEnabled" onchange="onCarparkEnabledChange()">
              啟用跨服車位（carpark.enabled）
            </label>

            <select id="editCarparkTier" class="form-control" style="margin-top:4px;">
              <option value="silver">鉑銀（silver）— 唯一已驗證</option>
            </select>

            <div style="font-size:0.72em; color:#8ea6c8; margin-top:4px;">勾選後會自動把 experimental_cocos_navigation 設為 true（scheduler gating 前置條件）。其他 carpark 欄位（avoid_lots / 白夜總數 / cluster / prefer_back）保留 JSON 既有值。</div>

          </div>

          <div>

            <label style="font-size:0.8em;">Viewport Width</label>
```

- [ ] **Step 3: Verify the HTML still parses + the new IDs exist**

Run (from repo root):
```bash
PYTHONIOENCODING=utf-8 C:/Users/Eric/.conda/envs/mushroom1/python.exe -c "
from html.parser import HTMLParser
src = open(r'templates/dashboard.html', encoding='utf-8').read()
class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = set()
    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == 'id': self.ids.add(v)
p = P(); p.feed(src)
for need in ('chkCarparkEnabled', 'editCarparkTier', 'webBackendGroup', 'editWebScreenshotMethod', 'editWebViewportWidth'):
    print(f'{need}: {\"OK\" if need in p.ids else \"MISSING\"}')
"
```

Expected: every line ends `OK`. If `chkCarparkEnabled` or `editCarparkTier` is `MISSING`, the Edit didn't land. If the parser raises, something broke (unclosed tag) — revert and retry.

---

### Task 2: Add JS state + checkbox→select wiring

**Files:**
- Modify: `templates/dashboard.html` — JS block. Two insertions:
  1. A module-level `let _existingCarpark = {};` near the top of the JS block.
  2. A new function `onCarparkEnabledChange()` near `toggleBackendInputs()` (line ~2354).

- [ ] **Step 1: Find the JS scope anchor**

Run: `grep -n "function toggleBackendInputs" templates/dashboard.html`

Expected:
```
2354:    function toggleBackendInputs() {
```

We'll add `_existingCarpark` near where other module-level lets/vars live, and `onCarparkEnabledChange()` directly above `toggleBackendInputs`.

- [ ] **Step 2: Add the module-level `_existingCarpark` declaration**

The JS block has many `let _lvWs = null;` style declarations. Find a good anchor with grep:
`grep -n "let _lvWs\|let _lvIp" templates/dashboard.html`

Expected:
```
... two matches around line 2680–2700 ...
```

Use Edit to add the new declaration right after the `let _lvIp = null;` line.

`old_string`:
```js
    let _lvIp = null;
```

`new_string`:
```js
    let _lvIp = null;
    let _existingCarpark = {};
```

(If `_lvIp` appears more than once, use `replace_all: false` and add enough surrounding context to disambiguate. Use the FIRST module-level declaration site, not local re-declarations inside functions.)

- [ ] **Step 3: Add the `onCarparkEnabledChange()` function**

Insert directly above `function toggleBackendInputs()`.

`old_string`:
```js
    function toggleBackendInputs() {
```

`new_string`:
```js
    function onCarparkEnabledChange() {
      const chk = document.getElementById('chkCarparkEnabled');
      const sel = document.getElementById('editCarparkTier');
      if (chk && sel) sel.disabled = !chk.checked;
    }

    function toggleBackendInputs() {
```

- [ ] **Step 4: Verify JS still parses**

The page is served as a Flask template (no bundler), but we can syntax-check the JS by extracting it. Use a one-shot script:

```bash
PYTHONIOENCODING=utf-8 C:/Users/Eric/.conda/envs/mushroom1/python.exe -c "
import re, subprocess, tempfile, os
src = open(r'templates/dashboard.html', encoding='utf-8').read()
# Extract every <script>...</script> block
blocks = re.findall(r'<script[^>]*>(.*?)</script>', src, re.S)
joined = '\n;\n'.join(blocks)
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write(joined); path = f.name
# Use node --check if available, else just grep for the new symbols.
for sym in ('onCarparkEnabledChange', '_existingCarpark'):
    print(f'{sym}: {\"FOUND\" if sym in joined else \"MISSING\"}')
os.unlink(path)
"
```

Expected: both symbols `FOUND`.

---

### Task 3: Extend `openSettings()` to read carpark

**Files:**
- Modify: `templates/dashboard.html` — inside `openSettings(ip)`, after the `web_viewport_height` read (around line 2776), before `toggleBackendInputs()` (line 2778).

- [ ] **Step 1: Locate the insertion point**

Run: `grep -n "editWebViewportHeight'\\).value = config.web_viewport_height\|toggleBackendInputs()" templates/dashboard.html`

Expected (two matches near each other in openSettings):
```
2776:        document.getElementById('editWebViewportHeight').value = config.web_viewport_height || 960;
2778:        toggleBackendInputs();
```

- [ ] **Step 2: Insert the carpark read**

`old_string`:
```js
        document.getElementById('editWebViewportHeight').value = config.web_viewport_height || 960;

        toggleBackendInputs();
```

`new_string`:
```js
        document.getElementById('editWebViewportHeight').value = config.web_viewport_height || 960;

        _existingCarpark = (config.carpark && typeof config.carpark === 'object') ? config.carpark : {};
        document.getElementById('chkCarparkEnabled').checked = _existingCarpark.enabled === true;
        const tier = String(_existingCarpark.cross_tier || 'silver').toLowerCase();
        const tierSel = document.getElementById('editCarparkTier');
        // Future-proof: if config has a tier we don't list yet, keep it as the selected value
        // by adding a transient option. This avoids silently downgrading on save.
        if (tier && !Array.from(tierSel.options).some(o => o.value === tier)) {
          const opt = document.createElement('option');
          opt.value = tier; opt.text = `${tier}（未驗證 — 已存在 JSON）`;
          tierSel.appendChild(opt);
        }
        tierSel.value = ['silver','gold','diamond','bronze','server'].includes(tier) ? tier : 'silver';
        onCarparkEnabledChange();

        toggleBackendInputs();
```

- [ ] **Step 3: Smoke-test openSettings by loading the dashboard**

Start the dashboard if not already running:
```bash
curl -s -m 3 http://127.0.0.1:5002/ > /dev/null && echo "dashboard up" || echo "dashboard DOWN — start with: conda activate mushroom1; python control_panel_app.py"
```

If up: open `http://127.0.0.1:5002` in a browser, click 設定 on `emulator-5556`, verify:
- Modal opens without JS console errors (F12 → Console).
- `啟用跨服車位` checkbox is **checked** (because 5556 has `enabled: true` in JSON).
- `跨服車座等級` select shows `鉑銀（silver）— 唯一已驗證`.

Don't save yet — saveConfig isn't wired for carpark in this task.

---

### Task 4: Extend `saveConfig()` to build merged payload

**Files:**
- Modify: `templates/dashboard.html` — inside `saveConfig()`, after the `web_viewport_height: ...` line in the payload object (around line 2850), before the closing `};`.

- [ ] **Step 1: Locate the saveConfig payload tail**

Run: `grep -n "web_viewport_height: parseInt" templates/dashboard.html`

Expected (two matches; saveConfig is the second one, around line 2850):
```
2428:        web_viewport_height: parseInt(document.getElementById('editWebViewportHeight').value || '960', 10)
2850:        web_viewport_height: parseInt(document.getElementById('editWebViewportHeight').value || '960', 10)
```

Use the **second** match (inside `saveConfig`, NOT the one inside `startWebLogin`).

- [ ] **Step 2: Modify the payload tail to add carpark + cocos flag**

Match enough context to target only the `saveConfig` instance. The `saveConfig` instance ends with `};` then a try/catch.

`old_string`:
```js
        web_viewport_height: parseInt(document.getElementById('editWebViewportHeight').value || '960', 10)

      };

      try {

        const resp = await fetch(`/api/config/${ip}`, {
```

`new_string`:
```js
        web_viewport_height: parseInt(document.getElementById('editWebViewportHeight').value || '960', 10)

      };

      // Carpark: build merged dict so we don't wipe avoid_lots/cluster/etc.
      // (backend update_device_config does shallow current.update — see spec)
      const cpEnabled = document.getElementById('chkCarparkEnabled').checked;
      const cpTier = document.getElementById('editCarparkTier').value || 'silver';
      const hasExisting = Object.keys(_existingCarpark).length > 0;
      if (cpEnabled || hasExisting) {
        payload.carpark = Object.assign({}, _existingCarpark, {
          enabled: cpEnabled,
          cross_tier: cpTier,
        });
      }
      // Enabling carpark requires experimental_cocos_navigation (scheduler gating).
      // We set it on enable but DON'T clear it on disable — other future cocos
      // features may depend on it.
      if (cpEnabled) {
        payload.experimental_cocos_navigation = true;
      }

      try {

        const resp = await fetch(`/api/config/${ip}`, {
```

- [ ] **Step 3: Verify the new payload logic is in saveConfig (not startWebLogin)**

Run:
```bash
PYTHONIOENCODING=utf-8 C:/Users/Eric/.conda/envs/mushroom1/python.exe -c "
src = open(r'templates/dashboard.html', encoding='utf-8').read()
# saveConfig must contain the merged-carpark block, startWebLogin must NOT
sc_start = src.index('async function saveConfig')
sc_end = src.index('async function', sc_start + 1)
in_save = src[sc_start:sc_end]
swl_start = src.index('async function startWebLogin')
swl_end = src.index('async function', swl_start + 1)
in_swl = src[swl_start:swl_end]
print('saveConfig has carpark merge:', 'payload.carpark = Object.assign' in in_save)
print('startWebLogin has carpark merge (should be False):', 'payload.carpark = Object.assign' in in_swl)
"
```

Expected:
```
saveConfig has carpark merge: True
startWebLogin has carpark merge (should be False): False
```

---

### Task 5: Manual end-to-end validation

These checks match the spec's test plan. Run against the live dashboard. If the bot is currently running, no need to stop it — the dashboard reads/writes `bot_config.json` directly and `load_config()` re-reads on every call (no module-level cache).

**Pre-check:** capture current `carpark` state for 5556 so we can verify other fields are preserved.

```bash
PYTHONIOENCODING=utf-8 C:/Users/Eric/.conda/envs/mushroom1/python.exe -c "
import json
cfg = json.load(open(r'bot_config.json', encoding='utf-8-sig'))
print(json.dumps(cfg['devices']['emulator-5556'].get('carpark'), ensure_ascii=False, indent=2))
"
```

Expected output (the block we set earlier this session):
```json
{
  "enabled": true,
  "cross_tier": "silver",
  "cross_lot_preference": "back",
  "cluster": true,
  "avoid_lots": [1, 2, 3, 4],
  "daytime_total": 6,
  "daytime_cross": 1,
  "nighttime_total": 5,
  "nighttime_cross": 0
}
```

Keep this output — Step 5c verifies the 6 non-UI fields survived.

- [ ] **Step 5a: web_h5 device shows controls + correct values**

In browser → 設定 for `emulator-5556`:
- Checkbox `啟用跨服車位` is **checked**
- Select shows `鉑銀（silver）— 唯一已驗證`
- F12 console has no errors

- [ ] **Step 5b: adb device hides controls**

In browser → 設定 for `adb-fc65396d-4LPqmI._adb-tls-connect._tcp`:
- The whole `web_h5` block (including the new checkbox + select) is hidden
- F12 console has no errors

- [ ] **Step 5c: Save preserves the unmanaged carpark fields**

In browser → 設定 for `emulator-5556` → click 儲存 (no UI changes).
After save, run the same Pre-check command above. Verify the printed dict is **identical** to the Pre-check output — all 9 keys present, same values.

- [ ] **Step 5d: Uncheck → save → re-check is round-trip safe**

In browser → 設定 for `emulator-5556`:
1. Uncheck `啟用跨服車位` → 儲存.
   Verify JSON: `carpark.enabled = false`; all other 8 keys unchanged.
2. Re-open settings, re-check the checkbox → 儲存.
   Verify JSON: `carpark.enabled = true`; `experimental_cocos_navigation = true`; all 8 other keys unchanged.

- [ ] **Step 5e: New device with no carpark block stays clean if unchecked**

Verify on a device that has NO carpark block. (`emulator-5558` and `emulator-5560` qualify — they're web_h5 but never had carpark configured.)

In browser → 設定 for `emulator-5558`:
1. Open settings — checkbox **unchecked**, select silver (defaults).
2. Save WITHOUT checking the box.
   Verify: `emulator-5558` JSON has **no** `carpark` key (Object.keys check below).
3. Open again, check the box → 儲存.
   Verify: `emulator-5558` JSON now has `{"enabled": true, "cross_tier": "silver"}` (exactly two keys, no extras).

Verification command:
```bash
PYTHONIOENCODING=utf-8 C:/Users/Eric/.conda/envs/mushroom1/python.exe -c "
import json
cfg = json.load(open(r'bot_config.json', encoding='utf-8-sig'))
d = cfg['devices']['emulator-5558']
print('carpark key present:', 'carpark' in d)
print('carpark value:', json.dumps(d.get('carpark'), ensure_ascii=False))
print('cocos flag:', d.get('experimental_cocos_navigation'))
"
```

After step 5e.3 expected output:
```
carpark key present: True
carpark value: {"enabled": true, "cross_tier": "silver"}
cocos flag: True
```

If 5e.2 leaves `carpark key present: True`, the "omit when unchecked & no prior block" guard in saveConfig is wrong — revisit Task 4 Step 2.

- [ ] **Step 5f: Revert any test mutations on 5558**

If 5e.3 left `carpark` on 5558, decide whether to keep it:
- If you want 5558 in the carpark fleet, keep it.
- If 5558 was test-only, run:
  ```bash
  PYTHONIOENCODING=utf-8 C:/Users/Eric/.conda/envs/mushroom1/python.exe -c "
  import json, io
  p = r'bot_config.json'
  cfg = json.load(open(p, encoding='utf-8-sig'))
  cfg['devices']['emulator-5558'].pop('carpark', None)
  cfg['devices']['emulator-5558'].pop('experimental_cocos_navigation', None)
  with open(p, 'w', encoding='utf-8') as f:
      json.dump(cfg, f, ensure_ascii=False, indent=4)
  print('cleaned')
  "
  ```

---

### Task 6: Commit (requires user authorization)

Per project CLAUDE.md: "NEVER commit changes unless the user explicitly asks you to."

- [ ] **Step 1: Ask the user for commit approval**

Show the user a draft commit message + the exact diff list. Do NOT run `git commit` until they say yes.

Draft message:
```
feat(dashboard): add carpark tier picker to per-device settings modal

- New 啟用跨服車位 checkbox + 跨服車座等級 select inside #webBackendGroup
- Stash-and-spread carpark dict on save so backend's shallow update_device_config
  merge can't wipe avoid_lots/cluster/etc.
- Auto-set experimental_cocos_navigation: true when carpark is enabled
- Silver is the only tier listed (only verified path in carpark_auto.py)

Spec: docs/superpowers/specs/2026-05-25-carpark-tier-picker-design.md
```

Files in the commit:
- `templates/dashboard.html` (modified)
- `docs/superpowers/specs/2026-05-25-carpark-tier-picker-design.md` (new)
- `docs/superpowers/plans/2026-05-25-carpark-tier-picker.md` (new)
- `bot_config.json` is in the working tree from the earlier session edit (5556 + 7fe98fc6 carpark). Confirm with the user whether to bundle that into this commit or split — the JSON edit is a separate-ish change.

- [ ] **Step 2: After user approves, commit only the staged files**

DO NOT use `git add -A`. Stage specific paths only:
```bash
git -C "C:/nas同步_project/菇勇者全自動掛機" add templates/dashboard.html docs/superpowers/specs/2026-05-25-carpark-tier-picker-design.md docs/superpowers/plans/2026-05-25-carpark-tier-picker.md
# Plus bot_config.json only if user said yes to bundling it.
git -C "C:/nas同步_project/菇勇者全自動掛機" commit -m "<message from Step 1>"
```

---

## Self-review notes

- **Spec coverage:** every spec requirement maps to a task — HTML controls (Task 1), default values (Task 3), shallow-merge fix (Task 4), auto-enable cocos flag (Task 4), backend == adb hides controls (Task 1 / Task 5b), 5-step manual test plan (Task 5a–5e).
- **No placeholders:** every Edit shows the actual `old_string`/`new_string`, every verification step shows the exact command and expected output.
- **Type/symbol consistency:** `chkCarparkEnabled`, `editCarparkTier`, `_existingCarpark`, `onCarparkEnabledChange` used consistently across tasks 1–4.
- **Risk surface:** Edit's `old_string` uniqueness — `web_viewport_height` appears twice (Task 4 Step 1 calls this out; Step 3 verifies the right one was changed). `let _lvIp = null;` may appear inside functions too (Task 2 Step 2 calls out using `replace_all: false` + adding surrounding context if needed).

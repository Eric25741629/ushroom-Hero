# 菇勇者 Dashboard — UI Audit Baseline (Phase 1)

> Consolidated, deduplicated, severity-scored UI audit register for the Flask control dashboard
> (`control_panel/`, `templates/*.html`). Grounds the Phase 2 design-system refactor.
>
> **Sources merged:** (A) code-level 5-dimension audit (a11y-semantics, keyboard-focus, responsive,
> contrast-typography, ux-flow-states); (B) live browser audit Round 1 (reachable pages `/`,
> `/fly-pet/login`, `/updates/`; Lighthouse mobile). Same root cause = one row, both citations kept.
>
> **Worktree:** `C:/nas同步_project/菇勇者全自動掛機/.claude/worktrees/design-system`
> **Templates (verified line counts):** dashboard.html 4609 · fly_pet.html 2338 · tools_optimize.html 577 ·
> inventory.html 443 · fly_pet_login.html 108 · readme_viewer.html 91.
> **Date:** 2026-06-21.

---

## 1. Executive Summary

### Severity counts (after dedup)

| Severity | Count | Meaning |
|----------|------:|---------|
| CRITICAL | 15 | Blocks a user (keyboard-trapped, SR can't operate, irreversible action with no guard, data unreachable) |
| HIGH | 22 | Major a11y/state/contrast gap that degrades a primary flow |
| MED | 22 | Maintainability / scan-ability / sub-AA contrast / tap-target |
| LOW | 11 | Polish, decorative, or exempted-but-noted |
| **Total** | **70** | |

Positives are tracked separately in §6 (not counted as defects).
Counts include Round 2 (authenticated data pages, §7): +2 CRITICAL, +4 HIGH, +5 MED net-new rows;
other R2 findings deduped into existing rows (filter-sheet→C1, all-3-no-`<main>`→C4, flypet contrast→C6).

### Top 5 must-fix

1. **Modals are keyboard traps (every page).** No Escape handler, no focus move-in, no focus trap, no focus restore on any of the dashboard's 8 modals, fly_pet's drawer/sheet/confirm, or the shared idle/kick modals in inventory & tools. The inventory **idle modal auto-disconnects after 60s** while a keyboard/SR user may never even receive focus. → `app.js` `openModal/closeModal` manager. (CRITICAL ×5 deduped)
2. **`globalAction()` fires bulk destructive ops with ZERO confirmation.** `dashboard.html:4542-4566` builds the confirm string `msg` but never calls `confirm(msg)` — goes straight to `Promise.all(...fetch)`. One misclick on 全部暫停/全部恢復/全部跳過睡眠 hits the whole fleet. (CRITICAL — verified)
3. **fly_pet login form has no programmatic labels.** `fly_pet_login.html:96-101` — `<label>帳號</label>` with no `for`, `<input name=username>` with no `id`/`aria-label`. Lighthouse `label` audit fails (weight 10) → a11y **77**. SR users cannot fill the only auth gate. (CRITICAL — verified)
4. **Entire fly_pet pet-management surface is mouse-only.** Pet cards (`<article>`), species rows (`<div class=sp-item>`), 全選此種類, 詳情 link (`<span>`), collapse headers — all reached only by delegated click on non-focusable elements. Browse→select→dissolve is keyboard-inoperable. → `keyboardable()` helper + role/tabindex. (CRITICAL ×2 deduped)
5. **inventory & tools have NO loading / empty / error states — and the disconnected state leaks a raw internal error.** Primary data views (`#spGrid`, `#gBody`, `#adGrid`, `#rsCard`, `#tableWrap`) stay blank during fetch and show nothing on zero results; worse (R2), the disconnected state renders the raw internal error verbatim into the page: `連線失敗: no captured creds for 'web-001' at C:\...\auth_state\_auth_capture_web-001.json; run: python tools/adb_token_login.py --device web-001` — leaking the full server path + a dev command, and it IS the entire empty state. → `components.css` `.skeleton/.empty-state/.error-state` + never render server paths (server-side log only). (CRITICAL R2 + HIGH ×2)

> **Round 2 (authenticated data pages) is merged below.** Lighthouse mobile a11y with real content: **/fly-pet 87** (select-name, 8 contrast nodes, landmark), **/inventory 90** (contrast, landmark; BP 96 from a 502), **/tools-optimize 90** (7 contrast nodes, landmark; BP 96 from a 502). [R1: dashboard 93, login 77.] Full detail in §7.

---

## 2. Cross-Cutting Issues (≥2 pages) — Phase 2 library drivers

Each row defines a requirement the shared lib must satisfy *once* so every page inherits it.

| # | Sev | Issue | Single lib mechanism | Pages touched |
|---|-----|-------|----------------------|---------------|
| C1 | CRITICAL | Modals: no Esc, no focus move-in, no focus trap, no focus restore | `app.js` `openModal()/closeModal()` manager (records `activeElement`, moves focus in, traps Tab, document-level Esc closes top-most `.modal-overlay.active`, restores on close) + `components.css` `.modal-overlay/.modal` with `role=dialog aria-modal` | dashboard, fly_pet, inventory, tools_optimize |
| C2 | CRITICAL | No `aria-live` on any status/toast/log/result; actions silent to SR | `app.js` `toast()` with a single `role=status aria-live=polite` region (announcer) | dashboard, fly_pet, inventory, tools_optimize |
| C3 | CRITICAL | Destructive/irreversible/coin-spending actions guarded by native `confirm()` or nothing | `app.js` `confirmDialog({title,body,danger})` styled modal → replaces native confirm + the dead `globalAction` string | dashboard (globalAction), inventory (decompose), tools (carpark/gacha/relic/ad), fly_pet (presetDelete) |
| C4 | HIGH | No landmarks; `landmark-one-main` fails every page; no `<main>`/`<nav aria-label>`/`<header>` | `_assets_head.html` skip-link + per-template `<main>`/`<nav aria-label>` wrappers (Phase 3) | all 6 |
| C5 | HIGH | Tap targets < 44px (top-bar 30px, nav-rail 42px, table th, checkboxes) | `components.css` `.btn/.nav-btn { min-height:44px }` + `@media (pointer:coarse)` enlarged hit area | dashboard, inventory, tools_optimize |
| C6 | HIGH | Bright status hues used as TEXT on white/tint (mint/amber/coral/rose/pastel quality) — all fail 4.5:1, some ~1.3:1. **See §3.2a for the live-measured fly_pet breakdown (32 styles / 6,776 instances; 4 root tokens cover ~5,900).** **R2 confirmed live (8+ nodes):** brand orange `#e0653a` + light-grey text below AA on real content — buttons 載入/啟動自動繁殖/abStartBtn 3.44:1 (white on `#e0653a`), flypet home-link 3.44:1 (13px), tools home-link 3.44:1, counters `#sTotal`/`#sFiltered` 3.22:1, `#speciesCount` 2.77:1, `.nm` name labels 3.08:1, success toast 3.34:1, inventory `.err #e26b86` 3.14:1 + connState badge | `tokens.css` accessible status-text tokens (`--status-ok-text #1d7a59`, `--status-warn-text #8a6210`, `--status-danger-text #b8455f`, `--status-accent-text #c14f2a`); **darken text-orange to ~`#c14f2a`, name/counter text to near-ink; orange reserved for large/bold only**; wired in `components.css`; bright base hues only for borders/fills | inventory, tools_optimize, fly_pet, dashboard (wake-countdown) |
| C7 | HIGH | No `:focus-visible` on fly_pet / inventory / tools / login; `outline:none` strips UA ring | `components.css` universal `:focus-visible { outline:2px solid var(--focus-ring); outline-offset:2px }` (matches dashboard L143) loaded via `_assets_head.html` | fly_pet, inventory, tools_optimize, fly_pet_login |
| C8 | HIGH | Custom clickables non-focusable (`div`/`span` with click delegation): cards, species rows, collapse headers, sortable `th`, task-tab chips, 詳情 links | `app.js` `keyboardable(el, onActivate)` (role+tabindex+Enter/Space) | dashboard (task tabs, program-info), fly_pet (cards/species/collapse/detail), inventory (sortable th) |
| C9 | HIGH | Inconsistent notification systems: `alert()` ~30× (dashboard), shared `setStatus()` span (inventory/tools), `toast()` (fly_pet) | `app.js` single `toast()` + `confirmDialog()`; replace all call sites (keep window globals for inline onclick) | dashboard, inventory, tools_optimize, fly_pet |
| C10 | HIGH | inventory & tools: no loading / empty / error states for data views | `components.css` `.skeleton`, `.empty-state`, `.error-state` (with retry) + `app.js` `setLoading()` | inventory, tools_optimize, (dashboard grid first-paint) |
| C11 | MED | Tab UIs are plain `<div>` chips — no `role=tablist/tab/tabpanel`, no `aria-selected`, no arrow-key roving | `components.css` `.tab-bar` (ARIA tabs) + `app.js` roving-tabindex helper | dashboard (task-settings 11-13 tabs), inventory (spirit/gem tabs) |
| C12 | MED | Fixed-px typography everywhere (10.5/11/12/13px), no rem/clamp → ignores OS/root font-size; several below 12-14px low-vision floor | `tokens.css` rem/clamp type scale (`--text-xs … --text-hero`) migrated in `components.css` | inventory, tools_optimize, fly_pet, dashboard |
| C13 | MED | `--ink-faint #a59a87` (2.4-2.8:1) used for *meaningful* labels, not just placeholders | `tokens.css` `--text-faint` ≥4.5:1 (e.g. `#837866` / reuse `--ink-soft #6f6657` 5.65:1) | dashboard (info labels, badges), inventory, tools |
| C14 | MED | Hairline borders `--line #ece3d3` (1.1-1.3:1) carry meaning (inputs, table separators, tabs) — below 3:1 UI minimum | `tokens.css` `--line-accessible` ≥3:1 for meaningful boundaries; keep `--line` for decorative dividers | all (dashboard, inventory, tools) |
| C15 | MED | Inline 2-col `grid-template-columns:1fr 1fr` inside modals never collapse on mobile | `components.css` `.grid-2 { grid-template-columns:1fr } @media(max-width:480px)` + generic `[style*=1fr 1fr]` fallback | dashboard (config/task modals), fly_pet (breed grid) |
| C16 | MED | `min-width` px-traps on modals/cards (340/320px) cause overflow ≤375px | `components.css` `.modal { width:min(440px, calc(100vw - 24px)); min-width:0 }` + `.info-card` width-driven reset | dashboard, fly_pet |
| C17 | LOW | Mixed breakpoint set (820/768/767/480/390/1023) — inconsistent collapse bands | `tokens.css` `--bp-sm/--bp-md/--bp-lg` single source; align all media queries | dashboard, fly_pet, inventory, tools |
| C18 | LOW | No favicon (404) + 53× "No label associated with a form field" console warning | `_assets_head.html` favicon link + per-input label fixes (Phase 3) | all (favicon), dashboard (warnings) |
| C19 | LOW | Wasteful polling: `/api/status` ~6×/few-sec, `/api/frontend_version` every 3s | `app.js` `startFrontendVersionWatch` (debounce/throttle) + a sensible poll interval | dashboard |
| C20 | CRITICAL | Raw internal error rendered verbatim into the page (info-leak): disconnected data pages dump `連線失敗: no captured creds for 'web-001' at C:\...auth_state\...; run: python tools/adb_token_login.py --device web-001`; `/updates/` dumps `update.txt 讀取失敗: [Errno 2] ... C:\...\update.txt`. Leaks full server path + dev command; IS the entire empty state | `components.css` `.empty-state`/`.error-state` (friendly "尚未連線此裝置，點「連線」開始"); server returns a generic message, real path/command to server log only | inventory, tools_optimize, readme_viewer |
| C21 | HIGH | Off-canvas panes (fly_pet filter sheet, master/detail slide panes) pushed off-screen via `transform:translateX(...)` but stay `display:flex`, no `inert`/`aria-hidden` → their controls (filter sheet = 23) remain tab-reachable and in the a11y tree when "closed" | `app.js` sheet/pane manager sets `inert` + `aria-hidden` and removes from tab order on close; pair with the `openModal/closeModal` Esc/focus manager (C1) | fly_pet |

---

## 3. Per-Page Issue Tables

> `lib-target` columns: **T**=`tokens.css`, **CC**=`components.css`, **JS**=`app.js`, **HEAD**=`_assets_head.html`, **PG**=per-page template edit (Phase 3).
> "live" = browser/Lighthouse Round 1; "code" = static audit. Both cited where they overlap.

### 3.1 dashboard.html (4609 lines)

| Sev | Location (file:line) | Issue | Fix | lib-target |
|-----|----------------------|-------|-----|------------|
| CRITICAL | dashboard.html:1531-2143 (modals); no Escape anywhere (live + code) | 8 modal-overlays (config/taskSettings/ocr/programInfo/bugFeedback/register/liveView) don't respond to Esc; some only close on backdrop; no focus move-in/trap/restore | Route all open*/close* through `openModal/closeModal`; document Esc closes top-most active | JS, CC (C1) |
| CRITICAL | dashboard.html:4542-4566 (verified) | `globalAction()` builds `msg` but never calls `confirm()`; 全部暫停/恢復/跳過睡眠 fire on whole fleet unguarded | `if (!await confirmDialog(msg)) return;` before `Promise.all` | JS (C3) |
| CRITICAL | dashboard.html:2918 (code) | No `aria-live` on status/toast/log/result; silent to SR | `toast()` announcer region | JS (C2) |
| HIGH | dashboard.html:2256-2302 (code) | `fetchStatus()` 1s poll swallows failures (console.error only); grid shows stale cards forever, no offline banner, no first-paint skeleton | Global connection-state banner + skeleton grid on first load | JS, CC (C10) |
| HIGH | dashboard.html:4484-4514, 3123, 3127 (code) | skipSleep/forceSleep/recoverScreen → `callApi()` console.error only; no toast, no button-lock | Route through feedback helper (button-lock + toast), mirror deviceControl | JS (C9) |
| HIGH | dashboard.html:2919,1272,1832 (code) | icon-only buttons no aria-label; tabs no tablist/aria-selected; nav no aria-current | aria-label; `.tab-bar`; `nav[aria-label]`/aria-current | CC, PG (C4,C11) |
| HIGH (live) | top-bar buttons 30px tall; nav-rail icon buttons 42px | Tap targets < 44px (WCAG 2.5.5) | `.btn/.nav-btn { min-height:44px }` | CC (C5) |
| HIGH (live) | task-settings modal ~412px: `.task-tab-chip` row overflows horizontally, no wrap/scroll | 11+ category chips clip | `.tab-bar` scroll/wrap + roving tabs | CC (C11) |
| HIGH | dashboard.html:638 (code) | `.label` info-grid field labels use `--ink-faint #a59a87` 2.77:1 — meaningful text fails 4.5:1 | `--text-faint` ≥4.5:1 | T, CC (C13) |
| HIGH | dashboard.html:3009,3015,3026 (code) | Wake-countdown `.wake-num` 18px bold painted amber `#e0a939` 1.97:1 / mint `#3fb389` 2.43:1 via JS | Set `--safe-color`/`--warn-color` to deep variants (#1d7a59 / #8a6210) | T (C6) |
| HIGH | dashboard.html:1160-1166 (code) | `.war-room-frame` no height cap, no viewport sync → embedded sub-pages force nested horizontal scroll on mobile | `app.js` iframe-fit (postMessage width/height) + `.war-room-shell` mobile padding 0; long-term render same-origin | JS, CC (C16) |
| HIGH | dashboard.html:1841,1853,1972,2016,1584,1636,1701 (code) | Inline `grid-template-columns:1fr 1fr` in config/task modals; only `.checkbox-group[style*=grid]` collapses (L1196), bare grids (1841/1853) not caught | `.grid-2` collapse + generic `[style*=1fr 1fr]` ≤480px | CC (C15) |
| MED | dashboard.html:3730-3741, 3690-3696, 1236-1238 (code) | task-tab chips are `<div>` click-only; not focusable; no roving/arrow-key tablist | `<button role=tab>` + `keyboardable()`; container `role=tablist` | JS, CC, PG (C8,C11) |
| MED | dashboard.html:1287-1525, 2386-2392 (code) | `switchPage` never moves focus into the shown iframe; verify hidden pages use `display:none` not opacity | `frame.focus()` after switch; confirm `display:none` | JS, PG |
| MED | dashboard.html:1268-1283 (code) | No skip-to-main-content link; must tab toggle + 5 nav buttons every page switch | `.skip-link` reveal-on-focus + `tabindex=-1` on page wrapper | HEAD, CC (C4) |
| MED | dashboard.html:665, 300, 321 (code) | `.task-badge`/`.ocr-badge`/`.ocr-runtime` default `#a59a87` 2.57-2.77:1; `.done/.ok` mint-deep-on-wash 4.45:1 (just under) | `--text-faint`; darken mint-deep to ~#176b4e or lighten wash | T, CC (C13) |
| MED (live) | `#ocr-status.ocr-badge.ok` text `#1d7a59` on `#ddf0e8` = 4.45:1 (12px) | sub-AA on the .ok variant specifically | fg ~`#176046` or ≥14px bold | T, CC (C6,C13) |
| MED | dashboard.html:51 (code) | `--line #ece3d3` (1.1:1) / `--line-strong #ddd2bd` (1.3-1.5:1) used as meaningful card/input/table boundaries | `--line-accessible` ≥3:1 for meaningful borders | T, CC (C14) |
| MED | dashboard.html:331-332 (code) | `.program-info { min-width:320px }` px-trap; only reset at 820px | min-width:0 + flex-basis/max-width; width-driven `.info-card` | CC (C16) |
| MED | dashboard.html:1131-1135,1187 (code) | `.labeler-grid` collapses at 820px while rail already top-bar at that band → cramped 768-820px | Align to shared 768px (`--bp-md`) | T, CC (C17) |
| MED | dashboard.html:2449-2493, 3430-3462, 4518-4532 (code) | ~30× native `alert()` (bug feedback, labeler, web login, register, toggleDevice, refresh, saveConfig, OCR) — blocking, inconsistent | `toast()`/`confirmDialog()` | JS (C9) |
| MED | dashboard.html:2910-2943, 3105-3129 (code) | Weak hierarchy: up to 6 equal-weight `.btn` per card; 強制休眠 same weight as routine 跳過睡眠 | `.btn` variants (primary/secondary/ghost/danger); group disruptive under overflow | CC, PG |
| MED | dashboard.html:3769-3782, 3641-3669 (code) | 進階設定 exposes 25+ ws_token fields across 10 tabs as flat same-weight rows; no master toggle, no enabled-count badge, no elevated save | Per-tab master toggle + count badge + primary 套用; form rhythm | CC, PG |
| LOW | dashboard.html:810 (code) | `.btn-locked/.btn:disabled opacity:.5` → ~2:1 (WCAG-exempt) but dimming-only cue | Non-opacity disabled treatment (desaturate + glyph) | CC |
| LOW | dashboard.html:702 (code) | `.log-box::before` 7px decorative dots `#5b5240` on `#221d16` 2.17:1 (decorative; log text 10.3:1 fine) | `aria-hidden`; no content impact | PG |
| LOW (live) | no favicon link | favicon 404 | favicon in head | HEAD (C18) |
| LOW (live) | console: "No label associated with a form field (count: 53)" | unlabeled inputs across config/settings | label/aria-label sweep | PG (C18) |
| LOW (live) | `/api/status` ~6×/few-sec + `/api/frontend_version` every 3s | wasteful polling | debounce/throttle + sane interval | JS (C19) |

### 3.2 fly_pet.html (2338 lines)

| Sev | Location (file:line) | Issue | Fix | lib-target |
|-----|----------------------|-------|-----|------------|
| CRITICAL | fly_pet.html:973-993, 1364-1372 (code) | Pet cards `<article class=card>` no tabindex/role; toggleSel/openDetail only via delegated mousedown/click on `#galleryStage`; 詳情 link `<span>`, 全選此種類 unreachable by keyboard — browse→select→dissolve mouse-only | `tabindex=0 role=button aria-pressed` on card + delegated keydown; 詳情 as `<button>`; `keyboardable()` | JS, PG (C8) |
| CRITICAL | fly_pet.html:1124-1131, 1337-1347 (code) | Species rows `<div class=sp-item>` / `<div class=sp-all>` non-focusable; selectSpecies click-only — main left-pane filter unreachable | Render as `<button>` or role+tabindex+Enter/Space (keep `data-sp`) | JS, PG (C8) |
| HIGH | fly_pet.html:535,666,713 (collapse-hdr); 1283,1294-1302,1306,1413 (detail drawer) (code) | `collapse-hdr` `<div onclick=toggleCollapse>` no role/tabindex/keydown; detail drawer `#galleryDetailScrim` opened from non-focusable cards, no Esc/focus move-in/restore | role=button tabindex=0 aria-expanded + Enter/Space; route drawer through modal manager | JS, CC, PG (C1,C8) |
| HIGH | fly_pet.html:73,299,329 (outline:none) + whole file (no :focus-visible) (code/live) | No designed focus ring; UA ring invisible on warm washi `#faf6ee` | Shared `:focus-visible` ring; remove bare outline:none | CC, HEAD (C7) |
| HIGH | fly_pet.html:1268-1305, 957-993 (code) | **收藏/收進群組 button does not exist.** Pets carry `is_collected` (used to block dissolve at 1623/1656) but no control sets it; drawer only offers 設為基底/A/B, 加入選取, 分解 | Add 收藏/收進群組 action → collect endpoint, toast, lockmark update; filter flag chip (633-637); **Phase 3 named-group + random-pair feature** | PG (+JS toast) |
| CRITICAL | gallery `<img>` icons (R2 live; real 548-pet/36-species render) | **620/620 gallery icons have NO `alt`** → entire core content invisible to screen readers | `alt`=species name per icon | PG |
| HIGH | `<select id=devSel>` (R2 live) | Device select has no label → Lighthouse `select-name` (w10) fails → a11y 87. inventory & tools DO wrap selects in `<label>` — copy that pattern | `<label for>` or `aria-label` | PG (C18) |
| HIGH | search + 篩選種類 + 3 ID number inputs (5 total) (R2 live) | Form inputs labelled by placeholder only — no programmatic label | `id`+`<label for>` (or `aria-label`) on all 5 | PG (C18) |
| HIGH | `aside.fg-sheet` filter sheet (R2 live; supersedes the MED code row below) | ⚙進階 filter sheet: no Esc-close, no focus-in, no `role=dialog`/`aria-modal` (same root as dashboard modals) | Route through shared modal/sheet manager (C1) | JS, CC (C1) |
| HIGH | off-canvas panes: closed `aside.fg-sheet` + master/detail slide panes (R2 live) | Closed sheet pushed off-canvas via `transform:translateX(396px)` but stays `display:flex`, NO `inert`/`aria-hidden` → its 23 controls stay tab-reachable + in a11y tree | `inert`+`aria-hidden`, remove from tab order when closed | JS, CC (C21) |
| HIGH | 載入 / 啟動自動繁殖 / `#abStartBtn` / counters / `.nm` names / success toast (R2 live, user-reported) | Light-on-light: white on brand orange `#e0653a` = 3.44:1; `#sTotal`/`#sFiltered` 3.22:1; `#speciesCount` 2.77:1; `.nm` 3.08:1; success toast 3.34:1 — 8 nodes fail AA | Darken text-orange to ~`#c14f2a`; name/counter text to near-ink; orange for large/bold only | T, CC (C6) |
| MED | view toggle (▤依種類/☰平鋪), 守護靈/神器, 詞條/附魔石 tab UIs (R2 live) | Tab/toggle UIs have no `role=tablist/tab/tabpanel`, no `aria-pressed`/`aria-selected` | `.tab-bar` ARIA roles + roving tabindex | CC, JS (C11) |
| HIGH | whole page (R2 live, dedup→C4, not recounted) | No `<main>` landmark → `landmark-one-main` fails | `<main>` wrapper + skip-link | PG, HEAD (C4) |
| MED | card detail panel (`.detail-open` + breed panel) (R2 live) | Detail panel has NO visible close button and no Escape; only re-clicking the same card closes it | Add `×` close button + Escape (via modal manager) | JS, CC, PG (C1) |
| CRITICAL | fly_pet.html:165,185 (code) | `.ec-2` chip text `--eq2 #ffd54f` on `#fff8e3` = **1.33:1**; `.pq-3 #ffd54f` on `#fff7dc` = **1.32:1** (11px) — invisible | `--chip-text` dark ≥4.5:1 for chip labels; bright hue → border/dot only | T, CC (C6) |
| CRITICAL | fly_pet.html:382,384 (code) | `.star-row .st` star/rank `--fg-amber #e0a939` 11px on white = **2.12:1**; `.lockmark` same | `amber-deep #8a6210` (5.48:1) text token | T, CC (C6) |
| MED | fly_pet.html:511,537,754-762,497-506 (code) | confirm `#modalBg` has Esc→hideModal (good) but no focus move-in on open, no restore, no trap; label-no-for; glyph close no aria-label | Route through modal manager; aria-label glyph; label `for` | JS, CC, PG (C1) |
| ~~MED~~ → see HIGH R2 | fly_pet.html:605-655, 1385-1392, 1409 (code) | Filter bottom-sheet `#gallerySheet`/`aside.fg-sheet` no focus-in/Esc/trap; batch bar `#galleryBatchbar` at DOM end, focus doesn't flow to it. **Upgraded to HIGH by R2 live row above (sheet + off-canvas tab-reachability).** Batch-bar focus-flow detail kept here | Route sheet through sheet/scrim manager; move/route focus to batch bar | JS, CC (C1) |
| MED | fly_pet.html:164 (code) | `.ec-1/.ec-3/.pq-1` blue `#64b5f6` on blue tint ~2.0:1; `.pq-2` purple similar | `--chip-text` dark foreground | T, CC (C6) |
| MED | fly_pet.html:385 (code) | `.fightmark` white on red `#ef5350` 11px bold = 3.49:1 | `--status-danger-fill` deeper red (#d32f2f ~5.0:1) | T, CC (C6) |
| MED | fly_pet.html:378 + many (310,315,327,345,382,385,389-393,406,456,966) (code) | `.mbadge` 10.5px + many 11/11.5px fixed labels below low-vision floor, no scaling | rem/clamp type scale, 13px floor | T, CC (C12) |
| MED | fly_pet.html:1787-1799,1801-1807,1768-1775,1756-1766,2006-2014 (code) | Mixed confirm paradigm: doSell styled modal; doBreed modal (non-destructive); doCollect/doHatch/doShelve toast-only; presetDelete native `confirm()` | Destructive→`confirmDialog`; benign→optimistic+toast+undo | JS (C3,C9) |
| MED | fly_pet.html:236, 209,138, 469-486 (code) | `.modal { min-width:340px }` ~17px margin at 375px; breed/partner/legacy `.tbl-wrap min-width:1120px` outside responsive gallery scope | `width:min(440px, calc(100vw-24px)); min-width:0`; bring blocks under shared `.modal/.table-scroll/.grid-auto` | CC (C16) |
| MED | fly_pet.html:495,511,537 (code) | No aria-live on toast; label-no-for; span onclick no role | `toast()` aria-live; aria-label/role sweep | JS, PG (C2) |
| LOW | fly_pet.html:218-222 (code) | `.toast-box` no max-width → long Chinese toasts exceed viewport; batch bar overlaps iOS home indicator | `.toast { max-width:calc(100vw-32px) }`; safe-area-inset-bottom on batch bar | CC |
| LOW | fly_pet.html:102-108, 698-707 (code) | `.preset-toggle` / auto-breed preset rows risk non-focusable-div pattern | Audit runtime rows; real `<button>` or `keyboardable()`; focus ring | JS, PG (C7,C8) |
| LOW | fly_pet.html:2149-2168,2170-2181, 2135 (code) | Auto-breed run state subtle; per-slot preset editable mid-run with no warning | Running badge/pulse; disable per-slot edits or toast "applies next tick" | CC, PG |
| LOW | fly_pet.html:1741-1749,1732-1738,1746,1735 (code) | Raw `JSON.stringify(eg)` + `_raw` key=value debug dumped in user-facing breed panel | Formatted labeled fields or hide behind debug toggle | PG |

#### 3.2a fly_pet 淡字 — exact low-contrast text offenders + token fixes (USER-REPORTED, live-measured)

> The user's specific pain ("飛寵頁淡底配淡字，看不清楚"). Live audit on **/fly-pet desktop, 548 pets**:
> **32 distinct low-contrast text styles across 6,776 instances.** This is the same root as cross-cutting **C6**,
> but the fix here is **root-cause token edits** (tokens.css + fly_pet Phase 3), NOT per-element patching.
> The 4 root-cause fixes below cover **~5,900 of 6,776 instances**. Evidence: `flypet_lowcontrast_text.png`.

**Root-cause token fixes (do these 4 — they resolve ~87% of instances):**

| # | Root cause | Measured | Fix (token) | Covers |
|---|-----------|----------|-------------|--------|
| 1 | Entry-tag palette `.ec-1..7` — saturated PASTEL text on same-hue pale chip (the #1 offender) | 1.2–2.9:1 | Darken each `.ec` TEXT to a ≥4.5 shade of its hue, keep pale chip bg (or move hue to border/dot). Suggested: blue `#1d6fb8`, yellow `#8a6d00`, teal `#0f766e`, red `#b91c1c`, purple `#6d28d9`, pink `#a21caf`. → tokens `--chip-text` + per-quality accessible text values | ~1,749 chip instances |
| 2 | Muted token `#a59a87` on white — ALL secondary/meta (`.sub`/`.gcount`/`.sh-count`/`.detail-link`/`.sel-pill`/`.fg-glabel`/search icon) | 2.77:1 | → `#6f6657` (≈4.9:1; already used & passing on the login page). → tokens `--text-faint` / `--color-ink-soft` | (large meta set) |
| 3 | Brand orange `#e06539` as TEXT (`.mbadge.lv` ×548, `.mbadge.gen`, `.nm` species name, `.gsel-all`, count `<b>`) | 3.2–3.45:1 | Text → `#b4471f` (≥4.5); keep `#e06539` only for large/bold or white-on-fill. → tokens `--status-accent-text` | (×548+ per element) |
| 4 | Empty star `.st.off #ddd2bd` | 1.45:1 | → ~`#b59f78` + outline so empty vs filled distinguishable | ×2,718 |

> **Token-value note:** §2 C6 / §5 use `#c14f2a` for the accent-text per the R2 brief; this subsection records the more
> precise live-derived set (orange `#b4471f`, grey `#6f6657`, the 6 per-quality hues, star `#b59f78`) and the
> per-tag breakdown. Both are AA-passing; **reconcile to one canonical set in tokens.css at Phase 2** (flagged to lead).

**Worst elements (selector | sample | color → bg | ratio | px/wt | count):**

| Selector | Sample | Color → bg | Ratio | px/wt | Count |
|----------|--------|-----------|------:|-------|------:|
| `.ec-7` | 輕裝上陣 | `#5eead4` → `#d8efed` | **1.23** | 11/700 | ×91 |
| `.ec-2` | — | `#ffd54f` → `#fff8e3` | **1.33** | — | ×463 |
| `.ec-6` | — | `#f0abfc` → `#f9ddfe` | **1.41** | — | ×9 |
| `.st.off` | empty star | `#ddd2bd` → `#fdfbf7` | **1.45** | — | ×2,718 |
| `.ec-3` | — | `#ffe08a` → (tint) | **1.65** | — | ×186 |
| `.ec-5` | — | `#b9a4ff` → `#e8e1f4` | **1.68** | — | ×102 |
| `.ec-1` | — | `#64b5f6` → `#ebf5fe` | **2.01** | — | ×856 |
| `.sel-pill` | — | `#a59a87` → white | **2.68** | — | ×548 |
| `.detail-link` | — | `#a59a87` → white | **2.68** | — | ×548 |
| `.ec-4` | — | `#ef5350` → `#fce3e3` | **2.87** | — | ×42 |
| `.nm` | species name | `#e06539` → `#fcf0eb` | **3.08** | — | — |
| `.mbadge.lv` | level | `#e06539` → `#faf6ee` | **3.20** | — | ×548 |
| `.gsel-all` | 全選此種類 | `#e06539` | **3.45** | — | ×36 |
| `.mbadge.gen` | gen | `#c14f2a` → `#faf6ee` | **4.41** (just under) | — | ×548 |

> **Orange BUTTONS** (載入 / 交配 / 分解 / active toggles): white text on the orange GRADIENT computes **3.45–4.03:1**
> — borderline large/bold, **lower priority than the pastel chips**. Fix in the same accent-fill pass, not first.

### 3.3 inventory.html (443 lines)

| Sev | Location (file:line) | Issue | Fix | lib-target |
|-----|----------------------|-------|-----|------------|
| CRITICAL | inventory.html:168-188, 383-384 (code) | idleModal/kickModal `showModal/hideModal` toggle class only — no Esc, no focus move-in, no trap, no restore; **idle auto-disconnects after 60s** while user may never get focus | Route through modal manager; Esc = safe default (繼續使用 / 知道了); focus first button on open | JS, CC (C1) |
| CRITICAL | inventory.html:343-362 (code) | `doDecompose()` (賣神器附魔石) irreversible, guarded only by native `confirm()`; no detail of which/how many gems | `confirmDialog({danger,body:count+quality})` styled modal | JS (C3) |
| CRITICAL | disconnected-state render (R2 live; web-001 was disconnected) | Disconnected/empty state dumps the RAW internal error into the page: `連線失敗: no captured creds for 'web-001' at C:\...auth_state\_auth_capture_web-001.json; run: python tools/adb_token_login.py --device web-001` — leaks full server path + dev command, and IS the entire empty state | Styled empty/disconnected card ("尚未連線此裝置，點「連線」開始"); raw path/command to server log only | CC, PG (C20) |
| HIGH | inventory.html:213-256,258-270,129,143-165 (code) | No loading/empty/error states for `#spGrid` / `#gBody`; blank during fetch, blank on zero, error only in `#status` span | `.skeleton` during load; `.empty-state` on zero; inline `.error-state`+retry on fail | CC, JS (C10) |
| HIGH | inventory.html:386-395, 440 (code) | No first-load/empty/offline device state; init() auto-connects; empty `web_h5` select silent; load buttons clickable → opaque failure | Friendly empty state + disable 讀取 buttons while `connState !== 'on'`; inline error+retry | CC, JS (C10) |
| HIGH | inventory.html:11-89 (no :focus-visible), 64,156-159 (sortable th) (code) | No `:focus-visible`; sortable `<th onclick=gemSort()>` not focusable, no role/keydown — column sort keyboard-inoperable | Shared `:focus-visible`; `th` → role=button tabindex=0 aria-sort + Enter/Space | CC, JS (C7,C8) |
| HIGH | inventory.html:55,69,88 (code) | `.lockbadge`/`.note`/`.cd` amber `#e0a939` text+border 1.97-2.12:1 (text/border fail) | `--status-warn-text #8a6210` (5.48:1) | T, CC (C6) |
| HIGH | inventory.html:54,78 (code) | `.lvl` mint `#3fb389` 2.43:1; `.conn.on` mint text+border 2.43/2.62:1 | `--status-ok-text #1d7a59` (5.28:1); mint only as fill | T, CC (C6) |
| HIGH | inventory.html:74,70,79 (code) | `.eqbadge` coral `#e0653a` 3.45:1 text; `.err`/`.conn.off` rose `#e26b86` 3.14/2.92:1 | coral-deep `#c14f2a` / rose-deep `#b8455f` text tokens | T, CC (C6) |
| CRITICAL | inventory.html:236, 63,65,71,145-152 (code) | Gem action toolbar (標記前N + 等級< + 4 buttons) is flex-wrap that overflows 375px; nowrap header; decompose action hard to reach without panning | `.toolbar--stack` (column ≤480px); sticky first column in `.tablewrap`; `var(--text-sm)` | CC, T (C12) |
| MED | inventory.html:62-64 (code) | Sortable `th` (6px 8px) + checkbox cells under 44×44 tap target | `th.sortable` min-height 44px; enlarged checkbox hit area `@media(pointer:coarse)` | CC (C5) |
| MED | inventory.html:307,322-324,150,343-362 (code) | 分解 button not disabled during in-flight POST → double-click fires twice (checkboxes correctly disable for protected gems) | Disable button + spinner for POST duration, re-enable in finally | JS (C3) |
| MED | inventory.html:2 (verified) | `<html lang="zh-Hant">` mismatches the zh-TW used by 4 other templates | Standardize lang | PG, HEAD |
| MED | inventory.html:155 (code) + R2 live | 10 `<th>` none with `scope`, table has no `<caption>`; sort not a button | scope=col + `<caption>` + aria-sort + role=button | PG (C8) |
| MED | connState 未連線 badge + buttons + `.err #e26b86` (R2 live) | connState badge + buttons fail contrast; `.err #e26b86` on white = 3.14:1; a 502 console error on auto-load against the disconnected device (Best-Practices 96) | Contrast → deep tokens (C6); handle 502 → empty state, no console error (C20) | T, CC, JS (C6,C20) |
| HIGH | whole page (R2 live, dedup→C4, not recounted) | No `<main>` landmark → `landmark-one-main` fails | `<main>` wrapper + skip-link | PG, HEAD (C4) |
| LOW | inventory.html:19-20,32-58 (code) | Global px type (`body 14px`, many 12-13px) — no rem, no zoom scaling | tokens type scale (Phase 2) | T (C12) |
| LOW | inventory.html:113-127,134-142,364 (code) | Hidden affordance: tab needs 讀取 click first, no prompt in empty panel → looks broken | Idle prompt ('連線後按「讀取守護靈」'); consider auto-load on activation | CC, PG (C10) |

### 3.4 tools_optimize.html (577 lines)

| Sev | Location (file:line) | Issue | Fix | lib-target |
|-----|----------------------|-------|-----|------------|
| CRITICAL | tools_optimize.html:210-230, 407-408 (code) | idle/kick `showModal/hideModal` class-toggle only — no Esc/focus-in/trap/restore (same pattern as inventory) | Route through modal manager; Esc → safe default; focus first button | JS, CC (C1) |
| CRITICAL | disconnected-state render (R2 live; web-001 disconnected) | Same raw-error info-leak as inventory: disconnected/empty state dumps `連線失敗: no captured creds for 'web-001' at C:\...; run: python tools/adb_token_login.py ...` — full path + dev command, IS the entire empty state | Styled empty/disconnected card; raw path/command to server log only | CC, PG (C20) |
| HIGH | tools_optimize.html:178-180,194-207,463-472 (code) | `#adGrid`/`#rsCard`/`#tableWrap` start hidden, no idle prompt; failures only in note span; carpark ~30s job no skeleton/progress | Idle prompt + loading skeleton per tool + inline error+retry | CC, JS (C10) |
| HIGH | tools_optimize.html:11-80 (no :focus-visible) (code/live) | No `:focus-visible`; action buttons/selects rely on low-visibility UA ring on `#f4efe6/#faf6ee` | Shared `:focus-visible` ring | CC, HEAD (C7) |
| HIGH | tools_optimize.html:39,38,69,62,54-55 (code) | Systemic status-text fail: `.good` mint 2.62:1, `.err` rose 3.14:1, `.canget .rs` amber 2.12:1, maxed mint 2.62:1, conn on/off | Route all status text through `*-deep` tokens | T, CC (C6) |
| HIGH | tools_optimize.html:31, 27,40,119-126,43,46 (code) | `input[type=number]{width:130px}` fixed; control rows + KPI flex (gap:22px) overflow 375px; 8-col plan table nowrap | `input{width:100%;max-width:130px}`; `.kpi` 2-col grid ≤480px; `var(--text-sm)` | CC, T (C12,C16) |
| MED | tools_optimize.html:320,368-372,486,554 (code) | Destructive/coin-spending guarded by native `confirm()`: doExecute (菇車幣), doDraw (抽卡券), doAdClaim, doRsExec (遺物碎片) | `confirmDialog` showing concrete cost + danger primary | JS (C3) |
| MED | tools_optimize.html:47-49 (code) | `.log`/`.kpi`/plan table px fonts; wide scroll region, no scroll affordance on mobile | `.table-scroll::after` fade; `var(--text-xs/--text-sm)` | CC, T (C12) |
| MED | tools_optimize.html:2 (verified) | `<html lang="zh-Hant">` mismatch | Standardize lang | PG, HEAD |
| MED | tools_optimize.html:202 (code) | progress bar no `role=progressbar` | `role=progressbar` | PG |
| MED | home link (R2 live) | 中控面板 home link `#e0653a` on white = 3.44:1 (13px) — fails AA (same orange/red root as C6) | Darken text-orange to ~`#c14f2a` | T, CC (C6) |
| MED | auto-load against disconnected device (R2 live) | 502 console error on auto-load while disconnected (Best-Practices 96) | Handle 502 → empty state, no console error | JS (C20) |
| LOW | tools_optimize.html:36 (code) | `button:disabled opacity:.5` dimming-only (exempt) | non-opacity disabled cue | CC |
| LOW | tools_optimize.html:17,44,46,53,58 (code) | Global px type, no rem | tokens type scale | T (C12) |
| LOW | tools_optimize.html:248 (code) | '沒有 web_h5 裝置' only in status span (better than inventory but still not a panel state) | panel-level empty state | CC (C10) |

### 3.5 fly_pet_login.html (108 lines)

| Sev | Location (file:line) | Issue | Fix | lib-target |
|-----|----------------------|-------|-----|------------|
| CRITICAL | fly_pet_login.html:96-101 (verified live) | Inputs have NO programmatic label: `<label>帳號</label>` no `for`, `<input name=username>` no `id`/`aria-label`. Lighthouse `label` (weight 10) fails → a11y **77**. SR can't fill form | `id` + `<label for>` + `autocomplete=username` / `current-password` | PG |
| MED (live) | fly_pet_login.html:103 + submit style | Submit white on `#e0653a` = 3.45:1 (~15px) fails AA | bg ~`#c14f2a` (≈4.5:1) or 18px/700 | T, CC (C6) |
| LOW | fly_pet_login.html:52, 64-75 (code) | `outline:none` on inputs (replaced by border+shadow, ok) but submit + 返回 link no `:focus-visible` | Shared `:focus-visible` ring | CC, HEAD (C7) |
| LOW (live) | fly_pet_login.html:97,101 | inputs missing `autocomplete` | add autocomplete attrs | PG |
| HIGH | fly_pet_login.html (whole) (live C4) | No `<main>`/landmark; `landmark-one-main` fails | `<main>` wrapper + skip-link | PG, HEAD (C4) |

> Note: `/fly-pet`, `/inventory`, `/tools-optimize` all redirect to this login when unauthenticated → their Lighthouse mobile a11y = **77** (this page's label+contrast+landmark fails). Authenticated runs are the Round 2 scope (§7).

### 3.6 readme_viewer.html (91 lines) — `/updates/`

| Sev | Location (file:line) | Issue | Fix | lib-target |
|-----|----------------------|-------|-----|------------|
| MED (live) | `/updates/` render: `update.txt 讀取失敗: [Errno 2] ... C:\...\design-system\update.txt` | Raw OS error leaks full server path; no friendly empty state (info-leak + UX) | Catch read error → friendly empty/error state, never echo absolute path | PG (CC `.empty-state`) |
| MED (live) | readme_viewer.html theme | Dark-navy theme is an outlier off the washi/tatami palette (visual inconsistency) | Re-skin to washi tokens (or keep deliberate dark, but use `tokens.css` dark scope) | T, PG |
| LOW | readme_viewer.html:16-55 (code) | Uses rem/em (good) + pre-wrap/break-word (good); but `body padding:24px` fixed eats 13% at 375px; long unbroken tokens trigger inner overflow-x | `padding: clamp(12px,4vw,24px)` shared spacing token | T, PG (C17) |

---

## 4. Phase 2 — Shared Library Requirements (checklist)

The lib must ship these capabilities so Phase 3 is mostly class-swaps.

### `static/lib/tokens.css`
- [ ] Washi palette single source + back-compat aliases for current var names (`--coral-rim`, `--warn-color`, `--safe-color`, `--accent/--accent2`, `--ink-faint`, `--line/--line-strong`) so existing inline JS/CSS keeps resolving. (C6,C13,C14)
- [ ] WCAG-AA status-text tokens: `--status-ok-text #1d7a59`, `--status-warn-text #8a6210`, `--status-danger-text #b8455f`, `--status-accent-text` (`#c14f2a` per R2 brief / `#b4471f` per §3.2a live — **pick ONE canonical, both AA**); status-fill tokens (deeper red `#d32f2f`); `--chip-text` (dark, ≥4.5:1 on any quality tint). (C6, §3.2a)
- [ ] Per-quality accessible entry-tag text values (the §3.2a `.ec-1..7` root fix, ~1,749 instances): blue `#1d6fb8`, yellow `#8a6d00`, teal `#0f766e`, red `#b91c1c`, purple `#6d28d9`, pink `#a21caf`; empty-star `.st.off` → `#b59f78`+outline (×2,718). (§3.2a)
- [ ] `--text-faint` ≥4.5:1 (e.g. `#837866` / reuse `--ink-soft`); reserve `#a59a87` for decorative only. (C13)
- [ ] `--line-accessible` ≥3:1 for meaningful borders (inputs/controls/table separators); keep `--line` decorative. (C14)
- [ ] rem/clamp type scale `--text-xs … --text-hero` (13px floor) replacing fixed px. (C12)
- [ ] `--focus-ring`, `--scrim`, `--shadow-*`. (C1,C7)
- [ ] Single breakpoint set `--bp-sm/--bp-md/--bp-lg` (retire 820/767/480/390/1023 mix). (C17)

### `static/lib/components.css`
- [ ] `.btn` (+variants primary/secondary/ghost/danger, all `min-height:44px`); `.nav-btn` ≥44px. (C5)
- [ ] `.modal-overlay` / `.modal` (+`.modal--confirm` danger) with `role=dialog aria-modal` styling; `.sheet`/`.scrim`. (C1)
- [ ] `.data-table` + `.table-scroll` (horizontal scroll + sticky first column + fade affordance) + `.toolbar--stack`. (C12,inventory)
- [ ] `.chip`/`.badge` (chip text from `--chip-text`, hue on border/dot only). (C6)
- [ ] `.card`/`.surface`; `.status-pill`; `.tab-bar` (ARIA tablist visual). (C11)
- [ ] `.toast` (max-width-capped, safe-area aware); `.spinner`; `.skeleton`; `.empty-state`; `.error-state` (with retry). (C10,C2)
- [ ] `.empty-state`/`.disconnected-card` must accept a *friendly* message — never render a raw server path/command (the disconnected data pages currently dump `auth_state\...json` + a `python tools/...` command). (C20)
- [ ] `.form-control`/`.field-inline`; `.grid-2` (collapses ≤480px) + generic inline-grid collapse. (C15)
- [ ] Universal `:focus-visible { outline:2px solid var(--focus-ring); outline-offset:2px }`. (C7)
- [ ] `.skip-link` (reveal-on-focus). (C4)
- [ ] `@media (prefers-reduced-motion: reduce)` block (port dashboard's, apply lib-wide). (positive to preserve)
- [ ] `@media (pointer:coarse)` enlarged hit areas for small controls. (C5)

### `static/lib/app.js` (keep `window` globals for inline `onclick`)
- [ ] `apiGet`/`apiPost` (centralize fetch + error handling). (C9)
- [ ] `toast()` backed by one `role=status aria-live=polite` announcer. (C2,C9)
- [ ] `openModal()/closeModal()` manager: record activeElement → move focus in → trap Tab → document-level Esc closes top-most active → restore focus. (C1)
- [ ] `confirmDialog({title,body,danger})` styled confirm (replaces native confirm + dead globalAction string). (C3)
- [ ] `setLoading()` / `setStatus()` / `renderLog()`. (C10)
- [ ] `keyboardable(el, onActivate)` (role+tabindex+Enter/Space) for custom clickables + roving-tabindex tab helper. (C8,C11)
- [ ] Sheet/off-canvas manager: on close set `inert` + `aria-hidden` and remove the pane from tab order (fly_pet filter sheet = 23 controls stay reachable when "closed"); shares the Esc/focus logic with `openModal/closeModal`. (C21,C1)
- [ ] `esc`, `$`, `debounce`/`throttle`, `pollJob`. (C19)
- [ ] `loadWebDevices`, `createWsSession`, `startFrontendVersionWatch`, `applyEmbedClass`. (shared infra / C19)

### `templates/_assets_head.html` (include in all 6)
- [ ] Fonts + lib links with `?v={{ frontend_version }}` cache-bust.
- [ ] `<html lang>` standardized (zh-TW) — fixes inventory/tools `zh-Hant` mismatch.
- [ ] Skip-link markup + favicon link (fixes 404). (C18)

### Backend (server-side, not pure CSS/JS — flagged here so Phase 3 doesn't miss it)
- [ ] The disconnected/read-error responses that the data pages + `/updates/` render must return a generic user-facing message; the real OS error / server path / `python tools/...` command goes to the server log only. The lib's `.empty-state`/`.error-state` is the *display*; the *string* has to be sanitized at the route. (C20)

---

## 5. Phase 3 — Per-Page Fix List (ordered, beyond class-swaps)

1. **dashboard.html** — (a) `globalAction`: insert `confirmDialog` guard (the verified dead-`msg` bug); (b) wire all 8 open*/close* through `openModal/closeModal`; (c) route skipSleep/forceSleep/recoverScreen + ~30 `alert()` sites through `toast()`; (d) `fetchStatus` offline banner + first-paint skeleton; (e) task-tab chips → `role=tab` + `keyboardable`, container `role=tablist`; (f) `<main>`/`<nav aria-label>`/skip-link wrappers; (g) iframe-fit for `.war-room-frame`; (h) `.grid-2` on inline modal grids (incl. bare 1841/1853); (i) per-tab master toggle + enabled-count badges in 進階設定; (j) button hierarchy on device action bar; (k) label sweep (53 console warnings); aria-label on icon buttons; aria-hidden the 7px log dots.
2. **fly_pet.html** — (a) cards/species/collapse → focusable + `keyboardable`; (b) drawer/sheet/confirm through modal manager **+ off-canvas sheet/panes get `inert`+`aria-hidden` when closed (R2: 23 controls stay tab-reachable)**; (c) **add 收藏/收進群組 action** (links to Phase 3 named-group + random-pair feature) + collect filter chip; (d) chip text → `--chip-text`, star/lock amber → deep token, **orange text → `#c14f2a` + names/counters → near-ink (R2: 8 nodes)**; (e) standardize confirm paradigm (destructive→`confirmDialog`, benign→optimistic+toast); (f) `.modal` width clamp; pull breed/partner/legacy table under shared `.modal/.table-scroll`; (g) toast max-width + safe-area; (h) replace raw JSON/`_raw` dumps with labeled fields; (i) `<main>`/landmark; **(j) R2: `alt`=species-name on all 620 gallery icons; (k) R2: `<label for>`/`aria-label` on devSel + search + 篩選 + 3 ID inputs; (l) R2: view-toggle/守護靈-神器/詞條-附魔石 → ARIA tab roles; (m) R2: card detail panel gets a visible `×` close + Escape.**
3. **inventory.html** — (a) idle/kick through modal manager (Esc=safe default; note 60s auto-disconnect); (b) `doDecompose` → `confirmDialog` with count/quality + disable-during-POST; (c) loading/empty/error states for `#spGrid`/`#gBody` + device empty/offline state + disable 讀取 when not connected **+ R2: replace the raw-error disconnected dump with a styled disconnected card (sanitize string server-side); handle the 502 → empty state, no console error**; (d) sortable `th` → role=button + aria-sort + keydown, scope=col **+ `<caption>` (R2)**; (e) status text → deep tokens (incl. connState badge + `.err` R2); (f) `.toolbar--stack` + sticky-first-column table; (g) `lang` → zh-TW; idle prompt in empty panels.
4. **tools_optimize.html** — (a) idle/kick through modal manager; (b) idle prompt + loading skeleton + inline error per tool (`#adGrid`/`#rsCard`/`#tableWrap`) **+ R2: styled disconnected card replacing the raw-error dump (sanitize server-side); handle 502 → empty state, no console error**; (c) destructive `confirm()` (carpark/gacha/ad/relic) → `confirmDialog` with cost; (d) status text → deep tokens **+ home link orange → `#c14f2a` (R2)**; (e) `input` width + `.kpi` grid + table scroll affordance; (f) `role=progressbar`; `lang` → zh-TW.
5. **fly_pet_login.html** — (a) `id`+`<label for>`+`autocomplete` on both inputs (the a11y-77 root cause); (b) submit bg → `#c14f2a` (AA); (c) `<main>`/landmark + skip-link; (d) `:focus-visible` on submit + back-link.
6. **readme_viewer.html** — (a) catch `update.txt` read error → friendly empty/error state, never echo the absolute server path (info-leak); (b) re-skin to washi tokens (or scope a deliberate dark theme via tokens); (c) `body padding: clamp(12px,4vw,24px)`.

---

## 6. Positives to Preserve

- Responsive breakpoints **820/768/390** work: side-rail collapses to top bar; device cards stack to 1 col ≤768px. (preserve behavior, just unify the breakpoint values via tokens.)
- A `@media (prefers-reduced-motion: reduce)` block exists (dashboard L1212) — port it lib-wide, don't drop it.
- Global `*:focus-visible { outline }` visible ring on dashboard (L143, L214, L346, L765) — generalize to all pages, don't regress dashboard.
- Logical DOM tab order; **no positive `tabindex`** anywhere (good).
- **No horizontal page overflow** on dashboard / login / updates at tested widths.
- Status is generally **not color-alone** (text labels + emoji accompany dots) — the contrast problem is the colored *text*, not missing redundancy. Keep the redundant cues.
- dashboard uses real `<button>`/`<a>` for almost all controls (tab-reachable) and has a correctly instrumented `role=button tabindex=0` + Enter/Space on the program-info card (L1303/L2436) — pattern to replicate, not replace.
- Lighthouse mobile: dashboard a11y **93**, Best-Practices **100**, SEO **90** (login-redirect pages 77 is the login page's fault, fixed by §3.5).
- readme_viewer already honors zoom (rem/em + pre-wrap/break-word) — only needs spacing token.
- **R2:** No horizontal page overflow on any of /fly-pet, /inventory, /tools-optimize — off-canvas panes clip cleanly via `transform` (the off-canvas tab-reachability is the a11y bug C21, but the layout itself is sound).
- **R2:** inventory & tools wrap **all** their selects + inputs in `<label>` — this is the pattern fly_pet should copy (fly_pet's devSel + 5 form inputs are the only unlabeled ones).
- **R2:** visible focus ring on all controls + logical tab order on /fly-pet; mobile master-detail slide pattern works.

## 6b. Residual / Known-Risk

- **Hardcoded dashboard credentials** `infinite` / `infiniteroot` at `control_panel/shared/auth.py:6` (`_FLY_PET_USERS = {"infinite": "infiniteroot"}`). **Security note only — DO NOT change as part of the design-system refactor.** Recorded for awareness; any rotation is a separate, owner-approved task.
- **iframe architecture risk:** inventory/tools/fly_pet render inside `.war-room-frame` iframes. The lib's tokens/components only apply if each iframe page also includes `_assets_head.html`. True fix (same-origin non-iframe rendering) is larger scope than Phase 2/3 — the iframe-fit JS helper is the interim mitigation. (C16)
- **Back-compat var aliasing required:** dashboard sets colors via inline JS reading `--warn-color`/`--safe-color` (wake-countdown L3009+) and via `style*="grid"` selectors. tokens.css MUST keep these names resolving or the migration silently breaks live JS. (C6,C15)
- **Round-1 raster caveat:** the test Chrome had a ~500px CSS-width floor, so true 375px could not be rastered; CSS ≤390 rules exist in source and were read statically, but visual 375px verification is deferred.
- **Code-audit line numbers** were spot-verified (login labels, globalAction, focus-visible, lang, auth) and matched; the remaining citations are from the static audit and should be re-confirmed at edit time (templates are large and actively changing — dashboard 4609 lines).

---

## 7. Round 2 — Authenticated Data Pages (MERGED)

Authenticated live audit complete. All R2 findings are integrated above: the per-page tables (§3.2 fly_pet, §3.3 inventory, §3.4 tools — rows tagged "(R2 live)"), the cross-cutting drivers (C6 contrast specifics, C20 raw-error leak, C21 off-canvas inert), the §4 lib checklist, the §5 per-page edits, and the §6 positives. This section is the consolidated R2 summary.

**Page states observed:** /fly-pet rendered REAL cached data (548 pets / 36 species). /inventory + /tools-optimize were in the DISCONNECTED state (device `web-001` had no captured creds).

**Lighthouse mobile a11y (real content):**

| Page | a11y | Best-Practices | Failing audits |
|------|-----:|---------------:|----------------|
| /fly-pet | 87 | — | select-name (devSel), color-contrast (8 nodes), landmark-one-main |
| /inventory | 90 | 96 | color-contrast, landmark-one-main; BP 96 = 502 console error (auto-load vs disconnected device) |
| /tools-optimize | 90 | 96 | color-contrast (7 nodes), landmark-one-main; BP 96 = 502 console error |
| (R1) dashboard | 93 | 100 | — |
| (R1) /fly-pet/login | 77 | — | label, color-contrast, landmark |

### 7.1 /fly-pet (real 548-pet render)
- CRITICAL: 620/620 gallery `<img>` icons have no `alt` → core content invisible to SR. → §3.2 row + §5(2j).
- HIGH: devSel `<select>` no label (select-name w10); search + 篩選 + 3 ID inputs (5) no labels. → §3.2 + §5(2k).
- HIGH: ⚙進階 filter sheet (`aside.fg-sheet`) no Esc/focus-in/`role=dialog`/`aria-modal` (→C1); closed sheet stays `display:flex` off-canvas with no `inert`/`aria-hidden`, 23 controls tab-reachable (→C21). → §5(2b).
- HIGH (user-reported, light-on-light): 8 nodes fail — buttons 載入/啟動自動繁殖/`#abStartBtn` 3.44:1, counters 3.22:1, `#speciesCount` 2.77:1, `.nm` 3.08:1, success toast 3.34:1; root = brand orange `#e0653a` + light-grey text (→C6). **Full 32-style / 6,776-instance breakdown + 4 root-cause token fixes in §3.2a.** → §5(2d).
- MED: view-toggle / 守護靈-神器 / 詞條-附魔石 are not ARIA tabs (→C11); card detail panel has no visible close + no Escape (→C1). → §5(2l,2m).

### 7.2 /inventory (disconnected)
- CRITICAL: disconnected state dumps the raw internal error (auth_state path + `python tools/adb_token_login.py` command) as the entire empty state (→C20). → §3.3 + §5(3c).
- MED: 10 `<th>` no `scope`, no `<caption>`; connState badge + `.err #e26b86` 3.14:1 contrast; 502 console error on auto-load (BP 96). → §3.3 + §5(3c,3d,3e).
- HIGH: no `<main>` (→C4).

### 7.3 /tools-optimize (disconnected)
- CRITICAL: same raw-error info-leak as inventory (→C20). → §3.4 + §5(4b).
- MED: home link `#e0653a` 3.44:1 (→C6); 502 console error on auto-load (BP 96). → §3.4 + §5(4d).
- HIGH: no `<main>` (→C4); 7 contrast nodes (→C6).

**Screenshots on record:** flypet_desktop/tablet/mobile.png, flypet_filter_sheet.png, flypet_detail_drawer.png, inventory_*.png, tools_*.png.

**Residual after R2:** still not visually rastered at true 320px (only down to the prior ~375/390 band); inventory + tools data-grid loading/empty/error states could not be observed in the *connected* state (device was disconnected) — their connected-state behavior is inferred from code (§3.3/§3.4 code rows) and should be re-verified once a device has captured creds.

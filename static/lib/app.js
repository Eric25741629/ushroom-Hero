/*!
 * app.js — shared vanilla-JS helpers for the 菇勇者 dashboard (Phase 2 design system).
 *
 * Companion of static/lib/tokens.css + static/lib/components.css. No framework, no build step.
 * Loaded ALONGSIDE each page's existing inline <script> (Phase 3 removes the inline copies).
 *
 * BACK-COMPAT CONTRACT (do not break the running pages):
 *  - Every export lives on a single `window.UI` namespace.
 *  - We ALSO mirror exports onto `window.<fn>` so inline `onclick="toast(...)"` keeps working,
 *    BUT only via setGlobal(), which NEVER overwrites a name an inline page script already owns
 *    (dashboard has its own `closeModal()`, inventory/tools have their own `showModal`/`$`/etc.).
 *    Inline page code therefore always wins until Phase 3 deletes it; the lib only fills the gaps.
 *  - On load we ONLY: (a) inject one aria-live announcer, (b) install ONE document Escape handler,
 *    (c) applyEmbedClass(). We never auto-bind to or mutate existing page widgets.
 *  - Loading this file on a page that doesn't use it is harmless.
 *
 * BREAKPOINTS: CSS @media cannot read custom properties, so the literal set 480 / 768 / 1024 is
 * used in components.css. JS that needs to branch on width uses the same literals (see BP below).
 */
(function (root) {
  'use strict';

  // Single, consistent breakpoint set (mirrors components.css @media literals): 480 / 768 / 1024.
  var BP = { sm: 480, md: 768, lg: 1024 };

  // ---------------------------------------------------------------------------
  // DOM micro-helpers
  // ---------------------------------------------------------------------------

  /** Escape a string for safe insertion as text into innerHTML. */
  function esc(s) {
    if (s === null || s === undefined) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  /** getElementById. */
  function $(id) {
    return document.getElementById(id);
  }

  /** querySelectorAll -> real Array (optionally scoped). */
  function $$(sel, ctx) {
    return Array.prototype.slice.call((ctx || document).querySelectorAll(sel));
  }

  /** Resolve an element from either an element or an id string. */
  function resolveEl(elOrId) {
    if (!elOrId) return null;
    return typeof elOrId === 'string' ? $(elOrId) : elOrId;
  }

  /** Trailing-debounce: fire fn once after `ms` of quiet. */
  function debounce(fn, ms) {
    var t = null;
    ms = ms || 200;
    return function () {
      var ctx = this, args = arguments;
      if (t) clearTimeout(t);
      t = setTimeout(function () { t = null; fn.apply(ctx, args); }, ms);
    };
  }

  /** Leading-throttle: fire fn at most once per `ms`. */
  function throttle(fn, ms) {
    var last = 0, t = null;
    ms = ms || 200;
    return function () {
      var ctx = this, args = arguments, now = Date.now(), wait = ms - (now - last);
      if (wait <= 0) {
        if (t) { clearTimeout(t); t = null; }
        last = now;
        fn.apply(ctx, args);
      } else if (!t) {
        t = setTimeout(function () {
          last = Date.now(); t = null; fn.apply(ctx, args);
        }, wait);
      }
    };
  }

  // ---------------------------------------------------------------------------
  // API helpers — fetch + JSON + {status:'ok'} envelope + 401 -> login redirect
  // (lifted from fly_pet.html:1441/1449)
  // ---------------------------------------------------------------------------

  var LOGIN_URL = '/fly-pet/login';

  async function apiGet(url) {
    var r = await fetch(url);
    if (r.status === 401) { root.location.href = LOGIN_URL; throw new Error('unauthorized'); }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    if (d.status !== 'ok') throw new Error(d.message || 'error');
    return d;
  }

  async function apiPost(url, body) {
    var r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (r.status === 401) { root.location.href = LOGIN_URL; throw new Error('unauthorized'); }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    if (d.status !== 'ok') throw new Error(d.message || 'error');
    return d;
  }

  // ---------------------------------------------------------------------------
  // Toast — one injected role=status aria-live=polite announcer (C2)
  // (markup lifted from fly_pet.html:745; classes match components.css .toast/.toast-*)
  // ---------------------------------------------------------------------------

  var ANNOUNCER_ID = 'ui-toast-region';
  var TOAST_TTL_MS = 3500;

  /** Create (once) and return the shared aria-live toast region. */
  function ensureAnnouncer() {
    var el = $(ANNOUNCER_ID);
    if (el) return el;
    el = document.createElement('div');
    el.id = ANNOUNCER_ID;
    el.className = 'toast-region';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.setAttribute('aria-atomic', 'true');
    // If the document body isn't ready yet, defer until it is.
    if (document.body) document.body.appendChild(el);
    else document.addEventListener('DOMContentLoaded', function () {
      if (!$(ANNOUNCER_ID) && document.body) document.body.appendChild(el);
    });
    return el;
  }

  /**
   * Show a transient toast that is also announced to screen readers.
   * type: 'info' | 'ok' | 'err' (aliases: 'success'->ok, 'error'/'danger'->err, 'warn').
   */
  function toast(msg, type) {
    type = type || 'info';
    if (type === 'success') type = 'ok';
    if (type === 'error' || type === 'danger') type = 'err';
    var region = ensureAnnouncer();
    var el = document.createElement('div');
    el.className = 'toast toast--' + type;
    el.textContent = msg;
    region.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, TOAST_TTL_MS);
    return el;
  }

  // ---------------------------------------------------------------------------
  // Modal manager (C1) — focus move-in + trap + Esc-closes-topmost + focus restore.
  // Supports both class conventions: 'active' (dashboard) and 'show' (inventory/tools/fly_pet).
  // Maintains a stack so nested modals close top-most-first and restore focus correctly.
  // ---------------------------------------------------------------------------

  var FOCUSABLE_SEL = [
    'a[href]', 'button:not([disabled])', 'textarea:not([disabled])',
    'input:not([disabled]):not([type="hidden"])', 'select:not([disabled])',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  // Stack of { el, cls, trigger } for currently-open overlays (top = last).
  var modalStack = [];

  function visibleFocusable(container) {
    return $$(FOCUSABLE_SEL, container).filter(function (n) {
      return n.offsetWidth > 0 || n.offsetHeight > 0 || n.getClientRects().length > 0;
    });
  }

  function focusFirst(overlay) {
    var f = visibleFocusable(overlay);
    if (f.length) { f[0].focus(); return; }
    // No focusable child: make the dialog itself focusable and focus it.
    if (!overlay.hasAttribute('tabindex')) overlay.setAttribute('tabindex', '-1');
    overlay.focus();
  }

  /** Trap Tab within the top-most overlay. Called from the global keydown handler. */
  function trapTab(e, overlay) {
    if (e.key !== 'Tab') return;
    var f = visibleFocusable(overlay);
    if (!f.length) { e.preventDefault(); overlay.focus(); return; }
    var first = f[0], last = f[f.length - 1], active = document.activeElement;
    if (e.shiftKey) {
      if (active === first || !overlay.contains(active)) { e.preventDefault(); last.focus(); }
    } else {
      if (active === last || !overlay.contains(active)) { e.preventDefault(); first.focus(); }
    }
  }

  /**
   * Open a modal overlay by id (or element).
   * opts.cls: overlay "open" class ('active' default for dashboard; pass 'show' for inv/tools).
   */
  function openModal(idOrEl, opts) {
    var overlay = resolveEl(idOrEl);
    if (!overlay) return null;
    opts = opts || {};
    var cls = opts.cls || 'active';
    var trigger = document.activeElement;
    overlay.classList.add(cls);
    if (overlay.getAttribute('role') !== 'dialog') overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.removeAttribute('aria-hidden');
    modalStack.push({ el: overlay, cls: cls, trigger: trigger });
    // Defer focus so layout/transition settles before measuring focusables.
    setTimeout(function () { focusFirst(overlay); }, 0);
    return overlay;
  }

  /** Close a specific modal (or, if omitted, the top-most open one) and restore focus. */
  function closeModal(idOrEl, opts) {
    var entry;
    if (idOrEl === undefined || idOrEl === null) {
      entry = modalStack.pop();
    } else {
      var overlay = resolveEl(idOrEl);
      var idx = -1;
      for (var i = modalStack.length - 1; i >= 0; i--) {
        if (modalStack[i].el === overlay) { idx = i; break; }
      }
      if (idx >= 0) entry = modalStack.splice(idx, 1)[0];
      else entry = { el: overlay, cls: (opts && opts.cls) || 'active', trigger: null };
    }
    if (!entry || !entry.el) return;
    // Remove whichever known open-class is present so callers don't have to pass it again.
    entry.el.classList.remove(entry.cls);
    entry.el.classList.remove('active');
    entry.el.classList.remove('show');
    entry.el.setAttribute('aria-hidden', 'true');
    entry.el.removeAttribute('aria-modal');
    // Restore focus to the element that had it when this modal opened.
    if (entry.trigger && typeof entry.trigger.focus === 'function' && document.contains(entry.trigger)) {
      try { entry.trigger.focus(); } catch (e) { /* ignore */ }
    }
  }

  /** Close the top-most open overlay (used by Esc handler). */
  function closeTopModal() {
    if (!modalStack.length) return false;
    closeModal();
    return true;
  }

  // ---------------------------------------------------------------------------
  // confirmDialog (C3) — styled async replacement for native confirm().
  // Builds a self-contained .modal-overlay > .modal.modal--confirm, routes through the
  // modal manager (focus/trap/Esc/restore), resolves a Promise<boolean>.
  // ---------------------------------------------------------------------------

  function confirmDialog(opts) {
    opts = opts || {};
    var title = opts.title || '請確認';
    var bodyText = opts.body || '';
    var danger = !!opts.danger;
    var okLabel = opts.okLabel || (danger ? '確定' : '確定');
    var cancelLabel = opts.cancelLabel || '取消';

    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'modal-overlay';

      var modal = document.createElement('div');
      modal.className = 'modal modal--confirm' + (danger ? ' modal--danger' : '');

      var h = document.createElement('div');
      h.className = 'modal-title';
      h.textContent = title;

      var b = document.createElement('div');
      b.className = 'modal-body';
      b.textContent = bodyText;

      var actions = document.createElement('div');
      actions.className = 'modal-row';

      var cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'btn btn--secondary';
      cancelBtn.textContent = cancelLabel;

      var okBtn = document.createElement('button');
      okBtn.type = 'button';
      okBtn.className = 'btn ' + (danger ? 'btn--danger' : 'btn--primary');
      okBtn.textContent = okLabel;

      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);
      modal.appendChild(h);
      if (bodyText) modal.appendChild(b);
      modal.appendChild(actions);
      overlay.appendChild(modal);
      overlay.setAttribute('aria-labelledby', '');
      (document.body || document.documentElement).appendChild(overlay);

      var settled = false;
      function finish(val) {
        if (settled) return;
        settled = true;
        closeModal(overlay);
        // The overlay's transition (if any) is short; remove on next tick.
        setTimeout(function () { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }, 0);
        resolve(val);
      }
      cancelBtn.addEventListener('click', function () { finish(false); });
      okBtn.addEventListener('click', function () { finish(true); });
      // Backdrop click = cancel.
      overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) finish(false); });
      // Mark so the global Esc handler resolves us as cancel when it closes this overlay.
      overlay._uiConfirmCancel = function () { finish(false); };

      openModal(overlay, { cls: 'active' });
    });
  }

  // ---------------------------------------------------------------------------
  // Loading / status / log helpers
  // ---------------------------------------------------------------------------

  /** Toggle a button into a spinning, disabled state and back (lifted from fly_pet.html:765). */
  function setLoading(btn, on) {
    btn = resolveEl(btn);
    if (!btn) return;
    if (on) {
      btn.disabled = true;
      if (btn._uiPrevHTML === undefined) btn._uiPrevHTML = btn.innerHTML;
      btn.setAttribute('aria-busy', 'true');
      btn.innerHTML = '<span class="spinner" aria-hidden="true"></span> ...';
    } else {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
      if (btn._uiPrevHTML !== undefined) { btn.innerHTML = btn._uiPrevHTML; btn._uiPrevHTML = undefined; }
    }
  }

  /** Set a status element's text + error styling (generalizes inventory/tools setStatus). */
  function setStatus(elOrId, text, isErr) {
    var el = resolveEl(elOrId);
    if (!el) return;
    el.textContent = text || '';
    el.className = isErr ? 'err' : 'spin';
  }

  /** Render an array of log lines into a container, escaping each line. */
  function renderLog(elOrId, lines) {
    var el = resolveEl(elOrId);
    if (!el) return;
    el.style.display = 'block';
    el.innerHTML = (lines || []).map(function (l) { return '<div>' + esc(l) + '</div>'; }).join('');
    el.scrollTop = el.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // keyboardable (C8) — make a custom clickable (div/span) keyboard-operable.
  // ---------------------------------------------------------------------------

  /**
   * Add role=button (when not a native button), tabindex=0, and Enter/Space activation.
   * Idempotent: tags the element so re-applying does not double-bind.
   */
  function keyboardable(el, onActivate) {
    el = resolveEl(el);
    if (!el || el._uiKeyboardable) return el;
    var tag = (el.tagName || '').toLowerCase();
    var isNativeBtn = tag === 'button' || (tag === 'input' && /^(button|submit|reset)$/i.test(el.type || ''));
    if (!isNativeBtn) {
      if (!el.hasAttribute('role')) el.setAttribute('role', 'button');
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    }
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        if (typeof onActivate === 'function') onActivate.call(el, e);
      }
    });
    el._uiKeyboardable = true;
    return el;
  }

  /** Delegated variant: apply keyboardable to all current matches of a selector. */
  function applyKeyboardable(selector, onActivate) {
    $$(selector).forEach(function (el) { keyboardable(el, onActivate); });
  }

  // ---------------------------------------------------------------------------
  // pollJob — generic job poller (generalizes tools_optimize pollJob to any job URL)
  // ---------------------------------------------------------------------------

  /**
   * Poll a job endpoint until it leaves the 'running' state.
   * @param {function(): string} jobUrlFn  returns the URL to poll each tick.
   * @param {function(object)} onUpdate     called with job each tick.
   * @param {object} [o]  { intervalMs=1200 }
   * @returns {Promise<object|null>} the terminal job, or null on transport/envelope error.
   */
  async function pollJob(jobUrlFn, onUpdate, o) {
    o = o || {};
    var intervalMs = o.intervalMs || 1200;
    while (true) {
      await new Promise(function (r) { setTimeout(r, intervalMs); });
      var j;
      try {
        var url = typeof jobUrlFn === 'function' ? jobUrlFn() : jobUrlFn;
        var r = await fetch(url);
        j = await r.json();
      } catch (e) {
        return null;
      }
      if (j.status !== 'ok') return null;
      var job = j.job;
      if (onUpdate) onUpdate(job);
      if (!job || job.status !== 'running') return job;
    }
  }

  // ---------------------------------------------------------------------------
  // loadWebDevices — fill a <select> with web_h5 devices (generalizes inventory/tools loadDevices)
  // ---------------------------------------------------------------------------

  /**
   * Populate a device <select> with web_h5 bots from /api/status.
   * @returns {Promise<boolean>} true if at least one device was added.
   */
  async function loadWebDevices(selectEl, o) {
    o = o || {};
    var sel = resolveEl(selectEl);
    if (!sel) return false;
    var def = o['default'] || 'emulator-5554';
    try {
      var r = await fetch('/api/status');
      var j = await r.json();
      var bots = j.bots || {};
      sel.innerHTML = '';
      Object.keys(bots).forEach(function (ip) {
        var b = bots[ip];
        if (b.backend !== 'web_h5') return;
        var opt = document.createElement('option');
        opt.value = ip;
        opt.textContent = (b.name || ip) + ' (' + ip + ')';
        sel.appendChild(opt);
      });
      if (Array.prototype.some.call(sel.options, function (op) { return op.value === def; })) {
        sel.value = def;
      }
      return sel.options.length > 0;
    } catch (e) {
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // createWsSession — pure-WS persistent session controller
  // (lifted from inventory.html:373-438 + ensureConnected from tools_optimize)
  // ---------------------------------------------------------------------------

  /**
   * Build a self-contained WS-session controller (connect / keepalive / idle-logout / kick).
   * Caller wires its own button/status via the callbacks; the controller owns the timers.
   *
   * opts:
   *   getDev()        -> current device id string (required)
   *   onStatus(state) -> 'on' | 'connecting' | 'off'  (update connState/btn)
   *   onConn(bool)    -> optional: connected/disconnected transitions
   *   idleSec         -> idle threshold (default 1800; ?idle=<sec> query override wins)
   *   keepaliveMs     -> heartbeat interval (default 30000)
   *   kickModalId     -> id of the "kicked elsewhere" modal (opened via modal manager on kick)
   *   idleModalId     -> id of the idle-countdown modal
   *   idleCdId        -> id of the element showing the idle countdown number (default 'idleCd')
   *   modalCls        -> open-class for those modals (default 'show')
   *
   * Returns { connect, disconnect, toggle, onDevChange, ensureConnected, get dev }.
   */
  function createWsSession(opts) {
    opts = opts || {};
    var getDev = opts.getDev || function () { return ''; };
    var onStatus = opts.onStatus || function () {};
    var onConn = opts.onConn || function () {};
    var idleSec = parseInt(new URLSearchParams(root.location.search).get('idle'), 10) || opts.idleSec || 1800;
    var keepaliveMs = opts.keepaliveMs || 30000;
    var kickModalId = opts.kickModalId || null;
    var idleModalId = opts.idleModalId || null;
    var idleCdId = opts.idleCdId || 'idleCd';
    var modalCls = opts.modalCls || 'show';

    var connectedDev = null, keepaliveTimer = null, idleTimer = null, idleCdTimer = null;
    var IDLE_CD_SEC = 60, ACT_THROTTLE_MS = 5000, lastAct = 0;

    function setState(s) {
      onStatus(s);
      if (s === 'on') onConn(true);
      else if (s === 'off') onConn(false);
    }

    async function connect() {
      var d = getDev();
      if (!d) { setState('off'); return false; }
      setState('connecting');
      try {
        var r = await fetch('/api/ws_session/' + encodeURIComponent(d) + '/connect', { method: 'POST' });
        var j = await r.json();
        if (j.status === 'ok') {
          connectedDev = d; setState('on'); startKeepalive(); resetIdle();
          return true;
        }
        setState('off'); return false;
      } catch (e) { setState('off'); return false; }
    }

    async function disconnect() {
      stopKeepalive(); clearIdle();
      var d = connectedDev || getDev();
      connectedDev = null; setState('off');
      try { await fetch('/api/ws_session/' + encodeURIComponent(d) + '/disconnect', { method: 'POST' }); }
      catch (e) { /* ignore */ }
    }

    function toggle() { return connectedDev ? disconnect() : connect(); }

    async function onDevChange() {
      if (connectedDev && connectedDev !== getDev()) await disconnect();
      return connect();
    }

    /** Connect only if not already connected; returns whether we end up connected. */
    async function ensureConnected() {
      if (connectedDev) return true;
      return connect();
    }

    function startKeepalive() { stopKeepalive(); keepaliveTimer = setInterval(pingOnce, keepaliveMs); }
    function stopKeepalive() { if (keepaliveTimer) { clearInterval(keepaliveTimer); keepaliveTimer = null; } }

    async function pingOnce() {
      var d = connectedDev;
      if (!d) return;
      try {
        var r = await fetch('/api/ws_session/' + encodeURIComponent(d) + '/ping', { method: 'POST' });
        var j = await r.json();
        if (j.kicked) onKicked();
        else if (!j.alive) { stopKeepalive(); connectedDev = null; setState('off'); }
      } catch (e) { /* ignore */ }
    }

    function onKicked() {
      stopKeepalive(); clearIdle(); connectedDev = null; setState('off');
      if (kickModalId && $(kickModalId)) openModal(kickModalId, { cls: modalCls });
    }

    function resetIdle() {
      clearIdle();
      if (!connectedDev) return;
      idleTimer = setTimeout(beginIdleCountdown, idleSec * 1000);
    }
    function clearIdle() {
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (idleCdTimer) { clearInterval(idleCdTimer); idleCdTimer = null; }
    }
    function beginIdleCountdown() {
      var left = IDLE_CD_SEC;
      var cd = $(idleCdId);
      if (cd) cd.textContent = left;
      if (idleModalId && $(idleModalId)) openModal(idleModalId, { cls: modalCls });
      idleCdTimer = setInterval(function () {
        left--;
        var c = $(idleCdId);
        if (c) c.textContent = left;
        if (left <= 0) { clearInterval(idleCdTimer); idleCdTimer = null; idleLogoutNow(); }
      }, 1000);
    }
    function idleLogoutNow() {
      if (idleModalId && $(idleModalId)) closeModal(idleModalId, { cls: modalCls });
      disconnect();
    }

    // User activity resets the idle timer (but NOT during the final countdown); throttled.
    function onActivity() {
      if (idleCdTimer) return;
      var now = Date.now();
      if (now - lastAct > ACT_THROTTLE_MS) { lastAct = now; if (connectedDev) resetIdle(); }
    }
    ['mousemove', 'keydown', 'click', 'scroll'].forEach(function (ev) {
      root.addEventListener(ev, onActivity, { passive: true });
    });
    // Release the connection promptly on unload (sweeper is the 90s safety net).
    root.addEventListener('beforeunload', function () {
      if (connectedDev) navigator.sendBeacon('/api/ws_session/' + encodeURIComponent(connectedDev) + '/disconnect');
    });

    return {
      connect: connect,
      disconnect: disconnect,
      toggle: toggle,
      onDevChange: onDevChange,
      ensureConnected: ensureConnected,
      get dev() { return connectedDev; }
    };
  }

  // ---------------------------------------------------------------------------
  // startFrontendVersionWatch (C19) — reload page when the frontend asset version changes
  // (lifted from readme_viewer.html:66-89; default interval relaxed 3s -> 4s)
  // ---------------------------------------------------------------------------

  var _fvTimer = null;

  function startFrontendVersionWatch(ms) {
    ms = ms || 4000;
    if (_fvTimer) return _fvTimer;
    // Seed from the meta tag _assets_head.html emits, falling back to first fetch.
    var meta = document.querySelector('meta[name="frontend-version"]');
    var current = meta ? (meta.getAttribute('content') || '') : '';

    async function check() {
      try {
        var r = await fetch('/api/frontend_version?_=' + Date.now(), { cache: 'no-store' });
        var d = await r.json();
        var next = d.version || '';
        if (!current) { current = next; return; }
        if (next && next !== current) root.location.reload();
      } catch (e) { /* network blip; try again next tick */ }
    }
    _fvTimer = setInterval(check, ms);
    check();
    return _fvTimer;
  }

  // ---------------------------------------------------------------------------
  // applyEmbedClass — tag <html> when this document is rendered inside an iframe
  // so components.css can drop chrome (the dashboard embeds these pages as iframes).
  // ---------------------------------------------------------------------------

  function isEmbedded() {
    try { return root.self !== root.top; } catch (e) { return true; /* cross-origin = embedded */ }
  }

  function applyEmbedClass() {
    var html = document.documentElement;
    if (isEmbedded()) html.classList.add('is-embedded');
    else html.classList.add('is-standalone');
    return isEmbedded();
  }

  // ---------------------------------------------------------------------------
  // Defensive boot: ONLY (a) announcer, (b) document Esc handler, (c) applyEmbedClass.
  // ---------------------------------------------------------------------------

  function installEscHandler() {
    if (root._uiEscInstalled) return;
    root._uiEscInstalled = true;
    document.addEventListener('keydown', function (e) {
      if (modalStack.length) {
        var top = modalStack[modalStack.length - 1];
        if (e.key === 'Tab') { trapTab(e, top.el); return; }
        if (e.key === 'Escape') {
          e.stopPropagation();
          var el = top.el;
          closeTopModal();
          // confirmDialog overlays resolve(false) on Esc-close.
          if (el && typeof el._uiConfirmCancel === 'function') el._uiConfirmCancel();
        }
      }
    }, true);
  }

  function boot() {
    ensureAnnouncer();
    installEscHandler();
    applyEmbedClass();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // ---------------------------------------------------------------------------
  // Namespace + guarded window globals (inline page code always wins; we only fill gaps).
  // ---------------------------------------------------------------------------

  var UI = {
    BP: BP,
    esc: esc, $: $, $$: $$, debounce: debounce, throttle: throttle,
    apiGet: apiGet, apiPost: apiPost,
    toast: toast, ensureAnnouncer: ensureAnnouncer,
    openModal: openModal, closeModal: closeModal, closeTopModal: closeTopModal,
    confirmDialog: confirmDialog,
    setLoading: setLoading, setStatus: setStatus, renderLog: renderLog,
    keyboardable: keyboardable, applyKeyboardable: applyKeyboardable,
    pollJob: pollJob,
    loadWebDevices: loadWebDevices, createWsSession: createWsSession,
    startFrontendVersionWatch: startFrontendVersionWatch,
    isEmbedded: isEmbedded, applyEmbedClass: applyEmbedClass
  };
  root.UI = UI;

  /** Assign window.<name> ONLY if the page hasn't already defined that global (back-compat). */
  function setGlobal(name, fn) {
    if (typeof root[name] === 'function') return; // inline page script owns it; don't clobber.
    root[name] = fn;
  }

  // New, non-colliding names: safe to expose for inline onclick handlers.
  // (Name-colliding ones — closeModal/showModal/hideModal/toast/$/setStatus/apiGet/apiPost/
  //  setLoading/esc/pollJob/loadDevices — are only filled when the page lacks its own.)
  [
    'apiGet', 'apiPost', 'toast', 'openModal', 'closeModal', 'closeTopModal',
    'confirmDialog', 'setLoading', 'setStatus', 'renderLog',
    'keyboardable', 'applyKeyboardable', 'esc', '$', '$$',
    'debounce', 'throttle', 'pollJob',
    'loadWebDevices', 'createWsSession', 'startFrontendVersionWatch',
    'isEmbedded', 'applyEmbedClass'
  ].forEach(function (name) { setGlobal(name, UI[name]); });

})(typeof window !== 'undefined' ? window : this);

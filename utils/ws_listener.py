"""In-process WebSocket frame listener for the web_h5 game client.

Hooks `netManager._cnet.sendMessage` / `reciveMsg` from inside the page so
every WS frame's `(cmd_id, direction, ts, len)` is captured into a JS-side
ring buffer. The Python side drains the buffer through `page.evaluate(...)`
and embeds recent frames into `ActionTraceRecorder` events, giving each
bot action an automatic mapping to the cmds it triggered.

Why in-process (not external CDP attach):
- Single source of truth — runs in the same page Playwright already owns.
- No second connection, no cross-process lock contention.
- Each device thread owns its own Playwright session, so drains are
  thread-local sync calls.

Frame schema (one entry per WS message):
    {"cmd": int, "dir": "tx"|"rx", "ts": int (epoch ms), "len": int}

The listener does NOT capture body bytes by default — only cmd + length —
to keep the JS-side ring small. For schema work, `WSFrameTracker` can opt
into a per-cmd capture budget (`AUTO_CAPTURE_CMDS`); bodies hit `tmp_ws_capture/auto/<device>/`
during normal bot runs and stop once each cmd has `samples_per_cmd`
samples on disk. No manual dump tool needed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


_BUF_MAX = 500


# Idempotent installer. Runs in the page; polls netManager until ready,
# then monkey-patches sendMessage / reciveMsg to push into a ring buffer.
_INSTALL_JS = r"""
() => {
  if (window.__bot_ws_installed) return 'already';
  const tryInstall = () => {
    if (window.__bot_ws_installed) return true;
    const nm = window.netManager;
    if (!nm || !nm._cnet) return false;
    const sock = nm._cnet;
    if (sock.__bot_ws_hooked) { window.__bot_ws_installed = true; return true; }
    if (typeof sock.sendMessage !== 'function' || typeof sock.reciveMsg !== 'function') return false;

    if (!window.__bot_ws_buf) window.__bot_ws_buf = [];
    const BUF_MAX = __BUF_MAX__;

    const lenOf = (b) => {
      if (!b) return 0;
      if (typeof b.byteLength === 'number') return b.byteLength;
      if (typeof b.length === 'number') return b.length;
      return 0;
    };

    const maybeCapture = (cmd, dir_, body) => {
      // Body capture is opt-in per cmd_id. The Python side calls
      // start_body_capture(cmds) which populates window.__bot_body_cfg.
      // Each cmd gets a (max_per_cmd, max_bytes) budget, decremented as
      // we capture; once exhausted further calls just record meta only.
      const cfg = window.__bot_body_cfg;
      if (!cfg) return;
      const slot = cfg[cmd];
      if (!slot || slot.remaining <= 0) return;
      if (!body) return;
      let bytes;
      try {
        if (body instanceof Uint8Array) bytes = body;
        else if (body.buffer) bytes = new Uint8Array(body.buffer, body.byteOffset || 0, body.byteLength);
        else if (Array.isArray(body)) bytes = Uint8Array.from(body);
        else return;
      } catch (e) { return; }
      const cap = Math.min(bytes.length, slot.max_bytes || 65536);
      const arr = new Array(cap);
      for (let i = 0; i < cap; i++) arr[i] = bytes[i];
      window.__bot_body_store.push({
        cmd: cmd | 0, dir: dir_, ts: Date.now(),
        full_len: bytes.length, body: arr,
      });
      slot.remaining -= 1;
    };

    const origSend = sock.sendMessage.bind(sock);
    sock.sendMessage = function (cmd, body) {
      try {
        const buf = window.__bot_ws_buf;
        buf.push({cmd: cmd | 0, dir: 'tx', ts: Date.now(), len: lenOf(body)});
        if (buf.length > BUF_MAX) buf.splice(0, buf.length - BUF_MAX);
        maybeCapture(cmd, 'tx', body);
      } catch (e) {}
      return origSend(cmd, body);
    };

    const origRecv = sock.reciveMsg.bind(sock);
    sock.reciveMsg = function (cmd, body) {
      try {
        const buf = window.__bot_ws_buf;
        buf.push({cmd: cmd | 0, dir: 'rx', ts: Date.now(), len: lenOf(body)});
        if (buf.length > BUF_MAX) buf.splice(0, buf.length - BUF_MAX);
        maybeCapture(cmd, 'rx', body);
      } catch (e) {}
      return origRecv(cmd, body);
    };

    sock.__bot_ws_hooked = true;
    window.__bot_ws_installed = true;
    return true;
  };

  if (tryInstall()) return 'installed';
  if (!window.__bot_ws_install_timer) {
    window.__bot_ws_install_timer = setInterval(() => {
      if (tryInstall()) {
        clearInterval(window.__bot_ws_install_timer);
        window.__bot_ws_install_timer = null;
      }
    }, 200);
  }
  return 'pending';
}
""".replace("__BUF_MAX__", str(_BUF_MAX))


# Configures per-cmd body capture budgets. Pass {} to disable.
_SET_BODY_CAPTURE_JS = r"""
([cmds, maxPerCmd, maxBytes]) => {
  if (!Array.isArray(cmds) || cmds.length === 0) {
    window.__bot_body_cfg = null;
    window.__bot_body_store = window.__bot_body_store || [];
    return 'cleared';
  }
  if (!window.__bot_body_store) window.__bot_body_store = [];
  const cfg = {};
  for (const c of cmds) {
    cfg[c | 0] = { remaining: maxPerCmd | 0, max_bytes: maxBytes | 0 };
  }
  window.__bot_body_cfg = cfg;
  return 'configured';
}
"""


# Drains the body store (returns and clears). Bodies are int arrays.
_DRAIN_BODIES_JS = r"""
() => {
  const store = window.__bot_body_store || [];
  window.__bot_body_store = [];
  return store;
}
"""


# Returns frames whose ts > sinceMs, capped at maxN (oldest dropped).
_DRAIN_JS = r"""
([sinceMs, maxN]) => {
  const buf = window.__bot_ws_buf || [];
  if (!buf.length) return [];
  const out = [];
  for (let i = 0; i < buf.length; i++) {
    const e = buf[i];
    if (e && e.ts > sinceMs) out.push(e);
  }
  if (maxN > 0 && out.length > maxN) {
    return out.slice(out.length - maxN);
  }
  return out;
}
"""


def install_ws_listener(page: Any) -> str:
    """Install (or re-confirm) the WS hook in `page`. Idempotent.

    Returns the status string from JS: 'installed', 'already', or 'pending'
    (netManager not ready yet — the JS schedules a polling retry).
    Raises Exception on Playwright eval failure (e.g. page closed).
    """
    return str(page.evaluate(_INSTALL_JS))


def drain_ws_frames(
    page: Any,
    since_ms: int,
    max_n: int = 50,
) -> List[Dict[str, Any]]:
    """Drain frames captured since `since_ms` (epoch ms in the page's clock).

    Returns at most `max_n` frames, newest preserved when capping. If the
    listener wasn't installed, returns []. Raises on page eval failure.
    """
    raw = page.evaluate(_DRAIN_JS, [int(since_ms), int(max_n)])
    if not raw:
        return []
    out: List[Dict[str, Any]] = []
    for e in raw:
        try:
            out.append(
                {
                    "cmd": int(e.get("cmd", 0)),
                    "dir": str(e.get("dir", "")),
                    "ts": int(e.get("ts", 0)),
                    "len": int(e.get("len", 0)),
                }
            )
        except Exception:
            continue
    return out


def now_ms() -> int:
    """Wall-clock ms — matches JS `Date.now()` for cross-side comparison."""
    return int(time.time() * 1000)


def set_body_capture(
    page: Any,
    cmd_ids: List[int],
    max_per_cmd: int = 5,
    max_bytes: int = 65536,
) -> str:
    """Tell the page to start (or stop) capturing full body bytes for these cmds.

    Pass an empty list to disable. Up to `max_per_cmd` frames per cmd_id will
    be retained (per direction is NOT distinguished — tx and rx share the
    budget); each frame's body is truncated at `max_bytes`.

    Returns 'configured' or 'cleared'.
    """
    return str(page.evaluate(
        _SET_BODY_CAPTURE_JS,
        [list(int(c) for c in cmd_ids), int(max_per_cmd), int(max_bytes)],
    ))


def drain_captured_bodies(page: Any) -> List[Dict[str, Any]]:
    """Return (and clear) all captured body frames.

    Each entry: `{"cmd": int, "dir": "tx"|"rx", "ts": int_ms,
                  "full_len": int, "body": bytes}`.
    `full_len` is the original length (so you can detect truncation);
    `body` is the actual captured bytes (≤ max_bytes from start_body_capture).
    """
    raw = page.evaluate(_DRAIN_BODIES_JS) or []
    out: List[Dict[str, Any]] = []
    for e in raw:
        try:
            body_arr = e.get("body") or []
            out.append({
                "cmd": int(e.get("cmd", 0)),
                "dir": str(e.get("dir", "")),
                "ts": int(e.get("ts", 0)),
                "full_len": int(e.get("full_len", 0)),
                "body": bytes(body_arr),
            })
        except Exception:
            continue
    return out


# High-value cmds whose request/response bodies are worth capturing during
# normal bot runs. Sourced from `tmp_ws_capture/ANALYSIS_20260509.md`:
#   神燈     — 0x0509 (RPC), 0x0304 (push), 0x0504 (push), 0x051f (RPC)
#   挖礦     — 0x0c03 (RPC), 0x0c01 (RPC), 0x0c21 (push), 0x0c07 (push),
#              0x0c11 (RPC, suspect mine inventory), 0x0c08 (RPC),
#              0x0c05/0x0c06 (push)
#   道具     — 0x0402 (push, 跨任務), 0x0302 (push, 玩家狀態)
#   好友     — 0x0f02 (sanity)
#   登入/全域 — 0x4707, 0x4501, 0x4504, 0x4202, 0x4215, 0x4216
#              (login-time bulk state syncs; 0x4202 can be 50KB+ so
#              capped via max_body_bytes default of 64KB)
# Capture is time-rolled: when (cmd, dir) hits `samples_per_cmd` on disk,
# a new sample is accepted only if the newest existing one is older than
# `refresh_interval_sec` (default 6 h) — then the oldest gets deleted.
AUTO_CAPTURE_CMDS: Set[int] = {
    0x0509, 0x0304, 0x0504, 0x051f, 0x0505,
    0x0c03, 0x0c01, 0x0c21, 0x0c07, 0x0c11, 0x0c08, 0x0c05, 0x0c06,
    0x0402, 0x0302,
    0x0f02,
    0x4707, 0x4501, 0x4504, 0x4202, 0x4215, 0x4216,
}

_DEFAULT_AUTO_CAPTURE_DIR = "tmp_ws_capture/auto"
# Re-arm the JS-side capture cfg at most this often (s). Re-arm replenishes
# the per-cmd budget after JS slot.remaining drops to zero, and picks up
# any (cmd, dir) that has just become "needs more" due to age.
_DEFAULT_ARM_INTERVAL_SEC = 60.0
# A (cmd, dir) at sample cap is considered "needs more" again once its
# newest sample is this many seconds old. Lets us catch schema drift
# without burning unbounded disk on stable cmds.
_DEFAULT_REFRESH_INTERVAL_SEC = 6 * 3600.0

# Default per-cmd disk budget (number of latest bodies to retain).
# Bumped from 5 → 50 for richer cross-validation: 50 frames = many
# different inventory states / dig outcomes / lamp results, enough for
# the user to hand-verify schema robustness.
_DEFAULT_SAMPLES_PER_CMD = 50

# Big-body cmds that fire rarely — keep a smaller window so disk usage
# stays modest. 0x4202 alone is ~57KB; 50 samples would be ~3MB per
# device. 10 still gives multiple login-time snapshots for diff-checking.
_SAMPLES_PER_CMD_OVERRIDES: Dict[int, int] = {
    0x4202: 10,
    0x4215: 10,
    0x4216: 10,
}


def _sanitize_device(device_id: str) -> str:
    text = str(device_id or "unknown").strip()
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text or "unknown"


class WSFrameTracker:
    """Per-device drain state holder.

    Tracks the last drain timestamp so each call returns only frames since
    the previous drain — i.e. frames that arrived between the previous
    action and now, attributable to that previous action.

    Optionally arms in-page body capture for `auto_capture_cmds` and
    persists captured bodies to `tmp_ws_capture/auto/<device>/` until each
    cmd has `samples_per_cmd` files on disk.
    """

    def __init__(
        self,
        device_id: Optional[str] = None,
        auto_capture_cmds: Optional[Iterable[int]] = None,
        samples_per_cmd: int = _DEFAULT_SAMPLES_PER_CMD,
        samples_per_cmd_overrides: Optional[Dict[int, int]] = None,
        max_body_bytes: int = 65536,
        capture_root: Optional[str] = None,
        refresh_interval_sec: float = _DEFAULT_REFRESH_INTERVAL_SEC,
        arm_interval_sec: float = _DEFAULT_ARM_INTERVAL_SEC,
    ) -> None:
        self._last_drain_ms: Optional[int] = None
        self._installed_page_id: Optional[int] = None
        self._install_failed_page_id: Optional[int] = None

        self._device_id = device_id
        self._samples_per_cmd = int(samples_per_cmd)
        # Per-cmd disk budget overrides (e.g. cap big login-bulk cmds).
        # Pass {} (not None) to disable defaults entirely.
        self._samples_overrides: Dict[int, int] = (
            dict(_SAMPLES_PER_CMD_OVERRIDES)
            if samples_per_cmd_overrides is None
            else {int(k): int(v) for k, v in samples_per_cmd_overrides.items()}
        )
        self._max_body_bytes = int(max_body_bytes)
        self._refresh_interval_sec = float(refresh_interval_sec)
        self._arm_interval_sec = float(arm_interval_sec)
        if auto_capture_cmds is None:
            self._auto_cmds: Set[int] = set(AUTO_CAPTURE_CMDS)
        else:
            self._auto_cmds = {int(c) for c in auto_capture_cmds}
        self._capture_root = (
            Path(capture_root) if capture_root else Path(_DEFAULT_AUTO_CAPTURE_DIR)
        )
        # Files on disk per (cmd, dir), sorted oldest→newest by ts in name.
        # Lazy-loaded; rotates as we accept new samples.
        self._samples: Optional[Dict[tuple, List[Path]]] = None
        # Wall-clock time of last set_body_capture re-arm — gated by
        # `_arm_interval_sec` so we don't burn an evaluate per drain.
        self._last_arm_ts: float = 0.0

    def _samples_cap_for(self, cmd: int) -> int:
        """Per-cmd disk budget — uses override map if present, else default."""
        return int(self._samples_overrides.get(int(cmd), self._samples_per_cmd))

    def ensure_installed(self, page: Any) -> bool:
        """Best-effort install. Returns True if (likely) hooked on `page`.

        Tracks the installed page by id() — if the device wrapper swaps in
        a fresh Playwright page (browser restart, navigation), this method
        re-installs against the new page. install_failed is also tied to
        page id so a fresh page gets a fresh attempt.

        After a successful install, also (re-)arms body capture for any
        cmds that haven't yet hit their on-disk sample budget.
        """
        if page is None:
            return False
        pid = id(page)
        if self._installed_page_id == pid:
            return True
        if self._install_failed_page_id == pid:
            return False
        try:
            status = install_ws_listener(page)
        except Exception:
            self._install_failed_page_id = pid
            return False
        if status in ("installed", "already"):
            self._installed_page_id = pid
            # New page = new clock baseline.
            self._last_drain_ms = None
            self._arm_body_capture_if_needed(page)
            return True
        # 'pending' = netManager not ready yet; retry on next call.
        return False

    def drain(self, page: Any, max_n: int = 50) -> List[Dict[str, Any]]:
        """Drain frames since the previous successful drain, and persist
        any captured bodies to `tmp_ws_capture/auto/<device>/`.

        Returns [] on first call (no baseline), on install failure, or on
        any Playwright error. Updates the high-water mark to current ms
        when frames are returned (or no frames since last call).
        """
        if not self.ensure_installed(page):
            return []
        # Persist bodies (best-effort, never raises).
        self._persist_captured_bodies(page)
        # Periodically refresh the JS-side budget so a long-running session
        # keeps capturing — without this the slot.remaining would zero out
        # and stay zero, missing later samples for schema-drift detection.
        self._maybe_periodic_arm(page)
        cur = now_ms()
        if self._last_drain_ms is None:
            self._last_drain_ms = cur
            return []
        try:
            frames = drain_ws_frames(page, since_ms=self._last_drain_ms, max_n=max_n)
        except Exception:
            return []
        self._last_drain_ms = cur
        return frames

    def reset(self) -> None:
        """Clear state — call after a browser restart."""
        self._last_drain_ms = None
        self._installed_page_id = None
        self._install_failed_page_id = None

    # ── body capture helpers ──────────────────────────────────────────────

    def _capture_dir(self) -> Optional[Path]:
        if not self._device_id or not self._auto_cmds:
            return None
        return self._capture_root / _sanitize_device(self._device_id)

    @staticmethod
    def _ts_from_path(p: Path) -> int:
        """Extract the `ts` (epoch ms) from `cmd_0x<id>_<dir>_<ts>.bin`."""
        try:
            return int(p.stem.split("_")[3])
        except (ValueError, IndexError):
            return 0

    def _load_samples(self) -> Dict[tuple, List[Path]]:
        """Map (cmd, dir) → list of sample files, sorted oldest→newest."""
        out: Dict[tuple, List[Path]] = {}
        d = self._capture_dir()
        if d is None or not d.exists():
            return out
        for fp in d.glob("cmd_0x*_*_*.bin"):
            try:
                parts = fp.stem.split("_")
                if len(parts) < 4:
                    continue
                cmd = int(parts[1], 16)
                dir_ = parts[2]
            except (ValueError, IndexError):
                continue
            out.setdefault((cmd, dir_), []).append(fp)
        for files in out.values():
            files.sort(key=self._ts_from_path)
        return out

    def _samples_dict(self) -> Dict[tuple, List[Path]]:
        if self._samples is None:
            self._samples = self._load_samples()
        return self._samples

    def _cmds_needing_more_samples(self) -> List[int]:
        """With pure-FIFO rolling, every auto cmd is always armed so the
        on-disk window stays current. Stable cmds (e.g. 0x4202, fired only
        at login) cost nothing extra — they only churn when the cmd
        actually fires. State-changing cmds (e.g. 0x0402 inventory push)
        get continuous fresh samples for cross-validation.

        `refresh_interval_sec` is retained on the constructor for backward
        compatibility but no longer gates capture rotation.
        """
        return list(self._auto_cmds)

    def _arm_body_capture_if_needed(self, page: Any) -> None:
        if not self._device_id or not self._auto_cmds:
            return
        need = self._cmds_needing_more_samples()
        try:
            if need:
                # JS-side budget is per cmd (tx+rx share). Give it 2x
                # samples_per_cmd so a single fresh page can cover both
                # directions without re-arming.
                set_body_capture(
                    page,
                    need,
                    max_per_cmd=self._samples_per_cmd * 2,
                    max_bytes=self._max_body_bytes,
                )
            else:
                set_body_capture(page, [])
            self._last_arm_ts = time.time()
        except Exception:
            pass

    def _maybe_periodic_arm(self, page: Any) -> None:
        """Re-arm if `arm_interval_sec` has elapsed since last arm.

        JS-side `slot.remaining` decrements with each capture and stays at
        zero forever once exhausted. Re-arming pumps the budget back up so
        we keep collecting fresh samples for rolling refresh / drift
        detection. Bounded by an interval so we don't burn an evaluate per
        drain.
        """
        if not self._device_id or not self._auto_cmds:
            return
        if time.time() - self._last_arm_ts < self._arm_interval_sec:
            return
        self._arm_body_capture_if_needed(page)

    def _persist_captured_bodies(self, page: Any) -> None:
        d = self._capture_dir()
        if d is None:
            return
        try:
            bodies = drain_captured_bodies(page)
        except Exception:
            return
        if not bodies:
            return
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        samples = self._samples_dict()
        any_change = False
        cur_wall_ms = now_ms()

        for b in bodies:
            cmd = int(b.get("cmd", 0))
            dir_ = str(b.get("dir", ""))
            if cmd not in self._auto_cmds:
                continue
            key = (cmd, dir_)
            files = samples.setdefault(key, [])
            cap = self._samples_cap_for(cmd)
            # Under cap → just accept.
            if len(files) < cap:
                self._write_sample(d, b, files, samples, key)
                any_change = True
                continue
            # At cap → pure FIFO: always keep N newest. Without rotation
            # here we'd drop fresh in-game state pushes (e.g. pickaxe
            # consume) for hours, defeating live cross-validation.
            # Roll: delete the oldest, then accept the new.
            try:
                old = files.pop(0)
                old.unlink(missing_ok=True)
                meta = old.with_suffix(".meta")
                meta.unlink(missing_ok=True)
            except OSError:
                pass
            self._write_sample(d, b, files, samples, key)
            any_change = True
        # Re-arm if a cmd's budget likely shifted (capture accepted /
        # rotation freed a slot). Cheap — single evaluate.
        if any_change:
            self._arm_body_capture_if_needed(page)

    def _write_sample(
        self,
        out_dir: Path,
        body_entry: Dict[str, Any],
        files_for_key: List[Path],
        samples: Dict[tuple, List[Path]],
        key: tuple,
    ) -> None:
        cmd, dir_ = key
        ts = int(body_entry.get("ts", now_ms()))
        full_len = int(body_entry.get("full_len", 0))
        body_bytes = body_entry.get("body", b"")
        fp = out_dir / f"cmd_0x{cmd:04x}_{dir_}_{ts}.bin"
        try:
            fp.write_bytes(body_bytes)
        except OSError:
            return
        if full_len and full_len != len(body_bytes):
            try:
                (out_dir / f"{fp.stem}.meta").write_text(
                    f"full_len={full_len}\ncaptured_len={len(body_bytes)}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        files_for_key.append(fp)
        files_for_key.sort(key=self._ts_from_path)
        samples[key] = files_for_key

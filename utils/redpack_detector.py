"""Detect claimable 紅包 (red envelopes) on the main page.

Two-tier strategy:

1. **Cheap gate** — `main_redpoint_has_unread(page)` reads the chat icon's
   RedPoint cocos node. Sub-millisecond. Catches both "new chat message"
   AND "new redbag" since the same indicator handles both.

2. **Precise probe** — `fetch_redbag_list(page)` sends WS cmd `0x2605`
   (empty body) and parses the response protobuf. Returns a list of
   `RedBagEntry` with sender, timestamp, etc. Costs one WS roundtrip
   (~50-150ms).

Use `has_claimable_redpack(page)` as the combined entry point: it short-
circuits via the cheap gate, then confirms via the API only when needed.

Cocos paths (Cocos Creator 3.6.3, 2026-05 schema):
    Main page RedPoint:
        /UIRoot/NormalView/MainView/subRoots/btnChatRoot/imgChatIcon/RedPoint
        Children: point, point1, txtNum — `active=true` on any = "unread"

    Chat panel redBag tab (when chat is open):
        /UIRoot/NormalView/ChatView/container/tab/redBagBtn/RedPoint

WS schema (extracted from index.966f5.js cmd table):
    0x2601 red.red_all_list   — full list (not used here)
    0x2602 red.red_send       — send a red bag
    0x2603 red.red_grab       — **claim/grab a red bag**, request {id, type}
    0x2604 red.red_pop        — pop / open detail
    0x2605 red.red_brief_list — brief list (`fetch_redbag_list` uses this)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from utils.protobuf_walk import read_varint as _pb_read_varint
from utils.protobuf_walk import walk_fields_lenient as _pb_walk_fields_lenient

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────
# Schema for 0x2605 response
# ───────────────────────────────────────────────────────────────────


@dataclass
class RedBagEntry:
    bag_id: int                  # field 1 — unique per-bag ID (= request.id for grab)
    cfg_id: int                  # field 2 — config table key (`configRed_packet`)
    state: int                   # field 3 — RedBagState; 2 = UnClaimed (claimable)
    sender_id: int               # field 4 — player who sent
    sender_name: str             # field 5 — sender's display name
    timestamp: int               # field 6 — unix seconds when sent
    raw_fields: dict = field(default_factory=dict)  # full parsed dict for forensics

    # Backwards-compat shim: existing callers used `entry.type_` thinking proto
    # field 3 was the grab `type`. It's NOT — it's `state`. Real grab `type`
    # comes from `configRed_packet.getDataByKey(cfg_id).type`. The bot uses
    # `claim_redpack(page, bag_id, cfg_id)` which resolves type internally.
    @property
    def type_(self) -> int:  # noqa: N802 — keep name for back-compat
        return self.state

    # Pre-2026-05-20 callers also referenced `field_2`. Keep alias.
    @property
    def field_2(self) -> int:
        return self.cfg_id


@dataclass
class ClaimResult:
    """Result of attempting to claim one red bag via 0x2603.

    Response channels (verified against the live server 2026-05-20):
      - **0x2603 rx**: server accepted the grab; body has the claim details.
      - **0x0201 rx**: server rejected (`error.error_info_s2c`); body[0] is
        the error_code varint. error_code 2 has been observed for
        already-claimed/expired bags.
    """
    bag_id: int
    success: bool                # True iff we got a 0x2603 response (grab accepted)
    response_cmd: int = 0        # the cmd id of the response we keyed off (0x2603 or 0x0201)
    response_body: bytes = b""   # raw response bytes
    response_fields: dict = field(default_factory=dict)  # parsed top-level fields
    error: Optional[str] = None  # send failure / timeout / server error description
    error_code: Optional[int] = None  # parsed from 0x0201 response when applicable
    amount: Optional[int] = None # parsed gold amount if we can identify the field


# ───────────────────────────────────────────────────────────────────
# Cheap gate (Option A)
# ───────────────────────────────────────────────────────────────────


_MAIN_REDPOINT_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return null;
  const scene = cc.director.getScene();
  // Walk to /UIRoot/NormalView/MainView/subRoots/btnChatRoot/imgChatIcon/RedPoint
  const path = ['UIRoot','NormalView','MainView','subRoots','btnChatRoot','imgChatIcon','RedPoint'];
  let n = scene;
  for (const p of path) {
    if (!n || !n.children) return null;
    n = n.children.find(c => (c.name || '') === p);
    if (!n) return null;
  }
  // Any child active → "unread" indicator visible
  return {
    container_active: !!n.active,
    any_child_active: (n.children || []).some(c => c.active),
    children: (n.children || []).map(c => ({name: c.name, active: c.active})),
  };
}
"""


def main_redpoint_state(page: Any) -> Optional[dict]:
    """Return the raw RedPoint state dict, or None if the node isn't found.

    `any_child_active` is the boolean we typically care about — it goes true
    when there's any unread (chat message OR redbag pending claim).
    """
    try:
        return page.evaluate(_MAIN_REDPOINT_JS)
    except Exception as e:
        logger.debug(f"[redpack] RedPoint probe failed: {e}")
        return None


def main_redpoint_has_unread(page: Any) -> bool:
    """True iff the main-page chat icon shows ANY unread (chat OR redbag).

    This is the cheap gate — gives a false positive when only chat is unread
    but no claimable redbag. Use `has_claimable_redpack` for redbag-specific
    confirmation.
    """
    state = main_redpoint_state(page)
    return bool(state and state.get("any_child_active"))


# ───────────────────────────────────────────────────────────────────
# Precise probe (Option C — send 0x2605, parse response)
# ───────────────────────────────────────────────────────────────────


_INSTALL_PROBE_JS = r"""
([ringMax]) => {
  if (window.__redpack_probe_inst) { window.__redpack_probe_ring = []; return 'already'; }
  const nm = window.netManager;
  if (!nm || !nm._cnet) return 'no_cnet';
  const sock = nm._cnet;
  if (typeof sock.sendMessage !== 'function' || typeof sock.reciveMsg !== 'function') return 'no_methods';
  window.__redpack_probe_ring = [];
  const RING_MAX = ringMax | 0 || 200;
  const toArr = (body) => {
    if (!body) return null;
    let bytes;
    try {
      if (body instanceof Uint8Array) bytes = body;
      else if (body.buffer) bytes = new Uint8Array(body.buffer, body.byteOffset || 0, body.byteLength);
      else if (Array.isArray(body)) bytes = Uint8Array.from(body);
      else return null;
    } catch (e) { return null; }
    const cap = Math.min(bytes.length, 65536);
    const arr = new Array(cap);
    for (let i = 0; i < cap; i++) arr[i] = bytes[i];
    return arr;
  };
  // Only capture cmd 0x2605 — keep ring tiny.
  const TARGET = 0x2605;
  const origRecv = sock.reciveMsg.bind(sock);
  sock.reciveMsg = function (cmd, body) {
    if ((cmd | 0) === TARGET) {
      try {
        const buf = window.__redpack_probe_ring;
        buf.push({ts: Date.now(), body: toArr(body)});
        if (buf.length > RING_MAX) buf.shift();
      } catch (e) {}
    }
    return origRecv(cmd, body);
  };
  window.__redpack_probe_inst = true;
  return 'installed';
}
"""

_CLEAR_PROBE_JS = r"() => { window.__redpack_probe_ring = []; return 'cleared'; }"
_DRAIN_PROBE_JS = r"() => { const b = window.__redpack_probe_ring || []; window.__redpack_probe_ring = []; return b; }"

_SEND_2605_JS = r"""
() => {
  const sock = window.netManager && window.netManager._cnet;
  if (!sock || typeof sock.sendMessage !== 'function') return {err: 'no_send'};
  try {
    sock.sendMessage(0x2605, new Uint8Array());
    return {sent: true};
  } catch (e) {
    return {err: String(e)};
  }
}
"""


# Sends 0x2603 (red.red_grab_c2s) via the high-level `netManager.send(...)`,
# NOT low-level `sock.sendMessage(0x2603, bytes)`. The high-level call goes
# through the protobuf serialization layer the game registers at startup,
# which adds the framing the server expects. Bypassing it with raw bytes
# returns 0x0201 error_code=2 even when the body's wire format is otherwise
# byte-identical (confirmed on 5554 2026-05-20).
_SEND_2603_JS = r"""
([id, type_]) => {
  if (!window.netManager || typeof window.netManager.send !== 'function') return {err: 'no_netmanager'};
  try {
    window.netManager.send('red.red_grab_c2s', {id: id, type: type_});
    return {sent: true};
  } catch (e) {
    return {err: String(e)};
  }
}
"""


# Captures BOTH 0x2603 (grab success) and 0x0201 (error.error_info_s2c).
# A claim attempt is resolved when EITHER fires.
_INSTALL_PROBE_2603_JS = r"""
([ringMax]) => {
  if (window.__redpack_grab_inst_v2) { window.__redpack_grab_ring_v2 = []; return 'already'; }
  const nm = window.netManager;
  if (!nm || !nm._cnet) return 'no_cnet';
  const sock = nm._cnet;
  if (typeof sock.reciveMsg !== 'function') return 'no_methods';
  window.__redpack_grab_ring_v2 = [];
  const RING_MAX = ringMax | 0 || 50;
  const toArr = (body) => {
    if (!body) return null;
    let bytes;
    try {
      if (body instanceof Uint8Array) bytes = body;
      else if (body.buffer) bytes = new Uint8Array(body.buffer, body.byteOffset || 0, body.byteLength);
      else if (Array.isArray(body)) bytes = Uint8Array.from(body);
      else return null;
    } catch (e) { return null; }
    const cap = Math.min(bytes.length, 65536);
    const arr = new Array(cap);
    for (let i = 0; i < cap; i++) arr[i] = bytes[i];
    return arr;
  };
  // 0x2603 = red.red_grab_s2c (success), 0x0201 = error.error_info_s2c (failure).
  const TARGETS = new Set([0x2603, 0x0201]);
  const origRecv = sock.reciveMsg.bind(sock);
  sock.reciveMsg = function (cmd, body) {
    const c = cmd | 0;
    if (TARGETS.has(c)) {
      try {
        const buf = window.__redpack_grab_ring_v2;
        buf.push({cmd: c, ts: Date.now(), body: toArr(body)});
        if (buf.length > RING_MAX) buf.shift();
      } catch (e) {}
    }
    return origRecv(cmd, body);
  };
  window.__redpack_grab_inst_v2 = true;
  return 'installed';
}
"""

_CLEAR_GRAB_JS = r"() => { window.__redpack_grab_ring_v2 = []; return 'cleared'; }"
_DRAIN_GRAB_JS = r"() => { const b = window.__redpack_grab_ring_v2 || []; window.__redpack_grab_ring_v2 = []; return b; }"


def fetch_redbag_list(page: Any, timeout: float = 2.0) -> List[RedBagEntry]:
    """Query the redbag list via WS cmd 0x2605 and parse the response.

    Returns [] on any error (probe install failed, no response in `timeout`,
    parse failure). Does NOT distinguish "no redbags exist" from "couldn't
    fetch" — callers needing that distinction should check `main_redpoint_state`
    first or wrap in try/except.

    Side-effect: installs an idempotent cocos-side hook for 0x2605 only.
    """
    try:
        install = page.evaluate(_INSTALL_PROBE_JS, [200])
    except Exception as e:
        logger.debug(f"[redpack] probe install failed: {e}")
        return []
    if install not in ("installed", "already"):
        logger.debug(f"[redpack] probe install returned {install}")
        return []

    try:
        page.evaluate(_CLEAR_PROBE_JS)
        send_res = page.evaluate(_SEND_2605_JS) or {}
    except Exception as e:
        logger.debug(f"[redpack] send failed: {e}")
        return []
    if not send_res.get("sent"):
        logger.debug(f"[redpack] send 0x2605 returned {send_res}")
        return []

    # Poll for response.
    deadline = time.monotonic() + timeout
    frames: list[dict] = []
    while time.monotonic() < deadline:
        try:
            frames = page.evaluate(_DRAIN_PROBE_JS) or []
        except Exception:
            frames = []
        if frames:
            break
        time.sleep(0.1)
    if not frames:
        logger.debug("[redpack] no 0x2605 response within timeout")
        return []

    body = bytes(frames[-1].get("body") or [])
    return _parse_redbag_list_response(body)


# ───────────────────────────────────────────────────────────────────
# Claim action (0x2603 red.red_grab)
# ───────────────────────────────────────────────────────────────────


def _encode_varint(v: int) -> bytes:
    out = bytearray()
    while v > 0x7f:
        out.append((v & 0x7f) | 0x80)
        v >>= 7
    out.append(v & 0x7f)
    return bytes(out)


def _build_grab_body(bag_id: int, type_: int) -> bytes:
    """Build the 0x2603 request body — `{id: bag_id, type: type_}` in proto3 wire.

    field 1 (id, varint), field 2 (type, varint). Order doesn't matter for
    proto3 but mirroring the JS call order (id first, type second) is
    closest to what the game itself sends.
    """
    return bytes([0x08]) + _encode_varint(bag_id) + bytes([0x10]) + _encode_varint(type_)


_LOOKUP_CFG_TYPE_JS = r"""
([cfgId]) => {
  // `configRed_packet` is a global config table exposed by the bundle.
  // .getDataByKey(cfg_id) returns a config entry whose `.type` is the
  // value the server expects for the grab cmd's `type` field (0 / 1 /
  // possibly others). NOT the same as proto field 3 of the brief list
  // (which is the bag's `state`).
  const cfg = window.configRed_packet;
  if (!cfg || typeof cfg.getDataByKey !== 'function') return {err: 'no_config'};
  const entry = cfg.getDataByKey(cfgId);
  if (!entry) return {err: 'no_entry', cfg_id: cfgId};
  return {type: entry.type, quality: entry.quality};
}
"""


def _lookup_cfg_type(page: Any, cfg_id: int) -> Optional[int]:
    """Look up `configRed_packet.getDataByKey(cfg_id).type` via JS.

    Returns None if the config table isn't loaded or the cfg_id is unknown.
    The bot should treat that as a "skip" — there's no point grabbing with
    a guessed type since the server rejects with error_code=2.
    """
    try:
        res = page.evaluate(_LOOKUP_CFG_TYPE_JS, [cfg_id]) or {}
    except Exception as e:
        logger.debug(f"[redpack] cfg lookup eval failed for cfg_id={cfg_id}: {e}")
        return None
    if res.get("err"):
        logger.debug(f"[redpack] cfg lookup err: {res}")
        return None
    t = res.get("type")
    return int(t) if isinstance(t, int) else None


# UnClaimed state value observed in `view.scroll._datas` on 5554. Any other
# state is either claimed (3?), expired (4?), or sender-side (UnSent). The
# JS view filter is `state == UnClaimed || state == UnSent` for the list
# render — but for our auto-grab path, we only want UnClaimed.
_STATE_UNCLAIMED = 2


def claim_redpack(page: Any, bag_id: int, cfg_id: int, timeout: float = 2.5) -> ClaimResult:
    """Send 0x2603 to grab one red bag, parse the response.

    Args:
        page: Playwright page bound to the game tab.
        bag_id: from `RedBagEntry.bag_id` (proto field 1 of the brief list)
        cfg_id: from `RedBagEntry.cfg_id` (proto field 2). The grab cmd's
                `type` param is **not** field 3 of the bag — it's
                `configRed_packet.getDataByKey(cfg_id).type`, looked up
                client-side. Sending the wrong `type` returns 0x0201
                error_code=2 (which we initially mistook for "expired").
        timeout: seconds to wait for the s2c response.

    Returns:
        ClaimResult — `success=False` covers all failure modes (send fail,
        timeout, server error). `response_body` always populated when we
        got a response, so callers can post-mortem.
    """
    result = ClaimResult(bag_id=bag_id, success=False, response_body=b"", response_fields={})

    # Resolve the correct `type` from the bundle's config table.
    cfg_type = _lookup_cfg_type(page, cfg_id)
    if cfg_type is None:
        result.error = f"cfg_id={cfg_id} not found in configRed_packet"
        return result

    # Install probe.
    try:
        install = page.evaluate(_INSTALL_PROBE_2603_JS, [50])
    except Exception as e:
        result.error = f"install failed: {e}"
        return result
    if install not in ("installed", "already"):
        result.error = f"install returned {install}"
        return result

    # Clear + send via high-level netManager.send.
    try:
        page.evaluate(_CLEAR_GRAB_JS)
        send_res = page.evaluate(_SEND_2603_JS, [bag_id, cfg_type]) or {}
    except Exception as e:
        result.error = f"send failed: {e}"
        return result
    if not send_res.get("sent"):
        result.error = f"send returned {send_res}"
        return result

    # Drain.
    deadline = time.monotonic() + timeout
    frames: list[dict] = []
    while time.monotonic() < deadline:
        try:
            frames = page.evaluate(_DRAIN_GRAB_JS) or []
        except Exception:
            frames = []
        if frames:
            break
        time.sleep(0.1)

    if not frames:
        result.error = "no response within timeout (server may be silently ignoring)"
        return result

    # If we got both 0x2603 and 0x0201 (race), prefer 0x2603 (the
    # success channel) — error.error_info is generic, success is specific.
    resp_frame = next((f for f in frames if int(f.get("cmd", 0)) == 0x2603), frames[-1])
    resp_cmd = int(resp_frame.get("cmd", 0))
    resp_bytes = bytes(resp_frame.get("body") or [])
    result.response_cmd = resp_cmd
    result.response_body = resp_bytes
    for f in _walk_pb(resp_bytes):
        result.response_fields[f["field"]] = f.get("value", f.get("bytes"))

    if resp_cmd == 0x2603:
        # Server-side ack — grab accepted. We DON'T extract amount here
        # because we don't have a verified success sample yet; the response
        # could be {echoed_bag_id, amount, sender_id, ...} in any order.
        # Once a real success body is captured, add field-mapping logic.
        result.success = True
    elif resp_cmd == 0x0201:
        # Error channel — field 1 is the error code.
        err_code = result.response_fields.get(1)
        result.error_code = int(err_code) if isinstance(err_code, int) else None
        result.error = f"server error code={result.error_code}"
        result.success = False
    else:
        result.error = f"unexpected response cmd 0x{resp_cmd:04x}"
        result.success = False
    return result


def claim_all_pending(page: Any) -> tuple[int, list[ClaimResult]]:
    """Convenience: fetch the brief list, try to grab each entry.

    Returns `(claimed_count, results)` where `results` has one ClaimResult
    per attempted bag (including failures). When the list is empty,
    short-circuits to `(0, [])` for ~50ms cost (one 0x2605 roundtrip).

    Note: an earlier version gated on `main_redpoint_has_unread` to avoid
    that 50ms — but observation on 5554 (2026-05-20) showed the chat-icon
    RedPoint reflects *unread chat messages*, NOT "claimable redpacks
    sitting on the server", so it was returning False even when 0x2605
    listed claimable bags. The gate is removed; we always fetch.
    """
    entries = fetch_redbag_list(page)
    if not entries:
        return 0, []
    results: list[ClaimResult] = []
    claimed = 0
    for e in entries:
        # Skip non-claimable states. brief_list may include already-grabbed
        # or sender-side bags; only state == UnClaimed (2) is grabbable.
        if e.state != _STATE_UNCLAIMED:
            continue
        r = claim_redpack(page, e.bag_id, e.cfg_id)
        results.append(r)
        if r.success:
            claimed += 1
        # Small spacing so we don't hammer the server back-to-back.
        time.sleep(0.2)
    return claimed, results


# ───────────────────────────────────────────────────────────────────
# Combined entry point
# ───────────────────────────────────────────────────────────────────


def has_claimable_redpack(page: Any) -> bool:
    """True iff `fetch_redbag_list` returns ≥1 entry.

    NOTE: cannot distinguish "list contains bags I already claimed" from
    "list contains bags I can claim" — the 0x2605 response schema doesn't
    expose a claim-status field. Treat any non-empty response as "maybe
    claimable" until a future patch separates the two.

    (Earlier this gated on `main_redpoint_has_unread` — removed because
    that RedPoint reflects unread chat messages, not server-side claimable
    redpacks. See note in `claim_all_pending`.)
    """
    return len(fetch_redbag_list(page)) > 0


# ───────────────────────────────────────────────────────────────────
# Protobuf walker — minimal proto3 wire decoder
# ───────────────────────────────────────────────────────────────────


_read_varint = _pb_read_varint


def _walk_pb(buf: bytes) -> List[dict]:
    """Walk a protobuf buffer, returning a flat list of {field, wire, ...}.

    Thin wrapper over `protobuf_walk.walk_fields_lenient` (tolerant variant:
    stops cleanly on truncation / unknown wire). Reshapes the shared
    `(field, wire, value)` tuples into this module's historic dict form —
    wire 2 (len-delim) payloads live under `"bytes"`, all others under
    `"value"`.
    """
    out: list[dict] = []
    for field_num, wire, value in _pb_walk_fields_lenient(buf):
        if wire == 2:
            out.append({"field": field_num, "wire": 2, "bytes": value})
        else:
            out.append({"field": field_num, "wire": wire, "value": value})
    return out


def _parse_redbag_entry(buf: bytes) -> Optional[RedBagEntry]:
    """Parse one inner RedBagEntry from its raw bytes."""
    raw = {}
    for f in _walk_pb(buf):
        raw[f["field"]] = f.get("value", f.get("bytes"))
    if 1 not in raw or 4 not in raw or 5 not in raw:
        return None
    sender_name_bytes = raw.get(5)
    if isinstance(sender_name_bytes, bytes):
        try:
            sender_name = sender_name_bytes.decode("utf-8")
        except UnicodeDecodeError:
            sender_name = sender_name_bytes.decode("utf-8", errors="replace")
    else:
        sender_name = str(sender_name_bytes)
    return RedBagEntry(
        bag_id=int(raw.get(1, 0)),
        cfg_id=int(raw.get(2, 0)),
        state=int(raw.get(3, 0)),
        sender_id=int(raw.get(4, 0)),
        sender_name=sender_name,
        timestamp=int(raw.get(6, 0)),
        raw_fields=raw,
    )


def _parse_redbag_list_response(body: bytes) -> List[RedBagEntry]:
    """Parse a 0x2605 RX body — `repeated RedBagEntry list = 2;`."""
    if not body:
        return []
    entries: list[RedBagEntry] = []
    for f in _walk_pb(body):
        if f["field"] == 2 and f["wire"] == 2:
            entry = _parse_redbag_entry(f.get("bytes") or b"")
            if entry is not None:
                entries.append(entry)
    return entries

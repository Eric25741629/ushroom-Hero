"""Live driver for 煩惱消 (act_type 224 / 左右消除) — reads the real board from
the running Cocos client and issues moves as Playwright mouse drags.

Pairs the pure solver (``game_actions.fannaoxiao_solver``) with the live game.
The board-read JS and the world->screen swipe math live here so the whole
read -> solve -> move loop is reusable from any Playwright ``page``.

LIVE-confirmed primitives (7fe98fc6, 2026-06-14; see
docs/protocol/FANNAOXIAO_RECON.md):
  - BOARD READ: ``uiMgr.getView("ActivityNewYear24MatchGameView")`` exposes
    ``RowList[].gridList[]`` ({pos,length,color,index}), ``curScore``,
    ``curLevel``, ``canOperate``, ``isWaitingTween``, ``isGameComplete``.
  - MOVE: a horizontal Playwright mouse drag on the canvas. nodeMap is anchored
    (0,0); column width T=66, row height D=60; design resolution is read from
    ``cc.view.getVisibleSize()`` and mapped to the canvas CSS rect. World Y is
    bottom-up so screen_y = top + (1 - worldY/designH) * rectHeight.
  - ENTRY: banner ``icon_224`` -> ActivityNewYear24MatchView -> ``content/btnStart``
    (send_24_100). The core game (start + drag moves to game over) is FREE; only
    the optional btnColor/btnHammer power-ups consume goods (never used here).

Bounded by design: callers pass ``max_moves`` so a run never grinds forever.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from game_actions.fannaoxiao_solver import (
    Block,
    Board,
    choose_move,
    is_game_over,
)

logger = logging.getLogger(__name__)

GAME_VIEW = "ActivityNewYear24MatchGameView"
ACTIVITY_VIEW = "ActivityNewYear24MatchView"
ICON_PATH = "/UIRoot/NormalView/MainView/top/systemTop/btnRightRoot/icon_224"
START_BTN_PATH = f"/UIRoot/NormalView/{ACTIVITY_VIEW}/content/btnStart"

T_COL = 66   # column width (game const T)
D_ROW = 60   # row height (game const D)


# ── JS: read the live board from the view instance ─────────────────────

_READ_BOARD_JS = r"""
() => {
  const v = window.uiMgr && window.uiMgr.getView && window.uiMgr.getView("%s");
  if (!v) return {open: false};
  const rows = [];
  for (const row of (v.RowList || [])) {
    if (!row) { rows.push(null); continue; }
    rows.push((row.gridList || []).map(g => ({pos: g.pos, length: g.length, color: g.color, index: g.index})));
  }
  return {
    open: true,
    curScore: v.curScore, curLevel: v.curLevel,
    isGameComplete: !!v.isGameComplete, canOperate: !!v.canOperate,
    isWaitingTween: !!v.isWaitingTween, isTweening: v.isTweening | 0,
    rowCount: (v.RowList || []).length,
    rows,
    nextRow: v.nextRowGridInfo || [],   // green-bar preview: [[pos, index], ...]
  };
}
""" % GAME_VIEW


# ── JS: map (rowFromBottom 1-based, col 0-based) -> canvas screen pixel ──
# Returns the click target for the CENTRE of that cell.
_CELL_SCREEN_JS = r"""
([rowFromBottom, col]) => {
  const v = window.uiMgr.getView("%s");
  if (!v || !v.utMap) return null;
  const ut = v.utMap;
  const T = %d, D = %d;
  const nx = (col + 0.5) * T;
  const ny = (rowFromBottom - 0.5) * D;
  const w = ut.convertToWorldSpaceAR(new cc.Vec3(nx, ny, 0));
  const canvas = document.querySelector('canvas');
  const rect = canvas.getBoundingClientRect();
  const vis = cc.view.getVisibleSize();
  const sx = rect.left + (w.x / vis.width) * rect.width;
  const sy = rect.top + (1 - w.y / vis.height) * rect.height;
  return {x: sx, y: sy};
}
""" % (GAME_VIEW, T_COL, D_ROW)


# ── JS: emit('click') on a node by path (entry/start buttons) ──────────
_CLICK_JS = r"""
([path]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked:false, err:'no_cc'};
  const scene = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || '') === p);
      if (!n) return null;
    }
    return n;
  };
  const node = find(scene, path.split('/').filter(Boolean));
  if (!node) return {clicked:false, err:'not_found', path};
  try { node.emit('click', node); return {clicked:true, active: !!node.active}; }
  catch(e) { return {clicked:false, err:String(e)}; }
}
"""


def _board_from_rows(rows: List[Optional[List[dict]]]) -> Board:
    """Convert the JS board read into the solver's Board (top-first, same as
    RowList ordering: rows[0] is the top row)."""
    out_rows = []
    for row in rows:
        if not row:
            out_rows.append(tuple())
            continue
        out_rows.append(tuple(
            Block(pos=int(g["pos"]), length=int(g["length"]), color=int(g["color"]))
            for g in row
        ))
    return Board(rows=tuple(out_rows))


def read_state(page) -> Dict[str, Any]:
    """Read the live game view state. Returns the raw dict (``open`` False if
    the game view isn't open)."""
    return page.evaluate(_READ_BOARD_JS) or {"open": False}


def read_board(page) -> Optional[Tuple[Board, Dict[str, Any]]]:
    """Read the board as a solver ``Board`` plus the state dict. None if the
    game view is not open."""
    st = read_state(page)
    if not st.get("open"):
        return None
    return _board_from_rows(st.get("rows") or []), st


def _wait_operable(page, timeout_sec: float = 4.0) -> bool:
    """Wait until the view is settled and accepts input (canOperate and not
    tweening). Returns False on timeout / game over / view closed."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        st = read_state(page)
        if not st.get("open"):
            return False
        if st.get("isGameComplete"):
            return False
        if st.get("canOperate") and not st.get("isWaitingTween") and st.get("isTweening", 0) == 0:
            return True
        time.sleep(0.15)
    return False


def issue_move(page, row_index: int, from_pos: int, to_pos: int, row_count: int) -> bool:
    """Drag the block at (row_index, from_pos) to to_pos via a Playwright mouse
    drag. ``row_index`` is top-first (Board/RowList order); the engine touch
    math uses row-from-bottom = row_count - row_index.

    Returns True if the drag was dispatched (not whether the board changed —
    the caller re-reads to confirm).
    """
    row_from_bottom = row_count - row_index
    sf = page.evaluate(_CELL_SCREEN_JS, [row_from_bottom, from_pos])
    st = page.evaluate(_CELL_SCREEN_JS, [row_from_bottom, to_pos])
    if not sf or not st:
        logger.warning("[fnx] cell->screen mapping failed (row_fb=%s)", row_from_bottom)
        return False
    page.mouse.move(sf["x"], sf["y"])
    page.mouse.down()
    mid_x = (sf["x"] + st["x"]) / 2
    page.mouse.move(mid_x, sf["y"], steps=4)
    page.mouse.move(st["x"], sf["y"], steps=4)
    page.mouse.up()
    return True


def enter_game(page, settle_sec: float = 3.0) -> bool:
    """From the main page: open the activity (icon_224) and start a new game.
    Returns True once the game view is open. Assumes the activity is LIVE
    (icon_224 active) and the banner rail is expanded.
    """
    page.evaluate(_CLICK_JS, [ICON_PATH])
    time.sleep(settle_sec)
    # start a fresh game (btnStart). btnContinue/btnRestart handled by caller if needed.
    res = page.evaluate(_CLICK_JS, [START_BTN_PATH])
    time.sleep(settle_sec)
    if not res.get("clicked"):
        logger.warning("[fnx] btnStart click failed: %s", res.get("err"))
    return read_state(page).get("open", False)


def play_bounded(page, max_moves: int = 40, move_pause: float = 0.4) -> Dict[str, Any]:
    """Run the read -> solve -> move loop for at most ``max_moves`` moves.

    Stops early on game over, no legal move, or a stuck board (the same board
    twice with no progress). Returns a summary dict with moves made, final
    score, max tile reached, and the stop reason. The game view must already
    be open (call ``enter_game`` first).
    """
    summary = {"moves": 0, "score": 0, "level": 0, "rows": 0,
               "stopped": "max_moves", "cleared_moves": 0}
    last_signature: Optional[str] = None
    stuck_count = 0

    for _ in range(max_moves):
        if not _wait_operable(page):
            st = read_state(page)
            summary["stopped"] = "game_over" if st.get("isGameComplete") else (
                "view_closed" if not st.get("open") else "not_operable")
            _record(summary, st)
            return summary

        read = read_board(page)
        if read is None:
            summary["stopped"] = "view_closed"
            return summary
        board, st = read
        _record(summary, st)

        if is_game_over(board):
            summary["stopped"] = "game_over"
            return summary

        signature = _signature(board) + "|" + str(st.get("nextRow") or [])
        if signature == last_signature:
            stuck_count += 1
            if stuck_count >= 2:
                summary["stopped"] = "stuck"
                return summary
        else:
            stuck_count = 0
        last_signature = signature

        # the green-bar preview makes the next spawn known -> feed it to the
        # solver for one-spawn-ahead lookahead.
        mv = choose_move(board, next_row=st.get("nextRow") or None)
        if mv is None:
            summary["stopped"] = "no_move"
            return summary

        row_index, from_pos, to_pos = mv
        ok = issue_move(page, row_index, from_pos, to_pos, st.get("rowCount", len(board.rows)))
        if not ok:
            summary["stopped"] = "move_failed"
            return summary
        summary["moves"] += 1
        time.sleep(move_pause)

    return summary


def _signature(board: Board) -> str:
    parts = []
    for row in board.rows:
        parts.append("+".join(f"{b.pos}_{b.length}_{b.color}" for b in row))
    return " ".join(parts)


def _record(summary: Dict[str, Any], st: Dict[str, Any]) -> None:
    if st.get("curScore") is not None:
        summary["score"] = st["curScore"]
    if st.get("curLevel") is not None:
        summary["level"] = st["curLevel"]
    if st.get("rowCount") is not None:
        summary["rows"] = st["rowCount"]

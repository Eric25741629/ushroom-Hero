"""星際探索 (SExplore) pure-WS runner.

The H5 client uses the ``star_explore`` module (not the older ``sea`` module).
This file contains only the native WebSocket protocol calls observed from the
live client, so the task can run while the page is not on the exploration view.
"""
from __future__ import annotations

import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# Live command ids from star_explore command registration.
CMD_INFO = 22017                  # star_explore_info_c2s/s2c
CMD_TIME = 22023                  # star_explore_time_c2s/s2c
CMD_ENTER = 22037                 # star_pve_enter_c2s/s2c
CMD_MOVE = 22039                  # star_pve_move_c2s (push-confirmed)
CMD_PVE_MAIN = 22046              # star_explore_pve_main_c2s/s2c
CMD_GRID = 22056                  # star_pve_grid_c2s/s2c
CMD_EVENT_CHOICE = 22057          # star_pve_event_choice_c2s/s2c
CMD_ASK_HELP = 22062              # star_pve_ask_help_c2s (push-confirmed)
CMD_ERROR = 0x0201

GRID_WIDTH = 27
MAX_FLOOR = 30

# EExploreType in the H5 client.
PVE = 1
NEXT_LEVEL = 2
GIFT = 3
TRAP = 4
OPEN_GRID = 5
CAVE = 6
DOUBLE_REWARD = 7
SELECT = 8
BOSS = 9

# pve_enter_s2c fields.
F_TYPE = 1
F_FLOOR = 2
F_FLOOR_STATUS = 3
F_POS_LIST = 4
F_MEMBER = 5
F_EVENT = 6
F_BOX = 7
F_BOX_TIMES = 8
F_CODE = 9


@dataclass(frozen=True)
class Member:
    role_id: int
    pos: tuple[int, int] | None
    target_pos: tuple[int, int] | None


@dataclass(frozen=True)
class Event:
    pos: tuple[int, int] | None
    event_id: int
    choice_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Box:
    """第 30 層寶箱及其全服開啟次數。"""

    position: tuple[int, int] | None
    open_times: int
    box_id: int = 0


@dataclass(frozen=True)
class State:
    floor: int
    floor_status: int
    positions: tuple[int, ...]
    members: tuple[Member, ...]
    events: tuple[Event, ...]
    code: int = 0
    boxes: tuple[Box, ...] = ()


class StarExploreServerError(RuntimeError):
    """The shared 0x0201 error channel rejected an SExplore request."""

    def __init__(self, code: int):
        self.code = int(code)
        super().__init__(f"star_explore server error code={self.code}")


def _varints(data: bytes) -> tuple[int, ...]:
    """Decode a packed repeated uint32 field without changing the shared codec."""
    values: list[int] = []
    off = 0
    while off < len(data):
        value = 0
        shift = 0
        while off < len(data):
            byte = data[off]
            off += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                values.append(value)
                break
            shift += 7
            if shift > 63:
                return tuple(values)
    return tuple(values)


def _repeated_uint(value: object) -> tuple[int, ...]:
    if isinstance(value, int):
        return (value,)
    if isinstance(value, (bytes, bytearray)):
        return _varints(bytes(value))
    return ()


def _pos(value: object) -> tuple[int, int] | None:
    if not isinstance(value, (bytes, bytearray)):
        return None
    fields = codec.walk_dict(bytes(value))
    x, y = fields.get(1), fields.get(2)
    if isinstance(x, int) and isinstance(y, int):
        return int(x), int(y)
    return None


def _member(value: object) -> Member:
    fields = codec.walk(bytes(value)) if isinstance(value, (bytes, bytearray)) else ()
    role_id = 0
    pos = None
    target_pos = None
    for field, item in fields:
        if field == 1 and isinstance(item, int):
            role_id = int(item)
        elif field == 4:
            pos = _pos(item)
        elif field == 5:
            target_pos = _pos(item)
    return Member(role_id, pos, target_pos)


def _event(value: object) -> Event:
    fields = codec.walk(bytes(value)) if isinstance(value, (bytes, bytearray)) else ()
    pos = None
    event_id = 0
    choice_ids: list[int] = []
    for field, item in fields:
        if field == 1:
            pos = _pos(item)
        elif field == 2 and isinstance(item, int):
            event_id = int(item)
        elif field == 8:
            choice_ids.extend(_repeated_uint(item))
    return Event(pos, event_id, tuple(choice_ids))


def _box(value: object) -> Box:
    """解析單一寶箱；兼容 p_pos 子訊息及線性格索引兩種實抓格式。"""
    if isinstance(value, int):
        return Box(_index_to_pos(int(value)), 0, int(value))
    if not isinstance(value, (bytes, bytearray)):
        return Box(None, 0)

    fields = codec.walk(bytes(value))
    pos = None
    scalar_fields: dict[int, int] = {}
    for field, item in fields:
        if isinstance(item, int):
            scalar_fields[field] = int(item)
        elif isinstance(item, (bytes, bytearray)) and pos is None:
            pos = _pos(item)

    # p_box 常見格式為 {pos#1, open_times#2}；若是直接的 p_pos，
    # 則 field 1/2 就是 x/y，不應把 y 誤當成開啟次數。
    if pos is None and 1 in scalar_fields and 2 in scalar_fields:
        pos = (scalar_fields[1], scalar_fields[2])
        times = 0
    else:
        times = (scalar_fields.get(2) or scalar_fields.get(3)
                 or scalar_fields.get(4) or 0)
    return Box(pos, int(times), int(scalar_fields.get(1) or 0))


def _parse_boxes(values: list[object], times: tuple[int, ...]) -> tuple[Box, ...]:
    """把 box 與 box_times 的平行欄位配回同一個寶箱。"""
    boxes = [_box(value) for value in values]
    if len(times) == len(boxes):
        boxes = [Box(box.position, int(open_times), box.box_id)
                 for box, open_times in zip(boxes, times)]
    return tuple(boxes)


def parse_enter(body: bytes) -> State:
    """Parse ``star_pve_enter_s2c`` including repeated packed fields."""
    floor = 0
    floor_status = 0
    code = 0
    positions: list[int] = []
    members: list[Member] = []
    events: list[Event] = []
    box_values: list[object] = []
    box_times: list[int] = []
    for field, value in codec.walk(body):
        if field == F_FLOOR and isinstance(value, int):
            floor = int(value)
        elif field == F_FLOOR_STATUS and isinstance(value, int):
            floor_status = int(value)
        elif field == F_POS_LIST:
            positions.extend(_repeated_uint(value))
        elif field == F_MEMBER:
            members.append(_member(value))
        elif field == F_EVENT:
            events.append(_event(value))
        elif field == F_BOX:
            if isinstance(value, (bytes, bytearray)):
                box_values.append(value)
            else:
                box_values.extend(_repeated_uint(value))
        elif field == F_BOX_TIMES:
            box_times.extend(_repeated_uint(value))
        elif field == F_CODE and isinstance(value, int):
            code = int(value)
    return State(floor, floor_status, tuple(positions), tuple(members),
                 tuple(events), code, _parse_boxes(box_values, tuple(box_times)))


def parse_grid(body: bytes) -> tuple[tuple[int, int] | None, int, int, int]:
    """Return ``(pos, event_id, double_times, code)`` from grid reply."""
    pos = None
    event_id = double_times = code = 0
    for field, value in codec.walk(body):
        if field == 1:
            pos = _pos(value)
        elif field == 2 and isinstance(value, int):
            event_id = int(value)
        elif field == 3 and isinstance(value, int):
            double_times = int(value)
        elif field == 5 and isinstance(value, int):
            code = int(value)
    return pos, event_id, double_times, code


def parse_info(body: bytes) -> dict:
    """Parse the activity progress snapshot used for the completion gate."""
    finish_floor = 0
    floor_rewards: list[int] = []
    for field, value in codec.walk(body):
        if field == 8 and isinstance(value, int):
            finish_floor = int(value)
        elif field == 9 and isinstance(value, int):
            floor_rewards.append(int(value))
    return {"finish_floor": finish_floor,
            "floor_rewards": tuple(floor_rewards)}


def build_pos(x: int, y: int) -> bytes:
    return codec.pb_uint(1, int(x)) + codec.pb_uint(2, int(y))


def build_enter(is_next: bool = False) -> bytes:
    return codec.pb_uint(1, 1 if is_next else 0)


def build_move(start: tuple[int, int], target: tuple[int, int]) -> bytes:
    return codec.pb_msg(1, build_pos(*start)) + codec.pb_msg(2, build_pos(*target))


def build_grid(target: tuple[int, int]) -> bytes:
    return codec.pb_msg(1, build_pos(*target))


def build_choice(pos: tuple[int, int], choice: int = 0) -> bytes:
    return codec.pb_msg(1, build_pos(*pos)) + codec.pb_uint(2, int(choice))


def build_ask_help(pos: tuple[int, int], help_type: int) -> bytes:
    return codec.pb_msg(1, build_pos(*pos)) + codec.pb_uint(2, int(help_type))


def _call(client: WSGameClient, cmd: int, body: bytes, *, timeout: float | None = None) -> bytes:
    """Call a same-command request while also accepting the standard error frame."""
    response_cmd, response = client.call_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout)
    if response_cmd == CMD_ERROR:
        error_code = codec.walk_dict(response).get(1, 0)
        raise StarExploreServerError(int(error_code or 0))
    return response


def _read_state(client: WSGameClient, *, timeout: float | None = None) -> State:
    return parse_enter(_call(client, CMD_ENTER, build_enter(False), timeout=timeout))


def _read_info(client: WSGameClient, *, timeout: float | None = None) -> dict:
    return parse_info(_call(client, CMD_INFO, b"", timeout=timeout))


def _role_position(client: WSGameClient, state: State) -> tuple[int, int] | None:
    creds = getattr(client, "_creds", None)
    role_id = int(getattr(creds, "role_id", 0) or 0)
    for member in state.members:
        if role_id and member.role_id == role_id:
            return member.pos
    return next((member.pos for member in state.members if member.pos), None)


def _index_to_pos(index: int) -> tuple[int, int]:
    return index % GRID_WIDTH + 1, index // GRID_WIDTH + 1


def _pos_to_index(pos: tuple[int, int]) -> int:
    return GRID_WIDTH * (pos[1] - 1) + pos[0] - 1


def _neighbors(pos: tuple[int, int], size: int):
    x, y = pos
    for candidate in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        cx, cy = candidate
        if cx >= 1 and cy >= 1 and _pos_to_index(candidate) < size:
            yield candidate


def _frontier_step(start: tuple[int, int], positions: tuple[int, ...]) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Find the first move through visited cells toward the nearest unopened cell."""
    if not positions:
        return None
    start_index = _pos_to_index(start)
    if not 0 <= start_index < len(positions):
        return None
    if any(_pos_to_index(n) < len(positions) and positions[_pos_to_index(n)] == 2
           for n in _neighbors(start, len(positions))):
        return None

    queue = deque([(start, [])])
    seen = {start}
    while queue:
        current, path = queue.popleft()
        for candidate in _neighbors(current, len(positions)):
            if candidate in seen:
                continue
            seen.add(candidate)
            idx = _pos_to_index(candidate)
            status = positions[idx]
            next_path = path + [candidate]
            if status == 2:
                # Move one cell at a time.  The client sends the role's current
                # position as start_pos and the first visited cell as target_pos;
                # it does not accept the whole A* path in one packet.
                return start, path[0] if path else candidate
            if status == 1:
                queue.append((candidate, next_path))
    return None


def _event_for_position(state: State, pos: tuple[int, int]) -> Event | None:
    return next((event for event in state.events if event.pos == pos), None)


def _resolve_event(client: WSGameClient, event: Event, *, select_choice: int | None,
                   ask_help: bool) -> str:
    if event.pos is None:
        return "event_without_position"
    if event.event_id in (CAVE, DOUBLE_REWARD, GIFT):
        return "passive_event"
    if event.event_id == NEXT_LEVEL:
        return "next_floor"
    if event.event_id == SELECT:
        choice = select_choice
        if choice is None and event.choice_ids:
            choice = event.choice_ids[0]
        choice = int(choice or 0)
    else:
        choice = 0
    if ask_help and event.event_id in (PVE, BOSS):
        # The help request is intentionally opt-in. It leaves the event alive,
        # so the next loop rereads the server state instead of pretending it is
        # completed.
        client.send(CMD_ASK_HELP, build_ask_help(event.pos, 2))
        return "asked_help"
    _call(client, CMD_EVENT_CHOICE, build_choice(event.pos, choice))
    return "choice"


def run(client: WSGameClient, *, device: Optional[str] = None,
        max_steps: int = 100, pace: float = 0.4, max_stuck: int = 4,
        advance_floor: bool = False, select_choice: int | None = None,
        ask_help: bool = False) -> dict:
    """Explore the current floor until stamina/frontier is exhausted.

    Movement and grid opening are confirmed by rereading ``star_pve_enter``.
    The bounded loop is important: if the server rejects a mutation or the H5
    activity changes its map format, the runner stops instead of consuming an
    unbounded number of exploration attempts.
    """
    log = logger
    prefix = f"[star_explore_ws][{device}]" if device else "[star_explore_ws]"
    actions = moves = grids = events = 0
    stuck = 0
    last_signature = None
    pending_event: Event | None = None
    # info/time 在跨服活動切換時可能暫時回 173；不能拿它們判斷活動結束。
    # 真正可操作的狀態以同一條連線上的 star_pve_enter 為準。
    try:
        _read_info(client)
    except StarExploreServerError as exc:
        if exc.code != 173:
            raise

    try:
        state = _read_state(client)
    except StarExploreServerError as exc:
        if exc.code == 173:
            return {"stop_reason": "activity_closed", "actions": 0}
        raise
    if state.code:
        return {"stop_reason": f"enter_code_{state.code}", "floor": state.floor,
                "actions": 0}

    if state.floor == MAX_FLOOR:
        if state.floor_status == 3:
            return {"stop_reason": "final_floor_complete", "floor": state.floor,
                    "actions": 0, "boxes": len(state.boxes)}
        candidates = [box for box in state.boxes
                      if box.position is not None and box.open_times == 0]
        if not candidates:
            reason = "final_floor_no_unopened_box" if state.boxes else \
                "final_floor_boxes_unavailable"
            return {"stop_reason": reason, "floor": state.floor,
                    "actions": 0, "boxes": len(state.boxes)}
        chosen = random.choice(candidates)
        grid_body = _call(client, CMD_GRID, build_grid(chosen.position))
        _grid_pos, event_id, _double_times, code = parse_grid(grid_body)
        if code:
            return {"stop_reason": f"final_box_code_{code}",
                    "floor": state.floor, "actions": 1, "grids": 1,
                    "box_position": chosen.position}
        return {"stop_reason": "final_box_opened", "floor": state.floor,
                "actions": 1, "grids": 1, "events": int(bool(event_id)),
                "box_position": chosen.position,
                "box_open_times": chosen.open_times}

    while actions < max(1, int(max_steps)):
        if state.floor_status == 3:
            if not advance_floor:
                return {"stop_reason": "floor_complete", "floor": state.floor,
                        "actions": actions, "moves": moves, "grids": grids,
                        "events": events}
            _call(client, CMD_ENTER, build_enter(True))
            actions += 1
            time.sleep(max(0.0, pace))
            state = _read_state(client)
            continue

        position = _role_position(client, state)
        event = pending_event if pending_event and pending_event.pos == position else None
        event = event or (_event_for_position(state, position) if position else None)
        if event is not None:
            pending_event = None
            outcome = _resolve_event(client, event, select_choice=select_choice,
                                     ask_help=ask_help)
            actions += 1
            events += 1
            log.info("%s floor=%s pos=%s event=%s outcome=%s",
                     prefix, state.floor, event.pos, event.event_id, outcome)
            if outcome == "next_floor":
                if not advance_floor:
                    return {"stop_reason": "next_floor_waiting", "floor": state.floor,
                            "actions": actions, "events": events}
                _call(client, CMD_ENTER, build_enter(True))
            time.sleep(max(0.0, pace))
            state = _read_state(client)
            continue

        signature = (state.floor, state.floor_status, state.positions, position,
                     tuple((e.pos, e.event_id) for e in state.events))
        if signature == last_signature:
            stuck += 1
            if stuck >= max(1, int(max_stuck)):
                return {"stop_reason": "deadloop", "floor": state.floor,
                        "actions": actions, "moves": moves, "grids": grids,
                        "events": events}
        else:
            stuck = 0
        last_signature = signature

        if position is None:
            return {"stop_reason": "no_role_position", "floor": state.floor,
                    "actions": actions, "moves": moves, "grids": grids,
                    "events": events}

        frontier = _frontier_step(position, state.positions)
        if frontier is None:
            # An unopened cell adjacent to the role is the only safe grid action.
            candidates = [n for n in _neighbors(position, len(state.positions))
                          if state.positions[_pos_to_index(n)] == 2]
            if not candidates:
                return {"stop_reason": "no_frontier", "floor": state.floor,
                        "actions": actions, "moves": moves, "grids": grids,
                        "events": events}
            target = candidates[0]
            grid_body = _call(client, CMD_GRID, build_grid(target))
            _grid_pos, event_id, _double_times, code = parse_grid(grid_body)
            actions += 1
            grids += 1
            if code:
                return {"stop_reason": f"grid_code_{code}", "floor": state.floor,
                        "actions": actions, "moves": moves, "grids": grids,
                        "events": events}
            if event_id:
                pending_event = Event(target, event_id)
            time.sleep(max(0.0, pace))
            state = _read_state(client)
            continue

        start, target = frontier
        client.send(CMD_MOVE, build_move(start, target))
        actions += 1
        moves += 1
        log.debug("%s floor=%s move=%s->%s", prefix, state.floor, start, target)
        time.sleep(max(0.0, pace))
        state = _read_state(client)

    return {"stop_reason": "budget_exhausted", "floor": state.floor,
            "actions": actions, "moves": moves, "grids": grids,
            "events": events}

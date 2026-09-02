from __future__ import annotations

from types import SimpleNamespace

from ws_token import codec
from ws_token import star_explore as se


def _member(role_id: int, x: int, y: int) -> bytes:
    return codec.pb_uint(1, role_id) + codec.pb_msg(4, se.build_pos(x, y))


def _event(x: int, y: int, event_id: int, *choices: int) -> bytes:
    body = codec.pb_msg(1, se.build_pos(x, y)) + codec.pb_uint(2, event_id)
    return body + b"".join(codec.pb_uint(8, choice) for choice in choices)


def _enter(*, positions: list[int], events: list[bytes] = (), floor: int = 29,
           floor_status: int = 1, boxes: list[int] = (),
           box_times: list[int] = ()) -> bytes:
    body = codec.pb_uint(2, floor) + codec.pb_uint(3, floor_status)
    body += b"".join(codec.pb_uint(4, value) for value in positions)
    body += codec.pb_msg(5, _member(7, 1, 1))
    body += b"".join(codec.pb_msg(6, event) for event in events)
    body += b"".join(codec.pb_uint(7, value) for value in boxes)
    body += b"".join(codec.pb_uint(8, value) for value in box_times)
    return body


def test_parse_enter_handles_positions_members_and_events():
    state = se.parse_enter(
        _enter(positions=[1, 2, 1], events=[_event(2, 1, se.SELECT, 11, 12)]))

    assert state.floor == 29
    assert state.floor_status == 1
    assert state.positions == (1, 2, 1)
    assert state.members[0].role_id == 7
    assert state.members[0].pos == (1, 1)
    assert state.events[0].pos == (2, 1)
    assert state.events[0].event_id == se.SELECT
    assert state.events[0].choice_ids == (11, 12)


def test_parse_enter_pairs_final_floor_boxes_with_global_open_counts():
    state = se.parse_enter(_enter(
        positions=[1, 1], floor=30,
        boxes=[0, 1], box_times=[4, 0]))

    assert [(box.position, box.open_times) for box in state.boxes] == [
        ((1, 1), 4), ((2, 1), 0)]



def _box(index: int, box_id: int, role_id: int, role_name: str) -> bytes:
    return (codec.pb_uint(1, index) + codec.pb_uint(2, box_id) +
            codec.pb_uint(3, role_id) + codec.pb_msg(4, role_name.encode()))


def test_parse_enter_decodes_live_selected_box_records_without_position_walk():
    body = (codec.pb_uint(2, 10) + codec.pb_uint(3, 1) +
            codec.pb_msg(7, _box(85, 450018, 123, "玩家")) +
            codec.pb_uint(8, 0))

    state = se.parse_enter(body)

    assert state.boxes[0].index == 85
    assert state.boxes[0].position is None
    assert state.boxes[0].box_id == 450018
    assert state.boxes[0].role_id == 123
    assert state.boxes[0].role_name == "玩家"


def test_build_box_uses_index_field():
    assert codec.walk(se.build_box(85)) == [(1, 85)]


def test_runner_selects_unclaimed_box_index_during_selection_stage():
    class BoxClient:
        _creds = SimpleNamespace(role_id=7)

        def call_for(self, cmd, body, *, expect_cmds, timeout=None):
            assert cmd in (se.CMD_INFO, se.CMD_ENTER, se.CMD_BOX)
            if cmd == se.CMD_INFO:
                return cmd, b""
            if cmd == se.CMD_ENTER:
                body = (codec.pb_uint(2, 10) + codec.pb_uint(3, 1) +
                        codec.pb_msg(7, _box(85, 450018, 123, "玩家")) +
                        codec.pb_uint(8, 0))
                return cmd, body
            return cmd, codec.pb_msg(1, _box(12, 450018, 456, "我"))

        def send(self, cmd, body=b""):
            raise AssertionError((cmd, body))

    result = se.run(BoxClient(), pace=0)

    assert result["stop_reason"] == "box_opened"
    assert result["box_index"] != 85
    assert result["actions"] == 1


def test_parse_info_reads_completed_floor_list():
    body = codec.pb_uint(8, 30) + codec.pb_uint(9, 29) + codec.pb_uint(9, 30)

    assert se.parse_info(body) == {
        "finish_floor": 30,
        "floor_rewards": (29, 30),
    }


def test_builders_match_live_star_explore_field_layout():
    assert codec.walk(se.build_enter(False)) == [(1, 0)]
    assert codec.walk(se.build_grid((2, 3))) == [(1, se.build_pos(2, 3))]
    assert codec.walk(se.build_choice((2, 3), 11)) == [
        (1, se.build_pos(2, 3)), (2, 11)]
    assert codec.walk(se.build_move((1, 1), (2, 1))) == [
        (1, se.build_pos(1, 1)), (2, se.build_pos(2, 1))]


def test_frontier_path_sends_only_the_next_cell():
    # 1=start, 1=visited, 2=unopened.  The move must be (1,1)->(2,1),
    # not a jump directly to (3,1).
    assert se._frontier_step((1, 1), (1, 1, 2)) == ((1, 1), (2, 1))


def test_runner_opens_unclaimed_box_on_final_floor():
    class FinalFloorClient:
        _creds = SimpleNamespace(role_id=7)

        def call_for(self, cmd, body, *, expect_cmds, timeout=None):
            if cmd == se.CMD_INFO:
                return cmd, codec.pb_uint(8, se.MAX_FLOOR)
            if cmd == se.CMD_ENTER:
                enter = (codec.pb_uint(2, se.MAX_FLOOR) + codec.pb_uint(3, 1) +
                         codec.pb_msg(7, _box(85, 450018, 123, "玩家")))
                return cmd, enter
            if cmd == se.CMD_BOX:
                return cmd, codec.pb_msg(1, _box(12, 450018, 456, "我"))
            raise AssertionError(cmd)

        def send(self, cmd, body=b""):
            raise AssertionError((cmd, body))

    result = se.run(FinalFloorClient(), pace=0)

    assert result["stop_reason"] == "box_opened"
    assert result["box_index"] != 85
    assert result["actions"] == 1



def test_runner_advances_after_box_selection_status():
    class AdvanceClient:
        _creds = SimpleNamespace(role_id=7)

        def __init__(self):
            self.enter_next = 0
            self.floor = 10

        def call_for(self, cmd, body, *, expect_cmds, timeout=None):
            if cmd == se.CMD_INFO:
                return cmd, b""
            if cmd == se.CMD_ENTER:
                fields = codec.walk(body)
                is_next = dict(fields).get(1, 0)
                if is_next:
                    self.enter_next += 1
                    self.floor = 11
                    return cmd, _enter(positions=[1, 2], floor=11, floor_status=1)
                if self.floor == 11:
                    return cmd, _enter(positions=[1, 2], floor=11, floor_status=1)
                return cmd, (codec.pb_uint(2, 10) + codec.pb_uint(3, 2) +
                             codec.pb_msg(7, _box(85, 450018, 123, "玩家")))
            raise AssertionError(cmd)

        def send(self, cmd, body=b""):
            raise AssertionError((cmd, body))

    client = AdvanceClient()
    result = se.run(client, pace=0, max_steps=1, advance_floor=True)

    assert client.enter_next >= 1
    assert result["floor"] == 11


def test_runner_opens_adjacent_unexplored_cell_then_stops_at_frontier():
    class FakeClient:
        _creds = SimpleNamespace(role_id=7)

        def __init__(self):
            self.enter_count = 0
            self.calls = []

        def call_for(self, cmd, body, *, expect_cmds, timeout=None):
            self.calls.append((cmd, body))
            if cmd == se.CMD_INFO:
                return cmd, codec.pb_uint(8, 0)
            if cmd == se.CMD_ENTER:
                self.enter_count += 1
                positions = [1, 2] if self.enter_count == 1 else [1, 1]
                return cmd, _enter(positions=positions)
            if cmd == se.CMD_GRID:
                return cmd, codec.pb_msg(1, se.build_pos(2, 1))
            raise AssertionError(cmd)

        def send(self, cmd, body=b""):
            self.calls.append((cmd, body))

    client = FakeClient()
    result = se.run(client, pace=0, max_steps=3)

    assert result["grids"] == 1
    assert result["stop_reason"] == "no_frontier"
    assert [cmd for cmd, _body in client.calls].count(se.CMD_GRID) == 1

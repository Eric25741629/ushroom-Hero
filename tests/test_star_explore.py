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
           floor_status: int = 1) -> bytes:
    body = codec.pb_uint(2, floor) + codec.pb_uint(3, floor_status)
    body += b"".join(codec.pb_uint(4, value) for value in positions)
    body += codec.pb_msg(5, _member(7, 1, 1))
    body += b"".join(codec.pb_msg(6, event) for event in events)
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


def test_runner_treats_all_floors_finished_as_normal_completion():
    class CompleteClient:
        def call_for(self, cmd, body, *, expect_cmds, timeout=None):
            assert cmd == se.CMD_INFO
            return cmd, codec.pb_uint(8, se.MAX_FLOOR)

    result = se.run(CompleteClient(), pace=0)

    assert result["stop_reason"] == "activity_complete"
    assert result["actions"] == 0


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

"""Pure-WS tests for the 菇菇武道會膜拜冠軍 command."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token import kungfu_race as kr  # noqa: E402
from ws_token.client import WSTimeoutError  # noqa: E402


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        self.calls.append((cmd, bytes(body), tuple(expect_cmds), timeout))
        if self.error:
            raise self.error
        return self.reply


def test_worship_request_is_empty_and_decodes_server_counter():
    client = FakeClient((kr.CMD_WORSHIP, codec.pb_uint(1, 21312)))

    result = kr.worship(client)

    assert result.success is True
    assert result.response_cmd == kr.CMD_WORSHIP
    assert result.worship == 21312
    assert client.calls == [
        (kr.CMD_WORSHIP, b"", (kr.CMD_WORSHIP, kr.CMD_ERROR), 6.0)
    ]


def test_worship_server_rejection_is_safe_noop():
    client = FakeClient((kr.CMD_ERROR, codec.pb_uint(1, 173)))

    result = kr.worship(client)

    assert result.success is False
    assert result.response_cmd == kr.CMD_ERROR
    assert result.error_code == 173


def test_worship_timeout_is_safe_noop():
    result = kr.worship(FakeClient(error=WSTimeoutError("no response")))

    assert result.success is False
    assert result.error == "no response"


def test_parse_worship_rejects_unexpected_command():
    result = kr.parse_worship(12345, b"")

    assert result.success is False
    assert result.error == "unexpected response command"

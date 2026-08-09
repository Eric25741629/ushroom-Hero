from __future__ import annotations

from game_actions.executors.single_backend_executor import (
    run_dragon_realm,
    run_fannaoxiao,
)


def test_dragon_realm_adapter_preserves_client_arguments():
    seen: list[str] = []

    result = run_dragon_realm(
        "device",
        "web-device",
        action=lambda: seen.append("called") or "done",
    )

    assert result == "done"
    assert seen == ["called"]


def test_fannaoxiao_adapter_preserves_client_arguments():
    seen: list[str] = []

    result = run_fannaoxiao(
        "device",
        "web-device",
        action=lambda: seen.append("called") or "done",
    )

    assert result == "done"
    assert seen == ["called"]

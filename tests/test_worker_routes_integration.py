"""Integration tests for the Flask worker-facing routes in
``control_panel_app`` — verifies poll/report/webhook plumbing without
touching the network and exercises the concurrent-queue path that the
H4 fix targeted.
"""
from __future__ import annotations

import importlib
import sys
import threading
import types
import unittest
from typing import Any, Dict, List


def _install_lightweight_stubs() -> None:
    """Make sure ``control_panel_app`` can be imported in a headless test.

    These stubs match what other tests in this repo install — they replace
    ADB / detector / CNN heavy modules with no-op shims.
    """
    if 'adb_operations' not in sys.modules:
        adb_stub = types.ModuleType('adb_operations')
        adb_stub.run_adb = lambda *args, **kwargs: ''
        sys.modules['adb_operations'] = adb_stub

    if 'game_state.detector' not in sys.modules:
        detector_stub = types.ModuleType('game_state.detector')
        detector_stub.stage_by_str = lambda d, ocr, img: 'unknown'
        sys.modules['game_state.detector'] = detector_stub

    if 'new_cnn.cnn_model' not in sys.modules:
        cnn_stub = types.ModuleType('new_cnn.cnn_model')
        cnn_stub.load_cnn_model = lambda path: None
        sys.modules['new_cnn.cnn_model'] = cnn_stub


class TestWorkerRoutesIntegration(unittest.TestCase):
    """Route-level integration tests for the worker-facing API.

    Each test resets the master's in-memory queues so they don't leak
    state across cases.
    """

    @classmethod
    def setUpClass(cls) -> None:
        _install_lightweight_stubs()
        # Other tests in this suite (test_bootstrap_api_services) install a
        # bare ModuleType stub for ``control_panel_app`` that lacks ``.app``.
        # Force a real import by evicting that stub if present.
        existing = sys.modules.get('control_panel_app')
        if existing is not None and not hasattr(existing, 'app'):
            del sys.modules['control_panel_app']
        cls.control_panel_app = importlib.import_module('control_panel_app')

    def setUp(self) -> None:
        self.cpa = type(self).control_panel_app
        self.client = self.cpa.app.test_client()
        # Reset shared module-level state under the lock so we don't
        # inherit anything from prior tests.
        with self.cpa._commands_lock:
            self.cpa._remote_commands.clear()
            self.cpa._worker_webhook_endpoints.clear()
            self.cpa._global_commands["refresh_needed"] = False

    # ------------------------------------------------------------------
    # Test 1 — valid worker polling with no queued commands returns OK.
    # ------------------------------------------------------------------
    def test_poll_commands_with_valid_worker_returns_empty_commands(self) -> None:
        # Arrange
        payload = {"worker_id": "school_laptop", "ips": ["emulator-5554"]}

        # Act
        resp = self.client.post("/api/poll_commands", json=payload)
        body = resp.get_json()

        # Assert
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["commands"], {})
        self.assertIn("global_commands", body)

    # ------------------------------------------------------------------
    # Test 2 — report_status registers a webhook, and _push_to_worker_webhook
    # finds + uses that endpoint (with requests.post monkeypatched).
    # ------------------------------------------------------------------
    def test_report_status_registers_webhook_and_push_finds_endpoint(self) -> None:
        # Arrange — capture the URL that _push_to_worker_webhook hits.
        captured: Dict[str, Any] = {}

        class _FakeResp:
            ok = True

        def _fake_post(url, json=None, timeout=None, verify=None):  # noqa: A002
            captured["url"] = url
            captured["json"] = json
            return _FakeResp()

        original_post = self.cpa.requests.post
        self.cpa.requests.post = _fake_post
        try:
            # Act — worker registers itself via report_status.
            report_payload = {
                "__META__": {
                    "worker_id": "school_laptop",
                    "webhook_url": "http://worker.example/cmd",
                }
            }
            resp = self.client.post("/api/report_status", json=report_payload)

            # Assert — registration succeeded.
            self.assertEqual(resp.status_code, 200)
            with self.cpa._commands_lock:
                self.assertIn("school_laptop", self.cpa._worker_webhook_endpoints)
                self.assertEqual(
                    self.cpa._worker_webhook_endpoints["school_laptop"]["url"],
                    "http://worker.example/cmd",
                )

            # Act — direct push uses the registered endpoint.
            pushed = self.cpa._push_to_worker_webhook(
                "school_laptop", {"hello": "world"}
            )

            # Assert — pushed via the captured URL.
            self.assertTrue(pushed)
            self.assertEqual(captured["url"], "http://worker.example/cmd")
            self.assertEqual(captured["json"], {"hello": "world"})
        finally:
            self.cpa.requests.post = original_post

    # ------------------------------------------------------------------
    # Test 3 — malicious webhook URL (non http/https) must NOT be registered.
    # ------------------------------------------------------------------
    def test_report_status_rejects_non_http_webhook_url(self) -> None:
        # Arrange — register a "good" worker first so the dict is non-empty.
        first = {
            "__META__": {
                "worker_id": "good_worker",
                "webhook_url": "https://worker.example/cmd",
            }
        }
        r1 = self.client.post("/api/report_status", json=first)
        self.assertEqual(r1.status_code, 200)

        # Act — attempt three malicious schemes.
        for bad_url in [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ftp://internal/private",
        ]:
            payload = {
                "__META__": {
                    "worker_id": f"bad_worker_{bad_url[:5]}",
                    "webhook_url": bad_url,
                }
            }
            r = self.client.post("/api/report_status", json=payload)
            self.assertEqual(r.status_code, 200)

        # Assert — only the good worker is in the registry.
        with self.cpa._commands_lock:
            self.assertEqual(
                set(self.cpa._worker_webhook_endpoints.keys()),
                {"good_worker"},
            )

    # ------------------------------------------------------------------
    # Test 4 — H4 regression: concurrent queue_command must not corrupt
    # the _remote_commands dict.
    # ------------------------------------------------------------------
    def test_queue_command_concurrent_writes_have_no_race(self) -> None:
        # Arrange — also monkeypatch requests.post so the webhook push
        # path (which queue_command triggers via _push_remote_command_if_possible)
        # doesn't touch the network.
        original_post = self.cpa.requests.post

        def _noop_post(*args, **kwargs):
            class _R:
                ok = False
            return _R()

        self.cpa.requests.post = _noop_post

        try:
            NUM_THREADS = 20
            COMMANDS_PER_THREAD = 25
            worker_id = "stress_worker"
            errors: List[BaseException] = []
            done_event = threading.Event()

            def _writer(thread_idx: int) -> None:
                try:
                    for j in range(COMMANDS_PER_THREAD):
                        ip = f"{worker_id}:emu-{thread_idx}"
                        key = f"key_{j}"
                        self.cpa.queue_command(ip, key, j)
                except BaseException as exc:  # noqa: BLE001 - test capture
                    errors.append(exc)

            threads = [
                threading.Thread(target=_writer, args=(i,))
                for i in range(NUM_THREADS)
            ]

            # Act — fire them all off.
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            done_event.set()

            # Assert — no exception during concurrent mutation.
            self.assertEqual(
                errors, [], f"Concurrent queue_command raised: {errors}"
            )

            # Assert — every remote_id we wrote to has all its keys.
            with self.cpa._commands_lock:
                # We wrote NUM_THREADS distinct remote_ids
                expected_ids = {
                    f"{worker_id}:emu-{i}" for i in range(NUM_THREADS)
                }
                actual_ids = {
                    rid for rid in self.cpa._remote_commands.keys()
                    if rid.startswith(f"{worker_id}:")
                }
                self.assertEqual(actual_ids, expected_ids)

                for rid in expected_ids:
                    cmds = self.cpa._remote_commands[rid]
                    # Every key_{j} for j in 0..COMMANDS_PER_THREAD-1
                    self.assertEqual(
                        set(cmds.keys()),
                        {f"key_{j}" for j in range(COMMANDS_PER_THREAD)},
                        f"remote_id {rid} missing keys: {sorted(cmds.keys())}",
                    )
        finally:
            self.cpa.requests.post = original_post

    # ------------------------------------------------------------------
    # Test 5 — report_status must pass the worker-reported status through
    # (掉線判離線 fix: master 以前永遠把 remote 裝置顯示成 ONLINE).
    # ------------------------------------------------------------------
    def test_report_status_passes_status_through(self) -> None:
        import bot_state

        remote_id = "w_test_status:emulator-9999"

        def _cleanup() -> None:
            with bot_state._global_lock:
                bot_state._states.pop(remote_id, None)
                bot_state._pause_events.pop(remote_id, None)
                bot_state._locks.pop(remote_id, None)

        _cleanup()
        try:
            # Act — worker reports the device as OFFLINE.
            payload = {
                remote_id: {
                    "status": "OFFLINE",
                    "task": "休眠中",
                    "step": "thread exit",
                    "logs": [],
                }
            }
            resp = self.client.post("/api/report_status", json=payload)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                bot_state.get_all_states()[remote_id]["status"], "OFFLINE"
            )

            # Act — worker reports it back ONLINE → master must flip back.
            payload[remote_id]["status"] = "ONLINE"
            resp = self.client.post("/api/report_status", json=payload)
            self.assertEqual(resp.status_code, 200)
            st = bot_state.get_all_states()[remote_id]
            self.assertEqual(st["status"], "ONLINE")
            self.assertNotIn("offline_since", st)
        finally:
            _cleanup()


if __name__ == "__main__":
    unittest.main()

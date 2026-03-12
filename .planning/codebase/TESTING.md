# Testing Notes

## Framework And Entry Points
- The repository currently mixes formal tests and ad-hoc executable test scripts.
- `tests/test_smoke_config_api.py` uses `unittest` (`unittest.TestCase`, `setUpClass`, `setUp`, `tearDown`).
- Several files named like tests are plain scripts driven by `if __name__ == "__main__":`, for example `test_json_manager.py` and `test_server_brain.py`.
- Assertion-style unit tests are also present in script-like modules such as `test_item_placement_guards.py`.

## Setup Observed
- Runtime dependencies in `requirements.txt` include `Flask` and `flask-cors`; no explicit pytest dependency is declared there.
- No tracked top-level test runner config was found via `git ls-files` filtering for `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`, `.coveragerc`, or `conftest.py`.
- `tests/test_smoke_config_api.py` constructs a Flask test client from `control_panel_app.app`.
- The smoke tests patch import-time dependencies by inserting stubs into `sys.modules` (`adb_operations`, `game_state.detector`, `new_cnn.cnn_model`).

## Structure
- Primary test directory: `tests/`.
- Observed files there: `tests/test_smoke_config_api.py` and `tests/mock_item_placement_rl_test.py`.
- Additional root-level test-like files: `test_item_placement_guards.py`, `test_json_manager.py`, `test_server_brain.py`, `test_stage_debug.py`, `test_mount_rush.py`, `test_minigame_ocr.py`, `dashboard_test.py`.
- Domain-specific test scripts also exist under `miner/scripts/` (for example `miner/scripts/test_void_logic.py`, `miner/scripts/test_streaming.py`).
- Third-party/vendor test trees exist under `OCR/PaddleOCR/tests/`; treat those separately from project-owned coverage.

## Mocks And Isolation Patterns
- Module stubbing through `types.ModuleType` + `sys.modules.setdefault(...)` is actively used in `tests/test_smoke_config_api.py`.
- Temporary filesystem isolation is used with `tempfile.TemporaryDirectory()` in `tests/test_smoke_config_api.py`.
- Global config file redirection is performed by replacing `config_manager.CONFIG_FILE` during test setup and restoring it in teardown.
- API behavior is verified through HTTP-level calls (`client.post`, `client.get`) rather than direct function invocation.

## Current Coverage Signals
- Strongest automated signal currently appears to be API smoke behavior around config endpoints in `tests/test_smoke_config_api.py`.
- Core execution paths (device orchestration, OCR flows, worker sync, startup recovery) in `bot_state.py`, `game_initialization.py`, `img_tools.py`, and `new_main_before20250514.py` appear only lightly covered by formal assertions.
- Presence of many manual/interactive scripts indicates substantial exploratory testing over deterministic CI-style suites.
- Existing runtime logs in `logs/` (for example `logs/emulator-5554.log`, `logs/emulator-5558.log`) provide operational evidence but are not coverage metrics.

## Practical Guidance For New Tests
- Prefer new deterministic tests under `tests/` with isolated temp files and explicit dependency stubs.
- Keep external dependencies (ADB, OCR server, network time APIs) mocked at module boundaries.
- Separate integration-like scripts from unit tests by naming and location to improve runner clarity.
- If adopting pytest later, add a single repo-level config and migrate script-style tests incrementally.

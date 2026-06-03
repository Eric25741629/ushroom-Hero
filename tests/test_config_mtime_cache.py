"""Tests for the process-wide mtime cache added to config_manager.load_config.

The cache must:
  a) avoid re-reading / re-writing the config file when nothing changed,
  b) pick up external file changes (mtime bump),
  c) hand out deep copies so caller mutation never corrupts the cache,
  d) be invalidated by in-process writers (save_config / update_*).
"""

import json
import os

import pytest

import config_manager


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point config_manager at a fresh temp file and reset cache state."""
    cfg_path = tmp_path / "bot_config.json"
    base = {
        "devices": {"emulator-5554": {"name": "alpha", "backend": "adb"}},
        "global": {"mode": "master"},
    }
    cfg_path.write_text(json.dumps(base, ensure_ascii=False, indent=4), encoding="utf-8")

    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(cfg_path))
    # Reset the module-level cache so previous tests can't leak a hit.
    monkeypatch.setattr(config_manager, "_config_cache", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_mtime_ns", None, raising=False)
    return cfg_path


def _count_opens(monkeypatch):
    """Patch builtins.open to count how many times the config file is opened.

    Returns a mutable dict with 'reads' and 'writes' counters scoped to the
    config file path only (ignores backup / unrelated files).
    """
    import builtins

    real_open = builtins.open
    counts = {"reads": 0, "writes": 0}

    def counting_open(file, mode="r", *args, **kwargs):
        try:
            same = os.path.abspath(str(file)) == os.path.abspath(config_manager.CONFIG_FILE)
        except Exception:
            same = False
        if same:
            if any(c in mode for c in ("w", "a", "x", "+")):
                counts["writes"] += 1
            else:
                counts["reads"] += 1
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)
    return counts


def test_second_load_does_not_reread_or_rewrite(temp_config, monkeypatch):
    """Two consecutive loads with no file change => file opened only on the first."""
    counts = _count_opens(monkeypatch)

    first = config_manager.load_config()
    assert first["devices"]["emulator-5554"]["name"] == "alpha"

    reads_after_first = counts["reads"]
    writes_after_first = counts["writes"]
    assert reads_after_first >= 1  # first call must actually read the file

    second = config_manager.load_config()
    assert second["devices"]["emulator-5554"]["name"] == "alpha"

    # Cache hit: no additional opens of the config file.
    assert counts["reads"] == reads_after_first, "2nd load should not re-read the config file"
    assert counts["writes"] == writes_after_first, "2nd load should not re-write the config file"


def test_mtime_bump_picks_up_new_content(temp_config, monkeypatch):
    first = config_manager.load_config()
    assert first["devices"]["emulator-5554"]["name"] == "alpha"

    # Rewrite the file with new content and bump mtime forward to guarantee a change.
    new_data = {
        "devices": {"emulator-5554": {"name": "beta", "backend": "adb"}},
        "global": {"mode": "master"},
    }
    temp_config.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    st = os.stat(temp_config)
    # Push mtime 10s into the future so the change is unambiguous even on
    # coarse-resolution filesystems.
    os.utime(temp_config, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))

    second = config_manager.load_config()
    assert second["devices"]["emulator-5554"]["name"] == "beta", (
        "mtime bump should invalidate the cache and return fresh content"
    )


def test_mutating_returned_dict_does_not_corrupt_cache(temp_config):
    first = config_manager.load_config()
    # Caller mutates the returned dict (this is what real callers do).
    first["devices"]["emulator-5554"]["name"] = "MUTATED"
    first["global"]["mode"] = "worker"
    first["devices"]["injected"] = {"name": "ghost"}

    second = config_manager.load_config()
    assert second["devices"]["emulator-5554"]["name"] == "alpha", (
        "cached result must be deep-copied; caller mutation must not leak"
    )
    assert second["global"]["mode"] == "master"
    assert "injected" not in second["devices"]


def test_writer_invalidates_cache(temp_config, monkeypatch):
    """save_config (and update_device_config) must force the next load to re-read."""
    first = config_manager.load_config()
    assert first["devices"]["emulator-5554"]["name"] == "alpha"

    # Persist a change via the public update path.
    config_manager.update_device_config("emulator-5554", {"name": "renamed"})

    after = config_manager.load_config()
    assert after["devices"]["emulator-5554"]["name"] == "renamed", (
        "writer must invalidate cache so subsequent load reflects the write"
    )


def test_load_after_save_config_reflects_change(temp_config):
    config_manager.load_config()  # warm the cache

    cfg = {
        "devices": {"emulator-5554": {"name": "gamma", "backend": "web_h5"}},
        "global": {"mode": "master"},
    }
    config_manager.save_config(cfg)

    reloaded = config_manager.load_config()
    assert reloaded["devices"]["emulator-5554"]["name"] == "gamma"
    assert reloaded["devices"]["emulator-5554"]["backend"] == "web_h5"


def test_auto_complete_runs_once_then_caches(temp_config, monkeypatch):
    """A config missing keys triggers a one-time rewrite; the next load is a hit
    (no further rewrite), proving we re-stat after the auto-complete rewrite."""
    # Write a config missing the 'global' subkeys so auto-complete kicks in.
    minimal = {"devices": {"emulator-5554": {"name": "alpha"}}}
    temp_config.write_text(
        json.dumps(minimal, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    monkeypatch.setattr(config_manager, "_config_cache", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_mtime_ns", None, raising=False)

    counts = _count_opens(monkeypatch)

    first = config_manager.load_config()
    assert "global" in first
    assert first["global"]["mode"] == "master"
    # The auto-complete path must have rewritten the file at least once.
    assert counts["writes"] >= 1

    writes_after_first = counts["writes"]
    reads_after_first = counts["reads"]

    second = config_manager.load_config()
    assert second["global"]["mode"] == "master"
    # No infinite re-read/re-write loop: second call is a pure cache hit.
    assert counts["writes"] == writes_after_first, "auto-complete must not rewrite on cache hit"
    assert counts["reads"] == reads_after_first, "cache hit must not re-read"

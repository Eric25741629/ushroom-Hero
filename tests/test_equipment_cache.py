"""Equipment cache: feed lamp drops, persist, look up."""
from __future__ import annotations

from pathlib import Path

from utils.equipment_cache import (
    EquipmentCache,
    parse_equipment_lamp_drops,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lamp_drops_0504_20lamps.bin"


def test_parse_equipment_lamp_drops_finds_equipment_only():
    """The 20-lamp capture should yield ≥1 equipment entries with valid
    template_ids (rarity 1..11 + slot 1..10). Non-equipment lamp entries
    are filtered out."""
    body = FIXTURE.read_bytes()
    entries = parse_equipment_lamp_drops(body)
    assert len(entries) >= 1
    for e in entries:
        assert 1 <= e["rarity"] <= 11
        assert 1 <= e["slot"] <= 10
        assert e["uid"] is not None
        assert e["template_id"] is not None
        # First-lamp fixture sanity: 41015001 should decode to 稀有 鞋 item=15001
        if e["template_id"] == 41015001:
            assert e["rarity_name"] == "稀有"
            assert e["slot_name"] == "鞋"
            # base_stats: 防禦/攻擊/生命 confirmed against fixture
            assert e["base_stats"][1024] == 774
            assert e["base_stats"][1001] == 2064
            assert e["base_stats"][1002] == 48288
            # affixes: 反擊 (1017) = 132 → 1.32%
            assert e["affixes"][1017] == 132


def test_cache_feed_then_lookup(tmp_path):
    cache = EquipmentCache(path=tmp_path / "equipment_cache.json")
    body = FIXTURE.read_bytes()
    added = cache.feed_lamp_drop_body(body)
    assert added >= 1
    entries = parse_equipment_lamp_drops(body)
    sample = entries[0]
    looked_up = cache.lookup_by_uid(sample["uid"])
    assert looked_up is not None
    assert looked_up["template_id"] == sample["template_id"]


def test_cache_persists_and_reloads(tmp_path):
    """Writing then reloading the cache returns the same entries."""
    path = tmp_path / "equipment_cache.json"
    cache = EquipmentCache(path=path)
    body = FIXTURE.read_bytes()
    cache.feed_lamp_drop_body(body)
    sample_uid = next(iter(cache.by_uid))
    expected = cache.by_uid[sample_uid]

    fresh = EquipmentCache(path=path)
    fresh.load()
    assert sample_uid in fresh.by_uid
    assert fresh.by_uid[sample_uid] == expected


def test_cache_idempotent_re_feed_doesnt_grow(tmp_path):
    """Feeding the same body twice doesn't add duplicates."""
    cache = EquipmentCache(path=tmp_path / "equipment_cache.json")
    body = FIXTURE.read_bytes()
    cache.feed_lamp_drop_body(body)
    first = len(cache.by_uid)
    cache.feed_lamp_drop_body(body)
    assert len(cache.by_uid) == first


def test_cache_stats_reports_rarity_and_slot_counts(tmp_path):
    cache = EquipmentCache(path=tmp_path / "equipment_cache.json")
    cache.feed_lamp_drop_body(FIXTURE.read_bytes())
    stats = cache.stats()
    assert stats["total"] == len(cache.by_uid)
    assert sum(stats["by_rarity"].values()) == stats["total"]
    assert sum(stats["by_slot"].values()) == stats["total"]


def test_describe_renders_human_readable(tmp_path):
    cache = EquipmentCache(path=tmp_path / "equipment_cache.json")
    cache.feed_lamp_drop_body(FIXTURE.read_bytes())
    sample = next(iter(cache.by_uid.values()))
    desc = cache.describe(sample)
    # Format like "稀有 鞋 [反擊=1.32%]"
    assert sample["rarity_name"] in desc
    assert sample["slot_name"] in desc
    if sample["affixes"]:
        assert "%" in desc


def test_empty_body_returns_no_entries():
    assert parse_equipment_lamp_drops(b"") == []


def test_cache_load_missing_file_yields_empty(tmp_path):
    cache = EquipmentCache(path=tmp_path / "nonexistent.json")
    cache.load()
    assert cache.by_uid == {}
    assert cache.stats()["total"] == 0

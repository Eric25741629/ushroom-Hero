"""靜態 config 中文名查表 — 神器附魔石 / 守護靈 / 屬性 的編號→中文名。

名稱表是遊戲客戶端的靜態資料（改版才變），由 ``tools/dump_config_names.py``
一次性 CDP dump 成 ``data/config_names.json``，之後純 WS 讀倉庫就**不必再開瀏覽器**。

JSON 格式（鍵一律字串）::

    {"spirit": {"<cfg>": "中文名"},
     "attr":   {"<id>":  "屬性名"},
     "suit":   {"<id>":  "套裝名"},
     "quality":{"<q>":   "品質名"}}

查不到一律回原編號的字串，永不拋例外，dashboard 不會因缺名而壞掉。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_NAMES_PATH = Path(__file__).resolve().parent.parent / "data" / "config_names.json"


@lru_cache(maxsize=1)
def _tables() -> dict[str, dict[str, str]]:
    """載入並快取 config_names.json（utf-8-sig）。缺檔/壞檔回空表，不拋例外。"""
    try:
        raw = json.loads(_NAMES_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        logger.warning("config_names.json 不存在（%s）— 名稱將回退為編號；"
                       "請跑 tools/dump_config_names.py 重新 dump", _NAMES_PATH)
        return {}
    except Exception as exc:  # 壞 JSON 等
        logger.warning("config_names.json 載入失敗：%s — 名稱回退為編號", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _lookup(table: str, key: int | str) -> str:
    """查 ``table`` 的 ``key``；查不到回 ``str(key)``。"""
    val = _tables().get(table, {}).get(str(key))
    return val if val else str(key)


def spirit_name(cfg: int | str) -> str:
    """守護靈 config_id → 中文名。"""
    return _lookup("spirit", cfg)


def attr_name(attr_id: int | str) -> str:
    """屬性詞條 id → 中文名。"""
    return _lookup("attr", attr_id)


def suit_name(suit_id: int | str) -> str:
    """神器附魔石套裝編號 → 中文套裝名。"""
    return _lookup("suit", suit_id)


def quality_name(quality: int | str) -> str:
    """品質編號 → 中文品質名（普通/稀有/…）。"""
    return _lookup("quality", quality)


def reload() -> None:
    """清掉快取，下次查詢重讀檔（dump 更新後可呼叫）。"""
    _tables.cache_clear()


if __name__ == "__main__":  # 簡易自我檢查：缺檔時回退為編號、不拋例外
    reload()
    assert spirit_name(999999) == "999999", "缺名應回退為編號字串"
    assert attr_name("12") == _lookup("attr", 12)
    print("config_names self-check ok; tables loaded:",
          {k: len(v) for k, v in _tables().items()})

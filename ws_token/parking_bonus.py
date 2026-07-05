"""車位加成公式（純函式）。

依 `docs/protocol/PARKING_DESIGN_CATALOG.json` 的裝飾說明文字，
把家園車位裝飾清單換算成四類車位加成：
菇車幣(coin) / 改裝點(mod) / 奇遇(spec) / 保護(protect)。

本模組為純函式：只依賴標準庫，不 import device_wrapper / cv2 / torch /
playwright / bot_state，import 時不做任何檔案讀取（catalog 為 lazy 載入）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 說明文字關鍵字 -> 加成類別。同一段落若含多個關鍵字，各自加同一個 parm 值。
_KEYWORD_CATEGORY: list[tuple[str, str]] = [
    ("菇車幣", "coin"),
    ("改裝點", "mod"),
    ("奇遇", "spec"),
    ("保護", "protect"),
]

# catalog 路徑：以 repo root 為基準，與 cwd 無關。
_CATALOG_PATH = Path(__file__).resolve().parents[1] / "docs" / "protocol" / "PARKING_DESIGN_CATALOG.json"

# lazy 快取：(id, level) -> 四類加成；id -> level 0 加成（fallback）。
_CATALOG: dict[tuple[int, int], dict[str, int]] | None = None
_CATALOG_LEVEL0: dict[int, dict[str, int]] | None = None


def parse_desc_bonus(desc: str, parm: list | None) -> dict[str, int]:
    """把單條裝飾說明文字換算成四類加成。

    以 `##N` 標記切段，取每個標記「前面」那段文字判斷關鍵字：
    含「菇車幣」加到 coin、「改裝點」加 mod、「奇遇」加 spec、「保護」加 protect，
    加的值為 parm[N-1]。parm 缺漏或索引越界一律當 0。
    純戰鬥屬性（攻擊/生命/防禦）不含上述關鍵字，四類皆 0。
    永遠回傳四個 key。
    """
    bonus = {"coin": 0, "mod": 0, "spec": 0, "protect": 0}
    if not desc:
        return bonus
    parm = parm or []
    # re.split 帶捕獲群組：偶數索引=文字段落、奇數索引=##後的數字 N。
    parts = re.split(r"##(\d+)", desc)
    for i in range(1, len(parts), 2):
        idx = int(parts[i]) - 1  # ##N -> parm[N-1]
        if idx < 0 or idx >= len(parm):
            continue
        try:
            value = int(parm[idx])
        except (TypeError, ValueError):
            continue
        segment = parts[i - 1]  # 標記前一段文字
        for keyword, category in _KEYWORD_CATEGORY:
            if keyword in segment:
                bonus[category] += value
    return bonus


def _load_catalog() -> None:
    """首次呼叫時載入 catalog 並建立快取（import 時不觸發）。"""
    global _CATALOG, _CATALOG_LEVEL0
    if _CATALOG is not None:
        return
    catalog: dict[tuple[int, int], dict[str, int]] = {}
    level0: dict[int, dict[str, int]] = {}
    with open(_CATALOG_PATH, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    for row in data.get("rows", []):
        rid = row.get("id")
        lev = row.get("level")
        if rid is None or lev is None:
            continue
        bonus = parse_desc_bonus(row.get("desc", ""), row.get("desc_parm"))
        catalog[(int(rid), int(lev))] = bonus
        if int(lev) == 0:
            level0[int(rid)] = bonus
    _CATALOG = catalog
    _CATALOG_LEVEL0 = level0


def lot_bonus(skin_list: list[tuple[int, int]]) -> dict[str, int]:
    """加總整個車位裝飾清單的四類加成。

    每個 (id, level) 先查 catalog[(id, level)]，找不到退回 id 的 level 0 加成，
    再找不到就略過該件。回傳四類總和。
    """
    _load_catalog()
    assert _CATALOG is not None and _CATALOG_LEVEL0 is not None  # for type checkers
    total = {"coin": 0, "mod": 0, "spec": 0, "protect": 0}
    for rid, lev in skin_list:
        bonus = _CATALOG.get((int(rid), int(lev)))
        if bonus is None:
            bonus = _CATALOG_LEVEL0.get(int(rid))
        if bonus is None:
            continue
        for category in total:
            total[category] += bonus[category]
    return total

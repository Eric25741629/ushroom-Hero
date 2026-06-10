"""Shared JSON IO helpers.

The bot writes JSON without a BOM (the correct, canonical form), but files
that round-trip through Windows editors / NAS sync can come back carrying a
UTF-8 BOM. Reading those with plain ``utf-8`` leaves a stray ``﻿`` glued
to the first key. ``read_json_bom_safe`` decodes with ``utf-8-sig`` so the BOM
is transparently stripped on read.

Only the read side needs this; writers should keep emitting no-BOM output.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

PathLike = Union[str, Path]


def read_json_bom_safe(path: PathLike) -> Any:
    """Read and parse a JSON file, tolerating an optional UTF-8 BOM."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

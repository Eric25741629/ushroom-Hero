"""Logical destinations the navigator can drive a device to.

Kept separate from navigator.py so TaskSpec modules can import it
without pulling in img_tools / OCR dependencies.
"""
from __future__ import annotations

from enum import Enum


class NavTarget(str, Enum):
    MAIN_PAGE = "main_page"
    LAMP_PAGE = "lamp_page"

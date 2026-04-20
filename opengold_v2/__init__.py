"""
OpenGold V2 - 神燈開裝備模組重構版

保留原有判斷邏輯，改善架構與可維護性
"""

from .config import OpenGoldConfig
from .models import SkillEntry, Equipment, ComparisonResult, OCRItem, ComparisonDetail, ParsedOCRResult, LampState
from .ocr_parser import OCRParser
from .skill_evaluator import SkillEvaluator
from .screenshot_logger import ScreenshotLogger
from .device_detector import DeviceDetector
from .ui_controller import UIController
from .lamp_service import LampService

__all__ = [
    'OpenGoldConfig',
    'SkillEntry',
    'Equipment',
    'ComparisonResult',
    'ComparisonDetail',
    'OCRItem',
    'ParsedOCRResult',
    'LampState',
    'OCRParser',
    'SkillEvaluator',
    'ScreenshotLogger',
    'DeviceDetector',
    'UIController',
    'LampService',
]

__version__ = '2.0.0'

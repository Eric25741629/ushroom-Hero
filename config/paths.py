from pathlib import Path

# Project root: .../config/paths.py -> parent is config/, parent.parent is root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime output folders
OCR_FAILS_DIR = PROJECT_ROOT / "ocr_fails"
OCR_ERRORS_DIR = PROJECT_ROOT / "ocr_errors"
OCR_FAILS_NEW_DIR = PROJECT_ROOT / "ocr_fails_new"
EXTRACTED_CELLS_DIR = PROJECT_ROOT / "extracted_cells"

# Training / dataset folders
DATASET_DIR = PROJECT_ROOT / "dataset"
DATASET_LOW_CONFIDENCE_DIR = DATASET_DIR / "low_confidence"
DATASET_MINES_DIR = DATASET_DIR / "mines"
DATASET_ERROR_DIR = DATASET_DIR / "error"

# String aliases for legacy callsites expecting str paths
OCR_FAILS_DIR_STR = str(OCR_FAILS_DIR)
OCR_ERRORS_DIR_STR = str(OCR_ERRORS_DIR)
OCR_FAILS_NEW_DIR_STR = str(OCR_FAILS_NEW_DIR)
EXTRACTED_CELLS_DIR_STR = str(EXTRACTED_CELLS_DIR)
DATASET_DIR_STR = str(DATASET_DIR)
DATASET_LOW_CONFIDENCE_DIR_STR = str(DATASET_LOW_CONFIDENCE_DIR)
DATASET_MINES_DIR_STR = str(DATASET_MINES_DIR)
DATASET_ERROR_DIR_STR = str(DATASET_ERROR_DIR)

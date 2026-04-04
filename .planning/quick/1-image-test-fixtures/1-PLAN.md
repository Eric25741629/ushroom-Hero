# Quick Task 1 Plan: 測試圖片資料夾與預期行為

## Goal
新增可放測試圖片的固定資料夾，並提供可測試的預期行為（圖片檔過濾與排序），讓測試可穩定驗證。

## Task 1
- files: `config/paths.py`
- action: 新增 `TEST_IMAGES_DIR` / `TEST_IMAGES_DIR_STR` 與 `iter_test_images()`。
- verify: 函式可在資料夾不存在時回傳空清單，存在時只回傳圖片副檔名檔案且排序穩定。
- done: constants 與 helper 已加入。

## Task 2
- files: `tests/fixtures/images/.gitkeep`
- action: 建立專用測試圖片資料夾並可被版本控制追蹤。
- verify: 路徑存在且 `.gitkeep` 存在。
- done: 目錄與追蹤檔案已建立。

## Task 3
- files: `tests/test_image_fixture_folder.py`
- action: 新增 pytest 測試覆蓋路徑常數與圖片過濾行為。
- verify: `pytest tests/test_image_fixture_folder.py -q` 通過。
- done: 三個測試案例已加入。
from pathlib import Path

from config.paths import TEST_IMAGES_DIR, TEST_IMAGES_DIR_STR, iter_test_images


def test_test_images_dir_points_to_project_fixtures():
    assert TEST_IMAGES_DIR == Path(TEST_IMAGES_DIR_STR)
    assert TEST_IMAGES_DIR.parts[-3:] == ("tests", "fixtures", "images")


def test_iter_test_images_filters_and_sorts(tmp_path):
    (tmp_path / "b.JPG").write_bytes(b"")
    (tmp_path / "a.png").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    result = iter_test_images(tmp_path)

    assert [p.name for p in result] == ["a.png", "b.JPG"]


def test_iter_test_images_on_project_folder_returns_paths_under_fixture_dir():
    result = iter_test_images()
    assert all(p.parent == TEST_IMAGES_DIR for p in result)
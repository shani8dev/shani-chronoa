"""Finding photos: incremental, private, and useful from names and dates alone.

Real files in a temporary Pictures folder. tesseract and ImageMagick may be
absent on a dev machine, and that is a case of its own: the index must still
work from what is there. OCR and EXIF on the real image are shani-testbed's.
"""

import os
import time

import pytest

from shani_chronoa import photo_library


@pytest.fixture
def pictures(tmp_path, monkeypatch):
    root = tmp_path / "Pictures"
    (root / "Shops").mkdir(parents=True)
    (root / "Shops" / "receipt-hardware-store.png").write_bytes(b"\x89PNG fake")
    (root / "beach-with-the-dog.jpg").write_bytes(b"\xff\xd8 fake")
    (root / ".thumbnails").mkdir()
    (root / ".thumbnails" / "hidden.png").write_bytes(b"x")
    (root / "notes.txt").write_text("not a photo")
    monkeypatch.setattr(photo_library, "pictures_dir", lambda: root)
    monkeypatch.setattr(photo_library, "_ocr", lambda path: "")
    monkeypatch.setattr(photo_library, "_exif", lambda path: ("", ""))
    return root


def test_indexes_photos_only_and_finds_them_by_name(pictures):
    r = photo_library.index()
    assert r["indexed"] == 2 and r["total"] == 2, "hidden folders and non-photos are left out"
    [hit] = photo_library.search("hardware receipt")
    assert hit["path"].endswith("receipt-hardware-store.png")
    assert photo_library.search("dog")[0]["path"].endswith("beach-with-the-dog.jpg")
    assert oct(os.stat(photo_library.db_path()).st_mode)[-3:] == "600"


def test_incremental_and_removal(pictures):
    photo_library.index()
    assert photo_library.index()["indexed"] == 0, "nothing changed, nothing is read again"
    beach = pictures / "beach-with-the-dog.jpg"
    time.sleep(0.01)
    beach.write_bytes(b"\xff\xd8 changed")
    assert photo_library.index()["indexed"] == 1
    beach.unlink()
    photo_library.index()
    assert photo_library.search("dog") == [] and photo_library.status()["total"] == 1


def test_dates_are_searchable_in_words(pictures, monkeypatch):
    monkeypatch.setattr(photo_library, "_exif", lambda path: ("2025:12:24 18:02:11", "Pixel 8"))
    photo_library.index()
    assert len(photo_library.search("december 2025")) == 2
    assert len(photo_library.search("pixel")) == 2


def test_text_in_a_photo_is_found(pictures, monkeypatch):
    monkeypatch.setattr(photo_library, "_ocr", lambda path: "TOTAL 450 HAMMER NAILS" if "receipt" in path.name else "")
    photo_library.index()
    [hit] = photo_library.search("hammer")
    assert hit["path"].endswith("receipt-hardware-store.png") and "HAMMER" in hit["text"]


def test_a_batch_limit_leaves_the_rest_for_next_time(pictures):
    r = photo_library.index(limit=1)
    assert r["indexed"] == 1 and r["waiting"] == 1
    assert photo_library.index(limit=1)["indexed"] == 1


def test_the_skill_says_what_to_do_when_nothing_is_indexed(pictures):
    from shani_chronoa.skills import photos
    assert "index the Pictures folder" in photos._run({"action": "search", "query": "dog"})
    assert photos._run({"action": "index"}).startswith("Indexed 2 photos")
    assert "beach-with-the-dog.jpg" in photos._run({"action": "search", "query": "dog"})

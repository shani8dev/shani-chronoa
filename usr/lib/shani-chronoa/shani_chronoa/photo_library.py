"""Find your photos: "the receipt from the hardware shop", "photos with the dog", "pictures from December".

An index of the user's Pictures folder in SQLite, built from what each photo
says about itself, with the system's own tools first:

- the file name and folder;
- EXIF (when it was taken, the camera) through ImageMagick's `identify`;
- any text in it (receipts, signs, screenshots) through tesseract.

When the optional extras are set up, more goes in, with no further download:
the objects and faces in it (`opencv`, the Photos extra), a one-line caption
(`local_vision`, the Eyes extra - slow on a CPU, so only when asked), and an
embedding of all of that (`local_embed`, the Memory extra) so a search finds
by meaning as well as by word.

Indexing is incremental (a file whose size and time are unchanged is not read
again), bounded per call, and private: everything stays in
~/.local/share/shani-chronoa/photos.sqlite, mode 0600.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from shani_chronoa import files

PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff", ".bmp", ".gif"}
MONTHS = ("january february march april may june july august september october november december").split()


def db_path() -> Path:
    return files.data_home() / "shani-chronoa" / "photos.sqlite"


def pictures_dir() -> Path:
    from shani_chronoa import imagegen
    return imagegen.pictures_dir().parent


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    db.execute("CREATE TABLE IF NOT EXISTS photos (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, "
               "taken TEXT, camera TEXT, labels TEXT, text TEXT, caption TEXT, words TEXT)")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS photo_fts USING fts5(path UNINDEXED, words, "
               "tokenize='unicode61 remove_diacritics 2')")
    db.execute("CREATE TABLE IF NOT EXISTS photo_vecs (path TEXT PRIMARY KEY, v BLOB)")
    return db


def _exif(path: Path) -> "tuple[str, str]":
    if not shutil.which("magick"):
        return "", ""
    proc = subprocess.run(["magick", "identify", "-format", "%[EXIF:DateTimeOriginal]|%[EXIF:Model]\\n", f"{path}[0]"],
                          capture_output=True, text=True, timeout=30)
    first = (proc.stdout.splitlines() or [""])[0]
    taken, _, camera = first.partition("|")
    return taken.strip(), camera.strip()


def _taken_words(taken: str, mtime: float) -> str:
    """'2025:12:24 18:02:11' -> '24 december 2025': the words a person searches a date with (file time without EXIF)."""
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})", taken or "")
    y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else \
        (time.localtime(mtime).tm_year, time.localtime(mtime).tm_mon, time.localtime(mtime).tm_mday)
    return f"{d} {MONTHS[mo - 1]} {y}" if 1 <= mo <= 12 else str(y)


def _ocr(path: Path) -> str:
    if not shutil.which("tesseract"):
        return ""
    from shani_chronoa.senses import ocr
    argv = ["tesseract", str(path), "-", "-l", ocr.build_language_argument(ocr.default_languages())]
    tessdata = ocr.find_tessdata_dir()
    if tessdata:
        argv += ["--tessdata-dir", tessdata]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout
    except subprocess.TimeoutExpired:
        return ""
    words = re.findall(r"[^\W_]{2,}", out)
    return " ".join(words) if len(words) >= 3 else ""


def _labels(path: Path) -> str:
    from shani_chronoa.opencv import runtime
    if not runtime.installed():
        return ""
    from shani_chronoa.opencv import detect
    cv2, np = runtime.load()
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return ""
    found = [b.label for b in detect.objects(image)]
    faces = len(detect.faces(image))
    labels = sorted(set(found))
    if faces:
        labels.append("face" if faces == 1 else "faces")
    if "person" in labels:
        labels.append("people")
    return " ".join(labels)


def _caption(path: Path) -> str:
    from shani_chronoa import local_vision
    if not local_vision.available():
        return ""
    try:
        data = path.read_bytes()
        return local_vision.describe(data, "Describe this photo in one short sentence.", 120)
    except (OSError, local_vision.VisionError):
        return ""


def index(root: Optional[Path] = None, limit: int = 50, seconds: float = 240, captions: bool = False) -> dict:
    """Bring the index up to date for up to `limit` new or changed photos; returns what was done."""
    root = root or pictures_dir()
    db = _connect()
    started, done, skipped = time.monotonic(), 0, 0
    seen = set()
    try:
        known = {p: (s, m) for p, s, m in db.execute("SELECT path, size, mtime FROM photos")}
        pending = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in PHOTO_TYPES:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                seen.add(str(path))
                if known.get(str(path)) != (st.st_size, st.st_mtime):
                    pending.append((path, st))
        for gone in set(known) - seen:
            db.execute("DELETE FROM photos WHERE path = ?", (gone,))
            db.execute("DELETE FROM photo_fts WHERE path = ?", (gone,))
            db.execute("DELETE FROM photo_vecs WHERE path = ?", (gone,))
        for path, st in pending[:limit]:
            if time.monotonic() - started > seconds:
                break
            taken, camera = _exif(path)
            labels, text = _labels(path), _ocr(path)
            caption = _caption(path) if captions else ""
            name_words = " ".join(re.findall(r"[A-Za-z]{2,}", f"{path.parent.name} {path.stem}"))
            words = " ".join(x for x in (name_words, _taken_words(taken, st.st_mtime), camera, labels, caption, text) if x)
            db.execute("INSERT OR REPLACE INTO photos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (str(path), st.st_size, st.st_mtime, taken, camera, labels, text, caption, words))
            db.execute("DELETE FROM photo_fts WHERE path = ?", (str(path),))
            db.execute("INSERT INTO photo_fts (path, words) VALUES (?, ?)", (str(path), words))
            db.execute("DELETE FROM photo_vecs WHERE path = ?", (str(path),))
            done += 1
        skipped = max(0, len(pending) - done)
        _fill_vectors(db)
        db.commit()
        total = db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    finally:
        db.close()
    return {"indexed": done, "waiting": skipped, "total": total, "seconds": round(time.monotonic() - started, 1)}


def _fill_vectors(db) -> None:
    from shani_chronoa import local_embed
    if not local_embed.configured():
        return
    rows = db.execute("SELECT p.path, p.words FROM photos p LEFT JOIN photo_vecs v ON v.path = p.path "
                      "WHERE v.path IS NULL LIMIT 256").fetchall()
    for at in range(0, len(rows), 32):
        batch = rows[at:at + 32]
        vectors = local_embed.embed([w for _p, w in batch], local_embed.DOCUMENT)
        if vectors is None:
            return
        db.executemany("INSERT OR REPLACE INTO photo_vecs VALUES (?, ?)",
                       [(p, local_embed.to_blob(v)) for (p, _w), v in zip(batch, vectors)])


def search(query: str, limit: int = 12, embed=None) -> List[dict]:
    """Photos about `query`, best first: words (FTS5) and, with the Memory extra, meaning, fused by rank."""
    from shani_chronoa import local_embed
    embed = embed or local_embed.embed
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 1]
    if not words or not db_path().exists():
        return []
    db = _connect()
    try:
        match = " OR ".join('"' + w.replace('"', "") + '"*' for w in words)
        by_words = [p for (p,) in db.execute("SELECT path FROM photo_fts WHERE photo_fts MATCH ? "
                                             "ORDER BY bm25(photo_fts) LIMIT ?", (match, limit * 2))]
        by_meaning = []
        asked = embed([query], local_embed.QUERY)
        if asked:
            q = asked[0]
            scored = [(p, local_embed.similarity(q, local_embed.from_blob(v)))
                      for p, v in db.execute("SELECT path, v FROM photo_vecs")]
            by_meaning = [p for p, s in sorted(scored, key=lambda x: -x[1]) if s >= 0.55][:limit * 2]
        fused: "dict[str, float]" = {}
        for ranked in (by_words, by_meaning):
            for rank, p in enumerate(ranked):
                fused[p] = fused.get(p, 0.0) + 1.0 / (60 + rank)
        out = []
        for p in sorted(fused, key=lambda x: -fused[x])[:limit]:
            row = db.execute("SELECT taken, labels, caption, text FROM photos WHERE path = ?", (p,)).fetchone()
            if row and Path(p).exists():
                taken, labels, caption, text = row
                out.append({"path": p, "taken": taken, "labels": labels, "caption": caption,
                            "text": (text or "")[:120]})
        return out
    finally:
        db.close()


def status() -> dict:
    if not db_path().exists():
        return {"total": 0}
    db = _connect()
    try:
        total = db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        with_text = db.execute("SELECT COUNT(*) FROM photos WHERE text != ''").fetchone()[0]
        with_labels = db.execute("SELECT COUNT(*) FROM photos WHERE labels != ''").fetchone()[0]
        return {"total": total, "with_text": with_text, "with_objects": with_labels}
    finally:
        db.close()


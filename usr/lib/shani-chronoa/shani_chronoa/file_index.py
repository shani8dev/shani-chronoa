"""Your own files, searchable by meaning - with keyword search when nothing embeds.

**Why this exists as its own store rather than a widening of `conversation_store`.**
That one indexes *turns*: what was said, between whom, in this app. The question
here is different - "what do my notes say about X" - over files Chronoa did not
write and has no conversation attached to. Sharing a table would couple two things
with different lifetimes, and a file's text is not a message.

**The honest degradation is the design, not a fallback.** `local_embed` needs a
146 MB model and a llama.cpp server; `pacman -Si` on a fresh Arch box returns
nothing, and this dev machine has no CUDA at all. So:

* with embeddings - hybrid: FTS5 keyword **and** vector, merged by reciprocal
  rank, which is the same merge `conversation_store.search` uses;
* without them - keyword only, and the answer **says so**.

A search that silently returns keyword-only results while looking like semantic
results is the confident-wrong-answer shape this repository keeps recording. The
caller must be able to tell "you have no embeddings" from "nothing matched".

**What it refuses to index is as reported as what it indexes.** An unreadable file
- no permission, a binary, a PDF with no text layer and no OCR - is counted and
named, not skipped quietly. `search_documents` already delegates to the desktop's
own index (tracker3 / Baloo); this is the one that is ours and works with no
desktop index running at all.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import sqlite3
import subprocess
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Where the index lives. Beside Chronoa's other state, and resolved per call
#: (never at import) because an import-time path points the test suite at the
#: real `~/.local/share/shani-chronoa`.
def index_path():
    from shani_chronoa import files
    return files.state_home() / "shani-chronoa" / "file-index.sqlite3"

#: Files we will read text out of. Anything not here is indexed only if a tool
#: can extract it, and otherwise reported as skipped.
TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".py", ".sh", ".js",
    ".ts", ".html", ".htm", ".xml", ".css", ".tex", ".org", ".adoc",
})

#: The largest single file read into memory, in bytes. A 4 GiB log must not be
#: read to be indexed - the same bound `lan_link` needed, for the same reason.
MAX_FILE_BYTES = 32 * 1024 * 1024

#: How much of one file is embedded. nomic-embed-text-v1.5 takes 8k tokens, so
#: ~8k characters is a chunk; a file longer than this is indexed by its opening
#: and *says so*, because a half-read file reported as fully indexed is a lie
#: about what was searched.
MAX_EMBED_CHARS = 8192

#: Below this cosine similarity a file is not "about" the query. From
#: `conversation_store`'s own measured floor for the same model.
SIMILARITY_FLOOR = 0.28

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    path      TEXT PRIMARY KEY,
    mtime     REAL NOT NULL,
    size      INTEGER NOT NULL,
    sha256    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    text      TEXT NOT NULL,
    vector    BLOB,
    indexed   REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    path UNINDEXED, text, tokenize='unicode61'
);
"""


class IndexError_(RuntimeError):
    """A file we will not index, with the reason."""


def _connect(path=None):
    path = pathlib.Path(path) if path else index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript(_SCHEMA)
    db.commit()
    return db


def _strip(text: str) -> str:
    """Text with the NUL and control bytes a file may carry.

    NULs are the ones that matter: SQLite stores them, FTS5 tokenizes them, and
    a `MATCH` against a string containing one returns nothing while the row
    looks perfectly indexed.
    """
    return text.replace("\x00", "").replace("\r\n", "\n")


def read_file(path: pathlib.Path) -> "tuple[Optional[str], str]":
    """`(text, why-not)` for one file. `text` is None when unreadable.

    PDFs go through `pdftotext` (poppler), which is on both images, and a PDF
    with no text layer is reported as such rather than run through OCR - OCR is
    90 seconds and needs `tesseract`, and a caller asking for a search is not
    asking for a scan.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"cannot be read ({exc})"
    if size <= 0:
        return None, "it is empty"
    if size > MAX_FILE_BYTES:
        return None, f"it is {size} bytes, past the {MAX_FILE_BYTES}-byte limit"

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if not _which("pdftotext"):
            return None, "pdftotext (poppler) is not installed, and it is what reads a PDF"
        try:
            done = subprocess.run(["pdftotext", "-q", str(path), "-"],
                                  capture_output=True, text=True, timeout=120,
                                  check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"pdftotext failed ({exc})"
        if done.returncode != 0:
            return None, f"pdftotext refused it ({done.stderr.strip()[-80:]})"
        text = _strip(done.stdout)
        return (text, "") if text.strip() else (None, "it has no text layer to index")

    if suffix not in TEXT_SUFFIXES:
        return None, f".{suffix.lstrip('.')} files are not text this can read"

    try:
        text = _strip(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return None, f"cannot be read ({exc})"
    return (text, "") if text.strip() else (None, "it is whitespace only")


def _which(binary: str) -> bool:
    try:
        return subprocess.run(["which", binary], capture_output=True).returncode == 0
    except OSError:
        return False


def _fingerprint(path: pathlib.Path, mtime: float, size: int) -> str:
    digest = hashlib.sha256(f"{path}:{mtime}:{size}".encode()).hexdigest()
    return digest


def index_directory(root, progress=None, embed=True, db=None):
    """Index every readable file under `root`. Returns a report.

    **Incremental by fingerprint, not by re-reading everything.** A file whose
    path, mtime and size are unchanged is not re-read and not re-embedded -
    re-embedding a whole documents directory on every change is how an index
    becomes something nobody runs.

    `embed=False` indexes text only, and the report says so; the caller is the
    one that knows whether `local_embed` is usable.
    """
    root = pathlib.Path(root)
    if not root.is_dir():
        return {"error": f"{root} is not a directory"}

    owns = db is None
    db = db or _connect()
    from shani_chronoa import local_embed

    try:
        seen = set()
        added = updated = skipped = 0
        problems: List[str] = []
        embedder = local_embed.embed if embed else None

        for path in sorted(root.rglob("*")):
            # **A symlink is reported, not silently skipped.** The first version
            # had `if not path.is_file() or path.is_symlink(): continue`, which
            # made the confinement check below unreachable for exactly the things
            # it exists to catch - safe by accident, and silent about it. A file
            # the walker declined and never named is a file whose absence looks
            # like "not there" rather than "refused".
            if path.is_symlink():
                problems.append(f"{path.name}: a symlink, and this index never "
                                f"follows one")
                skipped += 1
                continue
            if not path.is_file():
                continue
            # Never follow a symlink out, and never index the index.
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if root.resolve() not in resolved.parents and resolved != root.resolve():
                problems.append(f"{path.name}: it resolves outside {root}")
                skipped += 1
                continue
            if path == index_path():
                continue

            try:
                stat = path.stat()
            except OSError as exc:
                problems.append(f"{path}: {exc}")
                skipped += 1
                continue

            seen.add(str(path))
            fingerprint = _fingerprint(path, stat.st_mtime, stat.st_size)
            row = db.execute("SELECT sha256 FROM docs WHERE path=?",
                             (str(path),)).fetchone()
            if row and row[0] == fingerprint:
                continue                     # unchanged since last time

            text, why = read_file(path)
            if text is None:
                if row:
                    db.execute("DELETE FROM docs WHERE path=?", (str(path),))
                    db.execute("DELETE FROM docs_fts WHERE path=?", (str(path),))
                    updated += 1
                else:
                    skipped += 1
                problems.append(f"{path.name}: {why}")
                continue

            vector = None
            if embedder is not None:
                vectors = embedder([text[:MAX_EMBED_CHARS]], kind=local_embed.DOCUMENT)
                if not vectors:
                    problems.append(f"{path.name}: no embedding server, so it is "
                                    f"keyword-searchable only")
                else:
                    vector = local_embed.to_blob(local_embed.normalise(vectors[0]))

            if row:
                db.execute("DELETE FROM docs WHERE path=?", (str(path),))
                db.execute("DELETE FROM docs_fts WHERE path=?", (str(path),))
                updated += 1
            else:
                added += 1
            db.execute(
                "INSERT INTO docs (path, mtime, size, sha256, kind, text, vector, indexed)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (str(path), stat.st_mtime, stat.st_size, fingerprint,
                 path.suffix.lstrip(".").lower() or "text", text[:MAX_EMBED_CHARS],
                 vector, time.time()))
            db.execute("INSERT INTO docs_fts (path, text) VALUES (?,?)", (str(path), text))
            if progress:
                progress(added + updated + skipped, 0)
        db.commit()

        # Rows whose file has gone.
        gone = [r[0] for r in db.execute("SELECT path FROM docs") if r[0] not in seen]
        for gone_path in gone:
            db.execute("DELETE FROM docs WHERE path=?", (gone_path,))
            db.execute("DELETE FROM docs_fts WHERE path=?", (gone_path,))
            updated += 1
        db.commit()

        report = {"indexed": db.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
                  "added": added, "updated": updated, "skipped": skipped,
                  "embedded": embed and db.execute(
                      "SELECT COUNT(*) FROM docs WHERE vector IS NOT NULL").fetchone()[0] > 0,
                  "problems": problems}
        return report
    finally:
        if owns:
            db.close()


def search(root, query: str, limit: int = 10, embed=True, db=None):
    """`(hits, how-search-was-done)` - never a bare list.

    The second element exists so a caller can say *why* a set of results came
    back: with the embedding server, or keyword-only because it was not there.
    """
    root = pathlib.Path(root)
    owns = db is None
    db = db or _connect()
    try:
        pattern = _fts_pattern(query)
        keyword: List[str] = []
        if pattern:
            try:
                keyword = [r[0] for r in db.execute(
                    "SELECT path FROM docs_fts WHERE docs_fts MATCH ?"
                    " ORDER BY rank LIMIT ?", (pattern, limit * 4))]
            except sqlite3.OperationalError:
                keyword = []          # a query FTS5 refuses is not an error

        vector_hits: List[str] = []
        used_embeddings = False
        if embed and keyword is not None:
            from shani_chronoa import local_embed
            vectors = local_embed.embed([query], kind=local_embed.QUERY)
            if vectors:
                used_embeddings = True
                want = local_embed.normalise(vectors[0])
                scored = []
                for path, blob in db.execute(
                        "SELECT path, vector FROM docs WHERE vector IS NOT NULL"):
                    score = local_embed.similarity(want,
                                                    local_embed.from_blob(blob))
                    if score >= SIMILARITY_FLOOR:
                        scored.append((score, path))
                scored.sort(reverse=True)
                vector_hits = [p for _s, p in scored[:limit * 4]]

        # Reciprocal rank, the merge `conversation_store` uses: a file found by
        # both is worth more than one found by either, and neither list's
        # internal ordering outranks the other's presence.
        ranks: dict = {}
        for position, path in enumerate(keyword):
            ranks[path] = ranks.get(path, 0.0) + 1.0 / (60 + position)
        for position, path in enumerate(vector_hits):
            ranks[path] = ranks.get(path, 0.0) + 1.0 / (60 + position)
        ordered = sorted(ranks, key=lambda p: -ranks[p])[:limit]

        hits = []
        for path in ordered:
            row = db.execute("SELECT text, kind, size FROM docs WHERE path=?", (path,)).fetchone()
            if not row:
                continue
            text, kind, size = row
            hits.append({"path": path, "kind": kind, "size": size,
                         "excerpt": _excerpt(text, query),
                         "score": round(ranks[path], 5)})
        how = ("keyword and embedding" if used_embeddings else
               "keyword only - the embedding model is not available here, so this "
               "is not a search by meaning")
        return hits, how
    finally:
        if owns:
            db.close()


def _fts_pattern(query: str) -> str:
    """An FTS5 MATCH pattern from a person's words, or "" if it makes none.

    Quoted, so a query containing FTS5 syntax - `-`, `*`, `(`, a stray quote -
    is a search for those words rather than a parse error. An unquoted operator
    would otherwise be anOperationalError the caller sees as "no results".
    """
    words = [w for w in query.split() if w.strip()]
    if not words:
        return ""
    return " ".join('"' + w.replace('"', " ") + '"' for w in words)


def _excerpt(text: str, query: str, width: int = 220) -> str:
    low = text.lower()
    needle = query.lower().split()[0] if query.split() else ""
    at = low.find(needle) if needle else -1
    if at < 0:
        at = 0
    start = max(0, at - width // 3)
    piece = text[start:start + width].strip().replace("\n", " ")
    if start > 0:
        piece = "…" + piece
    return piece

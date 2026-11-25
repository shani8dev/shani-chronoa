"""Skill: what is eating my disk, by *kind* of file?

`disk_usage` answers per **directory** and `stale_files` per **age**. The
question people actually ask after either is by *kind*: "is it the videos, the
photos, or the documents?" - and that answer cuts across directories. A 180 GB
`Documents` folder that is 95% `.pdf` is a different problem from one that is
95% `.docx`, and no per-directory answer can tell them apart.

**The category is a guess and is labelled as one.** Extensions are a claim, not
a fact: `.dat`, `.bin` and no extension at all are common, and a file's name can
say anything. So every file is counted in a category, the categories are listed
with their rules visible in the answer, and **`other` is a first-class bucket
rather than an error** - a machine whose files are mostly unlabelled should say
so loudly, because that is the case where the answer is least useful and the
reader needs to know it.

A file is counted once, under the **last** suffix, because that is the one the
name is making a claim about (`notes.2024.txt` is text).

Nothing is deleted and nothing is moved; this reads.
"""

from __future__ import annotations

import os
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Suffix -> category. The mapping is a judgement, not a fact, and it is printed
#: with the answer so a reader can disagree with it rather than trust it.
_CATEGORIES = {
    "video": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg",
              "mpeg", "3gp", "ogv", "ts", "vob"},
    "audio": {"mp3", "flac", "wav", "ogg", "opus", "m4a", "aac", "wma", "amr",
              "aiff", "mid", "midi"},
    "image": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff", "tif",
              "heic", "heif", "raw", "cr2", "nef", "psd", "xcf", "ico"},
    "document": {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "tex", "epub",
                 "mobi", "pages", "abw", "csv", "tsv", "xlsx", "xls", "ods",
                 "pptx", "ppt", "odp", "key"},
    "archive": {"zip", "tar", "gz", "tgz", "bz2", "xz", "zst", "7z", "rar",
                "lz4", "lzo", "cab", "iso", "dmg", "jar", "war", "apk", "deb",
                "rpm"},
    "code": {"py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "cc",
             "rs", "go", "java", "kt", "rb", "php", "sh", "bash", "zsh", "fish",
             "pl", "lua", "sql", "html", "css", "scss", "json", "yaml", "yml",
             "toml", "xml", "ini", "cfg", "conf"},
    "disk": {"img", "qcow2", "vdi", "vmdk", "vhd", "vhdx", "wim", "squashfs"},
    "database": {"db", "sqlite", "sqlite3", "mdb", "accdb", "dbf"},
    "font": {"ttf", "otf", "woff", "woff2", "eot"},
}

#: The order the answer reports in. Biggest first would hide the categories with
#: no space, and a category with no files is information.
_ORDER = ("video", "archive", "image", "document", "audio", "code", "database",
          "disk", "font", "other")

#: Above this the walk stops and says so.
_CEILING = 60000


def _human(size: float) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _suffix(path: Path) -> str:
    """The **last** suffix, lowercased and without the dot. `notes.2024.txt`
    reports `txt`, because that is the claim the name is making."""
    name = path.name
    if "." not in name.lstrip("."):
        return ""
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def _category(suffix: str) -> str:
    for category, suffixes in _CATEGORIES.items():
        if suffix in suffixes:
            return category
    return "other"


def _walk(top: Path) -> "tuple[dict, dict, int, int, bool]":
    """(sizes, counts, files, skipped, truncated, unentered directories)."""
    sizes = {kind: 0 for kind in _ORDER}
    counts = {kind: 0 for kind in _ORDER}
    seen = 0
    skipped = 0
    truncated = False
    unentered: list = []

    def _onerror(exc):
        """**A directory this cannot enter must not silently shrink the
        total** - the absence-shaped green `stale_files` and `disk_usage` both
        already record. Measured: a mode-000 directory vanished and the answer
        reported 10 MiB for a tree holding 50.
        """
        nonlocal skipped
        skipped += 1
        where = getattr(exc, "filename", None)
        if where:
            unentered.append(str(where))
    for root, _dirs, names in os.walk(top, onerror=_onerror):
        for name in names:
            path = Path(root) / name
            try:
                info = path.lstat()
            except OSError:
                skipped += 1
                continue
            # **`lstat`, not `stat`, and the link is skipped.** Measured: `stat`
            # follows a symlink, so `link.mp4 -> real.mp4` was counted as a
            # second video file with the same size - the category's number went
            # up while the space did not. A link and its target are one file.
            if os.path.islink(path):
                continue
            if not os.path.isfile(path):
                continue
            seen += 1
            kind = _category(_suffix(path))
            sizes[kind] += info.st_size
            counts[kind] += 1
            if seen >= _CEILING:
                truncated = True
                break
        if truncated:
            break
    return sizes, counts, seen, skipped, truncated, unentered


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there is nothing to break down."
    except files.PathProblem as exc:
        return str(exc)

    sizes, counts, seen, skipped, truncated, unentered = _walk(top)
    if not seen:
        return (f"No readable files under {top}. That is not the same as it "
                "being empty - a permission problem looks like this too.")

    total = sum(sizes.values())
    lines = [f"{seen} readable file(s), {_human(total)} under {top}, by "
             f"category:"]
    for kind in _ORDER:
        if counts[kind] == 0:
            continue
        share = (sizes[kind] / total * 100) if total else 0
        lines.append(f"  {kind:10} {_human(sizes[kind]):>12}  "
                     f"{share:5.1f}%  {counts[kind]} file(s)")

    present = [k for k in _ORDER if counts[k]]
    biggest = max(present, key=lambda k: sizes[k]) if present else None
    if biggest:
        lines.append("")
        lines.append(f"**{biggest}** is the largest category, "
                     f"{_human(sizes[biggest])} of {_human(total)}.")

    if counts["other"]:
        other_share = sizes["other"] / total * 100 if total else 0
        lines.append("")
        lines.append(f"**{counts['other']} file(s) "
                     f"({_human(sizes['other'])}, {other_share:.1f}%) are "
                     "`other`** - no suffix this recognises. That is a limit of "
                     "this reader's table, not a fact about those files, and it "
                     "means the breakdown above is least reliable exactly where "
                     "the unknowns are.")
        lines.append("The categories come from these suffixes:")
        for kind in _ORDER:
            # **`other` has no suffix list** - it is the bucket for everything
            # the table does not name, so printing one is impossible and a
            # KeyError is what happened the first time this ran.
            if kind == "other" or not counts[kind]:
                continue
            shown = ", ".join("." + s for s in sorted(_CATEGORIES[kind])[:6])
            more = (" ..." if len(_CATEGORIES[kind]) > 6 else "")
            lines.append(f"  {kind}: {shown}{more}")

    if unentered:
        lines.append("")
        lines.append(f"**{len(unentered)} directory(ies) could not be "
                     "entered**, so their files are not in these figures: "
                     + ", ".join(unentered[:8])
                     + (f" (+{len(unentered) - 8} more)"
                        if len(unentered) > 8 else ""))
    if truncated:
        lines.append("")
        lines.append(f"Stopped after {seen} file(s); this is not a complete "
                     "list. Narrow the path and ask again.")
    if skipped:
        lines.append(f"({skipped} path(s) could not be read and are not "
                     "counted.)")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "space_by_type",
        "description": (
            "Break disk usage down by kind of file - video, audio, image, "
            "document, archive, code and so on - across all directories, rather "
            "than per directory. Use for 'what is eating my disk', 'is it the "
            "photos or the documents', 'what takes up the space in here'. Names "
            "the largest category, and reports files whose type it could not "
            "tell as a visible `other` bucket rather than dropping them. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to break down. Defaults to "
                                         "your home directory.")},
            },
        },
    },
}

SKILLS = [Skill(name="space_by_type", schema=SCHEMA, run=_run_skill)]

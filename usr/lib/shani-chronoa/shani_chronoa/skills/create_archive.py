"""Skill: make a zip or tar archive, and list or extract one.

Archiving is a task people do constantly and which nothing here could do. One
skill with a `format` argument rather than four skills, because the difference
between them is a flag.

Uses Python's own `zipfile` and `tarfile` rather than shelling out to `zip` and
`tar`, so there is no binary to be missing - the same reasoning as reading
`/proc` rather than calling `ps`.

**Zip-slip is checked on the way in and refused**, not extracted-then-validated:
an archive whose entries escape the destination is the one genuinely dangerous
thing an archive can carry, and this will not unpack it. The check is inline
here (`_escapes`) because this module owns the `extract` action; `extract_archive`
carries a stricter one for the unpack-only case, refusing link, device and FIFO
members as well.

Honesty rules: what was added and what was skipped are both reported, because
an archive that silently omits unreadable files is worse than one that fails.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_ENTRIES = 20000
_MAX_BYTES = 2 * 1024 * 1024 * 1024   # 2 GiB of content

SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_archive",
        "description": (
            "Create a zip or tar.gz archive from files or folders, or list or "
            "safely extract an existing one. Refuses archives whose entries "
            "would escape the destination."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'create', 'list' or 'extract'. Defaults to create.",
                },
                "source": {
                    "type": "string",
                    "description": "File or folder to archive, or the archive to list/extract.",
                },
                "destination": {
                    "type": "string",
                    "description": "The archive to write, or the folder to extract into.",
                },
                "format": {
                    "type": "string",
                    "description": "'zip' or 'tar.gz'. Defaults to zip.",
                },
            },
        },
    },
}


def _collect(path: Path) -> "tuple[list, list]":
    """(files to add, reasons they were skipped)."""
    if path.is_file():
        return [path], []
    kept, skipped = [], []
    for root, dirs, names in os.walk(path):
        for name in names:
            child = Path(root) / name
            try:
                child.stat()
                child.resolve().relative_to(path.resolve())
            except (OSError, ValueError) as exc:
                skipped.append(f"{child} ({exc})")
                continue
            if len(kept) >= _MAX_ENTRIES:
                skipped.append(f"{child} (past the {_MAX_ENTRIES}-entry limit)")
                continue
            kept.append(child)
    return kept, skipped


def _escapes(name: str) -> bool:
    """Zip-slip: an entry name that would land outside the destination."""
    if name.startswith("/") or name.startswith("\\"):
        return True
    depth = 0
    for part in Path(name.replace("\\", "/")).parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part not in (".", ""):
            depth += 1
    return False


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "create").strip().lower()
    if action not in ("create", "list", "extract"):
        return f"Action must be create, list or extract, not {action!r}."
    fmt = (arguments.get("format") or "zip").strip().lower()
    if fmt not in ("zip", "tar.gz", "targz", "tar"):
        return f"Format must be zip or tar.gz, not {fmt!r}."
    is_tar = fmt.startswith("tar")

    try:
        source = files.resolve(arguments.get("source") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not source.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              source, action)

    if action == "list":
        return _list(source, is_tar)
    if action == "extract":
        try:
            dest = files.resolve(arguments.get("destination") or ".")
        except files.PathProblem as exc:
            return str(exc)
        return _extract(source, dest, is_tar)
    return _create(source, (arguments.get("destination") or "").strip(), is_tar)


def _list(archive: Path, is_tar: bool) -> str:
    try:
        if is_tar or tarfile.is_tarfile(archive):
            with tarfile.open(archive) as t:
                names = [m.name for m in t.getmembers() if m.isfile()]
        elif zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                names = [i.filename for i in z.infolist() if not i.is_dir()]
        else:
            return (
                f"{archive} is neither a zip nor a tar archive, so it was not "
                f"listed. It is {files.human_size(archive.stat().st_size)}."
            )
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return f"Could not read {archive}: {type(exc).__name__}: {exc}"
    if not names:
        return f"{archive} contains no files."
    shown = names[:_MAX_ENTRIES]
    lines = [f"{archive} contains {len(names)} file(s):"]
    lines += [f"  {n}" for n in shown]
    if len(names) > len(shown):
        lines.append(f"  ... {len(names) - len(shown)} more not shown.")
    return "\n".join(lines)


def _extract(archive: Path, dest: Path, is_tar: bool) -> str:
    if not dest.exists():
        try:
            dest.mkdir(parents=True)
        except OSError as exc:
            return f"Could not create {dest} to extract into: {exc}"
    elif not dest.is_dir():
        return f"Could not extract into {dest}: it exists and is not a directory."
    try:
        if is_tar or tarfile.is_tarfile(archive):
            with tarfile.open(archive) as t:
                members = t.getmembers()
                bad = [m.name for m in members if m.isfile() and _escapes(m.name)]
                if bad:
                    return (
                        f"Refusing to extract {archive.name}: {len(bad)} entry(ies) "
                        f"would be written outside {dest}, e.g. {bad[0]!r}. This is "
                        f"the zip-slip pattern and nothing was extracted."
                    )
                t.extractall(dest, members=[m for m in members if m.isfile()],
                             filter="data")
                count = sum(1 for m in members if m.isfile())
        elif zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                bad = [i.filename for i in infos if _escapes(i.filename)]
                if bad:
                    return (
                        f"Refusing to extract {archive.name}: {len(bad)} entry(ies) "
                        f"would be written outside {dest}, e.g. {bad[0]!r}. This is "
                        f"the zip-slip pattern and nothing was extracted."
                    )
                z.extractall(dest, members=infos)
                count = len(infos)
        else:
            return f"{archive} is neither a zip nor a tar archive, so nothing was extracted."
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return f"Could not extract {archive}: {type(exc).__name__}: {exc}"
    return f"Extracted {count} file(s) from {archive.name} into {dest}."


def _create(source: Path, destination: str, is_tar: bool) -> str:
    if not destination:
        return "No destination was given, so there is nowhere to write the archive."
    suffix = ".tar.gz" if is_tar else ".zip"
    target = Path(files.resolve(destination).as_posix())
    if target.suffix == "":
        target = target.with_name(target.name + suffix)
    if target.exists():
        return (
            f"Refusing to overwrite {target}: it already exists. Choose another "
            f"name - an archive that silently replaces the previous one is how "
            f"the previous one is lost."
        )
    if target == source or source.is_dir() and target.is_relative_to(source):
        return f"Refusing to write {target} inside {source}."

    entries, skipped = _collect(source)
    if not entries:
        return f"Nothing to archive: {source} holds no readable files."
    total = 0
    for e in entries:
        try:
            total += e.stat().st_size
        except OSError:
            pass
    if total > _MAX_BYTES:
        return f"That is {files.human_size(total)}, over the {files.human_size(_MAX_BYTES)} limit for one archive."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_tar:
            with tarfile.open(target, "w:gz") as t:
                for e in entries:
                    t.add(e, arcname=str(e.relative_to(source.parent))
                          if source.is_dir() else e.name, recursive=False)
        else:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                for e in entries:
                    z.write(e, arcname=str(e.relative_to(source.parent))
                            if source.is_dir() else e.name)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        if target.exists():
            try:
                target.unlink()   # do not leave a half-written archive behind
            except OSError:
                pass
        return f"Could not write {target}: {type(exc).__name__}: {exc}"

    if not target.exists():
        return f"Reported success but {target} is not there."
    msg = f"Archived {len(entries)} file(s) ({files.human_size(total)}) into {target} ({files.human_size(target.stat().st_size)} compressed)."
    if skipped:
        msg += f" {len(skipped)} entr(ies) were skipped: {skipped[0]}"
    return msg


SKILLS = [Skill(name="create_archive", schema=SCHEMA, run=_run)]

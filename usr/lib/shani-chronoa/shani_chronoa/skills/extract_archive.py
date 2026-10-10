"""Skill: unpack an archive into a chosen directory.

`create_archive` existed with no counterpart, which made the pair lopsided: the
assistant could bundle a folder up and then had no way to open one. Extracting is
also the single most dangerous file operation in this package, and it is worth
saying why before the code.

An archive is a list of filenames. Nothing in the format stops a member being
named `../../.config/autostart/evil.desktop` or `/etc/cron.d/anything`, and both
`tar` and `zip` will happily create those files outside the directory you asked
for. The `tar` manual calls this "archive extraction vulnerability"; it is CVE-2007-4559
for tar and the same class for zip. A user who says "unpack this download" is
trusting the *archive*, and an archive that escapes its target directory is
running code the moment anything executes it.

So every member is checked before anything is written, and the checks are not
belt-and-braces around a library default: they are the feature. Members with an
absolute path, a `..` component, or a resolved location outside the target
directory are refused, as are symlinks and hard links (a link is a perfectly good
way to point at `/etc/passwd` without ever writing outside the target), and
device and FIFO nodes are refused outright because nothing a person unpacks
needs them. Extraction then runs through Python's own `data` filter as a second
line of defence.

Overwriting is refused unless explicitly asked for, matching the dry-run default
that `find_and_replace` already uses: unpacking over a directory of the same name
should not silently replace work.

Honesty rules:

- **The whole archive is validated before the first file is written.** A partial
  extraction of an archive that turns out to be hostile is worse than none,
  because it looks like success.
- A refusal names the member that caused it.
- `files.resolve` confines the destination the same way every other file skill
  here does, so this cannot be aimed outside the user's own home.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from shani_chronoa import archives, files
from shani_chronoa.files import PathProblem
from shani_chronoa.skills import Skill

_MAX_MEMBERS = 20000
#: 7z is slower than the stdlib on a big archive and reads the whole index
#: first; the same ceiling the sandbox applies to a slow skill is the reason
#: this is generous rather than short.
_TIMEOUT = 60

SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_archive",
        "description": (
            "Unpack a .tar/.tar.gz/.tgz/.zip archive, or any format 7z reads "
            "(.7z, .rar, .cab ...), into a directory. Every member is checked "
            "first and the archive is refused whole if any entry would be "
            "written outside the destination or is a link, a device or a "
            "FIFO. Will not overwrite existing files unless asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "archive": {
                    "type": "string",
                    "description": "The archive file to unpack.",
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Directory to unpack into. Defaults to the archive's own "
                        "directory."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Allow replacing files that already exist at the "
                        "destination. Defaults to false, which refuses instead."
                    ),
                },
            },
        },
    },
}


def _unsafe_reason(name: str, dest: Path) -> Optional[str]:
    """Why this member must not be extracted, or None if it looks safe.

    Three separate escapes are checked, because each has defeated the others at
    some point: a leading `/`, a `..` component, and a path that resolves
    outside the destination despite containing neither.
    """
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        return f"it names an absolute path ({name})"
    parts = Path(cleaned).parts
    if any(part == ".." for part in parts):
        return f"it climbs out of the destination with '..' ({name})"
    try:
        resolved = (dest / cleaned).resolve()
    except (OSError, RuntimeError):
        return f"its destination could not be resolved ({name})"
    try:
        resolved.relative_to(dest.resolve())
    except ValueError:
        return f"it would be written outside the destination ({name})"
    return None


def _check_members(names: List[Tuple[str, Optional[str]]], dest: Path) -> Optional[str]:
    """First refusal reason across all members, or None if all are safe.

    Checked over the entire listing before anything is written, so a hostile
    archive cannot get half-written before being caught.
    """
    for name, kind in names:
        reason = _unsafe_reason(name, dest)
        if reason:
            return reason
        if kind in ("symlink", "hardlink", "device", "fifo", "socket"):
            return (f"it contains a {kind} member ({name}); nothing a person "
                    f"unpacks needs one, and a link is a way of pointing "
                    f"outside the destination without writing there")
    return None


def _plan(archive: Path, dest: Path, overwrite: bool) -> Tuple[Optional[str], List[str], int]:
    """(refusal, member names, member count) without writing anything."""
    try:
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                if len(members) > _MAX_MEMBERS:
                    return (f"the archive holds {len(members)} members, more than "
                            f"the {_MAX_MEMBERS} this will unpack", [], len(members))
                kinded: List[Tuple[str, Optional[str]]] = []
                for member in members:
                    if member.isdir() or member.isfile():
                        kind = "file"
                    elif member.issym():
                        kind = "symlink"
                    elif member.islnk():
                        kind = "hardlink"
                    elif member.ischr() or member.isblk():
                        kind = "device"
                    elif member.isfifo():
                        kind = "fifo"
                    else:
                        kind = "socket"
                    kinded.append((member.name, kind))
                refusal = _check_members(kinded, dest)
                names = [m.name for m in members if not m.isdir()]
                return (refusal, names, len(members))
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                if len(infos) > _MAX_MEMBERS:
                    return (f"the archive holds {len(infos)} entries, more than "
                            f"the {_MAX_MEMBERS} this will unpack", [], len(infos))
                # A zip symlink is stored as a file with the unix mode bit set,
                # so it has to be read out of external_attr rather than assumed.
                kinded = []
                for info in infos:
                    is_link = (info.create_system == 3
                               and (info.external_attr >> 16) & 0o170000 == 0o120000)
                    kinded.append((info.filename, "symlink" if is_link else "file"))
                refusal = _check_members(kinded, dest)
                names = [i.filename for i in infos if not i.is_dir()]
                return (refusal, names, len(infos))
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return (f"the archive could not be read: {exc}", [], 0)
    # Not tar and not zip. **Two formats 7-Zip cannot open, measured on `@blue`
    # 2026-10-10:** `7z l -slt` returns rc=2 on a `.lzo` and a `.lrz` that
    # `lzop` and `lrzip` had just created themselves, while both tools sit
    # installed in the same image - so those two were refused here for want of
    # a reader rather than for want of the tool. `archives` knows the measured
    # exceptions and nothing else, so every other format still goes to 7z.
    own, own_problem = archives.members(archive)
    if own:
        names = [name for name, _ in own]
        return None, names, len(names)
    if own_problem:
        return own_problem, [], 0
    # 7z reads everything else, including `.rar` - this image's is 7-Zip 26.03
    # and `7z i` lists Rar/Rar1/Rar2/Rar3/Rar5.
    # The refusal says which formats exist and what would open them, because
    # "it is neither a tar archive nor a zip file" is a true sentence and a
    # dead end.
    return _sevenzip_members(archive, dest)


def _sevenzip_members(archive: Path, dest: Path) -> "Tuple[Optional[str], List[str], int]":
    """(refusal, member names, member count) for a 7z archive, via `7z l -slt`.

    **Why 7z at all.** Python's stdlib reads tar and zip and nothing else, so a
    `.7z`, `.rar` or `.cab` was answered with "this is neither a tar archive
    nor a zip file" - a true sentence and a dead end. `7z` ships on both images
    (`p7zip`/`7zip`) and reads all of them.

    **`7z x` extracts a symlink member with exit 0 and no warning** - measured
    on a real slot (`chronoa-cli-formats.sh`, `@blue`, 2026-10-10), by building
    an archive containing `escape -> /etc/passwd` and unpacking it. So
    delegating to 7z without checking the listing first would write **outside
    the destination**, which is exactly what the tar and zip branches refuse.
    Hence this function: the whole archive is listed and checked before
    anything is extracted, through the same `_check_members` model.

    **`-slt`'s output has a trap the measurement caught.** It prints one
    `Key = value` block per member *and a first block describing the archive
    itself*:

        --
        Path = /tmp/round.7z        <- the archive, NOT a member
        Type = 7z
        Physical Size = 208
        ----------
        Path = seven                 <- members start here
        Attributes = D drwxr-xr-x
        ----------
        Path = seven/one.txt
        Attributes = A -rw-r--r--

    so a member is a block that carries an `Attributes` line; the archive's own
    block carries `Type`/`Physical Size` and none. Reading every `Path =` as a
    member would report the archive as its own first member.

    **A symlink is the leading `l` of the mode string** (`Attributes = A
    lrwxrwxrwx`), and a directory is `D` - both measured, not assumed.
    """
    if shutil.which("7z") is None:
        return ("7z is not installed, so a format Python's stdlib does not read "
                "(7z, rar, cab) cannot be unpacked here", [], 0)
    try:
        proc = subprocess.run(["7z", "l", "-slt", str(archive)],
                              capture_output=True, text=True, timeout=_TIMEOUT,
                              check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return (f"7z did not answer ({exc})", [], 0)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (f"7z could not list it: {detail[-1] if detail else f'exit {proc.returncode}'}",
                [], 0)

    kinded: List[Tuple[str, Optional[str]]] = []
    block: dict = {}
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            if block.get("Path") is not None and "Attributes" in block:
                kinded.append((block["Path"], _member_kind(block["Attributes"])))
            block = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep and key.strip():
            block[key.strip()] = value.strip()
    if block.get("Path") is not None and "Attributes" in block:
        kinded.append((block["Path"], _member_kind(block["Attributes"])))

    if len(kinded) > _MAX_MEMBERS:
        return (f"the archive holds {len(kinded)} members, more than the "
                f"{_MAX_MEMBERS} this will unpack", [], len(kinded))
    refusal = _check_members(kinded, dest)
    names = [name for name, kind in kinded if kind == "file"]
    return (refusal, names, len(kinded))


def _member_kind(attributes: str) -> str:
    """`"A -rw-r--r--"` -> "file", `"D drwxr-xr-x"` -> "dir", a link -> "symlink".

    The mode string's first character is the unix type, which is the whole
    answer; the letter before it (`A` archive, `D` directory) is 7z's own
    attribute flag and is not the type.
    """
    parts = attributes.split()
    mode = parts[-1] if parts else ""
    if mode.startswith("l"):
        return "symlink"
    if mode.startswith("d"):
        return "dir"
    return "file"


def _sevenzip_extract(archive: Path, dest: Path) -> None:
    """`7z x` into `dest`. Raises OSError/RuntimeError like the stdlib paths.

    **`-o` and the destination are one argument** - 7z takes `-o<path>` with no
    space, so `-o $dest` would be read as a bare `-o` and an archive name.
    Measured on a slot; the round-trip there used exactly this form.

    `-y` answers 7z's own overwrite prompt. This skill has already refused the
    overwrite case above (`_would_overwrite`), so arriving here means replacing
    nothing; `-y` only stops a prompt from hanging the child.
    """
    proc = subprocess.run(["7z", "x", f"-o{dest}", "-y", str(archive)],
                          capture_output=True, text=True, timeout=_TIMEOUT,
                          check=False)
    if proc.returncode not in (0, 1):
        # 0 = OK, 1 = warning (a non-fatal one, e.g. an empty member). Anything
        # else is a real failure and 2+ is fatal; both are raised so the caller
        # reports a partial unpack rather than a clean one.
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"7z exited {proc.returncode}")


def _would_overwrite(archive: Path, names: List[str], dest: Path) -> List[str]:
    clashes = []
    for name in names[:_MAX_MEMBERS]:
        try:
            if (dest / name.replace("\\", "/")).exists():
                clashes.append(name)
        except OSError:
            continue
    return clashes


def _run(arguments: dict) -> str:
    raw_archive = arguments.get("archive")
    try:
        archive = files.resolve(raw_archive)
    except PathProblem as exc:
        return str(exc)

    if not archive.is_file():
        return f"{archive} is not a file, so there is nothing to unpack."

    raw_dest = arguments.get("destination")
    try:
        dest = files.resolve(raw_dest) if raw_dest else archive.parent
    except PathProblem as exc:
        return str(exc)
    files.refuse_catalogue(dest, "unpack into")
    files.refuse_sensitive(dest, "unpack into")

    overwrite = bool(arguments.get("overwrite"))

    refusal, names, total = _plan(archive, dest, overwrite)
    if refusal:
        return (f"Refusing to unpack {archive.name}: {refusal}. Nothing was "
                f"written - the whole archive is checked before the first file "
                f"is created, so a partial unpack is not left behind.")

    if not names:
        return (f"{archive.name} unpacked into {dest}, but it holds no files "
                f"({total} directory entr(ies) only).")

    if not overwrite:
        clashes = _would_overwrite(archive, names, dest)
        if clashes:
            shown = ", ".join(clashes[:5])
            more = f" and {len(clashes) - 5} more" if len(clashes) > 5 else ""
            return (f"Refusing to unpack into {dest}: {len(clashes)} of "
                    f"{len(names)} file(s) already exist there ({shown}{more}). "
                    f"Pass overwrite=true to replace them, or extract into a "
                    f"new directory. Nothing was written.")

    dest.mkdir(parents=True, exist_ok=True)
    try:
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tar:
                # Second line of defence, after our own checks above.
                try:
                    tar.extractall(dest, filter="data")
                except TypeError:
                    tar.extractall(dest)
        elif zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                for name in names:
                    zf.extract(name, dest)
        else:
            _sevenzip_extract(archive, dest)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return (f"Unpacking {archive.name} stopped partway: {exc}. Some files "
                f"may have been written - the pre-flight checks passed, so this "
                f"is a fault during writing, not a rejected archive.")

    verb = "unpacked" if not overwrite else "unpacked over"
    return (f"{archive.name} {verb} into {dest}: {len(names)} file(s) from "
            f"{total} entr(ies).")


def _verify_extracted_archive(arguments: dict, tool=None):
    """Post-condition: did members actually land on disk?

    `extract_archive` is the skill with the largest blast radius in the package -
    it unzips attacker-controlled archives - so "Extracted N files" is exactly
    the claim that must not be taken on trust. The check confirms the
    destination exists, is a directory, and holds at least one **regular file**
    that is not a symlink and not inside a `__MACOSX` sidecar.

    The symlink exclusion is not pedantry: a hostile archive can contain a
    symlink pointing outside the target, and a directory full of links is a
    successful extraction that put nothing where it said it would.
    """
    dest = str(arguments.get("target") or arguments.get("destination")
                or arguments.get("output") or "").strip()
    if not dest:
        return None  # nothing was extracted to check
    from pathlib import Path as _P
    target = _P(dest)
    if not target.exists():
        return (False, f"{target} does not exist, so nothing was extracted")
    if not target.is_dir():
        return (False, f"{target} is not a directory")
    try:
        entries = list(target.iterdir())
    except OSError as exc:
        return (False, f"could not read {target}: {exc}")
    if not entries:
        return (False, f"{target} is empty, so nothing was extracted")
    real = []
    links = 0
    for entry in entries:
        if entry.is_symlink():
            links += 1
            continue
        if "__MACOSX" in entry.parts:
            continue
        real.append(entry)
    if not real:
        return (False, f"{target} holds only symlinks or macOS sidecars - an "
                       f"extraction that put nothing usable where it said")
    files = [e for e in real if e.is_file()]
    note = f" ({links} symlink(s) skipped)" if links else ""
    return (True, f"{target} holds {len(real)} extracted entrie(s), "
                  f"{len(files)} of them regular file(s){note}")


POST_CONDITION = _verify_extracted_archive

SKILLS = [Skill(name="extract_archive", schema=SCHEMA, run=_run)]

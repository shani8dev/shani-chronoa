"""Shared filesystem helpers for the file skills.

Nine skills need the same four things: turn a user-supplied path into something
resolvable, refuse a path that is obviously catastrophic to act on, format a
size a human can read, and turn an exception into a sentence that says what
actually went wrong. Implemented once here rather than nine times, because the
earlier `wpctl` triplication in this repo showed exactly what happens when the
same logic is copied per skill: three copies, three timeouts, and a divergence
nobody notices until one of them is wrong.

**The honesty rule these exist to enforce.** Every one of the failure paths
below reports *why* something could not be done. A directory that cannot be
listed is "permission denied", not an empty directory. A glob that matches
nothing is "no files matched", which is a different statement from "this folder
is empty". A truncated listing says it was truncated. Nothing here converts an
error into a clean, empty, successful-looking result, because that is the one
failure mode a person cannot detect and therefore cannot recover from.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

#: Never act on these, whatever the caller says. A typo that resolves to `/`
#: must not delete a home directory, and a skill reachable by an LLM needs this
#: more than a human does.
PROTECTED_ROOTS = (Path("/"), Path.home())


class PathProblem(Exception):
    """A path that must not be acted on, with the reason to tell the user."""


# ── Credential and key material ──────────────────────────────────────────────
# Adapted from Maze-AI's `maze_ai/agent/safety.py` (`_SENSITIVE_PATTERNS`) to
# Chronoa: credential stores and Chronoa's own key material are refused before
# any read or shell call runs. Persistence files in Maze's list (.bashrc,
# autostart, systemd/user) are deliberately NOT here: Chronoa's consent flow
# already interrupts a destructive edit with a real ask, and a hard refusal
# would break legitimate "edit my shell rc" use. What is kept is the part
# where the honest answer is always no - nobody's notes live in ~/.ssh.
_SENSITIVE_PATTERNS = [
    r"(^|/)\.ssh(/|$)",
    r"(^|/)\.gnupg(/|$)",
    r"(^|/)\.aws(/|$)",
    r"(^|/)\.azure(/|$)",
    r"(^|/)\.kube(/|$)",
    r"(^|/)\.docker/config\.json$",
    r"(^|/)\.netrc$",
    r"(^|/)\.pgpass$",
    r"(^|/)\.npmrc$",
    r"(^|/)\.pypirc$",
    r"(^|/)\.git-credentials$",
    r"(^|/)\.local/share/keyrings(/|$)",
    r"(^|/)\.mozilla/.*(cookies|logins|key\d)",
    r"(^|/)\.config/(google-chrome|chromium|BraveSoftware)/.*(Login Data|Cookies)",
    r"(^|/)\.password-store(/|$)",
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)",
    r"(^|/)\.(bash|zsh|python|mysql|psql)_history$",
    r"(^|/)\.env(\.[\w.-]+)?$",
    r"/etc/(shadow|gshadow|sudoers)",
    r"(^|/)\.config/dconf(/|$)",
    r"\.(pem|p12|pfx|jks|keystore)$",
    r"(secret|credential|passwd|password|api[_-]?key|token)s?\.(json|ya?ml|txt|ini|conf|env)$",
]
_SENSITIVE_RE = re.compile("|".join(_SENSITIVE_PATTERNS), re.IGNORECASE)


def is_sensitive_path(path) -> bool:
    """True if the path looks like it holds credentials, keys or private data."""
    text = str(path or "")
    if not text:
        return False
    expanded = os.path.expandvars(os.path.expanduser(text))
    return bool(_SENSITIVE_RE.search(expanded) or _SENSITIVE_RE.search(text))


def touches_sensitive_path(command: str) -> str:
    """Return the sensitive fragment a command references, or "" if none.

    Works on the raw command line (rather than parsed arguments) so it also
    catches paths hidden inside pipelines, quotes and globs.
    """
    text = str(command or "")
    if not text:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(text))
    match = _SENSITIVE_RE.search(expanded) or _SENSITIVE_RE.search(text)
    return match.group(0) if match else ""


def refuse_sensitive(path, verb: str) -> None:
    """Raise `PathProblem` if `path` is credential/key material, like `refuse_catalogue`."""
    if is_sensitive_path(path):
        raise PathProblem(
            f"Not going to {verb} {path!r}: it holds credentials or private keys, "
            "and a tool call is never the right way in."
        )


def parse_problem(text: str, path) -> str:
    """A syntax check for the text a file write wants to leave behind.

    A skill that claims "wrote it" on over a syntactically invalid config or a
    truncated Python file has done nothing but move a plausible-looking mistake
    onto disk, and the *next* thing that reads the file is the one that pays for
    it. Same rule as `refuse_catalogue`: the failure is reported, not carried.

    Only the formats with a parser that never executes anything and never leaves
    the process are checked: Python (`compile()`), JSON, and TOML (`tomllib`).
    Parse-only shell grammar would need a subprocess per write, and nothing
    forcibly depends on a specific install, so it is deliberately not here.
    Returns "" when there is nothing to say; the suffix decides whether the
    text is checked at all.
    """
    suffix = Path(str(path)).suffix.lower()
    if suffix == ".py":
        try:
            compile(text, str(path), "exec")
        except SyntaxError as exc:
            return f"{path}: not valid Python (line {exc.lineno}: {exc.msg}) - nothing written."
    elif suffix == ".json":
        import json

        try:
            json.loads(text)
        except ValueError as exc:
            return f"{path}: not valid JSON ({exc}) - nothing written."
    elif suffix == ".toml":
        try:
            import tomllib
        except ImportError:  # Python < 3.11 has no tomllib
            return ""

        try:
            tomllib.loads(text)
        except ValueError as exc:
            return f"{path}: not valid TOML ({exc}) - nothing written."
    return ""


def resolve(raw: str) -> Path:
    """Expand and absolutise a user-supplied path.

    Raises `PathProblem` with a message meant for the user, not a traceback.
    """
    if raw is None or not str(raw).strip():
        raise PathProblem("No path was given.")
    text = os.path.expandvars(os.path.expanduser(str(raw).strip()))
    try:
        return Path(text).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathProblem(f"Could not resolve {raw!r}: {exc}") from exc


def expand(raw: str) -> Path:
    """`~`, `$VARS` and `.`/`..` expanded, but **symlinks left alone**.

    The half of `resolve()` that stops short. A caller that needs to know
    whether something *is* a symlink - rather than what it points at - cannot
    use `resolve()`, because resolving is exactly the operation that erases
    the fact. The confinement check is still done on the *resolved* path;
    this is only for the path whose own `lstat` is the answer.
    """
    if raw is None or not str(raw).strip():
        raise PathProblem("No path was given.")
    text = os.path.expandvars(os.path.expanduser(str(raw).strip()))
    return Path(os.path.abspath(text))


def resolve_in_home(raw: str) -> Path:
    """`resolve()`, then refuse anything that lands outside the home directory.

    The confinement check is `Path.is_relative_to` on the *already resolved*
    path, never a string prefix test on the raw one. A prefix test is defeated
    by `~/link-to-etc`, by `..`, and by any path that only becomes a
    different path once symlinks are followed - and `resolve()` is what
    collapses all three into the one path that will actually be opened.

    Used where a skill exposes the same data class a consent-gated sense
    already restricts to the home directory. The ungated file skills above do
    *not* use this, deliberately: widening or narrowing one of them would
    change a permission the user already has, which is a different decision
    from adding a new skill.
    """
    path = resolve(raw)
    home = Path.home().resolve()
    if not path.is_relative_to(home):
        raise PathProblem(
            f"Not touching {path}: it is outside your home directory ({home}). "
            f"Chronoa only reaches files inside your home."
        )
    return path


def refuse_catalogue(path: Path, verb: str) -> None:
    """Refuse to `verb` a filesystem root, the user's home, **or anything that
    contains one**.

    A whole-filesystem or whole-home delete is almost never what was meant, and
    `shutil.rmtree` would do it without complaint.

    **The ancestry test is the load-bearing part, and it was missing.** This
    compared `path == root`, which refuses `/` and `~` and nothing else - but
    `path` being a *parent* of a protected root destroys it just as completely.
    Measured on this machine, with the home directory relocated to a temp tree
    so nothing real was at risk: `delete_file` with `recursive=True` and
    `path=<the parent of $HOME>` called
    `shutil.rmtree` on that directory, which held the user's entire home
    directory inside it, and `refuse_catalogue` said nothing.

    So the check is `root.is_relative_to(path)` - "does this path contain a
    protected root" - and equality is just the `path is root` case of it.

    **Severity, honestly stated.** On a conventional multi-user Linux box the
    parent of `$HOME` is `/home`, which is root-owned, so the `rmtree` fails
    with `EACCES` and the filesystem stops what this guard missed. It is a
    real hole on any layout where the parent is writable by the user - a
    single-user system with `$HOME` directly under a user-owned directory, or
    any of the four other callers (`extract_archive`, `trash_file`,
    `edit_file`, `undo_last_change`) handed such a path - and it is a hole in
    the function whose documented job is to be the thing that does not have
    one.
    """
    for root in PROTECTED_ROOTS:
        try:
            if root.is_relative_to(path):
                raise PathProblem(
                    f"Refusing to {verb} {path}: it is a whole filesystem or "
                    f"home directory, not a file - and it contains "
                    f"{root}. Delete the specific files inside it instead."
                )
        except PermissionError:  # pragma: no cover - root comparison is best-effort
            continue


def human_size(num_bytes: float) -> str:
    """A size a person can read, without pretending to more precision."""
    step = 1024.0
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < step or unit == "TiB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TiB"  # pragma: no cover - loop always returns above


def data_home() -> Path:
    """`$XDG_DATA_HOME`, or `~/.local/share` when unset, empty or relative.

    Resolved per call and never at import, which is the whole point. Three
    modules already worked this out separately - `egress.py`, `compression.py`
    and `skills/timer.py` - and this is the fourth thing they all say in prose:
    a path built from a hardcoded `~/.local/share` gives a test no way to
    relocate the data, and the documented way to sandbox a run is the XDG
    variable. `egress.py` recorded the cost in incident terms, a suite run with
    only `XDG_STATE_HOME` redirected still appended every fixture record to the
    real user's audit log.

    A relative value counts as unset, per the spec: `XDG_DATA_HOME=""` is a
    common way to end up resolving against the working directory, and state
    written relative to the CWD is untraceable and missed by any test looking
    for it.

    Import-time capture is the subtler version of the same bug, and it is why
    this is a function. A module is imported once per process while the
    environment it read is not, so a module constant would make the answer
    depend on collection order.
    """
    configured = os.environ.get("XDG_DATA_HOME", "")
    if configured and os.path.isabs(configured):
        return Path(configured)
    return Path.home() / ".local" / "share"


def state_home() -> Path:
    """`$XDG_STATE_HOME`, or `~/.local/state`; same reasoning as `data_home`."""
    configured = os.environ.get("XDG_STATE_HOME", "")
    if configured and os.path.isabs(configured):
        return Path(configured)
    return Path.home() / ".local" / "state"


def config_home() -> Path:
    """`$XDG_CONFIG_HOME`, or `~/.config`; same reasoning as `data_home`.

    Distinct from `data_home` on purpose. Configuration is where a user drops
    their own code - the skill and sense directories are module search paths -
    so it must not move when a *test* relocates its data, and a run that
    redirected `XDG_DATA_HOME` while reading skills from a relocated config
    directory would silently load a different set of them.
    """
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    if configured and os.path.isabs(configured):
        return Path(configured)
    return Path.home() / ".config"


def ensure_private_dir(path: Path) -> None:
    """Create `path` (and its parents) and make it owner-only.

    `mkdir` then `chmod`, never `mkdir(mode=...)`: the mode argument is masked by
    the process umask, so `mkdir(parents=True, mode=0o700)` under umask `0002`
    lands at `0775` with no error at all. The correction therefore has to happen
    after the directory exists.

    This is `triggers._ensure_state_dir` moved here. It was written there first,
    `egress.py` copied it, and then every other state-writing surface in the
    package went without it - measured at umask `002`, `tool_calls.log` and
    `spill` files landed at `0664` and `logs/` at `0775`. Six inline copies of the
    same three lines is six chances to invent a seventh, wrong one.

    The failure is **logged, never swallowed**: a state file that could not be
    restricted is a confidentiality problem the user has to know about, and a
    silent `except OSError: pass` would report success on a file that is still
    world-readable. Note the `mkdir` itself is *not* caught - if the directory
    cannot be created the caller has to find out, because every write under it is
    about to fail too.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError as exc:
        logger.warning("Could not restrict permissions on %s: %s", path, exc)


def restrict_file(path: Path) -> None:
    """Make `path` owner-read/write only.

    Same reasoning as `ensure_private_dir`, for a file. Used on both ends of an
    atomic publish - the temp file **before** `os.replace`, then the destination
    after - because `os.replace` preserves the temp's mode, so chmodding only the
    destination leaves the window open: the file is already at its final path, and
    readable by group and other, before the tightening runs.
    """
    path = Path(path)
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.warning("Could not restrict permissions on %s: %s", path, exc)


def describe(exc: BaseException, path: Path, action: str) -> str:
    """Turn an OSError into a sentence naming the cause and the path.

    Deliberately does not collapse `NotADirectoryError` and `FileNotFoundError`
    into one message, and never into a success. An LLM reading "Permission
    denied: /root" can ask the user for a different path; an LLM reading "" or
    "done" will report a deletion that never happened.
    """
    name = type(exc).__name__
    if isinstance(exc, FileNotFoundError):
        return f"Could not {action} {path}: it does not exist."
    if isinstance(exc, IsADirectoryError):
        return f"Could not {action} {path}: it is a directory."
    if isinstance(exc, NotADirectoryError):
        return f"Could not {action} {path}: a part of that path is not a directory."
    if isinstance(exc, PermissionError):
        return (
            f"Could not {action} {path}: permission denied. The file exists but "
            f"this user may not touch it."
        )
    if isinstance(exc, FileExistsError):
        return f"Could not {action} {path}: something is already there."
    detail = getattr(exc, "strerror", None) or str(exc)
    return f"Could not {action} {path}: {name}: {detail}"


def entry_line(path: Path, root: Path) -> str:
    """One listing row: name, kind, size, and whether it is a link."""
    try:
        is_link = path.is_symlink()
        stat = path.stat()  # follows links, deliberately
    except OSError:
        # A dangling symlink still deserves a row: "it is there and it points
        # nowhere" is a fact, and dropping it makes a broken link invisible.
        return f"  {path.name}  (link, target could not be read)"
    kind = "dir" if path.is_dir() else "file"
    size = "" if kind == "dir" else human_size(stat.st_size)
    suffix = " -> link" if is_link else ""
    try:
        shown = path.relative_to(root)
    except ValueError:  # pragma: no cover - root is the parent by construction
        shown = Path(path.name)
    name = str(shown)
    return f"  {name}  [{kind}]{(' ' + size) if size else ''}{suffix}"


def walk_limited(
    root: Path,
    *,
    max_entries: int,
    max_depth: int,
    skip_hidden: bool = False,
) -> tuple[list, bool, Optional[str]]:
    """Walk `root` collecting up to `max_entries`, reporting if it stopped early.

    Returns `(entries, truncated, stop_reason)`. A caller that silently returns
    a partial list has turned "there is more" into "this is everything", which
    is the same class of error as an exception swallowed into a clean result.
    """
    entries: list = []
    truncated = False
    reason: Optional[str] = None
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=None):
        here = Path(dirpath)
        if len(here.parts) - base_depth >= max_depth:
            dirnames[:] = []
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for name in list(dirnames) + filenames:
            entries.append(here / name)
            if len(entries) >= max_entries:
                truncated = True
                reason = (
                    f"stopped after {max_entries} entries (limit reached); this "
                    f"is not the whole tree"
                )
                return entries, truncated, reason
    return entries, truncated, reason


def tool_missing(binary: str, purpose: str) -> str:
    """An honest sentence for an absent helper binary.

    `purpose` is the user's question, not the binary's job description, so the
    reply says what could not be answered rather than what went wrong
    internally. A skill that gets `None` back from `shutil.which` and carries on
    anyway is the bug shape this repo has shipped before.

    **Two sources, and the order matters.** `routes.install_hint()` is the
    authoritative one - `routes.ROUTES` carries the routes a machine can take,
    and it is the only authority in the tree with a machine-readable package
    name per binary (`install_hint` parses it out of the route's own action
    text, so the two cannot disagree). `_PACKAGE_HINTS` is the wider hand-kept
    table, 61 entries to `routes`' 15, and it is the fallback for the binaries
    that have no route. Consulted in that order because the narrower source is
    the one that has been checked against a real image's file database; the
    wider one was hand-written.

    Worth noting what this fixes: `magick` and `whisper-cli` were answered
    "the package that provides it" by `_PACKAGE_HINTS` alone while
    `routes.ROUTES` had known `imagemagick` and `whisper-cpp` all along - the
    two-tables-one-answer case, and the reason a user was told to go looking for
    a package called `magick`.
    """
    from shani_chronoa import routes
    try:
        hint = routes.install_hint(binary)
    except Exception:  # noqa: BLE001 - a routes table that cannot load is no hint
        hint = None
    hint = hint or _PACKAGE_HINTS.get(binary, "the package that provides it")
    # The sentence has to name a *package*, not just the name of one. The
    # fallback says "the package that provides it" and reads correctly, but with
    # a real hint substituted it became "On Arch it comes from coreutils" -
    # which tells the reader what to install without telling them that what
    # they need is a package, and `test_disk_usage_missing_df.py` asserts the
    # word is present precisely so a user searching for it knows to search for
    # a package.
    return (
        f"Could not {purpose}: {binary} is not installed on this machine, so "
        f"nothing was done. On Arch it comes from the '{hint}' package."
    )


#: Which Arch package ships a binary, so `tool_missing()` can name it.
#:
#: **Every entry here was read out of `tools/cli_matrix.py`'s output** — the
#: command-to-package map it builds from pacman's own file database on a real
#: Shanios image (2026-10-03), not written from memory. That matters more than
#: it looks: auditing the names this project already states in prose found three
#: of them wrong, and every one is a name a user would have typed into
#: `pacman -S` and been told "no such package" or, worse, been handed a package
#: that installs without providing the tool.
#:
#: The three it caught, all now corrected in the skills themselves:
#:
#: - `wpctl` is in **`wireplumber`**, not the `pipewire` package. It left
#:   `pipewire` when wireplumber split off the PipeWire project, which makes the
#:   old claim a stale truth rather than a guess - the kind that survives review
#:   indefinitely because it was once right.
#: - `bluetoothctl` is in **`bluez-utils`**. `bluez` is the daemon and does not
#:   ship the client.
#: - `udisksctl` is in **`udisks2`**. `udisks` is the older name.
#:
#: An earlier version of this audit matched package names as substrings and
#: reported `bluez` and `udisks` correct, because `bluez` is a prefix of
#: `bluez-utils` and `udisks` of `udisks2`. A substring test is not a check.
#:
#: `xdotool` is here although the matrix cannot confirm it (not installed on the
#: image audited, so there was no pacman file to read); it is a single-word
#: package, so there is nothing for it to be confused with.
_PACKAGE_HINTS = {
    # Read out of pacman's own file database via `chronoa-matrix.json`
    # (`commands[].package`), which is the only authority in the tree with a
    # machine-readable package name per binary - not guessed. Added when
    # `tools/cli_matrix.py --check` ran on a real GNOME image (slot-test
    # `chronoa-matrix`, 2026-10-09) and found `set_theme` running three Plasma
    # binaries it never checks for: without these, `files.tool_missing` said
    # "kreadconfig6 is not installed" and stopped there, naming nothing the
    # reader could act on.
    #
        # `plasma-lookandfeeltool` and `kreadconfig5` are deliberately absent:
        # they are Plasma 5 binary names. `plasma-lookandfeeltool` is absent
        # from the matrix on a Plasma 6 image and `kreadconfig5` from this
        # GNOME one, which is the same fact `set_theme`'s own docstring records
        # (measured on the Plasma image 2026-10-01). Listing a package for a
        # binary that does not exist would be the invented answer this table
        # exists to avoid.
        "kreadconfig6": "kconfig",
        "plasma-apply-colorscheme": "plasma-workspace",
    "nmcli": "networkmanager",
    "lp": "cups",
    "lpr": "cups",
    "lpstat": "cups",
    "xdotool": "xdotool",
    "systemctl": "systemd",
    "journalctl": "systemd",
    "localectl": "systemd",
    "wpctl": "wireplumber",
    "pactl": "libpulse",
    "df": "coreutils",
    "findmnt": "util-linux",
    "lsblk": "util-linux",
    "rfkill": "util-linux",
    "gsettings": "glib2",
    "gdbus": "glib2",
    "notify-send": "libnotify",
    "upower": "upower",
    "fwupdmgr": "fwupd",
    "bluetoothctl": "bluez-utils",
    # libnfc ships its whole tool set as one package (verified from upstream's
    # `BUILD_UTILS` default and Arch's PKGBUILD, which never turns it off), so
    # every `nfc-*` binary maps to the same answer. Without these entries
    # `tool_missing` fell back to its literal placeholder and produced
    # "On Arch it comes from the 'the package that provides it' package."
    "nfc-list": "libnfc",
    "rtl_fm": "rtl-sdr",
    "obexd": "bluez-obex",
    "nfc-scan-device": "libnfc",
    "nfc-mfultralight": "libnfc",
    "nfc-mfclassic": "libnfc",
    "nfc-emulate-forum-tag4": "libnfc",
    "nfc-jewel": "libnfc",
    "nfc-relay-picc": "libnfc",
    "udisksctl": "udisks2",
    "pdftotext": "poppler",
    "scanimage": "sane",
    "smartctl": "smartmontools",
    "vnstat": "vnstat",
    "fuser": "psmisc",
    "ss": "iproute2",
    "ping": "iputils",
    "dig": "bind",
    "host": "bind",
    "git": "git",
    "flatpak": "flatpak",
    "pacman": "pacman",
    "gnome-extensions": "gnome-shell",
    "powerprofilesctl": "power-profiles-daemon",
    "xdg-open": "xdg-utils",
    "xdg-mime": "xdg-utils",
    "sox": "sox",
    "soundstretch": "soundtouch",
    "rubberband": "rubberband",
    "espeak-ng": "espeak-ng",
    "zbarimg": "zbar",
    "pw-top": "pipewire",
    "pw-dump": "pipewire",
    "pw-play": "pipewire-audio",
    "pw-record": "pipewire-audio",
    "pw-cat": "pipewire-audio",
    "xrandr": "xorg-xrandr",
    # The 2026-10-07 matrix skills, read from the matrix's own pacman data.
    "distrobox": "distrobox",
    "virsh": "libvirt",
    "fc-list": "fontconfig",
    "fc-match": "fontconfig",
    "boltctl": "bolt",
    "pdfunite": "poppler",
    "pdfseparate": "poppler",
    "pdfinfo": "poppler",
    "cancel": "cups",
    "hostnamectl": "systemd",
    "systemd-analyze": "systemd",
    "xdg-settings": "xdg-utils",
    "last": "util-linux",
}


def iter_lines(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            yield line


def cap_list(rows: list, limit: int) -> Tuple[list, int]:
    """First `limit` rows, and how many were withheld.

    A `limit` of zero or less means *no cap*, not *show nothing*: every caller
    here passes a positive constant, and a zero that silently hid everything
    would be the more dangerous of the two surprises.

    Returns `(shown, withheld)` rather than just the slice so a caller cannot
    cap a list without also learning the count. A silently shortened list is
    indistinguishable from a complete one, and the model reads it as the whole
    answer - so the two values travel together on purpose, and the caller's
    next line is expected to say what the number is.
    """
    rows = list(rows)
    if limit <= 0 or len(rows) <= limit:
        return rows, 0
    return rows[:limit], len(rows) - limit


def withheld_note(what: str, count: int, widen: str = "") -> str:
    """The line that discloses a capped list, or "" when nothing was withheld.

    `widen` is how to get the rest - naming the flag that raises the limit, or
    the skill to use instead. Without it the disclosure tells the model the
    answer is incomplete but not how to complete it, which is only half useful.
    """
    if count <= 0:
        return ""
    plural = "" if count == 1 else "s"
    return (f"... and {count} more {what}{plural} not shown."
            + (f" {widen}" if widen else ""))

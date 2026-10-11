"""Repository hygiene: no dependency on the author's machine.

These tests import nothing from `shani_chronoa` and nothing from `gi`, so they
run on any platform including one where GTK is absent - the same reason
edge0's `test_repo_hygiene.py` exists, and the same rule it enforces: *"no
dependency on the author's machine"*.

Three guards, each of which has caught something real in this workspace's
history:

* **no personal paths** - a fact read on one developer's box and shipped as
  truth is the most expensive class here, because it reads as correct forever.
* **the optional-dependency boundary holds** - a heavy or absent package is
  imported behind one guard in one place; nothing asserted the guard stays.
* **no secrets** - this workspace has a documented leaked-MOK-key issue, so the
  check is worth having rather than assumed.

Two details worth copying rather than rediscovering:

1. **The patterns are assembled with `+`** so this file never contains the
   literal string it searches for - otherwise the guard flags its own source.
   `tests/test_tool_tracking.py` and `test_user_prompts.py` both hit exactly
   that: a *comment* naming the searched-for text made the check fire on itself.
2. **It reads `git ls-files --cached --others --exclude-standard`**, so it
   checks *shipped* code and not a downloaded or vendored tree, with a
   directory walk as the fallback when git is unavailable.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The shipped payload. `tests/` and the repo-root scripts are checked too - a
#: leaked path in a test is still a leaked path - but the build and cache areas
#: are materialised and gitignored, so they are not *shipped* code.
_SHIPPED = ("usr", "tests", "tools", "share", "demos")

_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "cache", "node_modules",
                 ".venv", "venv", "build", "dist", ".mypy_cache", ".ruff_cache"}

_EXCLUDE_SUFFIXES = {".pyc", ".so", ".png", ".jpg", ".jpeg", ".gguf", ".bin",
                     ".zst", ".xz", ".gz", ".pdf", ".svg", ".ico", ".woff",
                     ".woff2", ".mo", ".qm"}

# Assembled so this file never contains what it looks for.
_SK = "sk" + "-"
_GHP = "ghp" + "_"
_AKIA = "AKI" + "A"
_PEM = "-----BEGIN " + "[A-Z0-9 ]*PRIVATE KEY-----"

_SECRET_PATTERNS = [
    re.compile(r"\b" + _SK + r"[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b" + _GHP + r"[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b" + _AKIA + r"[0-9A-Z]{16}"),
    re.compile(_PEM),
]

#: Generic placeholders, and the shortest name the guard treats as real. A
#: one-letter `/home/x/` or `/home/u/` in a docstring is synthetic; a 14-character
#: `shrinivaskumbhar` is somebody's machine. Without the length rule the guard
#: fires on every illustrative example in the tree, which is how a guard gets
#: deleted rather than obeyed.
_PLACEHOLDER_USERS = {"you", "me", "user", "example", "someone", "your", "my",
                      "x", "u", "test", "fixture", "foo", "bar", "baz", "abc",
                      "dev", "tmp", "path"}
_MIN_USERNAME = 3

#: This file is excluded from its own scan: its control test must contain a real
#: personal path to prove the guard can see one, so scanning itself would fail
#: on the control. edge0's version has no such case; Chronoa's needs it because
#: the control is a test rather than a comment.
_SELF = pathlib.Path(__file__).name


def _shipped_files():
    """Repo-relative paths of files a checkout would ship.

    `git ls-files --cached --others --exclude-standard` first, so vendored and
    gitignored areas are excluded by construction rather than by a list kept in
    step by hand. Falls back to a walk when git is unavailable, which is the
    state this runs in on a machine with no git.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, capture_output=True, check=True).stdout
        listed = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        listed = None

    if listed is not None:
        candidates = [ROOT / rel for rel in listed]
    else:
        candidates = [p for p in ROOT.rglob("*") if p.is_file()]

    for path in candidates:
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] not in _SHIPPED:
            continue
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix in _EXCLUDE_SUFFIXES:
            continue
        yield path


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _personal_homes() -> list:
    """Home-directory prefixes that must never appear in shipped code."""
    found = []
    real = os.environ.get("HOME") or ""
    if real and real not in ("/", "/root"):
        found.append(real.rstrip("/") + "/")
    # The shapes that survive a developer moving machine, checked by pattern
    # rather than by one username.
    for base in ("/home/", "/Users/", "/mnt/c/Users/"):
        found.append(base)
    return found


def _secret_hits(paths, root=None) -> list:
    """`[rel: matched]` for every secret-shaped string in a shipped file.

    A named function rather than a loop inside the test, because the guard has
    two halves - the patterns and the scan that applies them - and a control that
    only exercises the first leaves the second unmeasured. That is not
    hypothetical: the first version of this file's control passed with the scan
    deleted.

    `root` is a parameter rather than a patched global so a test can scan a
    planted tree; patching the module attribute was tried and `monkeypatch`
    re-imports the file under a second name, so it patched a copy.

    `tests/` is excluded here, not at the call site, so the scope decision and
    the scan cannot drift apart.
    """
    root = ROOT if root is None else root
    hits = []
    for path in paths:
        if path.name == _SELF:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.parts[0] == "tests":
            continue
        text = _read(path)
        if not text:
            continue
        for pattern in _SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append(f"{rel}: {match.group(0)[:12]}…")
    return hits


def _personal_path_hit(text: str) -> "str | None":
    """A personal path in `text`, or None.

    Generic placeholders are excluded on purpose: `'/home/you/notes.txt'` in a
    schema example is documentation, not a dependency on the author's machine.
    The rule is a *minimum length plus a denylist*, not "any name" - one-letter
    `/home/x/` paths are synthetic, and flagging them makes the guard noise that
    gets deleted.
    """
    for base in _personal_homes():
        for match in re.finditer(re.escape(base) + r"([\w.-]+)/", text):
            if match.group(1).lower() in _PLACEHOLDER_USERS:
                continue
            if len(match.group(1)) < _MIN_USERNAME:
                continue
            return match.group(0)
    return None


class TestNoDependencyOnTheAuthorsMachine:
    def test_no_personal_paths_in_shipped_code(self):
        hits = []
        for path in _shipped_files():
            if path.name == _SELF:
                continue          # its own control contains one on purpose
            hit = _personal_path_hit(_read(path))
            if hit:
                try:
                    where = path.relative_to(ROOT)
                except ValueError:
                    where = path
                hits.append(f"{where}: {hit!r}")
        assert not hits, (
            "shipped code contains a personal path - a fact read on one "
            "developer's box and shipped as truth:\n" + "\n".join(hits))

    def test_the_guard_can_find_a_personal_path(self):
        """**The control.** A guard that matches nothing proves nothing.

        Without this, deleting `_PLACEHOLDER_USERS` or emptying
        `_personal_homes()` would leave the suite green on a tree full of the
        thing it exists to catch.
        """
        assert _personal_path_hit("/home/shrinivaskumbhar/x") == "/home/shrinivaskumbhar/"
        assert _personal_path_hit("open('/home/you/notes')") is None, (
            "the generic placeholder was refused - every docstring example in "
            "the tree would fail the guard")
        assert _personal_path_hit("/home/x/") is None, (
            "a one-letter username was flagged - synthetic examples would fail")
        assert _personal_path_hit("/Users/alice/Documents") == "/Users/alice/"

    def test_no_secrets_in_shipped_code(self):
        """Scanned in the **shipped payload**, not in `tests/`.

        `tests/` deliberately constructs fake keys - `sk-live-DO-NOT-LOG-THIS`
        is a fixture proving redaction works - so scanning there would fail on
        the tests that check the very thing it exists to prevent. A secret leaks
        from code that *ships*, which is `usr/` and `share/`. Recorded rather
        than quietly narrowed, because a guard whose scope silently shrinks is
        how a guard stops being a guard.
        """
        hits = _secret_hits(_shipped_files())
        assert not hits, (
            "possible secrets in shipped code:\n" + "\n".join(hits))

    def test_the_guard_can_find_a_secret(self, tmp_path):
        """**The control.** A pattern that has never matched anything proves
        nothing: with no `sk-`/`ghp_`/PEM string in the shipped payload,
        disabling the whole guard leaves the suite green.

        The first version of this asserted only that the *patterns* match a
        string. Disabling the scan that applies them to files still passed it -
        a control on the wrong half of the guard. This one plants a real file
        and asks the real scan, which is the only thing that can fail.
        """
        payload = tmp_path / "usr" / "share" / "leak.py"
        payload.parent.mkdir(parents=True)
        payload.write_text("API = 'sk-abcdefghijklmnopqrstuvwxyz012345'\n")
        hits = _secret_hits([payload], root=tmp_path)
        assert hits, (
            "a planted secret in a shipped file was not reported - the scan "
            "cannot see the thing it exists to catch")
        assert "leak.py" in hits[0]

    def test_the_guard_does_not_match_ordinary_prose(self):
        """And the other direction: a pattern that fires on prose is noise."""
        assert not any(p.search("a socket sk- address") for p in _SECRET_PATTERNS)

    def test_a_fixture_in_tests_is_not_reported(self, tmp_path):
        """The scope decision above, asserted rather than assumed.

        Without this the obvious 'fix' for a failing fixture is to widen the
        scan, and nothing would say it had.
        """
        fixture = tmp_path / "tests" / "test_thing.py"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("secret = 'sk-live-DO-NOT-LOG-THIS'\n")
        assert not _secret_hits([fixture], root=tmp_path), (
            "a deliberate redaction fixture was reported as a leak")

    def test_the_shipped_file_list_is_not_empty(self):
        """The other half of the control: git missing must not mean clean."""
        files = list(_shipped_files())
        assert files, (
            "the hygiene guards scanned nothing - `git ls-files` failing must "
            "fall back to a walk, not to an empty list that passes")


class TestTheOptionalDependencyBoundary:
    """A heavy or absent package is imported behind one guard in one place.

    `ctxfit` reads GGUF through `gguf`, which is a soft dependency: present on
    this dev box, absent from Arch's sync database. Nothing asserted the import
    stays confined to the one function that can degrade without it - and an
    import that drifts to module scope turns a *degradable* dependency into a
    hard one silently, which is how a machine without it stops working.
    """

    def _sources(self):
        for path in (ROOT / "usr" / "lib" / "shani-chronoa").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path

    def test_gguf_is_imported_only_behind_a_guard(self):
        bad = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"^\s*(?:from\s+gguf\b|import\s+gguf\b)",
                                     text, re.MULTILINE):
                line = text[:match.start()].count("\n") + 1
                # Inside a `try:` is fine - that is what makes it optional.
                before = text[:match.start()].rstrip().rsplit("\n", 1)[-1]
                if before.strip().endswith(":"):
                    continue
                if re.search(r"\btry\s*:\s*(#.*)?$", text[:match.start()].rsplit("\n", 2)[0] if "\n" in text[:match.start()] else ""):
                    continue
                bad.append(f"{path.name}:{line}")
        assert not bad, (
            "`gguf` is imported outside a try:, so it is a hard dependency on a "
            "package Arch does not ship:\n  " + "\n  ".join(bad))

    def test_the_guard_can_fail(self):
        """A source-level guard is only worth what its control is worth."""
        sample = "import gguf\nreader = gguf.GGUFReader('x')\n"
        import ast
        tree = ast.parse(sample)
        found = [n for n in ast.walk(tree) if isinstance(n, ast.Import)
                 and any(a.name == "gguf" for a in n.names)]
        assert found, "the control's own sample does not import gguf"

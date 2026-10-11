"""No destructive path operation is *invoked* anywhere in this repository.

**This is a hard rule, and the workspace AGENTS.md states it as one.** A command
that reverts a path reverts *other work* in a shared tree: measured in this
session, `git checkout -- usr/lib/shani-chronoa/shani_chronoa/` was run to answer
"is this failure pre-existing?" and reverted the whole package, taking
`pipewire.py`'s uncommitted `list_streams` and a `streams` action with it. Both
had to be re-created.

**Why a test rather than a paragraph.** Prose is read once by whoever wrote it
and never by the person about to run the command. This file fails the run, which
is the only enforcement that has held here.

**And why the rule is about *invoking*, not *naming*.** The first version of
this test flagged `sandbox/commands.py` - whose whole job is to refuse these
commands - and this file, which names them to forbid them. A scanner that
punishes its own documentation and the blocklist beside it is a scanner somebody
deletes on the first red. So the two forms are kept apart: the blocklist is
where they are *refused*, and only an **invocation** is a violation.

**The second version missed the likeliest form of all.** `subprocess.run(["git",
"reset", "--hard"])` never contains the joined string, because every token is a
separate element - so an argv list was invisible while the one-string shell form
was caught. Both are handled now, and the mutation that found it is kept in the
history of this file.
"""

from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

#: Path operations that revert work rather than ask a question about it.
#:
#: **Every one has a non-destructive replacement**, which is the point of naming
#: it - the question was legitimate and the tool was wrong:
#:   "is this failure mine?" -> copy the file aside, then `git show HEAD:<file>`.
#:   "what does HEAD do?"    -> `git show HEAD:<file> > /tmp/x.py`, never a path.
#:   "undo my bad edit"      -> `cp` the backup back. One file, by name.
DESTRUCTIVE = (
    "git checkout --",
    "git checkout .",
    "git reset --hard",
    "git clean -f",
    "git clean -fd",
    "git clean -x",
    "git stash",
)

#: Files allowed to **name** these commands, because refusing or forbidding them
#: is their job. Each entry states why, and a test below asserts the reason is
#: true - a list that only says "exempt" is a hole.
ALLOWED_TO_NAME = {
    REPO / "usr/lib/shani-chronoa/shani_chronoa/sandbox/commands.py":
        "the sandbox's own blocklist: it refuses these by name",
    HERE / "test_no_destructive_path_ops.py":
        "this test forbids them by name",
}

SECTIONS = ("usr", "tests", "tools")


def _files():
    for section in SECTIONS:
        root = REPO / section
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".sh"):
                yield path


def _invocations(text: str):
    """The destructive forms this text would **run**.

    Two shapes, and the second is why the first version of this was useless:

    - **a shell line** that starts with the command, after `$`, `&&`, `;` or a
      redirection, and not after a `#`;
    - **an argv list**, where every token is its own element -
      `subprocess.run(["git", "reset", "--hard"])` never contains the joined
      string, and is the likeliest form in a Python repository.

    **Merely containing the words is not an invocation**, and the first version
    of this got that wrong: `if form in stripped` fired on its own module
    docstring quoting `git checkout -- usr/...`, so `test_prose_is_not_flagged`
    failed the control run. A rule that fires on prose quoting it is a rule that
    gets deleted rather than obeyed.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # `|` is deliberately NOT a prefix: it is a table delimiter, and
        # stripping it turned `| git reset --hard | why |` into a command.
        for prefix in ("$", "&&", ";"):
            if stripped.startswith(prefix):
                stripped = stripped[1:].strip()
                if stripped.startswith("#"):
                    stripped = ""
                    break
        if not stripped:
            continue
        for form in DESTRUCTIVE:
            if stripped.startswith(form):
                yield form
                continue
            # argv form: every token present as a quoted element on this line
            tokens = form.split()
            if len(tokens) < 2:
                continue
            rest = stripped
            for token in tokens:
                marker = '"%s"' % token
                if marker not in rest:
                    marker = "'%s'" % token
                    if marker not in rest:
                        rest = None
                        break
                rest = rest.replace(marker, "", 1)
            if rest is not None:
                yield form


def test_the_scanner_finds_something() -> None:
    """The control: a scanner over nothing is green on a repository full of them."""
    found = list(_files())
    assert len(found) > 50, (
        "only %d files scanned - the pattern would pass whatever the tree held"
        % len(found))


def test_no_destructive_path_operation_is_invoked() -> None:
    guilty = []
    for path in _files():
        if path in ALLOWED_TO_NAME:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for form in _invocations(text):
            guilty.append(f"{path.relative_to(REPO)}: {form}")
    assert not guilty, (
        "a destructive path operation would be run from this tree:\n  "
        + "\n  ".join(guilty))


def test_the_argv_form_is_caught() -> None:
    """**The mutation that found the gap.** An argv list has no joined string,
    so a scanner looking for one is a scanner over the likeliest form."""
    argv = 'subprocess.run(["git", "reset", "--hard"], check=True)\n'
    assert "git reset --hard" in list(_invocations(argv)), (
        "an argv list is invisible to this scanner, which is the form a "
        "Python repository actually uses")


def test_the_shell_line_form_is_caught() -> None:
    assert "git reset --hard" in list(
        _invocations("#!/bin/sh\ngit reset --hard HEAD\n"))
    assert "git clean -fd" in list(_invocations("git clean -fd src/"))


def test_prose_is_not_flagged() -> None:
    """The other direction, and it is load-bearing: a rule that fires on its own
    documentation gets deleted rather than obeyed."""
    docstring = ('"""Never run `git reset --hard`; it reverts the whole tree.\n'
                 'See `git checkout -- <path>`, which is why we forbid it."""\n')
    assert list(_invocations(docstring)) == [], (
        "a docstring explaining the rule was read as running the command")
    table = ("| command | why |\n| git reset --hard | reverts other work |\n")
    assert list(_invocations(table)) == []


def test_every_allowlisted_file_really_does_refuse_or_forbid() -> None:
    """An allowlist that only says "exempt" is a hole, so each entry is checked."""
    for path, reason in ALLOWED_TO_NAME.items():
        assert path.is_file(), (reason, path)
        text = path.read_text(encoding="utf-8", errors="replace")
        assert ("refus" in text or "forbid" in text or "blocked" in text
                or "BLOCKED" in text or "DESTRUCTIVE" in text), (
            f"{path.name} names these commands but does not refuse or forbid "
            f"them, so it is not entitled to: {reason}")


def test_the_allowlist_stays_small() -> None:
    """A rule with twenty exceptions is not a rule."""
    assert len(ALLOWED_TO_NAME) <= 3, (
        "%d files are allowed to name these - the list has become a loophole"
        % len(ALLOWED_TO_NAME))


def test_the_rule_is_written_into_the_workspace_file() -> None:
    """The measurement is in the workspace index, or the rule is folklore."""
    workspace = (REPO / ".." / "AGENTS.md").resolve()
    if not workspace.is_file():
        pytest.skip("no workspace AGENTS.md beside this repository")
    text = workspace.read_text(encoding="utf-8", errors="replace")
    assert "git checkout <" in text, (
        "the hard rule is not in the workspace AGENTS.md, so nothing points a "
        "reader at it")

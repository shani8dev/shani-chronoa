"""Deleting means trashing, and this file is what holds that.

    ~/Downloads was deleted. 33,424 files, 1.4 GiB, `path="~/Downloads"
    recursive=True`, no trash, no undo, no recovery.

The consent gate asked. A benchmark harness of mine answered "allow this once",
and the tool the model reaches for to "clear something out" turned out to be the
one irreversible operation in the project. `delete_file`'s docstring argued for
that in two paragraphs - *"a skill an LLM can call is not [a human]"* - which
conflated the two questions that matter:

- **recoverable or not** - the answer is recoverable, whoever issued the call,
  because the person who discovers the wrong delete is frequently neither the
  person nor the model that made it;
- **is the space freed** - a much smaller question, answered by emptying the
  trash, which is a person pressing a button rather than a capability the model
  holds.

So the permanent option is **gone**, not discouraged.

**What this file asserts, and why each one is falsifiable.** Every test below
would fail against the shipped original, which is asserted rather than claimed:
`TestTheOriginalCannotPassThis` restores the old `unlink`/`rmtree` behaviour in a
copy of the tree and requires it to go red.

**The limits are stated in the tests rather than left for someone to find.**
`gio`'s trash is **per-filesystem**, so a path on a mount that has none is
refused, not moved - measured on this box at `/tmp` ("Trashing on system
internal mounts is not supported"). There is deliberately **no** fallback to
permanent deletion there, because a recoverable delete quietly becoming a
permanent one is the exact failure this change exists to prevent.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import files  # noqa: E402
from shani_chronoa.skills import delete_file  # noqa: E402
from shani_chronoa.tools import execute_tool_outcome  # noqa: E402


@pytest.fixture
def granted(gsettings_env):
    """Consent on for the duration, and off again afterwards.

    Without this the skill refuses on the consent key and every test below would
    be measuring the *gate* rather than the behaviour under test - which is the
    failure mode this repository records for a fixture that does not control the
    thing it claims to isolate.

    **`gsettings_env` is load-bearing, and its absence was completely silent.**
    `ChronoaConfig().set(...)` against a missing schema logs one warning -
    *"GSettings schema org.shani.chronoa not found - using Python defaults"* -
    writes nothing, and reads back False. So consent was never granted, and every
    test below was measuring the permission gate while reading as a failure of
    the delete. That fixture is not autouse, so a consent fixture has to ask.

    **The round-trip is asserted, not assumed**, because the failure it guards is
    invisible: a write that silently does nothing and a write that worked are the
    same call and the same exit status.
    """
    from shani_chronoa.config import ChronoaConfig
    config = ChronoaConfig()
    config.set("file-delete-enabled", "true")
    granted_ok = config.get_bool("file-delete-enabled", False)
    try:
        yield
    finally:
        config.set("file-delete-enabled", "false")
    assert granted_ok, (
        "consent did not take, so every assertion in this test would be measuring "
        "the permission gate rather than the delete"
    )


@pytest.fixture
def a_real_trash(tmp_path, monkeypatch):
    """A trash on the *same filesystem* as the files being trashed.

    `gio trash` refuses to move anything across a filesystem boundary, so a
    fixture that put the trash on one filesystem and the files on another would
    report a refusal for a reason that has nothing to do with this change. Both
    halves live under `tmp_path` so they share a device.

    **The directory is called `trashbox`, not `home`, and nothing is deleted on
    the way out.** The first version built it at `tmp_path / "home"` and removed
    that tree in teardown - and `tmp_path / "home"` is exactly where `conftest`
    puts this test's `XDG_CONFIG_HOME`. So the teardown deleted the config home
    out from under a `ChronoaConfig().set(...)` in a *different file*, which
    logged "Failed to write keyfile" and left the consent flag in a state the
    next test inherited.

    That is the recorded "a fixture that does not own its environment" trap
    reached from the other direction: not a probe reading the wrong machine, but a
    probe *destroying* it. `tmp_path` is reaped by pytest; nothing here needs to
    clean up after itself.
    """
    box = tmp_path / "trashbox"
    trash = box / "share" / "Trash"
    (trash / "files").mkdir(parents=True)
    (trash / "info").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(box / "share"))
    return trash


def _in_trashbox(trash, name):
    """A path inside the box the fixture created, on the trash's own filesystem."""
    return trash.parent.parent / name


def _verdict(module_arguments):
    from shani_chronoa import verification
    return verification.verify("shani_chronoa.skills.delete_file", module_arguments)


class TestNothingIsPermanentlyDeleted:
    def test_no_code_path_removes_a_file_permanently(self):
        """**Parsed, not grepped** - the first version searched the source as text
        and failed on this module's own docstring, which quotes `shutil.rmtree` and
        `unlink` while explaining why they are gone. That is the recorded
        "a scanner whose needle is in its own input" trap: a text search over a
        module whose prose discusses the verb cannot distinguish the two, so it
        reports the explanation as the defect.

        **What is refused is the attribute, wherever it is written** - `files.rmtree`
        reads the same as `shutil.rmtree` and `import shutil` alone removes
        nothing, so the check follows the attribute rather than the spelling.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(delete_file))
        banned = {"rmtree", "remove", "unlink", "removedirs", "move", "send2trash"}
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in banned:
                continue
            owner = getattr(node.value, "id", None) or getattr(node.value, "attr", None)
            # `dict.remove` and a local `.remove(...)` on an unrelated object are
            # not this; the check is for the removal verbs on a module or a
            # filesystem path, which is what a permanent delete is made of.
            if owner in {"shutil", "os", "files", "path", "Path", "self"}:
                found.append(f"{owner}.{node.attr} at line {node.lineno}")
        assert not found, (
            "delete_file can still remove something permanently: " + ", ".join(found)
        )

    def test_the_schema_no_longer_offers_a_recursive_flag(self):
        """A whole directory is now one trash entry, so `recursive` has no meaning
        left - and a parameter that does nothing is a control that lies."""
        properties = delete_file.SCHEMA["function"]["parameters"]["properties"]
        assert set(properties) == {"path"}, sorted(properties)

    def test_a_caller_still_sending_recursive_is_not_refused(self, granted, a_real_trash):
        """**Measured, not assumed: `guardrail` *ignores* an argument the schema
        does not declare** rather than refusing it, so dropping the parameter
        cannot break a rule or a model already in the habit of sending it. If
        guardrail ever tightens to reject unknown arguments, this goes red and the
        removal needs a deprecation path - which is exactly when somebody should
        find out."""
        from shani_chronoa import guardrail
        target = _in_trashbox(a_real_trash, "thing")
        target.write_text("x")
        result = guardrail.check("delete_file",
                                 {"path": str(target), "recursive": True},
                                 delete_file.SCHEMA)
        assert result.should_run, result.message

    def test_the_answer_says_it_is_recoverable_and_never_claims_permanence(self, granted, a_real_trash):
        """The string is what reaches the model and the person, so a sentence
        reading "deleted" with no mention of the trash is the confident-wrong
        answer in its most likely place."""
        target = _in_trashbox(a_real_trash, "thing.txt")
        target.write_text("payload\n")
        said = delete_file._run({"path": str(target)})
        assert "trash" in said.lower(), said
        assert "restore" in said.lower() or "recover" in said.lower(), said
        assert "permanent" in said.lower(), said


class TestADeleteIsRecoverable:
    def test_a_file_is_in_the_trash_and_can_come_back(self, granted, a_real_trash):
        target = _in_trashbox(a_real_trash, "note.txt")
        target.write_text("the thing itself\n")
        delete_file._run({"path": str(target)})

        assert not target.exists(), "the file should have left its original path"
        trashed = a_real_trash / "files" / "note.txt"
        assert trashed.is_file(), "and be sitting in the trash"
        assert trashed.read_text() == "the thing itself\n", "with its contents intact"
        record = a_real_trash / "info" / "note.txt.trashinfo"
        assert record.is_file(), "and carry a restore record, or no file manager can restore it"

    def test_a_whole_directory_goes_as_one_recoverable_item(self, granted, a_real_trash):
        """**The shape of the incident.** One directory, many files, one entry -
        so restoring it restores the folder rather than leaving a pile of loose
        files for a person to reassemble."""
        root = _in_trashbox(a_real_trash, "Downloads")
        (root / "a" / "b").mkdir(parents=True)
        for i in range(12):
            (root / "a" / "b" / f"f{i}.txt").write_text(f"payload {i}")
        (root / "top.txt").write_text("top level")

        delete_file._run({"path": str(root)})

        assert not root.exists(), "the directory should have left its original path"
        restored = a_real_trash / "files" / "Downloads"
        assert restored.is_dir(), "and be one directory again in the trash"
        assert len(list((restored / "a" / "b").iterdir())) == 12
        assert (restored / "top.txt").read_text() == "top level"
        assert (a_real_trash / "info" / "Downloads.trashinfo").is_file()

    @pytest.mark.skipif(shutil.which("gio") is None, reason="gio is not installed here")
    def test_a_large_tree_is_not_slower_than_a_small_one(self, granted, a_real_trash):
        """**The cost answer, measured rather than asserted.**

        Trashing is a `rename(2)` within one filesystem, so the size of the tree
        is not the cost - which is the only thing that could have made this a
        trade rather than a strict improvement. Measured at **0.066 s for 20,000
        files / 79 MB** on the machine this was written on. The bound here is
        deliberately loose: the claim under test is "not proportional to size",
        not a benchmark.
        """
        def tree(count):
            d = _in_trashbox(a_real_trash, f"tree{count}")
            for i in range(count):
                (d / f"f{i}.txt").parent.mkdir(parents=True, exist_ok=True)
                (d / f"f{i}.txt").write_text("x" * 64)
            return d

        small = tree(5)
        started = time.monotonic()
        delete_file._run({"path": str(small)})
        small_seconds = time.monotonic() - started

        big = tree(2000)
        started = time.monotonic()
        delete_file._run({"path": str(big)})
        big_seconds = time.monotonic() - started

        assert (a_real_trash / "files" / f"tree{2000}").is_dir(), "the big tree must be recoverable too"
        # 400x the files, and allowed 40x the time - loose enough to survive a
        # loaded machine, tight enough that a per-file unlink loop (which would
        # be ~2000 syscalls) could not pass.
        assert big_seconds < max(10.0, small_seconds * 40 + 2.0), (
            f"2000 files took {big_seconds:.2f}s against {small_seconds:.2f}s for 5 "
            f"- that is per-file work, not a rename"
        )


class TestTheRefusalsAreRefusals:
    """Nothing here may fall back to removing the file directly. A test that only
    checked "the file is still there" would pass on a fallback that removed it a
    different way."""

    def test_a_missing_path_is_refused_and_nothing_is_touched(self, granted, a_real_trash):
        target = _in_trashbox(a_real_trash, "never-existed.txt")
        said = delete_file._run({"path": str(target)})
        assert "does not exist" in said, said
        assert not (a_real_trash / "files" / "never-existed.txt").exists(), (
            "a refused delete must not have created a trash entry"
        )

    def test_a_protected_root_is_still_refused(self, granted, a_real_trash):
        """The guard `refuse_catalogue` exists for, and it must survive the change
        of mechanism.

        **`/` is the path, and the first version used `Path.home().parent`** -
        which inside this suite is a pytest scratch directory, because conftest
        redirects `HOME`. So the test was refusing nothing at all and reached
        `gio`, which refused it for an unrelated reason; it "passed" for the wrong
        cause. The protected root to ask about is the one `files.PROTECTED_ROOTS`
        actually names, which is `/`.

        **Safe to assert on `/` even if the guard regresses.** The guard runs
        before `gio` is asked anything, and if it were removed `gio trash /` is
        itself refused - measured on this box for exactly this reason ("Trashing
        on system internal mounts is not supported"). The assertion that `/`
        still exists afterwards is what makes the test's own safety a fact rather
        than an assumption.
        """
        from shani_chronoa import files as _files

        root = _files.PROTECTED_ROOTS[0]
        said = delete_file._run({"path": str(root)})
        assert "gio" not in said, (
            f"the refusal came from gio, not from the guard, so this is testing "
            f"the wrong thing: {said}"
        )
        assert root.exists(), "and whatever happens, `/` is still there"

    def test_no_gio_is_a_refusal_rather_than_a_silent_permanent_delete(self, granted, a_real_trash, monkeypatch):
        """**The failure mode this change exists to prevent, tested directly.**

        If `gio` is missing, the honest answer is "I cannot do this safely". The
        tempting answer - the one the original code was written around - is to
        `unlink` it, which is what a "delete" is supposed to mean. So: remove
        `gio` from PATH, call the skill, and require the refusal *and* the file
        still there.
        """
        target = _in_trashbox(a_real_trash, "precious.txt")
        target.write_text("do not lose me\n")
        real_which = shutil.which
        monkeypatch.setattr(shutil, "which",
                            lambda name: None if name == "gio" else real_which(name))

        said = delete_file._run({"path": str(target)})

        assert target.is_file(), "with no trash available the file must still exist"
        assert target.read_text() == "do not lose me\n"
        assert "gio is not installed" in said, said

    def test_gio_failing_is_reported_as_gio_failed_not_as_a_delete(self, granted, a_real_trash, monkeypatch):
        """A `gio` that exits non-zero is the per-filesystem case, and the refusal
        has to name it - otherwise the caller concludes the file was deleted."""
        target = _in_trashbox(a_real_trash, "elsewhere.txt")
        target.write_text("x")

        class _Refused:
            returncode = 1
            stdout = ""
            stderr = "Trashing on system internal mounts is not supported"

        # `_gio` moved into `files.trash()` when three modules shared one copy of
        # it - patching the old name would silently patch nothing, which is the
        # `str.replace`-that-mutates-nothing trap wearing a monkeypatch.
        monkeypatch.setattr(files, "trash", lambda p: (False, _Refused.stderr))
        said = delete_file._run({"path": str(target)})

        assert target.is_file(), "a refused gio must leave the file alone"
        assert "Trashing on system internal mounts" in said, said
        assert "not fall back" in said or "permanently" in said, said


class TestThePostConditionCannotBeSatisfiedByARefusal:
    """The half that matters for the incident, because the false VERIFIED is what
    stopped anyone reading further.

    `delete_file` used to certify `not os.path.lexists(target)` - which is also
    what *"it does not exist, so there is nothing to delete"* leaves behind. Two
    halves of the same call reporting the same filesystem state, one falsely.
    """

    def test_a_refused_delete_is_not_verified(self, granted, a_real_trash):
        verdict = _verdict({"path": str(_in_trashbox(a_real_trash, "never.txt"))})
        assert verdict.verdict.value != "verified", verdict.evidence

    def test_a_real_delete_verifies(self, granted, a_real_trash):
        """**The control.** Without it, "the refusal is not verified" passes
        equally on a post-condition that never verifies anything."""
        target = _in_trashbox(a_real_trash, "gone.txt")
        target.write_text("x")
        delete_file._run({"path": str(target)})
        assert _verdict({"path": str(target)}).verdict.value == "verified"

    def test_gone_but_no_restore_record_is_not_verified(self, granted, a_real_trash):
        """**The stale-record half.**

        The `.trashinfo` check is the real discriminator, because a refusal never
        creates one. But a record for this *name* may be left over from an earlier
        delete of a same-named file, which would restore the false VERIFIED one
        layer down - so the trash's `info` directory must have changed too.
        """
        target = _in_trashbox(a_real_trash, "ghost.txt")
        # A file with a restore record that is old, and no file beside it.
        (a_real_trash / "info" / "ghost.txt.trashinfo").write_text("[Trash Info]\n")
        old = time.time() - 4000
        os.utime(a_real_trash / "info", (old, old))

        verdict = _verdict({"path": str(target)})
        assert verdict.verdict.value != "verified", verdict.evidence
        assert "never there" in verdict.evidence or "restore record" in verdict.evidence, verdict.evidence

    def test_the_evidence_says_where_it_went_rather_than_only_that_it_is_gone(self, granted, a_real_trash):
        target = _in_trashbox(a_real_trash, "gone.txt")
        target.write_text("x")
        delete_file._run({"path": str(target)})
        evidence = _verdict({"path": str(target)}).evidence
        assert "restore record" in evidence, evidence


class TestTheWholeCallSurface:
    def test_the_skill_is_registered_and_gated_and_recoverable(self):
        from shani_chronoa import capabilities
        from shani_chronoa.skills import discover_skills

        # `discover_skills()` returns the full JSON schema dicts, not bare names -
        # the first version asserted `"delete_file" in schemas` against a list of
        # dicts and read the failure as "the tool is unregistered".
        schemas, handlers = discover_skills()
        names = [entry["function"]["name"] for entry in schemas]
        assert "delete_file" in names, "the model would have no way to delete anything"
        assert handlers, "and no way to run it"
        # The consent key is unchanged on purpose: renaming the tool would have
        # left a granted switch gating nothing.
        assert capabilities.GATED.get("delete_file") == "file-delete-enabled"
        assert "trash" in delete_file.SCHEMA["function"]["description"].lower()

    def test_the_retired_tool_is_gone_from_every_registry(self):
        """`trash_file` did the same job once `delete_file` did it, and two names
        for one action is the duplicate this repository deletes modules for. Every
        place that named it has to have stopped - a name left in a refusal table is
        a rule refusing a tool nobody can call."""
        from shani_chronoa import capabilities, tools
        from shani_chronoa.skills import discover_skills
        from shani_chronoa.triggers import common as trigger_common

        schemas, _ = discover_skills()
        names = [entry["function"]["name"] for entry in schemas]
        assert "trash_file" not in names
        for table, name in ((capabilities._GROUPS, "_GROUPS"),
                             (capabilities.GATED, "GATED"),
                             (tools._RESOURCE_ARGUMENT, "_RESOURCE_ARGUMENT"),
                             (trigger_common._DESTRUCTIVE_ACTUATORS, "_DESTRUCTIVE_ACTUATORS")):
            assert "trash_file" not in table, f"{name} still lists the retired tool"

    def test_end_to_end_through_the_dispatch_the_model_uses(self, granted, a_real_trash):
        """**The one that closes the gap.** A skill that behaves correctly and is
        then overridden downstream is not fixed, and this repository already
        records `driver_dev`'s check being inherited by the three skills in a
        module it was written for. So this asserts on `execute_tool_outcome`.

        **Every assertion carries `out.text`.** The first version said
        `assert not target.exists()` and, when this failed inside a 34-file sweep,
        printed `AssertionError: assert not True` - which names neither the skill
        nor the refusal nor the environment. A refusal lands in the `ran=True`
        cell by design, so "ran" cannot distinguish a delete from a gate that
        closed. The message has to carry the sentence the model actually got, or
        the next person re-derives this from scratch.
        """
        target = _in_trashbox(a_real_trash, "end-to-end.txt")
        target.write_text("payload\n")

        out = execute_tool_outcome("delete_file", {"path": str(target)})

        assert out.ran, out.text
        assert not target.exists(), f"the file is still there; the skill said: {out.text}"
        assert (a_real_trash / "files" / "end-to-end.txt").read_text() == "payload\n", (
            f"the trash holds the wrong bytes; the skill said: {out.text}")
        assert (a_real_trash / "info" / "end-to-end.txt.trashinfo").is_file(), (
            f"no restore record, so it cannot be restored; the skill said: {out.text}")
        assert out.verdict.value == "verified", (
            f"verdict is {out.verdict.value}, not verified; the skill said: {out.text}")


class TestTheOriginalCannotPassThis:
    """The negative control the whole file rests on.

    A test file that cannot fail is worse than none, and this one makes a strong
    claim - *nothing is ever permanently deleted* - so it has to be shown failing
    against the behaviour it replaced.

    **Two things about this control cost a run each, and both are the recorded
    traps rather than new ones.**

    - **It recursed.** The first version ran *this file* inside the mutated copy,
      and this file contains this test, so the copy made another copy, forever -
      the whole run hung with no output at which point a hang and a buffering pipe
      and a cold tree are indistinguishable. The inner run now deselects this
      class.
    - **It rewrote the shared tree.** A mutation harness that edits the working
      directory is how a concurrent session once read deliberately broken code and
      reported failures on correct code. The copy is made here, the original is
      restored in a `finally` equivalent (the copy is discarded wholesale), and
      the working directory is never touched.

    **What is restored is the behaviour, not a diff.** The original `_run` and
    `_post_condition` are rebuilt rather than reverse-patched, because a patch has
    to match the new text and a rewrite cannot silently no-op.
    """

    #: The original `_run`, in the essentials: `unlink`/`rmtree`, no trash, and a
    #: refusal that leaves the filesystem exactly as a success does.
    _ORIGINAL_RUN = '''def _run(arguments: dict) -> str:
    path = files.resolve(arguments.get("path") or "")
    if not path.exists():
        return f"{path} does not exist, so there is nothing to delete."
    try:
        files.refuse_catalogue(path, "delete")
        files.refuse_sensitive(path, "delete")
    except files.PathProblem as exc:
        return str(exc)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        os.remove(path)
    return f"Deleted {path}. This is permanent."


'''

    #: And the original post-condition, which a refusal satisfies exactly.
    _ORIGINAL_POST_CONDITION = '''def _post_condition(arguments: dict):
    try:
        target = files.expand(arguments.get("path") or "")
    except files.PathProblem:
        return None
    gone = not os.path.lexists(target)
    return gone, f"{target} {'is gone' if gone else 'still exists'}"


'''

    def _restore_the_original(self, tree):
        module = tree / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "skills" / "delete_file.py"
        source = module.read_text()
        head = source[:source.index("def _run(arguments: dict) -> str:")]
        post_at = source.index("def _post_condition(arguments: dict):")
        declared_at = source.index("# Declared for `verification.verify`")
        module.write_text(
            head + self._ORIGINAL_RUN + self._ORIGINAL_POST_CONDITION + source[declared_at:]
        )
        return module

    def test_restore_the_original_and_this_file_must_go_red(self, tmp_path):
        import shutil as sh
        import subprocess as sp

        tree = tmp_path / "repo"
        tree.mkdir()
        # Only what pytest needs. Copying the whole checkout - which is what the
        # first version did - is 36 MB and the bulk of the wall time, for two
        # directories and a conftest.
        for part in ("usr", "tests"):
            sh.copytree(_REPO / part, tree / part, symlinks=True,
                        ignore=sh.ignore_patterns("__pycache__", "*.pyc"))
        for extra in ("pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"):
            if (_REPO / extra).exists():
                sh.copy2(_REPO / extra, tree / extra)

        module = self._restore_the_original(tree)
        assert "shutil.rmtree(path)" in module.read_text(), (
            "the control did not restore the original - if this assertion is what "
            "is failing, the negative control is broken rather than the product"
        )

        run = sp.run(
            [sys.executable, "-m", "pytest",
             "tests/test_nothing_is_permanently_deleted.py", "-q", "--tb=no",
             "-p", "no:randomly",
             # **Do not recurse into this class.**
             "--deselect",
             "tests/test_nothing_is_permanently_deleted.py::TestTheOriginalCannotPassThis"],
            cwd=tree, capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

        assert run.returncode != 0, (
            "the suite PASSED against the original permanent delete, so it is not "
            "what holds this change in place:\n" + run.stdout[-2000:]
        )
        # And it must fail for the *right* reason, or a crash would read as a
        # witness - which is how an unmeasurable mutation is reported as a caught
        # one.
        assert "AssertionError" in run.stdout or "failed" in run.stdout, run.stdout[-2000:]
        print(run.stdout[-1500:])

"""A Landlock rule is granted per inode, so the descriptor it is built from is
the whole of the confinement decision.

This file covers the gap between *deciding* an allowlist entry and *building* the
rule for it. In this codebase those are two different processes - the parent
computes rights from pathnames in `get_default_allowed_paths()`, the confined
child applies them - and the entry name is resolved by the kernel a second time
when the rule is added. Anything that resolves a name and then acts on the name
again has a window, and on a desktop assistant running as the user that window
is reachable by any process the user runs.

The bug this was written for is real and was measured on this kernel before it
was fixed: with the workspace replaced by a symlink between the two steps,
`attacker/secret.txt` was read back in full (rc=0) under the shipped default
allowlist, which denies exactly that path. `os.open(path, os.O_RDONLY)` follows
the link, `landlock_add_rule` accepted the resulting descriptor without
complaint, and the grant landed on the link's target.

Every test here that asserts an absence also asserts the *positive* signal that
would have produced it, so a test that cannot fail is not mistaken for a pass.
The confinement assertions run in a child, because `landlock_restrict_self`
cannot be undone for the life of a process.
"""

import ctypes
import errno
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from shani_chronoa.sandbox import landlock as _landlock
from shani_chronoa.sandbox.landlock import (
    _LANDLOCK_ACCESS_FS_READ_DIR,
    _LANDLOCK_ACCESS_FS_READ_FILE,
    abi_version,
    get_default_allowed_paths,
    get_landlock_wrapper,
)

# `_pin_path`, `access_for_path_fd` and the module-scope `PathBeneathAttr` are
# reached through the module rather than imported by name, and wrapped rather
# than bound, on purpose. Against the pre-fix module none of them exist; a name
# import aborts collection with an ImportError, which reports zero tests and
# proves nothing, and a module-level binding of a missing attribute does the same
# thing more quietly. Calling through defers it to the individual test, so each
# one fails on its own.
def _pin_path(path):
    return _landlock._pin_path(path)


def access_for_path_fd(fd, rights, path):
    return _landlock.access_for_path_fd(fd, rights, path)

PKG_PARENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr",
    "lib",
    "shani-chronoa",
)

needs_landlock = pytest.mark.skipif(abi_version() < 1, reason="kernel has no Landlock")

SECRET = "LEAKED-ATTACKER-SECRET"


def _swap_fixture(tmp):
    """A workspace policy will name, plus a sibling it must never reach.

    Returns (workspace, attacker_dir). Both are real directories at first; the
    caller performs the swap, because the point is what the *ruleset* does when
    the name has moved on by the time it is applied.
    """
    workspace = os.path.join(tmp, "ws")
    attacker = os.path.join(tmp, "attacker")
    os.makedirs(workspace)
    os.makedirs(attacker)
    with open(os.path.join(workspace, "ok.txt"), "w") as handle:
        handle.write("workspace-ok")
    with open(os.path.join(attacker, "secret.txt"), "w") as handle:
        handle.write(SECRET)
    return workspace, attacker


def _run_wrapped(wrapper, paths, target, extra_env=None):
    """Apply `paths` in a real child, then `cat` one file through the scope."""
    import json

    env = dict(os.environ)
    env["PYTHONPATH"] = PKG_PARENT
    env["LANLOCK_ALLOWED_PATHS"] = json.dumps([list(entry) for entry in paths])
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, wrapper, "cat", target],
        capture_output=True, text=True, timeout=60, env=env,
    )


@needs_landlock
class TestASwappedWorkspaceCannotStealTheGrant:
    """The end-to-end shape of the race: policy names a directory, an attacker
    makes that name resolve somewhere else, and the grant follows the name."""

    def test_the_attacker_file_is_not_readable_through_a_swapped_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, attacker = _swap_fixture(tmp)
            # The policy decision is made first, exactly as `_run_landlock`
            # does: it computes the allowlist in the parent and hands it to a
            # child that applies it.
            paths = get_default_allowed_paths(workspace)

            shutil.rmtree(workspace)
            os.symlink(attacker, workspace)

            wrapper = os.path.join(tmp, "wrapper.py")
            with open(wrapper, "w") as handle:
                handle.write(get_landlock_wrapper())

            leaked = _run_wrapped(
                wrapper, paths, os.path.join(attacker, "secret.txt")
            )

            assert SECRET not in leaked.stdout, (
                "the attacker directory was readable under the shipped default "
                f"allowlist, which denies it: the grant followed the swapped name "
                f"instead of the inode; stdout={leaked.stdout!r} "
                f"stderr={leaked.stderr!r}"
            )
            assert leaked.returncode != 0, (
                f"the confined child reported success while confined to the wrong "
                f"directory: {leaked.stdout!r}"
            )

    def test_the_refusal_names_the_component_rather_than_failing_opaquely(self):
        # An absent leak is not enough on its own. `landlock_add_rule` rejects a
        # descriptor pinned to a symlink with a bare EINVAL, indistinguishable
        # from a malformed attribute struct - so "nothing leaked" would also be
        # true of a version that merely broke every rule. This asserts the
        # refusal is the one that was designed.
        with tempfile.TemporaryDirectory() as tmp:
            workspace, attacker = _swap_fixture(tmp)
            paths = get_default_allowed_paths(workspace)
            shutil.rmtree(workspace)
            os.symlink(attacker, workspace)

            wrapper = os.path.join(tmp, "wrapper.py")
            with open(wrapper, "w") as handle:
                handle.write(get_landlock_wrapper())

            result = _run_wrapped(
                wrapper, paths, os.path.join(attacker, "secret.txt")
            )

            assert "symlink component" in result.stderr, (
                "the swapped entry was not refused by name; stderr="
                f"{result.stderr!r}"
            )
            assert "ws" in result.stderr

    def test_a_swap_racing_the_ruleset_never_lands_the_grant_on_the_attacker(self):
        """The race proper: the name is moving while the child pins it.

        A thread swaps the workspace between the real directory and a symlink to
        the attacker for the whole duration, so each child observes whichever
        state the kernel happens to give it. The assertion is that *no* child
        ever reads the attacker - not that some particular child refuses, which a
        slower swapper could arrange.
        """
        rounds = 25
        with tempfile.TemporaryDirectory() as tmp:
            workspace, attacker = _swap_fixture(tmp)
            real = os.path.join(tmp, "ws-real")
            paths = get_default_allowed_paths(workspace)

            wrapper = os.path.join(tmp, "wrapper.py")
            with open(wrapper, "w") as handle:
                handle.write(get_landlock_wrapper())

            stop = threading.Event()
            swaps = [0]

            def swapper():
                while not stop.is_set():
                    for phase in range(2):
                        try:
                            if phase == 0:
                                os.rename(workspace, real)
                                os.symlink(attacker, workspace)
                            else:
                                os.unlink(workspace)
                                os.rename(real, workspace)
                            swaps[0] += 1
                        except OSError:
                            pass

            thread = threading.Thread(target=swapper, daemon=True)
            thread.start()
            try:
                outcomes = []
                for _ in range(rounds):
                    result = _run_wrapped(
                        wrapper, paths, os.path.join(attacker, "secret.txt")
                    )
                    outcomes.append(result)
            finally:
                stop.set()
                thread.join(timeout=5)
                if os.path.islink(workspace):
                    os.unlink(workspace)
                if os.path.exists(real):
                    os.rename(real, workspace)

            # The control has to have done something, or "no leak in 25 rounds"
            # is a statement about a swapper that never swapped.
            assert swaps[0] > rounds, (
                f"the swapper only completed {swaps[0]} half-swaps over {rounds} "
                "rounds, so this run could not have caught a race it never "
                "created; the test is not evidence of anything"
            )

            leaks = [r for r in outcomes if SECRET in r.stdout]
            assert not leaks, (
                f"{len(leaks)}/{rounds} children read the attacker directory "
                f"while the workspace name was being swapped underneath them; "
                f"first leak: {leaks[0].stdout!r}"
            )
            # The swap count above is the control: it proves a race was created.
            assert all(r.returncode != 0 for r in outcomes), (
                "expected every round to refuse once the workspace name cannot be "
                "trusted; a round that exited 0 confined successfully, which "
                "means the swapper was not actually racing"
            )

    def test_the_workspace_is_not_canonicalised_behind_the_callers_back(self):
        """Why the workspace is the one entry left un-resolved.

        If `get_default_allowed_paths()` called `realpath()` on the workspace it
        would bake a swap into the policy at the exact point this file exists to
        protect: the child would then faithfully pin the attacker's target. The
        trusted system constants below it *are* resolved, and only because they
        are literals in that function.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace, attacker = _swap_fixture(tmp)
            shutil.rmtree(workspace)
            os.symlink(attacker, workspace)

            entries = dict(
                (path, rights)
                for path, rights in get_default_allowed_paths(workspace)
            )

            assert workspace in entries, (
                "the workspace was rewritten before the ruleset was built, so the "
                f"allowlist now names the attacker: {sorted(entries)}"
            )
            assert os.path.realpath(attacker) not in entries

    def test_a_swap_that_lands_before_the_policy_is_built_still_cannot_steal_it(self):
        """The same hole, with the other ordering.

        `test_the_attacker_file_is_not_readable_through_a_swapped_workspace`
        swaps *after* the allowlist is computed, which is the window this file
        exists to close. This one swaps *before*, which is what an attacker with
        a foothold in the sandbox directory would actually do - and it is the
        ordering that canonicalising the workspace would quietly turn into a
        permanent grant rather than a race.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workspace, attacker = _swap_fixture(tmp)
            shutil.rmtree(workspace)
            os.symlink(attacker, workspace)

            paths = get_default_allowed_paths(workspace)
            assert not any(
                path == os.path.realpath(attacker) for path, _ in paths
            ), "the allowlist already names the attacker, so there is no race left to test"

            wrapper = os.path.join(tmp, "wrapper.py")
            with open(wrapper, "w") as handle:
                handle.write(get_landlock_wrapper())

            leaked = _run_wrapped(
                wrapper, paths, os.path.join(attacker, "secret.txt")
            )

            assert SECRET not in leaked.stdout, (
                "an attacker who swapped the workspace before the allowlist was "
                f"built was granted the directory it pointed at; stdout={leaked.stdout!r}"
            )

    def test_the_trusted_system_constants_resolve_through_merged_usr(self):
        """The other side of the same decision, and the regression it guards.

        `_pin_path` refuses a symlinked component, and on a merged-`/usr` system
        `/bin`, `/sbin`, `/lib` and `/lib64` are all symlinks. If the constants
        were not resolved here, confinement would fail on every modern Linux.
        """
        with tempfile.TemporaryDirectory() as tmp:
            paths = [path for path, _ in get_default_allowed_paths(tmp)]

            for name in ("/bin", "/sbin", "/lib", "/lib64"):
                assert name not in paths, (
                    f"{name} reached the ruleset unresolved, so pinning it would "
                    "refuse on any merged-/usr system"
                )
            for name in ("/usr", "/dev", "/proc", "/tmp", "/run", "/"):
                assert name in paths, f"{name} went missing from the allowlist"

            # The positive control on the same property: every entry the parent
            # emits must be pinnable, which is the thing that actually broke.
            for path in paths:
                fd = _pin_path(path)
                os.close(fd)


class TestPinningRefusesWhatItCannotTrust:
    def test_a_symlinked_final_component_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target")
            os.makedirs(target)
            link = os.path.join(tmp, "link")
            os.symlink(target, link)

            with pytest.raises(OSError) as excinfo:
                _pin_path(link)

            assert "symlink component" in str(excinfo.value)
            assert excinfo.value.errno != 0

    def test_a_symlinked_intermediate_component_is_refused(self):
        """The half `O_NOFOLLOW` on its own does not cover.

        `O_NOFOLLOW` applies to the final component only; a plain `open()`
        follows every intermediate one. This is the case that separates a real
        component-at-a-time walk from simply adding the flag to the old code.
        """
        with tempfile.TemporaryDirectory() as tmp:
            attacker = os.path.join(tmp, "attacker")
            os.makedirs(attacker)
            middle = os.path.join(tmp, "middle")
            os.makedirs(middle)
            os.symlink(attacker, os.path.join(middle, "leaf"))

            with pytest.raises(OSError) as excinfo:
                _pin_path(os.path.join(middle, "leaf"))

            assert "leaf" in str(excinfo.value)

    def test_a_relative_path_is_refused(self):
        with pytest.raises(OSError) as excinfo:
            _pin_path("relative/path")

        assert "absolute" in str(excinfo.value)

    def test_a_dotdot_component_is_refused(self):
        # `..` cannot be normalised without a symlink resolution that could walk
        # back out of the component already pinned.
        with pytest.raises(OSError) as excinfo:
            _pin_path("/tmp/../etc")

        assert ".." in str(excinfo.value)

    def test_a_pinned_descriptor_survives_the_name_moving_on(self):
        """The property that makes the pin worth anything."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _attacker = _swap_fixture(tmp)
            moved = os.path.join(tmp, "ws-moved")

            fd = _pin_path(workspace)
            try:
                os.rename(workspace, moved)
                os.symlink(_attacker, workspace)
                try:
                    assert stat.S_ISDIR(os.fstat(fd).st_mode)
                    assert os.path.realpath(f"/proc/self/fd/{fd}") == moved, (
                        "the descriptor stopped naming the pinned inode once the "
                        "directory was renamed"
                    )
                finally:
                    os.unlink(workspace)
                    os.rename(moved, workspace)
            finally:
                os.close(fd)

    def test_the_descriptor_does_not_survive_into_an_exec(self):
        with tempfile.TemporaryDirectory() as tmp:
            fd = _pin_path(tmp)
            assert os.get_inheritable(fd) is False
            os.close(fd)

    def test_pinning_the_root_works(self):
        fd = _pin_path("/")
        try:
            assert stat.S_ISDIR(os.fstat(fd).st_mode)
        finally:
            os.close(fd)


class TestDirectoryOnlyRightsAreClassifiedThroughTheDescriptor:
    def test_a_directory_keeps_its_directory_rights(self):
        with tempfile.TemporaryDirectory() as tmp:
            fd = _pin_path(tmp)
            try:
                rights = _LANDLOCK_ACCESS_FS_READ_DIR | _LANDLOCK_ACCESS_FS_READ_FILE
                assert access_for_path_fd(fd, rights, tmp) == rights
            finally:
                os.close(fd)

    def test_directory_rights_on_a_non_directory_are_refused_not_masked(self):
        """The two decisions were made in different processes.

        Rights are chosen in the parent by looking at a pathname; the descriptor
        is resolved in the child. If they disagree, a grant carrying `READ_DIR`
        on a plain file would silently grant less than policy believes - so the
        disagreement is raised rather than masked away.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plain.txt")
            with open(path, "w") as handle:
                handle.write("x")

            fd = _pin_path(path)
            try:
                assert access_for_path_fd(fd, _LANDLOCK_ACCESS_FS_READ_FILE, path) == (
                    _LANDLOCK_ACCESS_FS_READ_FILE
                )
                with pytest.raises(OSError) as excinfo:
                    access_for_path_fd(
                        fd,
                        _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_READ_DIR,
                        path,
                    )
                assert "directory-only rights" in str(excinfo.value)
            finally:
                os.close(fd)

    def test_apply_filesystem_allowlist_actually_classifies_through_the_descriptor(self):
        """Covers the call site, which the test above does not.

        `test_directory_rights_on_a_non_directory_are_refused_not_masked` calls
        `access_for_path_fd` itself, so it passes whether or not
        `apply_filesystem_allowlist` ever calls it - verified by mutation:
        removing that one call left this whole file green.

        The errno is asserted, not just the refusal, and that distinction was
        found by running the mutation rather than by reasoning about it. This
        kernel's `landlock_add_rule` *also* rejects directory-only rights on a
        plain file, with EINVAL (22). So a version with the call removed still
        refuses - it just refuses with the same bare EINVAL the kernel returns for
        a malformed attribute struct, which is the exact ambiguity this module's
        own history is a list of. ENOTDIR (20) is the module naming the problem
        before the syscall does.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plain.txt")
            with open(path, "w") as handle:
                handle.write("x")

            program = f"""
import sys
sys.path.insert(0, {PKG_PARENT!r})
from shani_chronoa.sandbox.landlock import apply_filesystem_allowlist
try:
    apply_filesystem_allowlist([({path!r}, {_LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_READ_DIR!r})])
except OSError as exc:
    print("REFUSED", exc.errno)
else:
    print("APPLIED")
"""
            result = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True, text=True, timeout=60,
            )

            assert result.returncode == 0, result.stderr
            assert "REFUSED" in result.stdout, (
                "apply_filesystem_allowlist added a rule carrying directory-only "
                "rights for a non-directory, so the rights chosen in the parent "
                f"and the descriptor resolved in the child were never compared; "
                f"stdout={result.stdout!r}"
            )
            assert f"REFUSED {errno.ENOTDIR}" in result.stdout, (
                "the refusal came from the kernel rather than from the module. "
                f"Expected ENOTDIR ({errno.ENOTDIR}) naming the disagreement; got "
                f"{result.stdout!r}. A bare EINVAL here is indistinguishable from "
                "a malformed landlock_path_beneath_attr, which is the failure this "
                "module has already shipped once."
            )


@needs_landlock
class TestAGrantBuiltFromAPinnedDescriptorSurvivesTheSwap:
    """The kernel half, run for real: the grant follows the descriptor."""

    def test_swapping_the_name_after_pinning_does_not_move_the_grant(self):
        # The attacker lives outside `tmp` on purpose: this test has to grant the
        # parent directory broadly so the confined child can perform the rename
        # itself, and a sibling under it would then be readable through that
        # grant rather than through the one under test.
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as outside:
            workspace = os.path.join(tmp, "ws")
            os.makedirs(workspace)
            with open(os.path.join(workspace, "ok.txt"), "w") as handle:
                handle.write("workspace-ok")
            attacker = os.path.join(outside, "attacker")
            os.makedirs(attacker)
            secret = os.path.join(attacker, "secret.txt")
            with open(secret, "w") as handle:
                handle.write(SECRET)
            moved = os.path.join(tmp, "ws-moved")

            program = f"""
import ctypes, os, sys
sys.path.insert(0, {PKG_PARENT!r})
from shani_chronoa.sandbox import landlock as L

# Two rules: the workspace, which is what the race is about, and the parent
# directory granted everything, so this test can perform the swap itself - the
# child is confined, and renaming a directory needs REFER, REMOVE_DIR and
# MAKE_DIR on the parent.
known = L._get_filesystem_rights(L.abi_version())
attr = L.RulesetAttr(handled_access_fs=known)
rs = L._syscall(L._LANDLOCK_SYSCALL_CREATE_RULESET,
                ctypes.addressof(attr), ctypes.sizeof(attr), 0)

for path, rights in (({workspace!r}, L._LANDLOCK_ACCESS_FS_READ_FILE),
                     ({tmp!r}, known)):
    fd = L._pin_path(path)
    try:
        rule = L.PathBeneathAttr(parent_fd=fd, allowed_access=rights)
        L._syscall(L._LANDLOCK_SYSCALL_ADD_RULE, rs, L._LANDLOCK_RULE_PATH_BENEATH,
                   ctypes.addressof(rule), 0)
    finally:
        os.close(fd)

L.no_new_privs()
L._syscall(L._LANDLOCK_SYSCALL_RESTRICT_SELF, rs, 0)
os.close(rs)

# Only now does the attacker move the name, after the grant exists.
os.rename({workspace!r}, {moved!r})
os.symlink({attacker!r}, {workspace!r})

for label, path in (("original", os.path.join({moved!r}, "ok.txt")),
                    ("attacker", {secret!r})):
    try:
        print(label, "READABLE", open(path).read())
    except OSError as exc:
        print(label, "refused", exc.errno)
"""
            result = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True, text=True, timeout=60,
            )

            assert result.returncode == 0, result.stderr
            assert "original READABLE workspace-ok" in result.stdout, (
                f"the pinned grant stopped working; stdout={result.stdout!r}"
            )
            assert "attacker refused 13" in result.stdout, (
                "renaming the pinned directory let the grant move onto whatever "
                f"took its name; stdout={result.stdout!r}"
            )

    def test_the_kernel_accepts_an_opath_descriptor_for_a_real_directory(self):
        # Guards the mechanism the whole fix rests on: if a future kernel or a
        # refactor stopped accepting O_PATH descriptors, confinement would fail
        # closed everywhere, and the race tests above would all still "pass"
        # because a refusal is also not a leak.
        with tempfile.TemporaryDirectory() as tmp:
            program = f"""
import ctypes, os, sys
sys.path.insert(0, {PKG_PARENT!r})
from shani_chronoa.sandbox import landlock as L
R = L._LANDLOCK_ACCESS_FS_READ_FILE
attr = L.RulesetAttr(handled_access_fs=R)
rs = L._syscall(L._LANDLOCK_SYSCALL_CREATE_RULESET,
                ctypes.addressof(attr), ctypes.sizeof(attr), 0)
fd = L._pin_path({tmp!r})
rule = L.PathBeneathAttr(parent_fd=fd, allowed_access=R)
L._syscall(L._LANDLOCK_SYSCALL_ADD_RULE, rs, L._LANDLOCK_RULE_PATH_BENEATH,
           ctypes.addressof(rule), 0)
L.no_new_privs()
L._syscall(L._LANDLOCK_SYSCALL_RESTRICT_SELF, rs, 0)
os.close(rs); os.close(fd)
open(os.path.join({tmp!r}, "ok.txt")).read()
print("GRANT-WORKS")
"""
            with open(os.path.join(tmp, "ok.txt"), "w") as handle:
                handle.write("hi")
            result = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True, text=True, timeout=60,
            )

            assert "GRANT-WORKS" in result.stdout, (
                f"an O_PATH descriptor was refused for a real directory: "
                f"{result.stderr!r}"
            )
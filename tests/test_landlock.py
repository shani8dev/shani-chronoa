"""Landlock confinement, verified by running it.

Replaces an earlier version of this file whose assumptions did not match the
kernel. Every assertion here was established by executing the real syscalls on
a Landlock-capable kernel, not by reading the module: the four bugs it takes to
make `landlock_add_rule` succeed are all invisible until the kernel answers.

The confinement tests must run in a CHILD. `landlock_restrict_self` cannot be
undone for the life of a process, so calling it in-process would brick the test
runner exactly as it would brick the app. That the parent stays unrestricted is
itself asserted, because it is the failure that matters.
"""

import os
import subprocess
import sys
import tempfile

import pytest

from shani_chronoa.sandbox.landlock import (
    _LANDLOCK_ACCESS_FS_READ_FILE,
    _get_filesystem_rights,
    abi_version,
    get_default_allowed_paths,
    get_landlock_wrapper,
    no_new_privs,
)

PKG_PARENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr",
    "lib",
    "shani-chronoa",
)

needs_landlock = pytest.mark.skipif(abi_version() < 1, reason="kernel has no Landlock")


def _confined_child(allow_dir, inside, outside):
    """Apply an allowlist in a child, then read one path in and one out."""
    program = f"""
import sys
sys.path.insert(0, {PKG_PARENT!r})
from shani_chronoa.sandbox.landlock import apply_filesystem_allowlist
apply_filesystem_allowlist([({allow_dir!r}, {_LANDLOCK_ACCESS_FS_READ_FILE!r})])
try:
    open({inside!r}).read(); print("INSIDE_ALLOWED")
except OSError as e: print("INSIDE_REFUSED", e.errno)
try:
    open({outside!r}).read(); print("OUTSIDE_ALLOWED")
except OSError as e: print("OUTSIDE_REFUSED", e.errno)
"""
    return subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
    )


def _workspace():
    tmp = tempfile.TemporaryDirectory()
    allowed = os.path.join(tmp.name, "allowed")
    os.makedirs(allowed)
    with open(os.path.join(allowed, "ok.txt"), "w") as h:
        h.write("hi")
    secret = os.path.join(tmp.name, "secret.txt")
    with open(secret, "w") as h:
        h.write("nope")
    return tmp, allowed, secret


def test_abi_version_is_reported():
    version = abi_version()

    assert isinstance(version, int)
    assert version >= 0, "a negative value means the query itself failed"


@needs_landlock
def test_handled_mask_carries_no_non_filesystem_bits():
    # Bits 16/17 are network rights and live in `handled_access_net`. Put one
    # in `handled_access_fs` and the kernel rejects the WHOLE ruleset with
    # EINVAL, which is how the first version of this module confined nothing.
    mask = _get_filesystem_rights(abi_version())

    assert mask & ~0xFFFF == 0, "handled_access_fs may only carry bits 0-15"


@needs_landlock
def test_a_path_inside_the_allowlist_is_readable():
    tmp, allowed, secret = _workspace()
    try:
        result = _confined_child(allowed, os.path.join(allowed, "ok.txt"), secret)

        assert result.returncode == 0, result.stderr
        assert "INSIDE_ALLOWED" in result.stdout
    finally:
        tmp.cleanup()


@needs_landlock
def test_a_path_outside_the_allowlist_is_refused_with_eacces():
    tmp, allowed, secret = _workspace()
    try:
        result = _confined_child(allowed, os.path.join(allowed, "ok.txt"), secret)

        assert result.returncode == 0, result.stderr
        assert "OUTSIDE_REFUSED 13" in result.stdout, (
            "a read outside the allowlist must fail with EACCES, which is what "
            f"proves the ruleset is enforced rather than merely created; got "
            f"{result.stdout!r}"
        )
    finally:
        tmp.cleanup()


@needs_landlock
def test_confining_a_child_leaves_the_parent_unrestricted():
    # If the module ever applied the ruleset in-process, this runner - and the
    # real app - would lose filesystem access permanently, with no undo.
    tmp, allowed, secret = _workspace()
    try:
        _confined_child(allowed, os.path.join(allowed, "missing"), secret)

        with open(secret) as handle:
            assert handle.read() == "nope", "the parent was confined by its child"
    finally:
        tmp.cleanup()


def test_no_new_privs_does_not_raise():
    no_new_privs()


def test_default_allowed_paths_are_absolute_and_non_empty():
    with tempfile.TemporaryDirectory() as tmp:
        paths = get_default_allowed_paths(tmp)

        assert paths, "confinement with no allowed paths cannot run anything"
        # Entries are (path, access_rights) pairs, not bare strings.
        assert all(isinstance(entry, tuple) and len(entry) == 2 for entry in paths)
        assert all(os.path.isabs(entry[0]) for entry in paths)


def test_the_wrapper_is_what_applies_the_ruleset():
    wrapper = get_landlock_wrapper()

    # The wrapper delegates to the module rather than inlining the syscall, so
    # what matters is that restricting happens in the child program and never in
    # the parent - `apply_filesystem_allowlist` is the only thing that may call
    # landlock_restrict_self, and only ever from here.
    assert "apply_filesystem_allowlist" in wrapper, (
        "the wrapper must be what applies the ruleset, so the parent never does"
    )
    assert "execvp" in wrapper, "the wrapper should exec the real command inside the scope"

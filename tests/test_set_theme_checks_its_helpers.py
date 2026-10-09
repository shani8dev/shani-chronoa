"""`set_theme` shelled to three Plasma binaries it never checked for.

Found by `tools/cli_matrix.py --check` on a real GNOME image, not by reading the
skill. The slot-test `chronoa-matrix` reported:

    set_theme: runs kreadconfig6, which is not installed here, and unchecked
    set_theme: runs plasma-apply-colorscheme, which is not installed here, and unchecked
    set_theme: runs plasma-lookandfeeltool, which is not installed here, and unchecked

**Nothing crashed, which is why it survived.** `_run_cmd` returns `None` when the
binary is absent and every caller handled that, so a person asking "am I in dark
mode?" on a GNOME desktop got a bare "no" with no reason — and a person on a
machine missing one helper got silence rather than the name of the package that
would fix it. That is the confident-wrong-answer shape this repo documents for the
sense layer, in a skill rather than a sense.

Two halves, and **both are asserted**, because either alone leaves the defect:

- **It checks first.** `_missing_kde_tools()` consults `shutil.which` over
  `_KDE_TOOLS` before anything is run, which is what `--check` looks for.
- **It names the package.** `files._PACKAGE_HINTS` gained `kreadconfig6 ->
  kconfig` and `plasma-apply-colorscheme -> plasma-workspace`, read out of
  pacman's own file database via `chronoa-matrix.json`'s `commands[].package` —
  the tree's only machine-readable authority for that question.

**`plasma-lookandfeeltool` deliberately has no package hint**, and that is the
assertion most likely to look like an oversight. It is a Plasma 5 binary; the
matrix on a Plasma 6 image does not contain it at all, which is the same fact
`set_theme`'s own docstring records. Naming a package for a binary that does not
exist would be the invented answer `_PACKAGE_HINTS` exists to avoid — so the test
asserts the *absence*, and says why.

**The matrix is the authority and it is checked against a real image.** When one is
present these assertions read `commands[].package` out of it rather than trusting
the literal above, so a future Arch move is caught here instead of shipping a
package name that no longer contains the binary — which is exactly the defect
class `test_package_names_match_arch.py` was written for.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import files  # noqa: E402
from shani_chronoa.skills import set_theme  # noqa: E402

#: Where a matrix written by a slot run may live, newest first.
_MATRIX_GLOBS = (
    "chronoa-matrix/chronoa-matrix.json",
    "../shani-install-media/test-env/matrix-out/matrix-*.json",
)


def _matrix() -> dict | None:
    for pattern in _MATRIX_GLOBS:
        for path in sorted(_REPO.glob(pattern)):
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError):
                continue
    return None


class TestItChecksBeforeItRuns:
    def test_every_plasma_helper_is_in_one_checked_list(self):
        """A list is what makes `--check` able to see it.

        `shutil.which` over `_KDE_TOOLS` names no literal binary for the pattern
        that scans for, which is why the names are asserted here: the matrix's
        own comment says a list-shaped lookup "checks first too, but names no
        literal the pattern can see". So the list is the contract.
        """
        assert set(set_theme._KDE_TOOLS) == {
            "kreadconfig6", "kreadconfig5", "plasma-apply-colorscheme",
            "plasma-lookandfeeltool"}, (
            f"the Plasma helpers this skill shells to are {set_theme._KDE_TOOLS}, "
            f"so the list `_missing_kde_tools()` checks no longer matches what it "
            f"runs")

    def test_every_binary_it_shells_to_is_on_that_list(self):
        """The reverse direction, which is the one that would actually bite.

        Asserting the list is right does not prove the *skill* uses it: a new
        `subproc.run(["some-new-helper"])` outside the list would pass the test
        above and still be unchecked. This is the guard against that.

        **Literals from `_run_cmd` only**, which is the narrow pattern that
        matches real argv. My first version also swept tuple literals and
        matched `("status", ...)` - a tuple inside the answer text - reporting
        it as a binary the skill shells to. A check that flags a string that is
        not a command is the "a negative control that cannot fail" trap reading
        the other way: it passes for a reason that has nothing to do with the
        thing it is meant to see.
        """
        source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa"
                  "/skills/set_theme.py").read_text()
        run = re.findall(r'_run_cmd\(\s*\[\s*"([^"]+)"', source)
        assert "kreadconfig6" in run, (
            "this test's pattern no longer matches the module, so it would pass "
            "whatever the module did")
        unchecked = sorted(set(run) - set(set_theme._KDE_TOOLS) - {"gsettings"})
        assert not unchecked, (
            f"set_theme runs {unchecked} but `_missing_kde_tools()` does not "
            f"check for them, so a machine without them gets silence instead of "
            f"the package name")

    def test_the_check_really_asks_which(self, monkeypatch):
        """Control: the helper is not a hardcoded list of "always missing".

        A `_missing_kde_tools()` that returned the same three names whatever the
        machine would satisfy both tests above while being useless.
        """
        asked = []

        def fake_which(name):
            asked.append(name)
            return None

        monkeypatch.setattr(shutil, "which", fake_which)
        assert set(set_theme._missing_kde_tools()) == set(set_theme._KDE_TOOLS)
        assert sorted(asked) == sorted(set_theme._KDE_TOOLS)

        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        assert set_theme._missing_kde_tools() == [], (
            "with every helper present the check still reports them missing, so "
            "it is answering from a constant rather than from the machine")


class TestItNamesThePackage:
    @pytest.mark.parametrize("binary,package", [
        ("kreadconfig6", "kconfig"),
        ("plasma-apply-colorscheme", "plasma-workspace"),
    ])
    def test_the_hint_names_a_package(self, binary, package):
        said = files.tool_missing(binary, "read the desktop appearance")
        assert package in said, (
            f"{binary}'s absence is answered without naming {package}, so the "
            f"reader is told what is wrong and not what to do: {said!r}")

    def test_the_answer_says_the_read_failed_not_that_the_theme_is_light(self):
        """A bare "no" is the confident wrong answer this fixes."""
        said = files.tool_missing("kreadconfig6", "read the desktop appearance")
        assert "nothing was done" in said, said

    def test_a_plasma_5_binary_names_no_package(self):
        """The assertion most likely to look like an oversight.

        `plasma-lookandfeeltool` does not exist on Plasma 6, so there is no
        package to name and the honest answer is the generic one. A hint added
        here would send somebody to install a package that cannot help.
        """
        assert "plasma-lookandfeeltool" not in files._PACKAGE_HINTS, (
            "plasma-lookandfeeltool is a Plasma 5 binary. The matrix on a "
            "Plasma 6 image does not contain it, so naming a package for it "
            "would be inventing the answer this table exists to avoid")


class TestAgainstARealImage:
    def test_the_matrix_agrees_with_the_hints(self):
        """Where a matrix exists, it is the authority - not this file.

        Read out of `commands[].package`, so an Arch move that moves a binary
        fails here rather than shipping a hint that no longer installs it.
        """
        matrix = _matrix()
        if matrix is None:
            pytest.skip("no matrix from a slot run; run slot-test chronoa-matrix")
        packages = {c["command"]: c.get("package")
                    for c in matrix.get("commands", [])}
        for binary, expected in (("kreadconfig6", "kconfig"),
                                 ("plasma-apply-colorscheme", "plasma-workspace")):
            actual = packages.get(binary)
            assert actual == expected, (
                f"the matrix read out of pacman's file database says "
                f"{binary} is in {actual!r}, but files._PACKAGE_HINTS says "
                f"{expected!r}. The matrix is right and the hint is stale")
        # The Plasma 5 binary is absent from a Plasma 6 image, which is the
        # measurement behind the test above rather than an assumption about it.
        if matrix.get("profile", "").startswith("plasma") or \
                "plasma-apply-colorscheme" in packages:
            assert "plasma-lookandfeeltool" not in packages, (
                "this image has plasma-lookandfeeltool, so the Plasma 5 "
                "assumption behind test_a_plasma_5_binary_names_no_package no "
                "longer holds and that test needs re-reading")

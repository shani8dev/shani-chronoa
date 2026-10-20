"""Every binary this tree looks for has a package to name.

`files.tool_missing(binary, purpose)` is the sentence a skill says when a
helper is absent, and **its whole job is to name the package that provides
the binary**. When the hint table has no entry it falls back to the literal
string *"the package that provides it"*, which tells a person nothing they
can act on - they cannot install a package with that name.

So this is a **gate on the population**, not on a table: `tools/audit_missing
_tool_hints.py` walks the AST for every call that looks for a binary
(`tool_missing`, `which`, `shutil.which`, `have`, `has_tool`, `available`,
`require`) and reports the ones with no package name. The audit is the
instrument; this makes sure it stays quiet.

**One entry is allowed, and it is deliberate.** `voices.py` looks for a
*system* Piper "if a package ever provides it" and otherwise uses the
user-local install the setup wizard puts there - and there is no Arch package
to name, because the repo already knows that **`piper` on Arch is a
gaming-mouse configurator**, not Piper TTS. So the lookup is for a
different name, and the refusal it feeds is the wizard's, not a package
install.

The rest were named on 2026-10-20, each checked against **Arch's own file
listing** rather than from memory - and four of the candidates were wrong,
three of them naming packages that **do not exist in Arch at all**
(`gtk2`, `xorg-x11-server-utils`, `x11-apps`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_AUDIT = _REPO / "tools" / "audit_missing_tool_hints.py"
_ROOT = _REPO / "usr" / "lib" / "shani-chronoa"

#: The one binary allowed to have no package name, and why.
ALLOWED = {
    "piper-tts":
        "a forward-looking lookup for a system Piper: the engine the wizard "
        "installs is in the user's home, and Arch's `piper` is a "
        "gaming-mouse configurator rather than Piper TTS, so there is no "
        "package to name",
}

#: A line of the audit's output: two spaces, the binary, then where it was.
_LINE = re.compile(r"^  (\S+)\s+\S+\.py:\d+")


def _audit_output() -> str:
    out = subprocess.run(
        [sys.executable, str(_AUDIT), str(_ROOT)],
        capture_output=True, text=True, cwd=str(_REPO), timeout=300,
    )
    assert out.returncode == 0, (
        f"the audit failed:\n{out.stdout}\n{out.stderr}")
    return out.stdout


def _unhinted() -> "set[str]":
    """The binaries the audit reports with no package name."""
    found = set()
    for line in _audit_output().splitlines():
        match = _LINE.match(line)
        if match:
            found.add(match.group(1))
    return found


class TestEveryBinaryNamesAPackage:
    """The population, and the one deliberate exception."""

    def test_the_audit_reports_only_the_deliberate_one(self):
        unhinted = _unhinted()
        assert unhinted == set(ALLOWED), (
            f"{len(unhinted)} binary/binararies the tree looks for have no "
            f"package name, so the refusal says 'the package that provides "
            f"it': {sorted(unhinted)}\n"
            f"Give each one its Arch package (checked against the package's "
            f"own file listing), or add it to ALLOWED here with the reason "
            f"it is deliberate.")

    def test_the_audit_actually_ran(self):
        """**The control, and the reason the other test means anything.**

        A gate over a population that is empty measures nothing, and the
        audit's own output is the evidence that it walked the tree at all.
        """
        out = _audit_output()
        assert "tool reference(s) resolved from" in out, out
        assert re.search(r"\d+ tool reference\(s\) resolved from \d+ files", out), out

    def test_a_binary_the_table_does_not_know_is_still_honest(self):
        """The fallback must stay a *fallback*, not a confident name.

        The table must not turn an unknown binary into a wrong package -
        that is the confident wrong answer the table exists to avoid.
        """
        sys.path.insert(0, str(_ROOT))
        from shani_chronoa import files

        out = files.tool_missing("definitely-not-a-real-binary", "do a thing")
        assert "not installed" in out
        assert "the package that provides it" in out
        assert "nothing was done" in out


class TestThePackageNamesAreRealOnes:
    """The names themselves, and the one that was not a package at all."""

    def test_the_x11_names_are_the_ones_arch_uses(self):
        """**Three of the four were wrong, and two of those names do not exist.**

        `x11-apps` and `xorg-x11-server-utils` are not Arch package names,
        so a refusal naming them sends a person to install a package that
        cannot be found - which is worse than naming nothing.
        """
        sys.path.insert(0, str(_ROOT))
        from shani_chronoa import files

        assert files._PACKAGE_HINTS["xwd"] == "xorg-xwd"
        assert files._PACKAGE_HINTS["xset"] == "xorg-xset"
        assert files._PACKAGE_HINTS["xinput"] == "xorg-xinput"
        assert files._PACKAGE_HINTS["gtk-launch"] == "gtk3"

    def test_the_screen_capture_refusal_names_a_package_that_exists(self):
        """The defect the same check found: a package that is not a package.

        `screengrab.py` told a person that `xwd` is "in the x11-apps
        package" - and no such package exists on Arch, so the sentence
        could not be acted on.
        """
        source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/screengrab.py")
        text = source.read_text(encoding="utf-8")
        assert "is in the xorg-xwd package" in text, (
            "the refusal should name the package that ships xwd")
        # **The name may still appear, in a sentence that says it is wrong.**
        # Both the refusal and the module's own prose were corrected in the
        # same pass, and the correction has to *name* the wrong name to be
        # useful - so the assertion is on what each line says, not on the
        # file not containing the string.
        for number, line in enumerate(text.splitlines(), 1):
            if "x11-apps" in line:
                assert "not" in line, (
                    f"screengrab.py:{number} names x11-apps without saying it "
                    f"is wrong: {line.strip()}")

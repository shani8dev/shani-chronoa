"""Every program this package runs is declared by its PKGBUILD - or on every
Shanios image, or left out on purpose with a reason in the PKGBUILD.

The check is shani-pkgbuilds' tools/check-deps.py: it runs the PKGBUILD's own
package() over this checkout, reads what the packaged files start (subprocess
and shutil.which, Process commands, shell scripts and `sh -c` strings) and
looks each program's Arch package up in the map built from pacman's file
database. What it found the first time it ran (2026-11-13): pw-record is in
pipewire-audio, not pipewire; pactl was "an optdepend" in a docstring only;
the Document Viewer needed mupdf-tools for nothing PyMuPDF could not do.

When it fails: declare the package (depends, or optdepends with what it is
for), or add `# check-deps: unlisted PROG - why` to the PKGBUILD. A program it
has never seen is "NOT IN MAP": run `tools/check-deps.py --refresh` in an Arch
container (shani-pkgbuilds/AGENTS.md).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = Path(os.environ.get("SHANI_PKGBUILDS", ROOT.parent / "shani-pkgbuilds")) / "tools" / "check-deps.py"
PKGBUILD = TOOL.parent.parent / ROOT.name / "PKGBUILD"

pytestmark = pytest.mark.skipif(not TOOL.exists() or not PKGBUILD.exists(),
                                reason="no shani-pkgbuilds beside this repo")


def test_every_program_run_is_declared():
    run = subprocess.run([sys.executable, str(TOOL), str(ROOT), "--pkgbuild", str(PKGBUILD)],
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr

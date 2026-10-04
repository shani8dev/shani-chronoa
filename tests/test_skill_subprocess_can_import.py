"""A skill runs in a *fresh* interpreter, which cannot see the parent's sys.path.

Every skill is invoked as `python3 -c "from shani_chronoa.skills.X import Y"`.
That child inherits the environment but not the parent's `sys.path`, and the
package installs to `/usr/lib/shani-chronoa/`, which is not a default
`site-packages` entry. So the child needs `PYTHONPATH` pointing at the package
directory, and nothing else supplies it.

This was broken for the entire life of the project and looked fine throughout,
for two compounding reasons. The launcher scripts fix their own `sys.path` with
`sys.path.insert`, which is never exported to anything they spawn; and
`redaction.child_env()` copies the parent environment, so any
developer who happened to have `PYTHONPATH` set in their shell saw every call
succeed. From a real `.desktop` launch, where it is not set, every single skill
call died with `ModuleNotFoundError: No module named 'shani_chronoa'`.

The MCP surface was the loudest symptom: `tools/list` answered with all 27
tools, and every single `tools/call` failed.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from shani_chronoa.sandbox.executor import _child_pythonpath

REPO = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO / "usr" / "lib" / "shani-chronoa"


def _child_env_without_pythonpath():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


class TestTheChildPathIsComputed:
    def test_it_points_at_the_directory_containing_the_package(self):
        assert _child_pythonpath().split(os.pathsep)[0] == str(PACKAGE_PARENT)

    def test_it_survives_the_parent_having_no_pythonpath(self, monkeypatch):
        monkeypatch.delenv("PYTHONPATH", raising=False)
        assert _child_pythonpath().split(os.pathsep)[0] == str(PACKAGE_PARENT)

    def test_it_appends_rather_than_replaces_an_existing_one(self, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/somewhere/of/the/users/own")
        got = _child_pythonpath()
        assert got.split(os.pathsep)[0] == str(PACKAGE_PARENT)
        assert "/somewhere/of/the/users/own" in got, (
            "a caller's own PYTHONPATH entries are clobbered")


class TestAChildCanActuallyImportThePackage:
    """The end-to-end claim, stated as an import rather than a string check."""

    def test_a_bare_python3_can_import_with_the_computed_path(self):
        proc = subprocess.run(
            [sys.executable, "-c", "import shani_chronoa; print(shani_chronoa.__file__)"],
            env={**_child_env_without_pythonpath(), "PYTHONPATH": _child_pythonpath()},
            capture_output=True, text=True, timeout=60, cwd=str(REPO),
        )
        assert proc.returncode == 0, (
            f"the child still cannot import the package: {proc.stderr.strip()}")

    def test_and_it_fails_without_it(self):
        """The negative control. Without this the test above proves nothing -
        a PYTHONPATH that is simply never needed would pass it too."""
        proc = subprocess.run(
            [sys.executable, "-c", "import shani_chronoa"],
            env={k: v for k, v in _child_env_without_pythonpath().items()
                 if k != "PYTHONPATH"},
            capture_output=True, text=True, timeout=60, cwd="/",
        )
        assert proc.returncode != 0, (
            "importing shani_chronoa succeeded with no PYTHONPATH at all, so "
            "the positive test is not testing what it claims")

    @pytest.mark.parametrize("tool,args", [
        ("get_datetime", {}),
        ("calculate", {"expression": "0.1+0.2"}),
    ])
    def test_a_real_skill_call_succeeds_with_no_pythonpath_in_the_parent(
            self, tool, args, monkeypatch):
        """Through `execute_tool` itself, with the parent's PYTHONPATH gone."""
        monkeypatch.delenv("PYTHONPATH", raising=False)
        from shani_chronoa.tools import execute_tool

        result = execute_tool(tool, args)
        assert "ModuleNotFoundError" not in result, (
            f"the skill subprocess could not import the package: {result[:200]}")
        assert "ERROR(exit=" not in result, result[:200]
        if tool == "calculate":
            assert result.startswith("0.3"), result[:200]

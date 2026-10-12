"""The /tmp reclaimer must never delete what this run is still using.

`conftest._reclaim_the_temp_dirs_this_suite_makes` exists because 14
`tempfile.mkdtemp()` call sites are not inside a `with`, and a run left **12 GB**
in `/tmp` - which on a 16 GB tmpfs makes unrelated tests fail with
`OSError: [Errno 122] Disk quota exceeded` while copying the schema.

**And it was deleting the live schema.** `COMPILED_SCHEMA_DIR` is built at
conftest import with `prefix="chronoa-schema-"`, and `"chronoa-"` is the first
entry in `_OWNED_TMP_PREFIXES`. So whichever session finished first removed the
compiled schema every other session was pointing at, and the signature is the
worst kind: `ChronoaConfig` logs one warning, writes nothing, and reads every
consent key back as its Python default. A run with no schema reported **48
teardown errors and 34 failures** and named nothing about a schema.

The fix is a liveness guard rather than a name change - dropping the prefix would
have leaked every `chronoa-schema-*` a dead run left, which is the reclaimer's
actual purpose.

**This file exists because a guard nobody can see failing is a guard nobody
should rely on**, so it plants a directory the reclaimer *would* collect and
requires it to survive, and plants one it *would not* collect and requires that
to survive too. The first is the property; the second is the control that stops
the first passing because the reclaimer stopped working.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_PROBE = r'''
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, {tests!r})
import conftest

tmp = Path(tempfile.gettempdir())
prefixes = conftest._OWNED_TMP_PREFIXES

# The one this session is using - the schema dir, under a prefix the reclaimer
# owns. This is the defect: before the guard it was collected mid-run.
live = Path(tempfile.mkdtemp(prefix="chronoa-schema-probe-"))
# And one nothing points at, which is what the reclaimer is for.
dead = Path(tempfile.mkdtemp(prefix="chronoa-dead-probe-"))

os.environ["GSETTINGS_SCHEMA_DIR"] = str(live)

# Run the reclaimer's own body, in a subprocess standing in for "this run ends".
import shutil
in_use = set()
for var in ("GSETTINGS_SCHEMA_DIR", "XDG_DATA_HOME", "XDG_STATE_HOME",
            "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME"):
    value = os.environ.get(var)
    if value:
        in_use.add(os.path.realpath(value))

for entry in tmp.iterdir():
    if not entry.is_dir() or not entry.name.startswith(prefixes):
        continue
    if os.path.realpath(entry) in in_use:
        continue
    shutil.rmtree(entry, ignore_errors=True)

print("LIVE_SURVIVED", live.exists())
print("DEAD_COLLECTED", not dead.exists())
shutil.rmtree(live, ignore_errors=True)
'''


def _run():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(tests=str(_REPO / "tests"))],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    out = dict(
        line.split(" ", 1)
        for line in result.stdout.splitlines()
        if " " in line and line.split(" ", 1)[0].isupper()
    )
    return out, result.stderr


def test_a_directory_this_run_is_using_survives_the_reclaimer():
    """**The defect.** `live` matches `_OWNED_TMP_PREFIXES` and is this session's
    `GSETTINGS_SCHEMA_DIR`, so before the guard it was removed - and everything
    after it in the session read consent keys as their defaults."""
    out, stderr = _run()
    assert out.get("LIVE_SURVIVED") == "True", f"the live schema dir was collected\n{stderr}"


def test_a_directory_nothing_uses_is_still_collected():
    """**The control, and the reason the test above means something.**

    A guard that simply stopped the reclaimer working would pass the property
    test and stop reclaiming 12 GB - which is the thing this fixture exists to
    prevent. So the reclamation is asserted too, not assumed.
    """
    out, stderr = _run()
    assert out.get("DEAD_COLLECTED") == "True", (
        f"the reclaimer stopped reclaiming, so /tmp will fill again\n{stderr}"
    )


def test_the_schema_directory_is_where_the_reclaimer_cannot_reach_it():
    """**The measurement behind the bug, pinned as a relationship rather than a
    string.**

    Two things were true at once and neither is wrong on its own: the schema dir
    lived under `/tmp` with a prefix the reclaimer owned, and the reclaimer's
    guard was per-process. Two concurrent runs each saw only their own
    `GSETTINGS_SCHEMA_DIR`, so whichever finished first removed the other's live
    schema - measured, with `COMPILED_SCHEMA_DIR` for a running session pointing
    at a directory that had already been collected.

    So the invariant is **where it is built**, not what it is called: outside the
    directory the reclaimer scans, which is what makes it safe against a
    concurrent run rather than only against its own.
    """
    import tempfile

    sys.path.insert(0, str(_REPO / "tests"))
    import conftest

    schema_dir = Path(conftest.COMPILED_SCHEMA_DIR)
    tmp = Path(tempfile.gettempdir()).resolve()
    assert not schema_dir.resolve().is_relative_to(tmp), (
        f"the compiled schema is under /tmp again ({schema_dir}), where the "
        f"reclaimer can reach it from a concurrent run"
    )
    assert (schema_dir / "gschemas.compiled").is_file(), (
        f"the compiled schema is missing from {schema_dir}, which is exactly what "
        f"a previous session's reclaimer does to the next one"
    )

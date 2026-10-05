"""`labnetworks` used to raise `NameError` on every single call.

`_record_state()` called `record_path()` but the module imported only
`load_record` and `state_dir` from `netprovision`, so the name did not exist.
That made the sense 100% broken - not "unavailable", but a crash - and it
survived because `grep -rn labnetworks tests/` returned nothing at all. This
file is the reason that cannot happen again: the first test here is the one
that would have caught it.

The module's own docstring makes a promise worth pinning, too. It says an
unreadable record is UNKNOWN and never "none recorded", because those are very
different claims - a missing file genuinely means nobody has recorded one,
while a corrupt file means we cannot tell. The three states are kept apart
below.

Consent is granted explicitly in every test that reads, so a future default
change cannot turn these into tests that pass by refusing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.senses import discover_senses  # noqa: E402
from shani_chronoa.senses import labnetworks as L  # noqa: E402


def _allowed():
    return mock.patch.object(ChronoaConfig, "sense_allowed", lambda self, n: True)


def _text(result) -> str:
    return getattr(result, "content", None) or str(result)


class TestItDoesNotCrash:
    def test_it_registers(self):
        assert "labnetworks" in discover_senses()

    def test_a_consented_call_returns_a_reading_not_a_name_error(self):
        """The regression. `record_path` was never imported, so every call
        raised before any read happened - the sense could produce nothing at
        all, granted or not."""
        with _allowed():
            out = _text(L.SENSES[0].run({}))
        assert "NameError" not in out
        assert out.strip(), "a consented call must say something"

    def test_every_name_it_uses_is_imported(self):
        """Cheap and general: `_record_state` must not reference a name the
        module failed to import. This is the check that would have caught the
        original bug without needing to know what `record_path` does."""
        import ast
        import builtins

        tree = ast.parse(Path(L.__file__).read_text())
        imported = set(dir(builtins))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                imported.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                imported.add(node.id)
            elif isinstance(node, ast.arg):
                imported.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                # `except ... as exc` binds a name that is then read. Missing
                # this is how `record_path` slipped through in the first place:
                # a name used only inside a `try` is exactly the one an import
                # line is easiest to get wrong for.
                imported.add(node.name)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        missing = sorted(used - imported)
        assert not missing, f"{L.__name__} uses names it never binds: {missing}"


class TestTheThreeStatesAreKeptApart:
    """Missing file, unreadable file, and a real record are three answers."""

    def test_no_record_file_is_not_an_error(self, tmp_path):
        with _allowed(), mock.patch.object(L, "record_path", lambda: tmp_path / "absent.json"):
            assert "no lab networks" in _text(L.SENSES[0].run({})).lower()

    def test_a_corrupt_file_is_not_reported_as_none(self, tmp_path):
        """The sharp edge. A corrupt file means we cannot tell, which is not
        the same claim as "there are none"."""
        bad = tmp_path / "network-records.json"
        bad.write_text("{ this is not json")
        with _allowed(), mock.patch.object(L, "record_path", lambda: bad):
            out = _text(L.SENSES[0].run({})).lower()
        assert "no lab networks are recorded" not in out, (
            "a corrupt record was reported as 'none recorded', which is the "
            "confident wrong answer this module's docstring rules out"
        )

    def test_a_real_record_is_read(self, tmp_path):
        # The record is a dict keyed by name (`netprovision.load_record`
        # returns `{}` for anything else), not a list of entries.
        good = tmp_path / "network-records.json"
        good.write_text(json.dumps({
            "lab-a": {"cidr": "10.10.0.0/24", "created": "2026-10-01"},
        }))
        with _allowed(), mock.patch.object(L, "record_path", lambda: good):
            out = _text(L.SENSES[0].run({}))
        assert "lab-a" in out

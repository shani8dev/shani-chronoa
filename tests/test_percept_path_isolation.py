"""A store handed a scratch durable file must not publish into the real home.

`_live_path` was made a per-instance attribute by `163ced2` (2026-09-29) so that a
store with a custom `durable_path` would not publish its live view into a shared
location - but the fix only replaced the *read at write time* with a read in
`__init__`. The fallback was left as the module global, so:

    PerceptStore(durable_path=/tmp/.../memory.jsonl)
      live_path -> ~/.local/share/shani-chronoa/percepts/live.json

The live view is published on every `add()` and `clear_transient()`, so any
caller passing only a durable path wrote the real user's `live.json`. That is
not theoretical: it was rewritten at 01:19 and again at 01:57 by test runs,
holding a fabricated snapshot whose content was a verbatim match for a vision
sense fixture ("A terminal window showing a failed test run"), i.e. test data in
a real person's data directory, describing a machine state that never existed.

`PERCEPT_DIR` hardcodes `~/.local/share/...` and ignores `XDG_DATA_HOME`, so
pointing the environment at a temp dir does not contain this: the path is
captured at import time from the real `$HOME`. The only thing that contains it
is the store itself honouring the `durable_path` it was given.

The rule pinned here: `--durable-file PATH` means PATH and nowhere else. Both
files live beside each other, or neither does.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.senses import store as store_mod  # noqa: E402
from shani_chronoa.senses.context import Percept  # noqa: E402
from shani_chronoa.senses.store import PerceptStore  # noqa: E402


def _a_percept(content: str = "battery at 100") -> Percept:
    return Percept(
        sense="power",
        kind="state",
        content=content,
        created_at=time.time(),
        sensitivity="public",
        ttl_seconds=600.0,
    )


class TestLivePathFollowsDurablePath:
    def test_a_custom_durable_path_owns_the_live_view(self, tmp_path):
        """The regression: no `live_path` given means "beside the durable file"."""
        store = PerceptStore(durable_path=tmp_path / "m.jsonl")

        assert store._live_path.is_relative_to(tmp_path)
        assert store._live_path == tmp_path / "live.json"

    def test_the_live_view_sits_beside_the_durable_file_in_its_own_directory(self, tmp_path):
        """The parent's *own* name is honoured, not flattened onto tmp_path.

        A scratch store is normally `scratch/memory.jsonl` inside some
        verification sandbox; the live view belongs to that scratch store, not
        to the directory that happens to contain it.
        """
        scratch = tmp_path / "scratch" / "run-1"
        store = PerceptStore(durable_path=scratch / "m.jsonl")

        assert store._live_path == scratch / "live.json"
        assert store._live_path.parent == store._durable_path.parent

    def test_the_live_view_is_named_like_the_default_one(self, tmp_path):
        """Isolation must not rename the file readers look for."""
        store = PerceptStore(durable_path=tmp_path / "m.jsonl")

        assert store._live_path.name == store_mod.LIVE_FILE.name

    def test_a_store_with_no_paths_still_uses_the_module_defaults(self):
        """The app's real store is unchanged: no argument, same two files."""
        store = PerceptStore()

        assert store._durable_path == store_mod.DURABLE_FILE
        assert store._live_path == store_mod.LIVE_FILE

    def test_an_explicit_live_path_wins_outright(self, tmp_path):
        """`live_path` is the caller naming both files, so nothing overrides it."""
        store = PerceptStore(
            durable_path=tmp_path / "elsewhere" / "m.jsonl",
            live_path=tmp_path / "published" / "view.json",
        )

        assert store._live_path == tmp_path / "published" / "view.json"

    def test_a_durable_path_given_as_a_string_is_honoured(self, tmp_path):
        """The CLI passes `args.durable_file`, an argparse string, not a Path."""
        store = PerceptStore(durable_path=str(tmp_path / "m.jsonl"))

        assert store._live_path == tmp_path / "live.json"


class TestNothingIsPublishedOutsideTheGivenPath:
    def test_adding_a_percept_writes_the_live_view_beside_the_durable_file(self, tmp_path):
        store = PerceptStore(durable_path=tmp_path / "scratch" / "m.jsonl")
        store.add(_a_percept())

        assert (tmp_path / "scratch" / "live.json").is_file()

    def test_a_transient_percept_is_published_too(self, tmp_path):
        """`add()` publishes for both tiers; a durable-only leak fix would miss this."""
        store = PerceptStore(durable_path=tmp_path / "m.jsonl")
        store.add(_a_percept("durable fact"))
        store.add(_a_percept("screen says something"))
        durable = Percept(sense="memory", kind="fact", content="the meeting is at 4pm",
                          created_at=time.time(), sensitivity="personal")
        store.add(durable)

        published = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
        assert [p["content"] for p in published["transient"]] == [
            "durable fact",
            "screen says something",
        ]
        assert published["durable_count"] == 1

    def test_clearing_the_transient_tier_publishes_to_the_same_place(self, tmp_path):
        """`clear_transient()` is a second publish site; a fix to `add()` alone misses it."""
        store = PerceptStore(durable_path=tmp_path / "m.jsonl")
        store.add(_a_percept())
        store.clear_transient()

        published = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
        assert published["transient"] == []

    def test_nothing_is_written_to_the_module_default_location(self, tmp_path):
        """The leak's landing site, observed rather than inferred.

        `store_mod.LIVE_FILE` is the path a leak writes to. The autouse fixture
        in `conftest.py` points it into a temp dir, so asserting it stays absent
        is a real observation of the leak - without the fixture this assertion
        would itself be the thing that dirties the developer's home.
        """
        assert not store_mod.LIVE_FILE.exists(), "fixture did not isolate LIVE_FILE"

        store = PerceptStore(durable_path=tmp_path / "m.jsonl")
        store.add(_a_percept())

        assert not store_mod.LIVE_FILE.exists()
        assert not store_mod.DURABLE_FILE.exists()


class TestDurablePathIsStillHonoured:
    def test_the_durable_file_is_where_the_caller_asked(self, tmp_path):
        """Guard against fixing the live path by breaking the durable one."""
        store = PerceptStore(durable_path=tmp_path / "m.jsonl")
        store.add(Percept(sense="memory", kind="fact", content="the meeting is at 4pm",
                          created_at=time.time(), sensitivity="personal"))

        assert [p.content for p in store.durable()] == ["the meeting is at 4pm"]
        assert (tmp_path / "m.jsonl").is_file()

    def test_a_relative_durable_path_is_resolved_against_its_own_parent(self, tmp_path, monkeypatch):
        """`memory.jsonl` and `live.json` stay siblings even with no directory part."""
        monkeypatch.chdir(tmp_path)
        store = PerceptStore(durable_path=Path("memory.jsonl"))

        assert store._live_path == Path("live.json")
        store.add(_a_percept())
        assert (tmp_path / "live.json").is_file()
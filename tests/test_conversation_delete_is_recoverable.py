"""`conversation_store.delete` trashes now, and that was untested when I changed it.

Every other part of "nothing is permanently deleted" came with a test that drove
the real path. This one did not: it was a one-line edit made late, while fixing
the last raw `unlink` in the tree, and it went in without a witness. A
destructive-safety change to somebody's saved history is not a thing to ship on
the strength of "it looks right".

**The signatures, because guessing them is how the first attempt of this file
failed.** `new_session(root)` returns a **session id**, not a path; the
transcript is `root / f"{sid}.jsonl"`; `append(message, path)` takes the message
*first*; `rename(root, ref, title)` and `delete(root, ref)` resolve an id, an
exact title, or a unique substring. The first version passed a filename to
`rename`, so the titles stayed empty and `delete` by title resolved nothing -
four failures that all read as "the feature is broken".

**What "recoverable" means here is narrower than for a file, and that is most of
this file.** The transcript comes back out of the trash. The **index entry does
not** - `delete()` drops the session from `index.json` and purges it from the
search index, because a deleted conversation that is still findable is one the
person cannot get rid of. So restoring the file does not bring the conversation
back in the app, and an answer claiming an undo here would be the confident wrong
answer this repository keeps paying for.
"""

import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import conversation_store  # noqa: E402


@pytest.fixture
def trash(tmp_path, monkeypatch):
    """A trash on the same filesystem as the store, so `gio` will move things.

    Under `tmp_path / "trashbox"` rather than a directory called `home` - the
    first version of this fixture used `home`, which is where `conftest` puts
    `XDG_CONFIG_HOME`, and deleting it on teardown broke a consent write in a
    different file.
    """
    box = tmp_path / "trashbox"
    real = box / "share" / "Trash"
    (real / "files").mkdir(parents=True)
    (real / "info").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(box / "share"))
    return real


@pytest.fixture
def store(tmp_path):
    """Two saved conversations, so `active` has somewhere to fall to."""
    root = tmp_path / "store"
    root.mkdir()

    def _conversation(title, text):
        sid = conversation_store.new_session(root)
        conversation_store.append({"role": "user", "content": text},
                                  root / f"{sid}.jsonl")
        conversation_store.rename(root, sid, title)
        return sid

    first = _conversation("a plan for the week", "hello")
    _conversation("something else", "second")
    return root, first


def _titles(root):
    return sorted(s["title"] for s in conversation_store.list_sessions(root))


class TestTheTranscriptIsRecoverable:
    def test_it_lands_in_the_trash_with_a_restore_record(self, store, trash):
        root, first = store

        sid = conversation_store.delete(root, first)

        assert sid == first, f"delete resolved {sid!r}, not {first!r}"
        assert not (root / f"{sid}.jsonl").exists(), "the transcript left its path"
        assert (trash / "files" / f"{sid}.jsonl").is_file(), (
            "but it is not in the trash, so nothing is recoverable")
        assert (trash / "info" / f"{sid}.jsonl.trashinfo").is_file(), (
            "and it carries no restore record, so no file manager can put it back")
        assert "hello" in (trash / "files" / f"{sid}.jsonl").read_text(), (
            "with its contents intact")

    def test_it_is_gone_from_the_store_and_the_search_index(self, store, trash):
        """The half that makes it a *delete* rather than a copy.

        A conversation left findable after being deleted is one the person cannot
        get rid of - the store's own comment says "deleted means gone from the
        search index too, now".
        """
        root, first = store
        conversation_store.delete(root, first)

        assert _titles(root) == ["something else"], _titles(root)
        hits = conversation_store.search(root, "hello")
        assert not [h for h in hits if "plan for the week" in str(h)], hits

    def test_deleting_by_title_works_too(self, store, trash):
        """`delete` accepts an id, an exact title or a unique substring - and the
        trash move must not have broken any of the three."""
        root, first = store

        sid = conversation_store.delete(root, "a plan for the week")

        assert sid == first
        assert (trash / "files" / f"{sid}.jsonl").is_file()


class TestARefusedTrashChangesNothing:
    """No fallback to `unlink`. That is the whole contract, and the index makes
    it worse than it looks: a failed move that had already forgotten the
    conversation would leave it unreachable *and* present."""

    def test_a_missing_trash_leaves_everything_alone(self, store, monkeypatch):
        root, first = store
        real_which = shutil.which
        monkeypatch.setattr(shutil, "which",
                            lambda n: None if n == "gio" else real_which(n))

        assert conversation_store.delete(root, first) == "", (
            "delete reported a session it could not have trashed")

        assert (root / f"{first}.jsonl").exists(), "the transcript must still be on disk"
        assert "a plan for the week" in _titles(root), _titles(root)

    def test_a_gio_that_fails_is_not_treated_as_a_success(self, store, monkeypatch):
        import subprocess as sp

        root, first = store

        class _Refused:
            returncode = 1
            stdout = ""
            stderr = "Trashing on system internal mounts is not supported"

        monkeypatch.setattr(sp, "run", lambda *a, **k: _Refused())

        assert conversation_store.delete(root, first) == ""
        assert (root / f"{first}.jsonl").exists(), "the transcript must still be on disk"
        assert "a plan for the week" in _titles(root)


class TestTheHonestLimit:
    def test_restoring_the_file_does_not_re_adopt_the_conversation(self, store, trash):
        """**The limit, stated as a test rather than a caveat in a commit body.**

        The content comes back; the conversation does not reappear in the app,
        because `delete()` removed it from the index on purpose. A caller who
        assumed otherwise would be wrong, so this pins the behaviour rather than
        leaving it to be discovered.
        """
        root, first = store
        conversation_store.delete(root, first)

        (root / f"{first}.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}')

        assert "a plan for the week" not in _titles(root), (
            "the store does not re-adopt a transcript it no longer indexes")
        # The bytes are in one place or the other; what is NOT true is the
        # conversation coming back on its own.
        in_trash = (trash / "files" / f"{first}.jsonl").is_file()
        on_disk = (root / f"{first}.jsonl").is_file()
        assert in_trash or on_disk
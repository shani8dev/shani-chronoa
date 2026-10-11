"""The file knowledge base, against real files and a real sqlite index.

The design question this file pins: **how does the answer degrade when there is
no embedding model?** `local_embed` needs a 146 MB download and a llama.cpp
server; a fresh Arch box has neither, and this dev machine has no CUDA at all. So
keyword-only must be a *labelled* state, not a silent one - a search that returns
keyword hits while looking semantic is the confident-wrong-answer shape this
repository keeps recording.

Every claim below is against real files in a tmp_path and a real sqlite database;
no engine is stubbed except where the test says so.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import file_index as F  # noqa: E402

TEXT = {
    "network.txt": "The wifi box in the study drops the link every evening around six.\n",
    "shopping.txt": "Groceries: oat milk, coffee, chickpeas.\n",
    "notes.md": "Meeting notes: the btrfs rollback needs a second green slot.\n",
}


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for name, body in TEXT.items():
        (root / name).write_text(body, encoding="utf-8")
    # A binary, an empty file, a subdirectory with its own file, and a symlink
    # pointing out - the four things a naive walker gets wrong.
    (root / "blob.bin").write_bytes(bytes(range(256)) * 8)
    (root / "empty.txt").write_text("")
    nested = root / "sub"
    nested.mkdir()
    (nested / "deep.txt").write_text("The deep file mentions the green slot too.\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside: the passphrase is hunter2\n")
    try:
        (root / "escape").symlink_to(outside / "secret.txt")
    except OSError:
        pass
    yield root, outside


def _db(tmp_path):
    return F._connect(tmp_path / "index.sqlite3")


class TestWhatItReads:
    def test_text_files_are_read(self, corpus):
        root, _outside = corpus
        text, why = F.read_file(root / "network.txt")
        assert text.startswith("The wifi box")
        assert why == ""

    def test_a_binary_is_refused_with_a_reason(self, corpus):
        root, _ = corpus
        text, why = F.read_file(root / "blob.bin")
        assert text is None and ".bin" in why

    def test_an_empty_file_is_refused(self, corpus):
        root, _ = corpus
        text, why = F.read_file(root / "empty.txt")
        assert text is None and "empty" in why

    def test_a_nul_does_not_survive_into_the_index(self, tmp_path):
        """SQLite stores NULs and FTS5 tokenizes them, so a MATCH against a
        string containing one returns nothing while the row looks perfectly
        indexed."""
        target = tmp_path / "nul.txt"
        target.write_bytes(b"before\x00after the wifi\n")
        text, why = F.read_file(target)
        assert "\x00" not in text and "after the wifi" in text


class TestTheIndexIsIncremental:
    def test_a_second_run_reads_nothing_again(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        first = F.index_directory(root, embed=False, db=db)
        second = F.index_directory(root, embed=False, db=db)
        assert first["added"] == 4
        assert second["added"] == 0 and second["updated"] == 0, (
            "an unchanged file was re-read - which is how an index becomes "
            "something nobody runs")

    def test_a_changed_file_is_re_indexed(self, corpus, tmp_path):
        import os
        import time
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        target = root / "network.txt"
        os.utime(target, (time.time() + 5, time.time() + 5))
        target.write_text("The wifi box is now fine.\n")
        second = F.index_directory(root, embed=False, db=db)
        assert second["updated"] >= 1

    def test_a_deleted_file_leaves_the_index(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        (root / "shopping.txt").unlink()
        F.index_directory(root, embed=False, db=db)
        row = db.execute("SELECT COUNT(*) FROM docs WHERE path LIKE '%shopping%'").fetchone()
        assert row[0] == 0, "a deleted file was still searchable"


class TestConfinement:
    def test_a_symlink_out_of_the_root_is_not_indexed(self, corpus, tmp_path):
        """The strongest idea in Maze-Connect's threat model, and the reason a
        filename check alone is not enough: the *name* is innocent and the thing
        it resolves to is not."""
        root, _outside = corpus
        db = _db(tmp_path)
        report = F.index_directory(root, embed=False, db=db)
        paths = [r[0] for r in db.execute("SELECT path FROM docs")]
        assert not any("escape" in p for p in paths), (
            "a symlink pointing outside the root was indexed")
        assert any("symlink" in p for p in report["problems"]), (
            "the refusal was not reported")

    def test_the_secret_outside_is_not_searchable(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        hits, _how = F.search(root, "passphrase", embed=False, db=db)
        assert not hits, "content outside the root was reachable through a link"


class TestSearchSaysWhichSearchItWas:
    def test_keyword_only_is_labelled_not_silent(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        hits, how = F.search(root, "wifi box", embed=False, db=db)
        assert hits and "keyword only" in how, (
            f"a keyword-only answer was returned as if it were a search by "
            f"meaning: {how!r}")

    def test_a_file_found_by_keyword_is_returned_with_an_excerpt(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        hits, _how = F.search(root, "green slot", embed=False, db=db)
        assert hits and "green slot" in hits[0]["excerpt"]

    def test_nested_files_are_found(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        hits, _how = F.search(root, "deep file", embed=False, db=db)
        assert any("deep.txt" in h["path"] for h in hits)

    def test_fts_syntax_in_a_query_is_a_search_not_an_error(self, corpus, tmp_path):
        """`-` and `*` are FTS5 operators. An unquoted one is an
        OperationalError, which a caller sees as "no results" - so the pattern is
        quoted and the words are searched for as words."""
        root, _ = corpus
        db = _db(tmp_path)
        F.index_directory(root, embed=False, db=db)
        for query in ("-wifi", "wifi*", '"wifi', "(wifi)", "NEAR(wifi box)"):
            hits, how = F.search(root, query, embed=False, db=db)
            assert isinstance(hits, list), f"{query!r} raised"

    def test_an_empty_query_makes_no_pattern(self):
        assert F._fts_pattern("   ") == ""
        # Every word quoted, with quotes inside a word replaced by spaces: a
        # word that is itself `"b"` becomes the quoted `" b "`, which FTS5
        # tokenizes as `b`. Asserted by *property* rather than by an exact
        # string, because the exact string is an implementation detail and the
        # property - every word quoted, no operator surviving - is the claim.
        pattern = F._fts_pattern('a "b" c')
        assert pattern.count('"') == 6, pattern
        assert "-" not in pattern and "*" not in pattern and "(" not in pattern


class TestTheSkillSurface:
    def _config(self, on=True):
        return type("C", (), {"get_bool": lambda s, k, d=False: on})()

    def test_the_switch_off_refuses_and_names_itself(self, corpus):
        from shani_chronoa.skills import search_my_files as S
        root, _ = corpus
        out = S._run({"action": "search", "query": "wifi",
                      "path": str(root), "_config": self._config(False)})
        assert "turned off" in out and "file-index-enabled" in out

    def test_a_wrong_action_is_refused_not_ignored(self):
        from shani_chronoa.skills import search_my_files as S
        out = S._run({"action": "share", "_config": self._config()})
        assert "must be one of" in out

    def test_a_search_with_no_query_asks_rather_than_returning_everything(self):
        from shani_chronoa.skills import search_my_files as S
        out = S._run({"action": "search", "_config": self._config()})
        assert "What should I look for" in out

    def test_an_empty_answer_names_why_it_is_empty(self, corpus, tmp_path):
        """Two different failures that look identical: "nothing matched" and
        "nothing is indexed". Only one is the caller's fault, and the caller
        must be able to tell them apart."""
        from shani_chronoa.skills import search_my_files as S
        root, _ = corpus
        out = S._run({"action": "search", "query": "quantum",
                      "path": str(root), "_config": self._config()})
        assert "Nothing found" in out and "reindex" in out


class TestTheReportCountsWhatItCouldNotRead:
    def test_unreadable_files_are_named_in_the_report(self, corpus, tmp_path):
        root, _ = corpus
        db = _db(tmp_path)
        report = F.index_directory(root, embed=False, db=db)
        assert report["skipped"] >= 2, "an unreadable file was not counted"
        named = [p for p in report["problems"] if "blob.bin" in p]
        assert named, "the unreadable file was skipped without being named"

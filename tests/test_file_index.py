"""The file knowledge base, against real files and a real sqlite index.

**Every test in this file gets its own index.** `_connect()` resolves
`index_path()` per call, and `index_directory`, `search` and `status` all call it
with no path - so without the autouse fixture below a test that indexes three
files leaves them for the next, which then counts them. It never showed until
two more classes landed here: a fresh run of the whole file went **14 red** while
every test passed on its own, and `status()` returned eight files from a
*previous run's* temporary directory for a test that had just indexed two.

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

@pytest.fixture(autouse=True)
def _own_index(tmp_path, monkeypatch):
    """Every test gets its own index. See the note at the top of this file."""
    monkeypatch.setattr(F, "index_path", lambda: tmp_path / "one-index.db")


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


# --- the semantic half, which this box cannot run ----------------------------
#
# `local_embed` is a 146 MB download and a llama.cpp server, and this machine has
# no CUDA, so every test above has exercised keyword-only. That left the whole
# vector path - the reciprocal-rank merge, SIMILARITY_FLOOR, the blob round trip -
# at zero coverage on the one box where the work is done.
#
# So the embedder is faked here. **And the first fake was too stupid to test what
# it was testing**: a plain bag-of-words gives "router" and "wifi" orthogonal
# vectors, so the synonym case - the only reason an embedding model exists - could
# not be demonstrated at all, and the test asserting it failed. What replaced it
# is a bag-of-words over *synonym groups*, so "router" and "wifi" land on the same
# dimension the way a real embedding puts them close. That is enough to test the
# merge honestly; it is not a model and does not pretend to be one.

#: Synonym groups, in the shape a real embedding approximates: words that mean
#: nearly the same thing sit close together.
SYNONYMS = (
    ("wifi", "router", "network", "link"),
    ("btrfs", "snapshot", "rollback"),
    ("slot", "green", "blue"),
    ("coffee", "groceries", "food"),
)

def _bag_of_words(text, groups):
    """A unit vector over *synonym groups*, not over raw words.

    `router` and `wifi` count into the same dimension, which is what makes the
    synonym test meaningful. A vector that counts only exact words is
    indistinguishable from keyword search, and a test that appears to cover the
    semantic half while exercising a second copy of the keyword half is worse
    than no test at all.
    """
    counts = [0.0] * len(groups)
    low = text.lower()
    for index, group in enumerate(groups):
        counts[index] = sum(float(low.count(w)) for w in group)
    norm = sum(c * c for c in counts) ** 0.5
    if norm == 0:
        # **Zero, not uniform.** The first version returned a uniform vector for
        # "no vocabulary overlap", and a uniform vector has cosine ~0.5 with every
        # non-orthogonal document - so "quantum tunnelling apparatus" matched the
        # wifi notes. That was an artefact of this fake rather than of `search`:
        # a real embedding model never returns "every direction equally" for a
        # nonsense query, it returns something with structure. Zero is the honest
        # answer for "no information", and `search` guards against it.
        return [0.0] * len(groups)
    return [c / norm for c in counts]


@pytest.fixture
def semantic(monkeypatch, tmp_path):
    """Index with a deterministic stand-in for the embedding model."""
    from shani_chronoa import local_embed

    def fake_embed(texts, kind=None, timeout=30.0, transport=None):
        if not texts:
            return []
        return [_bag_of_words(t, SYNONYMS) for t in texts]

    monkeypatch.setattr(local_embed, "configured", lambda: True)
    monkeypatch.setattr(local_embed, "embed", fake_embed)
    root = tmp_path / "docs"
    root.mkdir()
    (root / "network.txt").write_text(
        "The wifi box in the study drops the network link every evening.")
    (root / "ops.txt").write_text(
        "The btrfs rollback needs a second green slot for the router.")
    (root / "shopping.txt").write_text("Groceries: coffee and oat milk.")
    db = F._connect(tmp_path / "sem.sqlite3")
    F.index_directory(root, embed=True, db=db)
    return root, db


class TestTheEmbeddingIsBatched:
    """**One round trip per file was measured, and it is the only network call in
    here.** `local_embed.embed` takes a list; the first version passed a list of
    one inside the walk, so a 20-file directory made 20 HTTP round trips of batch
    size 1 and a 500-file directory would make 500. On a llama.cpp server that is
    the difference between two seconds and a minute, for no reason a caller can
    see.
    """

    def _index_with(self, monkeypatch, tmp_path, count):
        from shani_chronoa import local_embed
        sizes = []

        def fake(texts, kind=None, timeout=30.0, transport=None):
            sizes.append(len(texts))
            return [[1.0, 0.0] for _ in texts]

        monkeypatch.setattr(local_embed, "configured", lambda: True)
        monkeypatch.setattr(local_embed, "embed", fake)
        root = tmp_path / "docs"
        root.mkdir()
        for index in range(count):
            (root / f"f{index}.txt").write_text(f"the wifi box drops link {index}\n")
        F.index_directory(root, embed=True)
        return sizes

    def test_twenty_files_are_embedded_in_one_call(self, monkeypatch, tmp_path):
        sizes = self._index_with(monkeypatch, tmp_path, 20)
        assert sizes, "the embedder was never called"
        assert len(sizes) == 1, f"{len(sizes)} calls for 20 files: {sizes}"
        assert sizes[0] == 20

    def test_the_chunk_is_bounded_so_a_huge_directory_is_not_one_body(self, monkeypatch, tmp_path):
        """**The other direction.** One giant call would be a body that grows with
        the directory and a memory spike with it. The chunk is a declared constant
        so it can be pinned.
        """
        sizes = self._index_with(monkeypatch, tmp_path, 100)
        assert all(size <= F.EMBED_CHUNK for size in sizes), sizes
        assert len(sizes) > 1, "a 100-file directory went in one call"

    def test_vectors_stay_with_their_own_file(self, monkeypatch, tmp_path):
        """**The correctness half of batching.** A batch answered short, or a
        vector written against the wrong file, is a search that finds the wrong
        thing silently - which is worse than being slow. So the zip is over the
        chunk and a partial answer is logged rather than misaligned.
        """
        from shani_chronoa import local_embed

        def short_embed(texts, kind=None, timeout=30.0, transport=None):
            # half a batch: the caller must not misalign the rest
            return [[1.0, 0.0] for _ in texts[: len(texts) // 2]]

        monkeypatch.setattr(local_embed, "configured", lambda: True)
        monkeypatch.setattr(local_embed, "embed", short_embed)
        root = tmp_path / "docs"
        root.mkdir()
        for index in range(8):
            (root / f"h{index}.txt").write_text(f"the btrfs rollback needs slot {index}\n")
        report = F.index_directory(root, embed=True)
        assert report["indexed"] == 8, report
        assert report["problems"], "a short batch was not reported"


class TestTheSemanticHalf:
    def test_a_vector_is_actually_written(self, semantic):
        root, db = semantic
        rows = db.execute("SELECT COUNT(*) FROM docs WHERE vector IS NOT NULL").fetchone()
        assert rows[0] == 3, "no vectors were written, so the semantic half is dead"

    def test_the_blob_round_trips(self, semantic):
        root, db = semantic
        from shani_chronoa import local_embed
        blob = db.execute("SELECT vector FROM docs LIMIT 1").fetchone()[0]
        vector = local_embed.from_blob(blob)
        assert len(vector) > 0 and abs(sum(vector) - 1.0) < 0.001, (
            "the stored vector is not unit length, so similarity is not bounded")

    def test_a_synonym_is_found_that_keyword_would_miss(self, semantic):
        """**The claim of the whole feature.** The file says "wifi box"; the query
        says "router network". No word is shared, so a keyword search returns
        nothing while a vector search finds it - and that is exactly the case the
        embedding model exists for.
        """
        root, db = semantic
        hits, how = F.search(root, "router", embed=True, db=db)
        assert hits, "the query found nothing at all"
        assert "embedding" in how, f"answered as keyword-only: {how!r}"
        assert any("network.txt" in h["path"] for h in hits), (
            f"router did not find the wifi file: {[h['path'] for h in hits]}")

    def test_the_answer_says_it_searched_by_meaning(self, semantic):
        root, db = semantic
        _hits, how = F.search(root, "router", embed=True, db=db)
        assert "keyword and embedding" in how, how

    def test_hybrid_beats_either_alone(self, semantic):
        """**The control on the merge.** Reciprocal rank must rank a file found by
        *both* lists above one found by only one - otherwise the merge is
        decoration and the second list is noise.
        """
        root, db = semantic
        # "btrfs slot" appears verbatim in ops.txt, and shares words with it
        hits, _how = F.search(root, "btrfs slot", embed=True, db=db)
        assert hits and "ops.txt" in hits[0]["path"], (
            f"the file with both words was not first: {[h['path'] for h in hits]}")

    def test_a_file_below_the_floor_is_not_returned(self, semantic):
        """`SIMILARITY_FLOOR` is what stops every file in the index being "about"
        every query. A flooring bug would return the shopping list for a btrfs
        question and read as a clever match.
        """
        root, db = semantic
        hits, _how = F.search(root, "btrfs rollback", embed=True, db=db)
        assert not any("shopping.txt" in h["path"] for h in hits), (
            "an unrelated file passed the similarity floor")

    def test_a_file_that_shares_no_words_is_still_findable_by_meaning(self, semantic):
        """The other direction from the floor: the floor must not be so high that
        it only admits exact keyword matches, which would make the vector half
        cosmetic."""
        root, db = semantic
        hits, _how = F.search(root, "router", embed=True, db=db)
        assert any("network.txt" in h["path"] for h in hits)

    def test_a_query_the_index_cannot_answer_still_returns_a_labelled_empty(self, semantic):
        root, db = semantic
        hits, how = F.search(root, "quantum tunnelling apparatus", embed=True, db=db)
        assert not hits
        assert "keyword and embedding" in how, (
            "with the embedder present, an empty answer must still say which "
            "search produced it")


class TestADegenerateQueryVector:
    """**What the `any(want)` guard is actually for**, and why the mutation that
    removed it passed 27 tests first.

    With the synonym-group fake, an empty bag returns *zeros*, so a zero query
    scores 0 against every document and `SIMILARITY_FLOOR` rejects them all - the
    guard is redundant and removing it changes nothing. The case it exists for is
    a *near-uniform* vector, which is what a degenerate real embedding can return
    for text with no vocabulary overlap: cosine ~0.5 with every non-orthogonal
    document, comfortably past the floor, so a nonsense query matches everything.

    The first version of the fake returned a uniform vector and a test found
    "quantum tunnelling apparatus" returning the wifi notes. This is that test,
    kept, because a guard whose only motivating case is gone is a guard nobody can
    tell is load-bearing.
    """

    def test_a_uniform_query_vector_matches_nothing(self, monkeypatch, tmp_path):
        from shani_chronoa import local_embed

        def uniform_embed(texts, kind=None, timeout=30.0, transport=None):
            # A degenerate model: every text gets "every direction equally",
            # which has cosine ~0.5 with any real document.
            return [[1.0 / 3.0] * 3 for _ in texts]

        monkeypatch.setattr(local_embed, "configured", lambda: True)
        monkeypatch.setattr(local_embed, "embed", uniform_embed)

        root = tmp_path / "docs"
        root.mkdir()
        (root / "a.txt").write_text("the wifi box drops the network link")
        (root / "b.txt").write_text("the btrfs rollback needs a green slot")
        db = F._connect(tmp_path / "u.sqlite3")
        F.index_directory(root, embed=True, db=db)

        hits, _how = F.search(root, "quantum tunnelling apparatus", embed=True, db=db)
        assert not hits, (
            f"a query with no vocabulary matched {len(hits)} file(s) - a "
            f"near-uniform embedding has cosine ~0.5 with everything, and the "
            f"similarity floor is 0.28")


class TestTheWalkSkipsTheMachinesOwnHoard:
    """**Measured before the fix:** indexing a home-shaped tree walked into
    `.cache/`, stat-ed a 4 GiB model file and a 64 MiB log, and filled the report
    with "not text" lines about package caches - so the answer to "index my notes"
    was a wall of noise about the machine's own cache, and the walk examined 5
    files to find the 1 a person wanted.

    `temp_files` bounds the same walk for the same reason. `.local/share` is the
    debatable skip - a person may keep notes there - and it is skipped because it
    is also where Chronoa's own state, Flatpak apps and Trash live. `path=`
    subdirectories are still skipped, and that is recorded rather than assumed.
    """

    def _home(self, tmp_path):
        home = tmp_path / "home"
        (home / "notes").mkdir(parents=True)
        (home / "notes" / "todo.txt").write_text("buy coffee")
        (home / ".cache" / "llm").mkdir(parents=True)
        (home / ".cache" / "llm" / "model.gguf").write_bytes(b"M" * 4096)
        (home / ".cache" / "pkg").mkdir(parents=True)
        (home / ".cache" / "pkg" / "x.tar.zst").write_bytes(b"P" * 2048)
        (home / ".local" / "share").mkdir(parents=True)
        (home / ".local" / "share" / "big.log").write_text("x" * 4096)
        (home / "project").mkdir()
        (home / "project" / "README.txt").write_text("the readme")
        (home / "project" / "node_modules").mkdir()
        (home / "project" / "node_modules" / "dep.txt").write_text("a dependency")
        (home / "project" / "__pycache__").mkdir()
        (home / "project" / "__pycache__" / "x.txt").write_text("bytecode neighbour")
        return home

    def test_the_write_and_pycache_neighbours_are_left_alone(self, tmp_path):
        home = self._home(tmp_path)
        found = sorted(str(p).replace(str(home) + "/", "")
                       for p in F._walk(home, []))
        assert found == ["notes/todo.txt", "project/README.txt"], found

    def test_the_report_has_no_cache_noise(self, tmp_path):
        home = self._home(tmp_path)
        report = F.index_directory(home, embed=False)
        assert report["problems"] == [], report["problems"]
        assert report["indexed"] == 2, report
        assert report["examined"] == 2, (
            f"{report['examined']} files examined to find 2 - the walk is still "
            f"entering the cache")

    def test_a_named_directory_still_has_its_subdirectories_skipped(self, tmp_path):
        """The skip is on the *component*, so `~/projects/.cache` is skipped too -
        a build cache in a checked-out project is the machine's, not the person's.
        """
        home = tmp_path / "home"
        (home / "projects" / "app").mkdir(parents=True)
        (home / "projects" / "app" / "main.py").write_text("print(1)")
        (home / "projects" / "app" / ".cache").mkdir()
        (home / "projects" / "app" / ".cache" / "o.txt").write_text("an object file")
        (home / "projects" / "app" / "notes.txt").write_text("real notes")
        found = sorted(str(p).replace(str(home) + "/", "")
                       for p in F._walk(home, []))
        assert found == ["projects/app/main.py", "projects/app/notes.txt"], found

    def test_the_walk_stops_and_says_so_rather_than_never_ending(self, tmp_path):
        """The bound `temp_files` uses for the same walk: a home directory can
        hold millions of files and an index that runs for an hour without
        finishing is one nobody runs twice.
        """
        home = tmp_path / "many"
        home.mkdir()
        for index in range(F.WALK_CEILING + 5):
            (home / f"f{index}.txt").write_text("note")
        report = F.index_directory(home, embed=False)
        assert report["indexed"] <= F.WALK_CEILING, (
            "more was indexed than the ceiling allows")
        # the report says so rather than silently stopping
        assert report.get("truncated"), (
            "the walk stopped without saying it had - a partial index presented "
            "as a complete one is the confident-wrong-answer shape")


class TestTheIndexAnswersWhatItHolds:
    """`status()` exists because `search()` cannot answer the question a panel
    asks before searching anything.

    **An empty search result is the same either way** - "nothing matched" and
    "nothing was ever indexed" are opposite answers, and only the first is about
    the query. A caller holding only `search()` would have had to run a reindex
    to find out, which is a slow, side-effecting way to ask a question.
    """

    def _three(self, tmp_path, embed: bool = False):
        (tmp_path / "a.txt").write_text("the wifi box")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.md").write_text("the btrfs rollback")
        (tmp_path / "nested.txt").write_text("another note")
        F.index_directory(tmp_path, embed=embed)

    def test_it_counts_what_was_indexed(self, tmp_path):
        self._three(tmp_path)
        state = F.status()
        assert state["indexed"] == 3, state
        assert state["embedded"] == 0, state

    def test_an_empty_index_is_zero_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        state = F.status()
        assert state == {"indexed": 0, "embedded": 0, "root": None,
                         "newest": 0.0}, state

    def test_an_unreadable_index_is_zero_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        path = F.index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("this is not a database")
        state = F.status()
        assert state["indexed"] == 0, (
            "a panel calls this on open, before anything exists; a corrupt or "
            "absent index must be an answer, not a crash")

    def test_it_names_the_directory_the_files_share(self, tmp_path):
        self._three(tmp_path)
        state = F.status()
        assert state["root"] == str(tmp_path), state

    def test_files_in_more_than_one_tree_still_answer(self, tmp_path):
        """The panel has to name *what* is indexed, and a person who indexed
        two directories is told so rather than being shown either one."""
        one, two = tmp_path / "one", tmp_path / "two"
        one.mkdir()
        two.mkdir()
        (one / "a.txt").write_text("first")
        (two / "b.txt").write_text("second")
        db = F._connect()
        for root in (one, two):
            F.index_directory(root, embed=False, db=db)
        state = F.status(db=db)
        assert state["indexed"] == 2, state
        assert state["root"] == str(tmp_path), (
            "two directories under one tmp dir share the tmp dir, which is what "
            "the panel should say rather than either one of them")

    def test_it_says_how_many_can_be_searched_by_meaning(self, tmp_path, monkeypatch):
        from shani_chronoa import local_embed
        # **Indexed once, with embeddings, from empty.** `_three` defaults to
        # `embed=False`; indexing a second time with `embed=True` would find
        # every fingerprint unchanged and embed nothing, so the assertion would
        # read `embedded == 0` against a working path - a fixture answering the
        # test for it.
        monkeypatch.setattr(local_embed, "embed",
                            lambda texts, **k: [[1.0, 0.0] for _ in texts])
        monkeypatch.setattr(local_embed, "configured", lambda: True)
        self._three(tmp_path, embed=True)
        state = F.status()
        assert state["embedded"] == 3, state


class TestTheIndexAndTheSearchAgreeAboutScope:
    """Two defects, found by building a panel that asks both questions.

    **`index_directory(A)` then `index_directory(B)` erased A's rows.** The
    deletion pass removed every row *the walk did not see*, which is a second
    run wiping the first one's directory - silently, because nothing reports a
    deletion and the count still looks like a working index.

    **And `search(root, q)` read every row in the table.** So with `path=`
    naming one directory it answered about all of them. Together: "N files
    under X" was a number about one thing and results about another, which is
    the shape where a person cannot check either.
    """

    def _two(self, base):
        notes, work = base / "notes", base / "work"
        notes.mkdir()
        work.mkdir()
        (notes / "a.txt").write_text("the wifi box drops the link")
        (work / "b.txt").write_text("the wifi box is in the office")
        db = F._connect()
        F.index_directory(notes, embed=False, db=db)
        F.index_directory(work, embed=False, db=db)
        return notes, work, db

    def test_the_second_directory_does_not_erase_the_first(self, tmp_path):
        _notes, _work, db = self._two(tmp_path)
        assert F.status(db=db)["indexed"] == 2, (
            "indexing a second directory deleted the first one's rows; a panel "
            "showing 'N files' would be counting one directory's worth")

    def test_each_search_is_confined_to_what_it_was_given(self, tmp_path):
        notes, work, db = self._two(tmp_path)
        hits, _ = F.search(str(notes), "wifi box", embed=False, db=db)
        paths = [h["path"] for h in hits]
        assert paths == [str(notes / "a.txt")], paths
        hits, _ = F.search(str(work), "wifi box", embed=False, db=db)
        assert [h["path"] for h in hits] == [str(work / "b.txt")], paths

    def test_a_deleted_file_still_leaves_its_own_directory(self, tmp_path):
        """The confinement is on *this root*, so a file the person really did
        delete still leaves - without taking the other directory with it."""
        notes, _work, db = self._two(tmp_path)
        (notes / "a.txt").unlink()
        F.index_directory(notes, embed=False, db=db)
        remaining = F.status(db=db)["indexed"]
        assert remaining == 1, remaining

    def test_the_counts_and_the_results_describe_the_same_directory(self, tmp_path):
        """The number and the answers, about the same thing."""
        notes, _work, db = self._two(tmp_path)
        state, hits = F.status(db=db), None
        hits, _ = F.search(str(notes), "wifi box", embed=False, db=db)
        assert state["indexed"] == 2, state
        assert len(hits) == 1, hits

    def test_the_vector_half_is_confined_too(self, tmp_path, monkeypatch):
        """**The survivor, and it is the half that costs a person.**
        `local_embed` answered on this machine so the vector path never ran in
        any test, which is why a mutation removing its root check stayed green.
        A vector hit from a directory nobody named is a result the person cannot
        check, and it arrives *ranked by meaning* - which reads as the good half
        of the search rather than the unverified half.
        """
        from shani_chronoa import local_embed
        base = self._two(tmp_path)[0].parent
        (base / "third").mkdir()
        (base / "third" / "c.txt").write_text("the wifi box is in the third place")
        monkeypatch.setattr(local_embed, "embed",
                            lambda texts, **k: [[1.0, 0.0, 0.0] for _ in texts])
        monkeypatch.setattr(local_embed, "configured", lambda: True)
        monkeypatch.setattr(local_embed, "similarity",
                            lambda a, b: 0.9)

        db = F._connect()
        # **Written with vectors.** Indexing with `embed=False` leaves every
        # `vector` column NULL, so `SELECT path, vector FROM docs WHERE vector
        # IS NOT NULL` returns nothing and the guard under test is never reached
        # - which is exactly why the first version of this test passed against
        # the mutation it was written to catch.
        for directory in ((base / "notes"), (base / "work"), (base / "third")):
            F.index_directory(directory, embed=True, db=db)

        hits, how = F.search(str(base / "notes"), "wifi box", embed=True, db=db)
        assert how.startswith("keyword and embedding"), how
        paths = [h["path"] for h in hits]
        assert paths == [str(base / "notes" / "a.txt")], paths

    def test_status_is_confined_to_the_root_it_is_given(self, tmp_path):
        """**The panel's count and its results describe the same directory.**
        `status()` reported the whole index, so with two directories indexed a
        panel pointed at one of them said how many files were in both - while
        `search()` underneath it answered only about the one named. Same
        disagreement `search()` had, reached from the other end.

        Measured before the fix: `2 file(s) under <the shared parent>` over a
        search that returned only `notes/a.txt`.
        """
        notes, work, db = self._two(tmp_path)
        both = F.status(db=db)
        assert both["indexed"] == 2, both

        notes_only = F.status(root=notes, db=db)
        assert notes_only["indexed"] == 1, notes_only
        assert notes_only["root"] == str(notes), notes_only
        work_only = F.status(root=work, db=db)
        assert work_only["indexed"] == 1, work_only

    def test_status_with_no_root_still_reports_everything(self, tmp_path):
        notes, work, db = self._two(tmp_path)
        assert F.status(db=db)["indexed"] == 2

    def test_a_deleted_directory_is_forgotten_and_a_live_one_is_not(self, tmp_path):
        """**The defect my own scope fix introduced, and only a second run showed.**

        Confining the deletion to the root being indexed stopped a second run
        erasing the first one's directory - and forgot nothing at all. A directory
        the person deletes kept its rows forever, so `status()` counted files that
        no longer exist and a search returned paths resolving to nothing.

        The suite passed until it was run **twice**, which is what a machine does
        over days: 14 tests went red in the second pass, all of them reading a
        row count polluted by earlier runs' temp directories.

        The rule that satisfies both: a row is dropped when its file is genuinely
        absent, and for no other reason.
        """
        notes, work = tmp_path / "notes", tmp_path / "work"
        notes.mkdir()
        work.mkdir()
        (notes / "a.txt").write_text("the wifi box")
        (work / "c.txt").write_text("the deploy")
        db = F._connect()

        F.index_directory(notes, embed=False, db=db)
        assert F.status(db=db)["indexed"] == 1, F.status(db=db)

        # **Indexing a second directory leaves the first one entirely alone.**
        F.index_directory(work, embed=False, db=db)
        assert F.status(db=db)["indexed"] == 2, F.status(db=db)

        # **And deleting the first prunes it, at the next index of anything.**
        (notes / "a.txt").unlink()
        F.index_directory(work, embed=False, db=db)
        state = F.status(db=db)
        assert state["indexed"] == 1, state
        assert state["root"] == str(work), state

        hits, _how = F.search(str(tmp_path), "wifi box", embed=False, db=db)
        assert hits == [], f"a deleted file was found: {[h['path'] for h in hits]}"

    def test_an_empty_row_set_is_not_a_reason_to_delete_anything(self, tmp_path):
        """The other direction: a walk that finds nothing is not permission to
        empty the index."""
        live = tmp_path / "live"
        live.mkdir()
        (live / "keep.txt").write_text("still here")
        db = F._connect()
        F.index_directory(live, embed=False, db=db)
        empty = tmp_path / "empty"
        empty.mkdir()
        F.index_directory(empty, embed=False, db=db)
        assert F.status(db=db)["indexed"] == 1, F.status(db=db)

"""An edit whose `old_string` was re-typed, and the line nobody asked to change.

oh-my-openagent's hashline core carries `restoreOldWrappedLines`: when a model
re-emits a block it means to leave alone, it strips trailing spaces, re-wraps, or
drops a continuation token, so its transcription is not the bytes on disk. Its
answer is to find runs in the replacement that match a place in the original
*modulo whitespace* and put the original bytes back.

**Chronoa's version of the problem is a refusal, not a spurious diff.** `edit_file`
requires `old_string` to match exactly - which is the right rule, because a
whitespace-tolerant *write* would silently change lines nobody asked to change.
Measured before this change, a three-line function with trailing whitespace on its
second line:

    model's old_string -> exact matches: 0   (the whole edit is REFUSED)

That is safe, and it is a friction the model cannot see past, because the
difference is invisible in the text it wrote. So the *match* is retried ignoring
whitespace, and when exactly one span of the file tightens to the same thing, that
span's **own bytes** become `old_string` - which keeps the write exact.

Two halves, and the second is the one that makes it honest:

1. **The match.** `_match_ignoring_whitespace` returns the file's span for
   `old`, so the replacement is still a byte-for-byte substitution.
2. **The write.** `_restore_kept_lines` puts back, inside the region being
   replaced, the runs the model *quoted* - so the trailing whitespace it dropped
   does not land in the diff as a change to a line nobody touched.

**Everything here is asserted against the file that exists, never against a
normalisation of it.** The corrected `old` is compared to the bytes read off
disk, and the result of a real `_run` is compared to the file's own text.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

from shani_chronoa.skills import edit_file as E  # noqa: E402

#: A real shape: a comment with trailing whitespace, which is what an editor and
#: a git diff both preserve and a model's re-typing both lose.
DISK = ("def total(items):\n"
        "    # Sum the basket.   \n"
        "    return sum(i.price for i in items)\n")
MODEL_OLD = ("def total(items):\n"
             "    # Sum the basket.\n"
             "    return sum(i.price for i in items)\n")
MODEL_NEW = ("def total(items):\n"
             "    # Sum the basket.\n"
             "    return sum(i.price * i.qty for i in items)\n")


@pytest.fixture
def module(tmp_path, monkeypatch):
    """The skill with its consent stub and a real file inside the home."""
    monkeypatch.setenv("HOME", str(tmp_path))

    class _Cfg:
        def get_bool(self, key, default=False):
            return True

    monkeypatch.setattr(E, "ChronoaConfig", lambda: _Cfg())
    path = tmp_path / "mod.py"
    path.write_text(DISK)
    return path


class TestTheMatchIsRetried:
    """Half one: find the file's own bytes for a needle that was re-typed."""

    def test_a_retyped_needle_matches_zero_times_exactly(self):
        """**The measured defect**, stated first so the rest of the file is
        against it: the shipped rule refuses this edit outright."""
        assert DISK.count(MODEL_OLD) == 0

    def test_the_retry_finds_the_files_own_bytes(self):
        old, count, offset = E._match_ignoring_whitespace(DISK, MODEL_OLD)
        assert count == 1
        # **The file's bytes, not the model's transcription.** This is what keeps
        # the write exact: the trailing whitespace the model dropped is in `old`.
        assert old == DISK[offset:offset + len(old)]
        assert old != MODEL_OLD
        assert DISK.count(old) == 1

    def test_the_retry_returns_the_whole_span_not_the_needles_length(self):
        """**A truncation bug this file caught in itself.** Matching on characters
        stopped one short of the newline, so `text.replace(old, new)` left the
        original newline behind and a real edit came out `...items)\n\n`.

        The assertion is that the span is the file's **whole block** - longer than
        the needle, because the trailing whitespace it dropped is in the file -
        and that nothing is duplicated. (`"ms)" in old` is not the check: it
        occurs inside "items)", which the first version of this test asserted.)
        """
        old, count, offset = E._match_ignoring_whitespace(DISK, MODEL_OLD)
        assert count == 1
        assert old == DISK
        assert len(old) > len(MODEL_OLD), (len(old), len(MODEL_OLD))
        assert DISK.replace(old, MODEL_NEW, 1).count("\n") == DISK.count("\n"), (
            "the substitution duplicated the trailing newline")

    def test_an_exact_needle_is_returned_unchanged(self):
        """**The caller, not the helper, decides when to retry** - `_run` calls this
        only after `text.count(old) == 0`. So a needle that exact-matches is
        returned as the file's own bytes for it, and the write is byte-identical
        to what it was before this change: a file with no trailing whitespace
        behaves exactly as it did."""
        clean = "def total(items):\n    return 1\n"
        needle = "    return 1\n"
        old, count, offset = E._match_ignoring_whitespace(clean, needle)
        assert count == 1 and old == needle
        assert clean.replace(old, "    return 2\n", 1) == "def total(items):\n    return 2\n"

    @pytest.mark.parametrize("needle", ["x = 1\n", "if y:\n", "a = b\n",
                                         "v = 2\n", "i = 0\n"])
    def test_a_short_needle_is_never_retried(self, needle):
        """A needle of fewer than six tight characters occurs everywhere, so a
        whitespace-insensitive match on one is guaranteed ambiguous and the walk
        would only find noise. `restoreOldWrappedLines` uses the same floor.

        **The witness has to be a needle the file really contains.** The first
        version used a file that had none of them, so dropping the floor changed
        nothing and the test passed both ways.

        **`"return"` is exactly six tight characters and is deliberately NOT in
        this list** - it is in the next test, where it is the boundary case that
        *is* retried. Listing it here is what the first version did, and the test
        failed on the boundary for the wrong reason.
        """
        text = "a = 0\n" + needle + "b = 2\n"
        old, count, _offset = E._match_ignoring_whitespace(text, needle)
        assert count == 0 and old == needle, (
            f"{needle!r} (tight length {len(E._tight(needle))}) was retried, so "
            "the six-character floor on a needle is not load-bearing")

    def test_a_needle_at_exactly_six_is_retried(self):
        """The boundary, so the floor is not accidentally a seven.

        The line has to be exactly `return` - the first version used `return 1`,
        whose tight form is seven characters, so the needle never matched at all
        and the test failed for a reason that had nothing to do with the floor.
        """
        needle = "return\n"
        text = "a = 0\nreturn\nb = 2\n"
        old, count, offset = E._match_ignoring_whitespace(text, needle)
        assert count == 1 and old == "return\n", (count, old)

    def test_a_needle_absent_even_loosely_is_refused(self):
        old, count, offset = E._match_ignoring_whitespace(
            DISK, "def nowhere(items):\n    return None\n")
        assert count == 0 and offset == -1

    def test_an_ambiguous_needle_comes_back_ambiguous(self):
        """**The rule that makes the second chance safe.** A span that could be
        two places is never used - the count comes back as the number of loose
        matches, so the existing refusal names the alternatives exactly as
        before.

        **The two candidates must differ from the needle in whitespace ONLY.**
        The first version of this test used `value = 1` against `value = one`,
        which differs in *letters*, so the tight forms disagree and there is no
        ambiguity to find."""
        text = ("    value = 1\n"
                "    log(value)\n"
                "    value = 1\n"
                "    log(value)\n")
        needle = "value = 1\nlog(value)\n"        # whitespace-only difference
        old, count, offset = E._match_ignoring_whitespace(text, needle)
        assert count == 2 and offset == -1, (
            "an ambiguous needle was resolved to one place, which is the guess "
            "the exact-match rule exists to refuse")

    def test_the_retry_is_not_fooled_by_a_reorder(self):
        """Whitespace is the only difference tolerated. A needle whose words are
        in a different order is a different string, however it is spaced."""
        text = "alpha beta\n"
        old, count, _offset = E._match_ignoring_whitespace(text, "beta alpha\n")
        assert count == 0


class TestTheQuotedLinesArePutBack:
    """Half two: the lines the model meant to keep stay the file's own."""

    def test_a_quoted_block_comes_back_with_its_whitespace(self):
        region = DISK
        out = E._restore_kept_lines(region, MODEL_NEW)
        assert out == ("def total(items):\n"
                       "    # Sum the basket.   \n"
                       "    return sum(i.price * i.qty for i in items)\n")
        # The changed line is the model's; the kept one is the file's.
        assert "i.price * i.qty" in out
        assert "    # Sum the basket.   \n" in out

    def test_a_line_the_model_actually_changed_is_left_alone(self):
        """**The correction cannot invent an edit.** Only runs that match a place
        in the region are restored; a genuinely new line is passed through."""
        out = E._restore_kept_lines(DISK, "def total(items):\n"
                                        "    # Sum the basket.   \n"
                                        "    return sum(i.price for i in items) + 1\n")
        assert out.endswith("+ 1\n")

    def test_a_single_line_is_not_evidence_of_a_quote(self):
        """One line is as likely to be the model's own new text. A *run* is a
        transcription."""
        out = E._restore_kept_lines(DISK, "    # Sum the basket.\n")
        assert out == "    # Sum the basket.\n"

    def test_a_run_too_short_to_identify_a_place_is_left_alone(self):
        """`a b` / `c d` occur everywhere; the six-tight-character floor is the
        same one `restoreOldWrappedLines` uses.

        **The run must be short enough for the floor to bite, and the region must
        differ from it in whitespace so the difference is observable.** The first
        version used `return x` / `y = 2`, which is nine tight characters and
        passes the floor anyway - the test asserted the right answer and the
        mutation left it passing."""
        region = "a b  \nc d\n"
        out = E._restore_kept_lines(region, "a b\nc d\n")
        assert out == "a b\nc d\n", (
            f"a run of {len(E._tight('a b' + 'c d'))} tight characters was "
            "restored, so the floor on a restorable span is not load-bearing")

    def test_an_ambiguous_run_is_left_alone(self):
        """**The two candidates must be the same run, and the region must differ
        from it in whitespace so the restore is observable.** The first version
        used `value = one` / `value = two`, whose tight forms differ, so there was
        no ambiguity to find and the mutation passed."""
        region = "v = 1  \nfoo\nv = 1  \nfoo\n"
        out = E._restore_kept_lines(region, "v = 1\nfoo\n")
        assert out == "v = 1\nfoo\n", (
            "an ambiguous run was restored to the first place it matched")

    def test_blank_lines_are_never_part_of_a_run(self):
        """A blank line tightens to nothing, so a run containing one is ambiguous
        by construction and must never be restored.

        **The witness needs a region that holds a real run *and* a `new` with a
        blank inside it.** The first version used `"alpha\n\nbeta\n"` for both,
        which leaves the region with no usable key at all - the function returns
        early and the test passes with or without the check.
        """
        region = "pp qq  \nrr ss\n"
        quoted = "pp qq\n\nrr ss\n"          # the model inserted a blank line
        out = E._restore_kept_lines(region, quoted)
        assert out == quoted, (
            f"a run containing a blank line was restored: {out!r}")

    def test_an_empty_region_changes_nothing(self):
        assert E._restore_kept_lines("", MODEL_NEW) == MODEL_NEW

    def test_the_correction_is_confined_to_the_region(self):
        """**The narrowing that makes it Chronoa's rather than OmO's.** OmO
        searches the whole original file; here the search is the region being
        replaced, so a correction can only put back what was in the text it was
        asked to change."""
        region = DISK
        # A run in `new` that matches a line OUTSIDE the region is not restored.
        new = "OTHER\n    OTHER = 1\n"
        assert E._restore_kept_lines(region, new) == new


class TestTheSkillEndToEnd:
    """Through the real `_run`, with the file it really reads."""

    def test_the_edit_that_used_to_be_refused_now_lands(self, module, monkeypatch):
        out = E._run({"path": str(module), "old_string": MODEL_OLD,
                      "new_string": MODEL_NEW, "preview": True})
        assert "Preview only" in out, out
        assert module.read_text() == DISK, "a preview wrote the file"

    def test_the_write_keeps_the_untouched_line_exactly(self, module):
        E._run({"path": str(module), "old_string": MODEL_OLD,
                "new_string": MODEL_NEW})
        written = module.read_text()
        assert written == ("def total(items):\n"
                           "    # Sum the basket.   \n"
                           "    return sum(i.price * i.qty for i in items)\n")
        # **Not one newline fewer, and not one more.** The line-aligned match is
        # what guarantees this; a character match came out `...items)\n\n`.
        assert written.count("\n") == DISK.count("\n"), repr(written)

    def test_the_report_says_what_happened(self, module):
        out = E._run({"path": str(module), "old_string": MODEL_OLD,
                      "new_string": MODEL_NEW})
        assert "Replaced" in out and "undo_last_change" in out

    def test_a_plain_edit_is_unchanged_in_behaviour(self, module):
        """A model that copies the bytes exactly gets exactly what it got before:
        no correction runs, so the report and the file are identical."""
        module.write_text("a = 1\nb = 2\n")
        E._run({"path": str(module), "old_string": "a = 1\n",
                "new_string": "a = 3\n"})
        assert module.read_text() == "a = 3\nb = 2\n"

    def test_an_absent_string_is_still_refused(self, module):
        out = E._run({"path": str(module),
                      "old_string": "nothing like this is in the file",
                      "new_string": "x"})
        assert "does not contain that text" in out
        assert module.read_text() == DISK

    def test_an_ambiguous_string_is_still_refused_with_its_lines(self, module):
        """Same shape, end to end: the loose match must find *two* places, so the
        existing refusal with its line numbers is what the person gets."""
        module.write_text("v = 1\nfoo\nv = 1\nfoo\n")
        out = E._run({"path": str(module), "old_string": "v = 1\nfoo\n",
                      "new_string": "v = 9\nfoo\n"})
        assert "occurs" in out and "line" in out, out
        assert module.read_text() == "v = 1\nfoo\nv = 1\nfoo\n", "a refused edit wrote"

    def test_replace_all_still_means_all(self, module):
        module.write_text("x\nx\nx\n")
        out = E._run({"path": str(module), "old_string": "x\n",
                      "new_string": "y\n", "replace_all": True})
        assert module.read_text() == "y\ny\ny\n", out

    def test_an_unparseable_result_is_still_refused(self, module):
        """The parse check runs on the corrected text, so a correction that would
        produce a syntax error is refused rather than written."""
        module.write_text("def f(:\n    return 1\n")
        out = E._run({"path": str(module),
                      "old_string": "def f(:\n    return 1\n",
                      "new_string": "def f(:\n    return 2\n"})
        assert module.read_text() == "def f(:\n    return 1\n", out

    def test_the_tight_form_is_a_real_comparison(self):
        """`_tight` on the shapes this correction is about."""
        assert E._tight("a b\nc") == "abc"
        assert E._tight("  a\t\tb  \n") == "ab"
        assert E._tight("") == ""

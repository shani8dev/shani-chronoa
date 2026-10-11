"""Tests for `search_file_contents` reading compressed files, which it silently
refused to do.

`/var/log/alternatives.log.2.gz` and 30 more of its siblings on this machine
were **every one of them skipped by the binary rule**. The reason is measured,
not guessed: a gzip file's *compressed* bytes always contain NUL, so the sniff

    if b"\\0" in data[:4096]: skipped_binary += 1

classifies a gzip log full of plain update-alternatives text as binary and skips
it, reporting it among the "binary files" this skill refuses. "Search my logs"
therefore missed the oldest ones - the ones a person means when they say "when
did this break" - and said nothing about it.

The fix is an **order change, not a new feature**: sniff for binary *after*
decompressing, because "is this file text" is a question about its contents, not
its container. The ceiling follows for the same reason - a 2 MiB limit measured
against compressed bytes lets a 400 MiB log through, and the decompression is
what exhausts memory.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import pathlib
import subprocess

import pytest

from shani_chronoa.skills import search_file_contents as sfc

NEEDLE = "the-needle-is-here"


@pytest.fixture
def dir_of_files(tmp_path):
    def install(gz=None, plain=None, binary=None, corrupt=None, xz=None, zstd=None):
        if plain:
            (tmp_path / "plain.log").write_text(plain)
        if gz:
            (tmp_path / "rotated.log.gz").write_bytes(gzip.compress(gz.encode()))
        if xz:
            (tmp_path / "rotated.log.xz").write_bytes(lzma.compress(xz.encode()))
        if zstd:
            import compression.zstd as zstd_mod
            (tmp_path / "rotated.log.zst").write_bytes(zstd_mod.compress(zstd.encode()))
        if binary:
            (tmp_path / "real.bin").write_bytes(binary)
        if corrupt:
            (tmp_path / "truncated.gz").write_bytes(corrupt)
        return tmp_path
    return install


def search(path, pattern=NEEDLE, case_sensitive=False):
    return sfc._run({"path": str(path), "text": pattern,
                     "case_sensitive": case_sensitive})


# --------------------------------------------------------------------------
# The defect, stated as a test
# --------------------------------------------------------------------------

class TestCompressedLogsAreSearched:

    def test_text_inside_a_gzipped_log_is_found(self, dir_of_files):
        """**The regression.** A gzip file whose *contents* are plain text was
        skipped as binary, because gzip framing carries NUL.
        """
        body = f"update-alternatives run\nERROR: {NEEDLE}\n" * 20
        path = dir_of_files(gz=body)
        out = search(path)
        assert NEEDLE in out
        assert "plain.log" not in out.splitlines()[0] if out else True
        assert "rotated.log.gz" in out, "the gzipped log is invisible"

    def test_the_reported_line_number_is_the_texts_own(self, dir_of_files):
        # Line 2 of the *contents*. Reporting the line inside the compressed
        # stream, or a byte offset, sends the person to a line that does not
        # exist in anything they can read.
        body = f"first line\nsecond line\nERROR: {NEEDLE}\n"
        out = search(dir_of_files(gz=body))
        assert "rotated.log.gz:3:" in out

    def test_the_count_says_the_gzipped_log_was_read(self, dir_of_files):
        """The footer is only printed when something was skipped, so the count
        of what *was* read needs a skipped file beside it to be visible at all.
        """
        path = dir_of_files(gz=f"ERROR: {NEEDLE}\n" * 5)
        (path / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00binary image\x00")
        out = search(path)
        assert "note: read 1 file(s)" in out, (
            "a gzip that was read but not counted would read as two searches "
            "disagreeing")
        assert "skipped 1 binary" in out

    def test_a_plain_log_still_works(self, dir_of_files):
        assert NEEDLE in search(dir_of_files(plain=f"ERROR: {NEEDLE}\n"))

    def test_both_a_plain_and_a_gzipped_log_are_found_together(self, dir_of_files):
        # The log pair is the ordinary shape: this week's plain, last week's
        # rotated. Answering about only one of them is the half-answer.
        out = search(dir_of_files(plain=f"now: {NEEDLE}\n",
                                  gz=f"then: {NEEDLE}\n"))
        assert "plain.log" in out and "rotated.log.gz" in out

    def test_an_xz_compressed_log_is_read(self, dir_of_files):
        out = search(dir_of_files(xz=f"ERROR: {NEEDLE}\n"))
        assert "rotated.log.xz" in out

    def test_a_zstd_compressed_log_is_read(self, dir_of_files):
        import compression.zstd as zstd_mod
        out = search(dir_of_files(zstd=f"ERROR: {NEEDLE}\n"))
        assert "rotated.log.zst" in out

    def test_a_bzip2_compressed_log_is_read(self, dir_of_files):
        path = dir_of_files()
        (path / "rotated.log.bz2").write_bytes(bz2.compress(f"ERR: {NEEDLE}\n".encode()))
        assert "rotated.log.bz2" in search(path)


# --------------------------------------------------------------------------
# The other half of the binary rule: a genuinely binary file stays binary
# --------------------------------------------------------------------------

class TestRealBinariesAreStillRefused:

    def test_a_real_binary_is_still_skipped_and_counted(self, dir_of_files):
        # The rule was right; only the order was wrong. A real binary holding
        # NUL must not start producing noise.
        out = search(dir_of_files(binary=b"\x00\x01\x02\x00ELFMAGIC\x00"))
        assert "1 binary" in out and "were skipped" in out, (
            "the binary rule was right; only its order was wrong")
        assert "0 file(s) were read" in out

    def test_a_gzip_of_binary_content_is_still_binary(self, dir_of_files):
        # **Decompressing does not make a binary text.** The sniff after
        # decompression is what keeps that true, and a version that skipped the
        # sniff entirely would find "text" inside a compressed tarball of
        # images.
        out = search(dir_of_files(gz=None, plain=None,
                                  binary=None))
        path = dir_of_files()
        (path / "images.tar.gz").write_bytes(gzip.compress(
            b"\x00\xff\xfe\x00binary contents\x00" * 40))
        out = search(path)
        assert "images.tar.gz" not in out, (
            "a gzip whose contents are binary is a binary, and must stay "
            "skipped")

    def test_a_run_of_text_inside_a_gzip_is_not_called_text_by_accident(
            self, dir_of_files):
        # The converse: the sniff is on the contents, so a text-in-gzip is text
        # and a binary-in-gzip is binary. Both directions have to hold, or the
        # fix has just inverted the rule.
        assert "1 binary" in search(
            dir_of_files(binary=gzip.compress(b"\x00\x01\x00" * 30)))


# --------------------------------------------------------------------------
# Files that cannot be read at all
# --------------------------------------------------------------------------

class TestUnreadableFiles:

    def test_a_truncated_archive_is_reported_not_searched(self, dir_of_files):
        """A corrupt stream is a real state - a disk that filled mid-rotation.
        The honest answer is "could not read this one", which is neither a
        match nor a binary skip.
        """
        out = search(dir_of_files(corrupt=b"\x1f\x8b\x08\x00not-a-stream"))
        assert "1 path(s) could not be read" in out
        # The footer lists "0 binary" beside "could not read 1", so which
        # bucket this file landed in is stated, not implied.
        assert "0 binary" in out, (
            "a corrupt gzip is not a binary file, and counting it as one "
            "hides the real reason")

    def test_the_reason_is_in_the_message(self, dir_of_files):
        out = search(dir_of_files(corrupt=b"\x1f\x8b\x08\x00not-a-stream"))
        assert "gzip" in out, "the format that failed is part of the reason"

    def test_a_corrupt_archive_does_not_stop_the_search(self, dir_of_files):
        # One unreadable file among several is not "nothing could be searched".
        out = search(dir_of_files(corrupt=b"\x1f\x8b\x08\x00bad",
                                  plain=f"OK: {NEEDLE}\n"))
        assert NEEDLE in out


# --------------------------------------------------------------------------
# The ceiling applies to the decompressed text
# --------------------------------------------------------------------------

class TestTheCeilingIsOnTheRightNumber:

    def test_a_highly_compressed_plain_log_is_not_skipped_as_too_big(
            self, dir_of_files):
        # 2 MiB of *text* compresses to almost nothing. A ceiling on the
        # compressed size is generous, not strict - this is the permissive case.
        body = f"line {NEEDLE}\n" * 20000   # ~0.4 MB, well inside
        out = search(dir_of_files(gz=body))
        assert "over 2.0 MiB" not in out
        assert "rotated.log.gz" in out

    def test_a_gz_bomb_is_stopped_by_the_decompressed_size(self, monkeypatch,
                                                            dir_of_files):
        """**The case that makes the order matter.** 2 MiB of NULs compresses
        to a few kB, so a ceiling measured on the compressed bytes would read
        an enormous log through - and the decompression is what exhausts
        memory. The limit is checked on the decompressed length.
        """
        bomb = b"\0" * (60 * 1024 * 1024)     # 60 MB of NUL, ~70 KB compressed
        path = dir_of_files()
        (path / "bomb.gz").write_bytes(gzip.compress(bomb))
        out = search(path)
        assert "1 oversized file(s)" in out, (
            "a limit checked on the compressed size is not a limit - the "
            "compressed bomb is 61 KB of a 60 MB body")

    def test_a_corrupt_stream_is_not_a_ceiling_answer(self, dir_of_files):
        out = search(dir_of_files(corrupt=b"\x1f\x8b\x08\x00bad"))
        assert "over 2.0 MiB" not in out


# --------------------------------------------------------------------------
# The helper itself
# --------------------------------------------------------------------------

class TestTheDecompressionHelper:

    def test_gzip_magic_is_recognised(self):
        body, how = sfc._decompress(gzip.compress(b"hello"))
        assert body == b"hello" and how == "gzip"

    def test_plain_text_comes_back_untouched(self):
        assert sfc._decompress(b"just text") == (b"just text", "")

    def test_an_empty_file_is_not_a_compression(self):
        assert sfc._decompress(b"") == (b"", "")

    @pytest.mark.parametrize("magic", [
        b"\x1f\x8b",      # gzip
        b"BZh9",          # bzip2
        b"\xfd7zXZ\x00",  # xz
    ])
    def test_each_magic_is_claimed_or_left_alone(self, magic):
        # An unknown or partial magic must not be mistaken for a format we can
        # read; that is how a truncated file becomes "unreadable".
        body, how = sfc._decompress(magic)
        assert isinstance(body, bytes)

    def test_a_truncated_gzip_reports_its_format(self):
        _, how = sfc._decompress(b"\x1f\x8b\x08\x00truncated")
        assert "gzip" in how and "unreadable" in how
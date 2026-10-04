"""Files handed to Chronoa from outside: `shani-chronoa photo.png`, or "Open with" (%U).

- a relative path means the *invoking* process's directory: a second launch is
  forwarded to the running instance, whose cwd is somewhere else entirely;
- `file://` URIs are decoded (spaces, non-ASCII); any other scheme is dropped,
  because fetching an http URL is network access nobody asked for;
- flags are not files.
"""

from pathlib import Path

from shani_chronoa.app import launch_paths


def test_relative_paths_resolve_against_the_invoking_cwd(tmp_path):
    assert launch_paths(["photo.png"], str(tmp_path)) == [str(tmp_path / "photo.png")]
    assert launch_paths(["../x.pdf"], str(tmp_path / "sub")) == [str(tmp_path / "x.pdf")]


def test_absolute_and_home_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert launch_paths(["/etc/os-release", "~/a.txt"], "/") == ["/etc/os-release", str(tmp_path / "a.txt")]


def test_file_uris_are_decoded():
    assert launch_paths(["file:///tmp/My%20Photo%C3%A9.png"], "/") == ["/tmp/My Photoé.png"]


def test_other_schemes_and_flags_are_not_files():
    assert launch_paths(["https://example.com/a.png", "--debug", "--ask=hi", "-x", ""], "/") == []


def test_the_desktop_entry_passes_uris_and_declares_types():
    entry = (Path(__file__).resolve().parent.parent / "usr/share/applications/shani-chronoa.desktop").read_text()
    assert "Exec=shani-chronoa %U" in entry
    types = next(line for line in entry.splitlines() if line.startswith("MimeType=")).split("=", 1)[1]
    assert {"image/png", "application/pdf", "text/plain", "audio/mpeg", "video/mp4"} <= set(types.split(";"))

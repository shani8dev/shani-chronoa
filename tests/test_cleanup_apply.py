"""`cleanup_report` could measure and name the command; nothing could run it.

Every one of its four recommendations ended at a sentence telling the person
what to type in a terminal: empty the trash (already `empty_trash`'s job), clear
your cache, trim the system log, remove unused Flatpak runtimes. Three of the
four had no skill behind them, so "free up 20 GB" produced a number and then
nothing - the wrong shape of answer for an assistant that already holds the
measurement and a fixed whitelist of skills.

`cleanup_apply` is the action half for the two that are per-user and need no
root. The journal is left out on purpose and the reason is in the skill's
docstring: the sandbox blocks `setuid`, and nothing inside a sandboxed child can
answer a polkit prompt - `sandbox/executor.py` records exactly that failure.
The one root path in this tree is a `usr/bin/` helper the *person* runs through
`pkexec`, like `shani-chronoa-lab-network`.

**The scope cannot be widened by the model.** `target` is an enum of two and
there is no path argument, so "clear my Documents" is not a thing this skill can
be talked into. The cache directory's *contents* go; the directory stays,
because tools recreate cache paths they expect to exist.

The control matters more than the refusal: this repository has shipped two
switches that could never be turned on (`calendar_write`, `fm_radio`), each a
permanent refusal wearing the clothes of a permission. So the gate is asserted
to *open*, through a real keyfile backend, and the key is asserted to exist in
the compiled schema - which is the exact defect that produced the first one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import cleanup_apply as C  # noqa: E402


def _compile_schemas(directory: Path) -> None:
    source = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
    shutil.copytree(source, directory, dirs_exist_ok=True)
    result = subprocess.run(["glib-compile-schemas", str(directory)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert (directory / "gschemas.compiled").is_file(), (
        "glib-compile-schemas exited 0 and wrote nothing; a clean exit code "
        "proves nothing here")


def _keyfile(backend: Path, **settings) -> None:
    settings_dir = backend / "glib-2.0" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f"{k}={'true' if v else 'false'}" for k, v in settings.items())
    (settings_dir / "keyfile").write_text(f"[org.shani.chronoa]\n{lines}\n")


def _backend(tmp_path, monkeypatch, granted: bool):
    backend = tmp_path / "gsettings"
    _compile_schemas(backend)
    _keyfile(backend, **{C._CONSENT_KEY: granted})
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(backend))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(backend))
    from shani_chronoa.config import ChronoaConfig
    assert ChronoaConfig().get_bool(C._CONSENT_KEY, False) is granted, (
        "the control did not take, so every assertion below would pass "
        "regardless of the gate")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A cache directory with real content in it."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)  # the hermetic-env fixture already made it
    cache = home / ".cache"
    (cache / "an-app").mkdir(parents=True)
    (cache / "an-app" / "blob.bin").write_bytes(b"x" * 4096)
    (cache / "top-level.tmp").write_text("scratch")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return cache


class TestTheSwitchIsRealAndShutByDefault:
    def test_the_key_exists_in_the_schema(self):
        """The `fm_radio` / `calendar_write` defect, asserted directly.

        A `cleanup-enabled` that is not in the schema reads `False` forever,
        whatever the code says, and the refusal it produces is indistinguishable
        from a working permission.
        """
        xml = (_REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
        root = ET.fromstring(xml)
        keys = {k.get("name"): k for k in root.iter("key")}
        assert C._CONSENT_KEY in keys, (
            f"{C._CONSENT_KEY} is not in the schema, so the gate can never open")
        default = keys[C._CONSENT_KEY].find("default")
        assert (default is not None and default.text.strip() == "false"), (
            "a destructive permission that starts on is not one")

    def test_it_refuses_and_names_the_switch(self, tmp_path, monkeypatch, cache):
        _backend(tmp_path, monkeypatch, granted=False)
        out = C._run({"target": "cache"})
        assert C._CONSENT_KEY in out, (
            f"the refusal does not name the switch: {out!r}")
        assert "Refusing" in out or "refusing" in out

    def test_nothing_is_removed_while_it_is_off(self, tmp_path, monkeypatch, cache):
        _backend(tmp_path, monkeypatch, granted=False)
        C._run({"target": "cache"})
        assert sorted(p.name for p in cache.iterdir()) == ["an-app", "top-level.tmp"], (
            "files were removed by a refused call")

    def test_the_gate_opens_when_the_switch_is_on(self, tmp_path, monkeypatch, cache):
        _backend(tmp_path, monkeypatch, granted=True)
        out = C._run({"target": "cache"})
        assert "Refusing" not in out and "refusing" not in out, (
            f"the switch is on and it still refused: {out!r}")
        assert list(cache.iterdir()) == [], f"the cache was not emptied: {out!r}"


class TestTheScopeCannotBeWidened:
    def test_there_is_no_path_argument_to_widen(self):
        """The scope is the report's own list, structurally."""
        params = C._SCHEMA["function"]["parameters"]["properties"]
        assert set(params) == {"target"}, (
            f"a path-like argument would be something the model could aim: {sorted(params)}")
        assert params["target"]["enum"] == ["cache", "flatpak"]

    def test_a_target_outside_the_enum_is_refused(self, tmp_path, monkeypatch, cache):
        _backend(tmp_path, monkeypatch, granted=True)
        for bad in ("/etc", ".", "../..", "journal"):
            out = C._run({"target": bad})
            assert "must be one of" in out, f"{bad!r} was not refused: {out!r}"

    def test_the_directory_is_kept_only_its_contents_go(self, tmp_path, monkeypatch,
                                                          cache):
        """Some tools recreate a cache path they expect to already exist."""
        _backend(tmp_path, monkeypatch, granted=True)
        C._run({"target": "cache"})
        assert cache.is_dir(), "the cache directory itself was removed"

    def test_a_protected_root_is_refused_before_anything_is_removed(self):
        """The catalogue guard is consulted, and it refuses.

        Asserted rather than triggered: pointing the cache at a protected root
        and finding out afterwards is not a test, it is an incident - and the
        roots are the real `/` and the real home, resolved at import.
        """
        import pytest as _pytest

        from shani_chronoa import files

        root = next(iter(files.PROTECTED_ROOTS))
        with _pytest.raises(files.PathProblem):
            files.refuse_catalogue(root, "clear")

    def test_the_skill_consults_that_guard(self, tmp_path, monkeypatch, cache):
        """Wiring, so the guard above cannot become unreachable for this skill."""
        _backend(tmp_path, monkeypatch, granted=True)
        from shani_chronoa import files

        seen = []
        real = files.refuse_catalogue
        monkeypatch.setattr(files, "refuse_catalogue",
                            lambda path, verb: (seen.append((path, verb)), real(path, verb))[1])
        C._run({"target": "cache"})
        assert seen, "cleanup_apply cleared a directory without consulting refuse_catalogue"
        assert seen[0][0] == cache


class TestThePostConditionReadsTheFilesystem:
    def test_a_cleared_cache_verifies(self, tmp_path, monkeypatch, cache):
        _backend(tmp_path, monkeypatch, granted=True)
        C._run({"target": "cache"})
        ok, evidence = C._post_condition({"target": "cache"})
        assert ok, evidence

    def test_a_cache_still_holding_entries_fails(self, tmp_path, monkeypatch, cache):
        """The point of a post-condition: not taking the skill's word for it."""
        _backend(tmp_path, monkeypatch, granted=True)
        ok, evidence = C._post_condition({"target": "cache"})
        assert not ok, (
            f"a cache holding entries verified as cleared: {evidence!r}")

    def test_flatpak_is_not_checked_because_flatpak_proves_it(self, tmp_path,
                                                              monkeypatch):
        """`None`, not a fake pass.

        Flatpak refuses to remove a runtime any app still references, so there
        is nothing here to observe; answering True would be a claim.
        """
        assert C._post_condition({"target": "flatpak"}) is None

    def test_a_missing_cache_is_not_a_pass(self, tmp_path, monkeypatch):
        _backend(tmp_path, monkeypatch, granted=True)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "nowhere"))
        ok, evidence = C._post_condition({"target": "cache"})
        assert not ok, "a cache that does not exist verified as cleared"
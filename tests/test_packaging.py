"""RED tests: packaging/schema metadata (dead settings, icon, bytecode, Architecture, MCP stdio trust).

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import re
import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The Arch manifest that actually ships this package lives in the sibling
#: `shani-pkgbuilds` repo, which builds from this tree by pinned commit.
#: This repo used to carry a second, drifting copy of it, which is how
#: `llama-cpp` and `tesseract` went missing from a published image while
#: everything here read green. Tests read the real one, so a divergence
#: between the tree and what is packaged shows up here instead.
_PKGBUILD = Path(__file__).resolve().parents[2] / "shani-pkgbuilds" / "shani-chronoa" / "PKGBUILD"

PKG_DIR = REPO_ROOT / "usr/lib/shani-chronoa"
SCHEMA_SRC = REPO_ROOT / "usr/share/glib-2.0/schemas"


def _key_block(xml: str, name: str) -> str:
    """Extract one <key> block from the gschema XML."""
    match = re.search(rf'<key name="{name}".*?</key>', xml, re.S)
    assert match, f"key {name!r} not found in gschema"
    return match.group(0)


def _pkgbuild_array(name: str) -> list[str]:
    """Declared package names of one PKGBUILD array (depends, optdepends, ...).

    Scoped to the array's own block and shell-lexed, for two reasons a bare
    substring search over the whole file cannot give: a name mentioned inside
    a `#` comment is not a declared entry (both arrays carry comments that
    name real packages), and a check about `optdepends` must not be
    satisfiable by the same string appearing somewhere else in the file.
    The closing paren is matched with optional leading whitespace, because the
    canonical manifest indents its `optdepends` closer by two spaces while the
    one this file was written against put it at column 0 - and a column-anchored
    pattern reported "no optdepends array" for a manifest that plainly has one.
    That is the same failure as the drifted copy: an assertion that encodes one
    file's formatting rather than the property it means to check.
    """
    pkgbuild = _PKGBUILD.read_text()
    match = re.search(rf"^{name}=\s*\((.*?)^[ \t]*\)", pkgbuild, re.M | re.S)
    assert match, f"no {name}=( ... ) array in PKGBUILD"
    return [token.split(":", 1)[0] for token in shlex.split(match.group(1), comments=True)]


def _debian_field(field: str) -> list[str]:
    """Entries of one comma-separated DEBIAN/control field (Depends, Suggests).

    Field-scoped on purpose: the control file's Description body still names
    both speech programs, so a whole-file substring search could never fail
    for the reason these tests exist.
    """
    control = (REPO_ROOT / "DEBIAN/control").read_text()
    match = re.search(rf"^{field}:\s*(.+)$", control, re.M)
    assert match, f"no {field}: field in DEBIAN/control"
    return [entry.strip() for entry in match.group(1).split(",") if entry.strip()]


class TestSchemaMetadata:
    """The gschema must compile and every shipped key must have a Python consumer."""

    def test_schema_compiles_to_gschemas_compiled(self, compiled_schema_dir):
        # Baseline (AGENTS.md requirement): the gschema must actually compile.
        assert (compiled_schema_dir / "gschemas.compiled").is_file()

    def test_every_schema_key_has_python_consumer(self):
        # Given: the gschema XML defines a set of keys
        xml = (SCHEMA_SRC / "org.shani.chronoa.gschema.xml").read_text()
        keys = re.findall(r'<key name="([^"]+)"', xml)
        assert keys, "no keys parsed from gschema"
        py_sources = [p for p in PKG_DIR.rglob("*.py") if "__pycache__" not in str(p)]
        # When: each key is looked up in the Python package sources
        unconsumed = [k for k in keys if not any(k in src.read_text() for src in py_sources)]
        # Then: every shipped setting must have a Python consumer (no dead settings)
        assert unconsumed == []

    def test_notification_enabled_describes_actual_behavior(self):
        # Given: the gschema XML
        xml = (SCHEMA_SRC / "org.shani.chronoa.gschema.xml").read_text()
        # When: the notification-enabled key's documentation is read
        block = _key_block(xml, "notification-enabled")
        # Then: it must describe the real behavior - spoken replies (app.py's
        # _on_response_ready) and timer notifications (skills/timer.py) - not
        # a separate desktop-notification subsystem that does not exist.
        assert "speak" in block.lower()
        assert "timer" in block.lower()

    def test_hardware_profile_documents_supported_values_and_startup(self):
        # Given: the gschema XML
        xml = (SCHEMA_SRC / "org.shani.chronoa.gschema.xml").read_text()
        # When: the hardware-profile key's documentation is read
        block = _key_block(xml, "hardware-profile")
        # Then: it must list every supported value and state the startup semantics
        for value in ("auto", "low", "medium", "high", "gpu"):
            assert value in block.lower()
        assert "startup" in block.lower()


class TestPackagingMetadata:
    """Shipped payload metadata must be coherent (icon, bytecode, architecture)."""

    def test_declared_icon_exists_in_hicolor_dir(self):
        # Given: the .desktop file declares Icon=shani-chronoa
        desktop = (REPO_ROOT / "usr/share/applications/shani-chronoa.desktop").read_text()
        match = re.search(r"^Icon=(.+)$", desktop, re.M)
        assert match, "no Icon= line in desktop file"
        icon = match.group(1).strip()
        # When: the hicolor scalable apps dir is checked for that icon
        icon_path = REPO_ROOT / "usr/share/icons/hicolor/scalable/apps" / f"{icon}.svg"
        # Then: the declared icon must be shipped
        assert icon_path.is_file()

    def test_hicolor_icon_is_a_valid_svg(self):
        # Given: the shipped hicolor icon
        icon_path = REPO_ROOT / "usr/share/icons/hicolor/scalable/apps/shani-chronoa.svg"
        # When: its content is read
        content = icon_path.read_text()
        # Then: it must be a real SVG document, not an empty or placeholder file
        assert "<svg" in content
        assert content.strip().endswith("</svg>")

    def test_no_pycache_in_packaged_payload(self):
        # Given: the packaged payload tree (usr/)
        # When: it is scanned for __pycache__ directories
        pycache_dirs = list((REPO_ROOT / "usr").rglob("__pycache__"))
        # Then: no stale bytecode may be shipped
        assert pycache_dirs == []

    def test_no_bytecode_files_in_packaged_payload(self):
        # Given: the packaged payload tree (usr/)
        # When: it is scanned for .pyc bytecode files
        pyc_files = list((REPO_ROOT / "usr").rglob("*.pyc"))
        # Then: no stale bytecode may be shipped
        assert pyc_files == []

    def test_debian_architecture_is_all(self):
        # Given: the Debian control file
        control = (REPO_ROOT / "DEBIAN/control").read_text()
        match = re.search(r"^Architecture:\s*(.+)$", control, re.M)
        assert match, "no Architecture= line in DEBIAN/control"
        # Then: the package must be architecture-independent (pure Python payload)
        assert match.group(1).strip() == "all"

    def test_desktop_file_has_no_inert_mime_type(self):
        # Given: the desktop entry
        desktop = (REPO_ROOT / "usr/share/applications/shani-chronoa.desktop").read_text()
        # Then: no x-scheme-handler (no URI scheme handler is implemented, so
        # declaring one would be a lie to the desktop)...
        assert "x-scheme-handler" not in desktop
        # ...and every file type it declares is one it really opens: Exec passes
        # the files (%U), `app.launch_paths` turns them into attachments
        # ("Open with Chronoa", 2026-10-01), and each type is one `attachments`
        # accepts as a file. A MimeType with no handler behind it is the inert
        # declaration this test was written to forbid.
        if "MimeType=" in desktop:
            assert re.search(r"^Exec=shani-chronoa %[UF]$", desktop, re.M), "MimeType without %U/%F in Exec"
            from shani_chronoa import app
            assert callable(app.launch_paths)
            types = re.search(r"^MimeType=(.*)$", desktop, re.M).group(1).split(";")
            assert all(t.split("/")[0] in ("image", "audio", "video", "text", "application") for t in types if t)

    def test_arch_install_refreshes_icon_cache_on_remove(self):
        # Given: the Arch .install script
        install = (REPO_ROOT / "shani-chronoa.install").read_text()
        # When: the post_remove() function is read
        post_remove = re.search(r"post_remove\(\)\s*\{(.*?)\n\}", install, re.S)
        assert post_remove, "no post_remove() in shani-chronoa.install"
        # Then: it must refresh the icon cache, matching install/upgrade
        assert "gtk-update-icon-cache" in post_remove.group(1)

    def test_pkgbuild_installs_every_binary_not_a_hand_kept_list(self):
        """`shani-chronoa-daemon` and `shani-chronoa-search` were built,
        unit-tested, and named by a gschema description and a search-provider
        install - and left out of the PKGBUILD's install list, so neither existed
        on a real install while every test in this suite passed. A test that
        reads the list still cannot catch a *new* binary being forgotten, so this
        asserts the rule instead: the package installs whatever is in usr/bin.
        """
        # The real rule is not "install a glob" - it is **every launcher in
        # usr/bin is installed**. That holds whichever way the manifest spells
        # it, so this asserts the outcome instead of one implementation of it.
        # (The drifted in-repo copy used a glob; the canonical one lists them,
        # and when the two disagreed the glob-based assertion passed against a
        # file nobody built from.)
        pkgbuild = _PKGBUILD.read_text()
        every = sorted(p.name for p in (REPO_ROOT / "usr" / "bin").iterdir()
                       if p.is_file())
        assert len(every) >= 5, every
        missing = [name for name in every
                   if f"usr/bin/{name}" not in pkgbuild]
        assert not missing, (
            f"these launchers are in usr/bin but the manifest never installs "
            f"them: {missing}. A skill that shells out to one reports 'not "
            f"installed' on every machine."
        )

    def test_pkgbuild_installs_the_units_and_dbus_services_too(self):
        """Same class of omission, same fix. Without these the package has no
        `shani-chronoa-daemon.service` (so background mode cannot be started,
        and the Settings switch that enables it does nothing) and no
        SearchProvider service (so the desktop search entry points at a file
        that is not installed). shani-pkgbuilds installed both; this did not."""
        # Same outcome-first approach: every unit and every dbus service present
        # in the tree is installed by the manifest.
        pkgbuild = _PKGBUILD.read_text()
        units = sorted(p.name for p in
                       (REPO_ROOT / "usr/lib/systemd/user").glob("*.service"))
        assert len(units) >= 3, units
        missing_units = [u for u in units if u not in pkgbuild]
        assert not missing_units, f"units not installed: {missing_units}"
        services = sorted(p.name for p in
                          (REPO_ROOT / "usr/share/dbus-1/services").glob("*.service"))
        missing_services = [s for s in services if s not in pkgbuild]
        assert not missing_services, f"dbus services not installed: {missing_services}"

    def test_pkgbuild_installs_hicolor_icon(self):
        # Given: the Arch PKGBUILD
        pkgbuild = _PKGBUILD.read_text()
        # Then: it must install the icon into the hicolor scalable apps path
        assert "icons/hicolor/scalable/apps/shani-chronoa.svg" in pkgbuild

    def test_pkgbuild_never_ships_bytecode(self):
        # Given: the Arch PKGBUILD
        pkgbuild = _PKGBUILD.read_text()
        # Then: it must exclude __pycache__/pyc from the packaged payload
        assert "__pycache__" in pkgbuild
        assert "*.pyc" in pkgbuild

    def test_pkgbuild_ships_voice_input(self):
        """whisper-cpp is a HARD dependency, deliberately.

        This test used to assert the opposite - that STT must never be a hard
        dependency, on the grounds that `stt.py`'s `is_available()` returning
        False only logs a warning so the app stays usable as a text assistant.
        That reasoning is still true, and it is why `is_available()` is guarded
        the way it is, but the conclusion was wrong for a product whose whole
        point is voice. Shanios ships voice input working out of the box; an
        install that needs a second pacman command before the microphone does
        anything is a broken first run, not a minimal one.

        So the invariant now is the opposite one, and it is still an invariant:
        the binary must be declared **somewhere**. Silently dropping it would
        leave `shani-chronoa-sense` reporting "not installed" on every machine
        with no hint that voice input exists.
        """
        # Given: the Arch PKGBUILD's depends and optdepends arrays
        depends = _pkgbuild_array("depends")
        optdepends = _pkgbuild_array("optdepends")
        # When: whisper-cpp is looked up in each of them
        problems = []
        if "whisper-cpp" not in depends and "whisper-cpp" not in optdepends:
            problems.append(
                "'whisper-cpp' is declared nowhere, so a user installing from "
                "this PKGBUILD gets no voice input and no hint that the feature "
                f"exists; depends is {depends} and optdepends is {optdepends}"
            )
        # Then: voice input is present in a default install
        assert problems == []

    def test_pkgbuild_offers_rhvoice_for_tts_quality(self):
        # Given: the Arch PKGBUILD's depends and optdepends arrays
        depends = _pkgbuild_array("depends")
        optdepends = _pkgbuild_array("optdepends")
        # When: both halves of the RHVoice pair are looked up in each
        # (the pair is load-bearing: tts.py's rhvoice branch needs a language
        # pack *and* an actual voice - _rhvoice_has_voice() scans for one and
        # engine() skips rhvoice entirely when it finds none, so offering only
        # one half advertises a fallback that can never fire)
        problems = []
        for pkg in ("rhvoice-language-english", "rhvoice-voice-slt"):
            if pkg not in optdepends:
                problems.append(
                    f"{pkg!r} is not offered in optdepends, so there is no "
                    "natural-voice fallback between piper and espeak-ng"
                )
            if pkg in depends:
                problems.append(
                    f"{pkg!r} is a hard dependency, but speech already works "
                    "through espeak-ng, which every Shanios image ships"
                )
        # Then: both must be offered, neither required
        assert problems == []

    def test_pkgbuild_never_hard_depends_the_piper_binary(self):
        # Given: the Arch PKGBUILD's depends array
        depends = _pkgbuild_array("depends")
        # When: entries whose declared package name starts with "piper" are selected
        piper_deps = [dep for dep in depends if dep.startswith("piper")]
        # Then: none may. tts.py's engine() is now a four-way fallback, best
        # first - piper, rhvoice, espeak-ng, and kokoro when asked for - and only espeak-ng
        # ships in every Shanios image, so piper is never load-bearing. It is
        # also the wrong binary on Arch: extra/piper is the GTK gaming-mouse
        # configurator, a completely different program, so a hard dep on that
        # name installs the wrong binary which tts.py would then launch with
        # TTS arguments.
        #
        # The reasoning this comment used to carry is corrected (2026-10-02):
        # it said the piper TTS package "is not installable from the Arch repos
        # at all (it needs onnxruntime, which is not in them)". The runtime
        # arrived - `onnxruntime-cpu` has been in extra since 2026-09-04 - which
        # is exactly what made the Kokoro backend possible. The conclusion is
        # unchanged and now rests on the ordering rather than on a missing
        # package: espeak-ng is the only hard depend, so the other three are
        # never required for speech to work.
        assert piper_deps == []

    def test_kokoro_needs_no_package_and_espeak_stays_the_floor(self):
        # Kokoro runs through sherpa-onnx's release build, which carries its own
        # onnxruntime and is a pinned download into the user's home (voices.py),
        # exactly like Piper's. So *speech* may never name an onnxruntime package:
        # declaring one would point a user at a runtime the voice path never
        # reads, and would put it in front of every install.
        #
        # Corrected (2026-10-02, with u2net): the hard rule still holds in
        # `depends` - nothing about speech or about any other feature may make a
        # machine without onnxruntime uninstallable. What is no longer true is
        # that no package may be *offered*: `segmentation_u2net.py` genuinely
        # imports onnxruntime to cut a background out of any object (PP-HumanSeg
        # only knows about people), so `python-onnxruntime-cpu` belongs in
        # optdepends - a feature that needs a runtime is a feature a user may
        # decline, and `remove_background` then says what is missing instead of
        # quietly doing the people-only thing.
        depends = _pkgbuild_array("depends")
        optdepends = _pkgbuild_array("optdepends")
        problems = [f"{name!r} is a hard dependency, so a machine without it "
                    f"cannot install the assistant at all"
                    for name in depends if "onnxruntime" in name]
        u2net_source = (REPO_ROOT / "usr/lib/shani-chronoa/shani_chronoa/segmentation_u2net.py")
        if u2net_source.exists():
            assert "import onnxruntime" in u2net_source.read_text(), (
                "the object background remover is offered a runtime it never reads")
        if "espeak-ng" not in depends:
            problems.append(
                "'espeak-ng' is not a hard dependency although it is the one "
                "engine guaranteed to be present on Shanios, and it is the "
                "floor the whole fallback chain rests on"
            )
        assert problems == []

    def test_pkgbuild_offers_sox_without_requiring_it(self):
        # Given: the Arch PKGBUILD's depends and optdepends arrays
        depends = _pkgbuild_array("depends")
        optdepends = _pkgbuild_array("optdepends")
        # sox is what `tts.py:apply_timbre` shells out to for the pitch, tempo
        # and rate settings. It must be offered, because a user who does not
        # know it exists has no way to turn those three rows on; and it must not
        # be required, because speech works without it and a missing optional
        # engine has to degrade to a working assistant that says the transform
        # was skipped - not to an install that refuses to complete.
        problems = []
        if "sox" not in optdepends:
            problems.append(
                f"'sox' is offered nowhere (absent from optdepends), so the "
                f"pitch, tempo and rate rows in Settings cannot do anything and "
                f"nothing says the package is the reason; optdepends today is "
                f"{optdepends}"
            )
        if "sox" in depends:
            problems.append(
                "'sox' is a hard dependency, but speech does not need it: "
                "tts.py applies the timbre chain only when it is installed and "
                "speaks the reply unchanged otherwise, so requiring it would "
                "make an optional preference into an install-time requirement"
            )
        assert problems == []

    def test_debian_control_offers_sox_as_a_suggestion(self):
        # The Debian sibling of the check above, and the convention trap AGENTS.md
        # warns about: the field here is Suggests, not optdepends, and a `sox`
        # that only appeared in the Arch PKGBUILD would leave the Debian package
        # silently without the capability.
        #
        # `_debian_field` splits the field on commas and keeps each entry's
        # description attached, so the package name is the part before the
        # first " (". An entry whose *description* contains a comma arrives as
        # two pieces - `python3-onnxruntime (Kokoro TTS` and `a neural voice
        # instead of...` do, and go unnoticed because nothing tests for them -
        # so this one's description stays comma-free for the check to mean what
        # it says.
        names = [entry.split(" (", 1)[0].strip() for entry in _debian_field("Suggests")]
        assert "sox" in names, (
            f"DEBIAN/control does not offer sox as a Suggests entry: {names}"
        )

    def test_debian_control_does_not_hard_depend_piper_tts(self):
        # Given: the Debian control file's Depends field only (its Description
        # body still names both speech programs, so the field has to be the
        # thing under test or nothing here could fail)
        depends = _debian_field("Depends")
        # When: the hard dependencies are checked
        problems = []
        if "piper-tts" in depends:
            problems.append(
                "'piper-tts' is a hard dependency, but tts.py's engine() falls "
                "back from piper to rhvoice to espeak-ng - there are two further "
                "engines after piper, so it is never required for speech"
            )
        if "whisper.cpp" in depends:
            problems.append(
                "'whisper.cpp' is a hard dependency, but STT is optional: "
                "stt.py's is_available() returning False only logs a warning, "
                "app.py continues, and the app is fully usable as a text assistant"
            )
        if "espeak-ng" not in depends:
            problems.append(
                "'espeak-ng' is not a hard dependency although it is the one "
                "engine guaranteed to be present on Shanios - the exact "
                "inversion that matters: a Debian user is offered an "
                "uninstallable package for a feature that would have worked"
            )
        # Then: neither optional engine may be required, and the always-present
        # one must be
        assert problems == []


class TestMcpTrustModel:
    """Approved default: same-user stdio MCP trust. The server must never expose a network transport."""

    def test_mcp_server_is_stdio_only(self):
        mcp_src = (PKG_DIR / "shani_chronoa/mcp.py").read_text()
        assert 'transport="stdio"' in mcp_src
        assert 'transport="sse"' not in mcp_src
        assert 'transport="http"' not in mcp_src
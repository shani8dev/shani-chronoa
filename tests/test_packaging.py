"""RED tests: packaging/schema metadata (dead settings, icon, bytecode, Architecture, MCP stdio trust).

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import re
import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
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
    The closing paren is anchored to column 0 because optdepends descriptions
    legitimately contain `(` and `)` of their own.
    """
    pkgbuild = (REPO_ROOT / "PKGBUILD").read_text()
    match = re.search(rf"^{name}=\s*\((.*?)^\)", pkgbuild, re.M | re.S)
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
        # Then: the inert x-scheme-handler MIME declaration must be gone (no URI
        # handler is implemented, so declaring one would be a lie to the desktop)
        assert "MimeType=" not in desktop
        assert "x-scheme-handler" not in desktop

    def test_arch_install_refreshes_icon_cache_on_remove(self):
        # Given: the Arch .install script
        install = (REPO_ROOT / "shani-chronoa.install").read_text()
        # When: the post_remove() function is read
        post_remove = re.search(r"post_remove\(\)\s*\{(.*?)\n\}", install, re.S)
        assert post_remove, "no post_remove() in shani-chronoa.install"
        # Then: it must refresh the icon cache, matching install/upgrade
        assert "gtk-update-icon-cache" in post_remove.group(1)

    def test_pkgbuild_installs_hicolor_icon(self):
        # Given: the Arch PKGBUILD
        pkgbuild = (REPO_ROOT / "PKGBUILD").read_text()
        # Then: it must install the icon into the hicolor scalable apps path
        assert "icons/hicolor/scalable/apps/shani-chronoa.svg" in pkgbuild

    def test_pkgbuild_never_ships_bytecode(self):
        # Given: the Arch PKGBUILD
        pkgbuild = (REPO_ROOT / "PKGBUILD").read_text()
        # Then: it must exclude __pycache__/pyc from the packaged payload
        assert "__pycache__" in pkgbuild
        assert "*.pyc" in pkgbuild

    def test_pkgbuild_offers_whisper_cpp_as_optdepend(self):
        # Given: the Arch PKGBUILD's depends and optdepends arrays
        depends = _pkgbuild_array("depends")
        optdepends = _pkgbuild_array("optdepends")
        # When: whisper-cpp is looked up in each of them
        problems = []
        if "whisper-cpp" not in optdepends:
            problems.append(
                "'whisper-cpp' is offered nowhere (absent from optdepends), so a "
                "user installing from this PKGBUILD gets no hint that voice input "
                f"exists at all; optdepends today is {optdepends}"
            )
        if "whisper-cpp" in depends:
            problems.append(
                "'whisper-cpp' is a hard dependency, but STT is optional: "
                "stt.py's is_available() returning False only logs a warning, "
                "app.py continues, and the app is fully usable as a text assistant"
            )
        # Then: it must be offered, not required
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
        # Then: none may. tts.py's engine() is a three-way fallback, best
        # first - piper, then rhvoice, then espeak-ng - and only espeak-ng
        # ships in every Shanios image, so piper is never load-bearing. It is
        # also the wrong binary on Arch, twice over: the piper TTS package is
        # not installable from the Arch repos at all (it needs onnxruntime,
        # which is not in them), and Arch's extra/piper is the GTK
        # gaming-mouse configurator - a completely different program. A hard
        # dep on that name installs the wrong binary, which tts.py would then
        # launch with TTS arguments. Both facts are recorded in tts.py's own
        # comments; this test exists so the fix for the over-declared
        # dependencies above cannot resolve into that mistake.
        assert piper_deps == []

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
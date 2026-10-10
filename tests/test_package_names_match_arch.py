"""The Arch package names this project states to users, checked against reality.

Three skills told the user to install a package that does not contain the binary
they needed, found by auditing every package name in the tree against
`tools/cli_matrix.py`'s command-to-package map (built from pacman's own file
database on a real Shanios image). All three are corrected here and this file
exists so they cannot drift back.

The `wrong` column is the half that matters. Asserting only that the right name
appears would pass on a file that says "it comes from `bluez-utils`... actually
`bluez`", which is the shape a half-finished edit takes.
"""

import pathlib
from pathlib import Path

import pytest

from shani_chronoa import files

PACKAGE = Path(files.__file__).parent

# binary -> (correct Arch package, the old claim verbatim, files that stated it)
CORRECTED = {
    "wpctl": (
        "wireplumber",
        # The `pipewire` package. wpctl left it when wireplumber split off the
        # PipeWire project, so this was a stale truth rather than a guess.
        "pipewire package group",
        ["skills/privacy.py", "senses/audio.py"],
    ),
    "bluetoothctl": (
        "bluez-utils",
        # `bluez` is the daemon; the client is not in it.
        "comes from bluez.",
        ["skills/toggle_bluetooth.py"],
    ),
    "udisksctl": (
        "udisks2",
        # The pre-rename package name.
        'On Arch it comes from "\n            "udisks."',
        ["skills/manage_mount.py"],
    ),
}

#: Every binary `tool_missing()` is asked about across the skills, with the
#: package the matrix says ships it. A skill reaching for an unnamed binary
#: falls back to "the package that provides it", which is a sentence that helps
#: nobody install anything.
TOOL_MISSING_CALLED_WITH = {
    "systemctl": "systemd",
    "nmcli": "networkmanager",
    "wpctl": "wireplumber",
    "findmnt": "util-linux",
    "df": "coreutils",
    "journalctl": "systemd",
    "lp": "cups",
    "gsettings": "glib2",
    "xdotool": "xdotool",
}


def _source(relative: str) -> str:
    return (PACKAGE / relative).read_text()


def test_each_corrected_package_name_is_stated():
    for binary, (correct, _wrong, paths) in CORRECTED.items():
        for relative in paths:
            text = _source(relative)
            assert correct in text, f"{relative} should name {binary}'s package {correct}"


def test_no_file_still_makes_the_old_claim():
    """The half that catches a half-finished edit.

    Matching the old *wording* rather than the bare package name is deliberate,
    and the first version of this test got it wrong. It searched for `bluez`,
    which then failed against the corrected text - because the corrected text
    says "it comes from bluez-utils (the bluez package is the daemon and does
    not ship this client)". Naming the package you are steering someone away
    from is the point of the sentence. Searching for `bluez` as a substring also
    matches inside `bluez-utils`, which is the same prefix trap that hid two of
    these three defects during the audit itself.
    """
    for binary, (_correct, old_claim, paths) in CORRECTED.items():
        for relative in paths:
            text = _source(relative)
            assert old_claim not in text, (
                f"{relative} still contains the old claim {old_claim!r} for {binary}"
            )


def test_every_tool_missing_caller_names_a_real_package():
    """A vague fallback is the failure; assert none of these fall through."""
    for binary, package in TOOL_MISSING_CALLED_WITH.items():
        assert binary in files._PACKAGE_HINTS, f"{binary} has no package hint"
        assert files._PACKAGE_HINTS[binary] == package
        assert "the package that provides it" not in files.tool_missing(binary, "do a thing")


def test_tool_missing_still_honest_for_a_binary_it_does_not_know():
    """The table must not turn an unknown binary into a confident wrong name."""
    out = files.tool_missing("definitely-not-a-real-binary", "do a thing")
    assert "not installed" in out
    assert "the package that provides it" in out
    assert "nothing was done" in out


def test_the_three_known_traps_are_mapped_to_the_non_obvious_name():
    """Named separately because these are the ones a future edit will get wrong.

    Each of these has a same-prefixed sibling package, which is exactly why a
    substring check passes them: `bluez` in `bluez-utils`, `udisks` in
    `udisks2`, and `wireplumber` reached for as `pipewire`.
    """
    assert files._PACKAGE_HINTS["bluetoothctl"] != "bluez"
    assert files._PACKAGE_HINTS["udisksctl"] != "udisks"
    assert files._PACKAGE_HINTS["wpctl"] != "pipewire"
    for binary in ("bluetoothctl", "udisksctl", "wpctl"):
        hint = files._PACKAGE_HINTS[binary]
        assert hint in ("bluez-utils", "udisks2", "wireplumber")


def test_the_hint_table_has_no_value_that_is_its_own_binary():
    """`xdotool: xdotool` is legitimate; `gsettings: gsettings` is not a package."""
    real_packages = {
        "glib2", "bluez-utils", "wireplumber", "udisks2", "pipewire",
        "pipewire-audio", "libpulse", "coreutils", "util-linux", "systemd",
        "cups", "networkmanager", "xdotool", "psmisc", "iproute2", "iputils",
        "bind", "poppler", "sane", "smartmontools", "vnstat", "libnotify",
        "upower", "fwupd", "gnome-shell", "power-profiles-daemon", "xdg-utils",
        "sox", "soundtouch", "rubberband", "espeak-ng", "zbar", "git",
        "flatpak", "pacman", "xorg-xrandr",
        # Read out of chronoa-matrix.json's pacman data on 2026-10-07, for the
        # matrix skills (distrobox, virsh, fc-list/fc-match, boltctl).
        "distrobox", "libvirt", "fontconfig", "bolt",
        "libnfc",
        # Read out of the matrix's `commands[].package` on the GNOME image,
        # which is pacman's own file database rather than a guess: fprintd ships
        # /usr/bin/fprintd-{list,verify,enroll,delete}.
        "fprintd",
        # Read out of the matrix's `commands[].package` on 2026-10-10, for
        # `inspect_binary`: the binary called `readelf` ships in **binutils**;
        # Arch's `elfutils` (Core, 0.196-1) provides the `eu-*` spellings
        # instead. Naming `elfutils` here would send someone to install a
        # package that does not contain the binary they need - the same defect
        # as wpctl/pipewire above.
        "binutils",
        # Read out of the matrix's `commands[].package` on 2026-10-10, for the
        # shani-tools sweep. **`7z` is `7zip`, not `p7zip`** - the matrix caught
        # my guess, which is the same wpctl->pipewire class a fifth time.
        "iperf3", "rclone", "restic", "subversion", "mercurial", "7zip",
        "lrzip", "lzop", "unrar", "cabextract", "unarchiver", "arj",
        "openbsd-netcat", "nethogs", "bandwhich", "iftop", "socat",
        # `lsof` really is its own package - confirmed against the Arch
        # package API (`extra`, 4.99.7), not assumed from the binary name.
        "lsof",
        "sysstat", "lm_sensors",
        # Driver development (2026-10-10). Each checked against the Arch
        # package API on 2026-10-10, not read off a mirror: `i2c-tools extra
        # 4.4-4`, `v4l-utils extra 1.32.0-2`, `fuse3 extra 3.18.3-1`,
        # `base-devel core 1-2`, `dkms extra 3.4.4-1`.
        #
        # `base-devel` is a group rather than a package holding the compiler,
        # and that is the point of the hint: `pacman -S base-devel` is the
        # command that installs a C compiler, `make` and `patch` together, so
        # naming it is actionable where naming `gcc` alone would only install
        # half of what a build needs.
        "i2c-tools",
        "v4l-utils",
        "fuse3",
        "base-devel",
        "dkms",
        # Verified 2026-10-08 against the Arch package API: `rtl-sdr 2.0.3-1`,
        # `extra`, and its file list has `usr/bin/rtl_fm`.
        "rtl-sdr",
        # Verified 2026-10-08: `bluez-obex` in `extra` ships usr/lib/bluetooth/obexd and
        # obex.service (Arch splits it out of bluez and bluez-utils).
        "bluez-obex",
        # Verified 2026-10-09 against the matrix a slot run wrote from
        # pacman's own file database: `kconfig` ships exactly `kreadconfig6` and
        # `kwriteconfig6`, and nothing else in the image provides them. The
        # second pair is read the same way: `plasma-workspace` ships
        # `plasma-apply-colorscheme`, and `plasma-apply-lookandfeel` with it
        # (the Plasma 6 rename of `plasma-lookandfeeltool`, which `set_theme`
        # already falls back to). Added with `set_theme`'s helpers, which ran
        # them unchecked until `tools/cli_matrix.py --check` said so on a real
        # GNOME image.
        "kconfig",
        "plasma-workspace",
        # The NFC tool set. Verified against the Arch package API rather than
        # assumed: `libnfc 1.8.0-3`, repo `extra`, `arch=(x86_64)`,
        # depends=(libusb-compat pcsclite). Its ten `nfc-*` binaries all come
        # from that one package, because upstream's `BUILD_UTILS` defaults ON and
        # the Arch PKGBUILD never disables it.
    }
    for binary, hint in files._PACKAGE_HINTS.items():
        assert hint in real_packages, f"{binary} -> {hint!r} is not a known Arch package"


# --- the control: the audit must be able to fail ------------------------------


def test_control_the_trap_guard_rejects_a_reverted_table(monkeypatch):
    """Prove the table guard discriminates, by reverting each mapping.

    The prose checks above can only confirm the fixed wording is present; they
    cannot tell a correct package name from a merely-different one. This one
    can. Each mapping is put back to the value the audit found, and the guard's
    own predicate must then reject it - and `tool_missing()` must be shown
    handing the user the wrong name, because that is the actual harm.

    An earlier version of this control tried to reconstruct the old *prose* by
    string surgery and could not do it for the multi-line `udisks` claim, so it
    proved nothing. Reverting the table is both simpler and the check that
    matters.
    """
    correct = {"bluetoothctl": "bluez-utils", "udisksctl": "udisks2",
               "wpctl": "wireplumber"}
    reverted = {"bluetoothctl": "bluez", "udisksctl": "udisks", "wpctl": "pipewire"}

    for binary, wrong in reverted.items():
        monkeypatch.setitem(files._PACKAGE_HINTS, binary, wrong)
        assert files._PACKAGE_HINTS[binary] != correct[binary], (
            f"the guard must reject {binary} -> {wrong}"
        )
        assert "the package that provides it" not in files.tool_missing(binary, "do a thing")
        assert wrong in files.tool_missing(binary, "do a thing"), (
            "this is the harm: the user is told to install a package that does "
            "not contain the binary"
        )

    monkeypatch.undo()
    for binary, right in correct.items():
        assert files._PACKAGE_HINTS[binary] == right


def test_tool_missing_consults_the_routes_table_too():
    """Two tables, and the narrower one is the one that had been checked.

    `routes.install_hint()` is the machine-readable half of `routes.ROUTES`, the
    only authority in this tree with a package name per binary read out of a real
    image's file database. `files._PACKAGE_HINTS` is the wider hand-kept table —
    61 entries to `routes`' 15 — and `tool_missing` consulted only that one.

    The result was ten binaries answered with "the package that provides it"
    while `routes.ROUTES` had known the answer all along, and two of those ten
    are the non-obvious names this file exists to protect: `magick` is in
    `imagemagick`, `whisper-cli` is in `whisper-cpp`. Measured before the fix:
    both produced the vague sentence.

    Asserted per binary rather than as a count, so a table that loses an entry
    fails here instead of silently going back to being vague.
    """
    from shani_chronoa import routes

    checked = 0
    for binary in sorted(routes.ROUTES):
        hint = routes.install_hint(binary)
        if not hint:
            continue
        checked += 1
        out = files.tool_missing(binary, "do a thing")
        assert "the package that provides it" not in out, (
            f"{binary} is named as {hint!r} by routes but the sentence is vague:\n"
            f"  {out}")
        assert f"'{hint}'" in out, f"{binary} should name the {hint!r} package:\n  {out}"
    assert checked >= 8, f"only {checked} routes carry a package name; this proves little"


# --- the hint table against the matrix, its stated authority ----------------
#
# `files._PACKAGE_HINTS` is 85 hand-kept Arch package names, and until now nothing
# checked them against the thing the docstring above names as the authority:
# cli_matrix's command-to-package map, built from pacman's own file database on a
# real image. The tests above compared one hand-kept dict against another, so the
# chain never reached the matrix at all - the same shape as the NUT driver list,
# where 48 hand-kept names were verified against nothing real and 25 real drivers
# were refused.
#
# Measured when this was added: 76 of 85 agree, 0 disagree. The table is sound.
# What was missing was the check, and a table nobody re-measures is a table that
# rots - so this exists to keep it honest rather than to fix it.


def _matrix_packages():
    """{command: package} from the matrices a slot run wrote, or {} if absent.

    Both images are read, Plasma first, because the two sometimes disagree about
    which optional package is installed and either answer still establishes which
    package OWNS a binary. An entry with no package recorded is skipped rather
    than treated as a disagreement: the matrix did not say.

    Skipping when the matrices are absent is deliberate. They live in a sibling
    repo and only exist on a machine that has run the harness, so a test that
    required them would be permanently red on every other checkout - a failure
    that says nothing. A skip naming the reason is the honest answer, the same
    shape as test_sense_manifest's SKIP for hwmon and modelfit.
    """
    import json

    # Five levels up from .../usr/lib/shani-chronoa/shani_chronoa/files.py is
    # the workspace root that holds the sibling repos. The first version used
    # parents[3], which is .../usr - so the matrices were never found and all
    # three tests skipped themselves, which is the silent-pass shape this test
    # above exists to refuse.
    root = pathlib.Path(files.__file__).resolve().parents[5]
    packages = {}
    for relative in (
        "shani-install-media/test-env/mout-plasma/shanios-matrix.json",
        "shani-install-media/test-env/mout/matrix-gnome.json",
    ):
        path = root / relative
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        for entry in data.get("commands", []):
            command = entry.get("command")
            package = entry.get("package")
            if command and package and package not in ("", "_", None):
                packages.setdefault(command, package)
    return packages


def test_every_hint_the_matrix_can_check_agrees_with_it():
    """The authority is pacman's own answer, read off a real image.

    A hint naming a different package sends someone to `pacman -S <name>` and
    gets nothing - which is the whole failure _PACKAGE_HINTS exists to prevent,
    arriving by a different route.
    """
    packages = _matrix_packages()
    if not packages:
        pytest.skip("no command matrix in this checkout - this reads the "
                    "sibling shani-install-media matrices a slot run writes")
    wrong = []
    for binary, claimed in files._PACKAGE_HINTS.items():
        actual = packages.get(binary)
        if actual and actual != claimed:
            wrong.append(f"{binary}: hint says {claimed}, matrix says {actual}")
    assert not wrong, (
        "these package hints disagree with pacman's own command->package "
        "map, so tool_missing() would send someone to a package that does "
        "not provide the binary: " + "; ".join(wrong))


def test_the_matrix_covers_most_of_the_hint_table():
    """A guard against the check above passing because it cannot see anything.

    If the matrices stopped carrying the commands, the agreement test would pass
    vacuously - every hint skipped as not in the matrix. This asserts the overlap
    is real, so a truncated matrix is a failure rather than a green run.
    """
    packages = _matrix_packages()
    if not packages:
        pytest.skip("no command matrix in this checkout")
    checkable = [b for b in files._PACKAGE_HINTS if b in packages]
    assert len(checkable) >= 40, (
        "only %d of %d hints are checkable against the matrix - either the "
        "matrices stopped carrying commands or the table drifted away from them"
        % (len(checkable), len(files._PACKAGE_HINTS)))


def test_the_known_non_obvious_names_are_the_matrix_ones():
    """The traps this table was built to hold, pinned against the matrix.

    `rtl_fm` is the sharpest: the binary ships in `rtl-sdr`, not `rtl_fm`, and a
    hint that guessed the binary's own name would be wrong in exactly the
    direction the table exists to prevent.
    """
    packages = _matrix_packages()
    if not packages:
        pytest.skip("no command matrix in this checkout")
    for binary, expected in (("rtl_fm", "rtl-sdr"),
                             ("bluetoothctl", "bluez-utils"),
                             ("udisksctl", "udisks2"),
                             ("wpctl", "wireplumber")):
        if binary in packages:
            assert files._PACKAGE_HINTS[binary] == expected, (
                f"{binary} must map to {expected}, the package that owns it")

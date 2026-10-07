"""The twenty skills built from the cli_matrix shortlist (2026-10-07).

Each skill is exercised through `tools.execute_tool_outcome` - the real
dispatch, which runs the handler in a sandboxed child process - at least once,
because a test that calls `_run()` directly is not a test that the skill is
reachable (AGENTS.md: three skills once passed their unit tests while failing on
every real call). Parsers are tested directly as well, on real-shaped output.

Fakes are stateful shell scripts on PATH: a "set" writes the state a later
"read" reports, so the read-back and the post-condition verdict are measured
rather than assumed. Every fake that matters also has a negative control - a
fake that accepts the change and then reads back the old value - so a skill
that claimed "verified" without reading back would fail here.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import planmode, tools  # noqa: E402
from shani_chronoa.verification import Verdict  # noqa: E402


# ── harness ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, gsettings_env):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "HOME"):
        target = tmp_path / var.lower()
        target.mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(target))
    planmode.set_enabled(False)
    yield
    planmode.set_enabled(False)


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:{os.environ.get('PATH', '')}")
    return d


def fake(bindir: Path, name: str, body: str) -> Path:
    script = bindir / name
    script.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script.chmod(0o755)
    return script


def hide(bindir: Path, *names: str):
    """Shadow a real binary with one that does not exist, for 'not installed'."""
    for name in names:
        fake(bindir, name, "exit 127\n").unlink()


def grant(key: str, value: bool = True):
    """Write a consent key through an *external* keyfile backend, as Settings would."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    from shani_chronoa import config as config_mod
    keyfile = str(Path(os.environ["XDG_CONFIG_HOME"]) / "glib-2.0" / "settings" / "keyfile")
    backend = Gio.keyfile_settings_backend_new(keyfile, config_mod.SCHEMA_PATH, config_mod.SCHEMA_ID)
    settings = Gio.Settings.new_with_backend(config_mod.SCHEMA_ID, backend)
    settings.set_boolean(key, value)
    Gio.Settings.sync()


def call(name: str, arguments: dict):
    outcome = tools.execute_tool_outcome(name, arguments)
    text = getattr(outcome, "text", None) or getattr(outcome, "result", None) or str(outcome)
    assert "Traceback" not in text, text
    return outcome, text


def test_every_new_skill_is_registered():
    names = {t["function"]["name"] for t in tools.TOOLS}
    new = {"snapshot_status", "security_status", "list_containers", "list_vms", "boot_report",
           "disk_health", "temperatures", "usb_devices", "driver_info", "list_fonts",
           "photo_metadata", "crash_report", "login_history", "audio_output", "default_apps",
           "pdf_pages", "print_queue", "set_hostname", "set_locale", "speed_test"}
    assert new <= names, f"not registered: {sorted(new - names)}"


# ── 1. snapshot_status ───────────────────────────────────────────────────────

_STATUS = {"version": "20260925", "profile": "gnome", "channel": "stable", "booted_slot": "blue",
           "current_slot": "green", "previous_slot": "blue", "boot_failure": "", "boot_hard_failure": "",
           "auto_rollback_done": False, "candidate_boot": True, "reboot_needed": "20261001",
           "remote": {"stable": "", "latest": ""}, "update_available": None}


def test_snapshot_status_reads_the_deploy_contract(bindir):
    fake(bindir, "shani-deploy", f"echo 'some log line'\necho '{json.dumps(_STATUS)}'\n")
    hide(bindir, "btrfs")
    outcome, text = call("snapshot_status", {})
    assert outcome.ran
    assert "Running from slot @blue; the next boot uses @green" in text
    assert "update is installed in @green" in text
    assert "Rolling back would return to @blue" in text
    assert "20261001 is waiting for a reboot" in text
    assert "UNKNOWN" in text  # btrfs hidden: the snapshot half says so, it does not vanish


def test_snapshot_status_does_not_call_a_fallback_an_update():
    from shani_chronoa.skills import snapshot_status as s
    lines = "\n".join(s.describe_deploy({**_STATUS, "candidate_boot": False, "reboot_needed": "",
                                         "boot_failure": "green"}))
    assert "update is installed" not in lines
    assert "fell back after a failed boot" in lines
    assert "boot failure is recorded for @green" in lines


def test_snapshot_status_without_shani_deploy_is_unknown_not_no(bindir):
    hide(bindir, "shani-deploy", "btrfs")
    _, text = call("snapshot_status", {})
    assert "shani-deploy is not installed" in text and "UNKNOWN" in text
    assert "cannot roll back" not in text.lower()


# ── 2, 7, 12. sense wrappers ─────────────────────────────────────────────────

@pytest.mark.parametrize("name, marker", [
    ("security_status", "Firewall:"),
    ("temperatures", ""),
    ("crash_report", ""),
])
def test_sense_wrappers_run_through_dispatch(name, marker):
    outcome, text = call(name, {})
    assert outcome.ran and text.strip()
    assert marker in text


def test_a_sense_backed_skill_follows_its_senses_switch():
    """hwmon defaults off: the skill refuses, naming the switch, then reads once granted."""
    _, text = call("temperatures", {})
    assert "Not shown" in text and "hwmon-sense-enabled" in text
    grant("hwmon-sense-enabled")
    _, text = call("temperatures", {})
    assert "Not shown" not in text


def test_only_the_sense_half_of_a_mixed_skill_is_withheld(bindir):
    """Turning the snapshots sense off hides the snapshot list, not the deploy state."""
    fake(bindir, "shani-deploy", f"echo '{json.dumps(_STATUS)}'\n")
    grant("snapshots-sense-enabled", False)
    _, text = call("snapshot_status", {})
    assert "Running from slot @blue" in text
    assert "Not shown" in text and "snapshots-sense-enabled" in text


# ── 3. list_containers ───────────────────────────────────────────────────────

def test_list_containers_names_distroboxes(bindir):
    fake(bindir, "distrobox", """\
        echo 'ID           | NAME                 | STATUS             | IMAGE'
        echo 'a1b2c3d4e5f6 | arch-dev             | Up 2 hours         | quay.io/toolbx/arch-toolbox:latest'
        echo 'f6e5d4c3b2a1 | ubuntu               | Exited (0) 3 days  | docker.io/library/ubuntu:24.04'
    """)
    _, text = call("list_containers", {})
    assert "2 distrobox(es)" in text
    assert "arch-dev" in text and "Exited (0) 3 days" in text
    assert "Containers:" in text  # the podman half is still reported


def test_list_containers_without_distrobox_is_unknown(bindir):
    hide(bindir, "distrobox")
    _, text = call("list_containers", {})
    assert "UNKNOWN" in text and "'distrobox' package" in text


# ── 4. list_vms ──────────────────────────────────────────────────────────────

def test_list_vms_reports_each_connection_separately(bindir):
    fake(bindir, "virsh", """\
        case "$2" in
          qemu:///session) printf ' Id   Name      State\\n-------------------------\\n 1    win11     running\\n -    fedora    shut off\\n';;
          *) echo "error: failed to connect to the hypervisor" >&2; exit 1;;
        esac
    """)
    _, text = call("list_vms", {})
    assert "2 VM(s), 1 running" in text
    assert "fedora" in text and "shut off" in text
    assert "qemu:///system): could not be read" in text and "not empty" in text


def test_list_vms_without_libvirt(bindir):
    hide(bindir, "virsh")
    _, text = call("list_vms", {})
    assert "'libvirt' package" in text and "not the same as none" in text


# ── 5. boot_report ───────────────────────────────────────────────────────────

def test_boot_report_quotes_systemd_analyze(bindir):
    fake(bindir, "systemd-analyze", """\
        case "$1" in
          time) echo 'Startup finished in 4.1s (firmware) + 2.0s (loader) + 1.5s (kernel) + 9.8s (userspace) = 17.4s';;
          blame) printf '6.002s NetworkManager-wait-online.service\\n1.200s plymouth.service\\n0.500s udisks2.service\\n';;
        esac
    """)
    _, text = call("boot_report", {"top": 2})
    assert "= 17.4s" in text
    assert "NetworkManager-wait-online.service" in text and "udisks2" not in text
    assert "not necessarily" in text  # blame is not presented as the cause


def test_boot_report_while_booting_is_unknown(bindir):
    fake(bindir, "systemd-analyze", "echo 'Bootup is not yet finished.' >&2; exit 1\n")
    _, text = call("boot_report", {})
    assert "Boot timing is UNKNOWN" in text and "not yet finished" in text


# ── 6. disk_health ───────────────────────────────────────────────────────────

_SHANIOS_TREE = [{"name": "nvme0n1", "type": "disk", "fstype": None, "mountpoints": [None], "children": [
    {"name": "nvme0n1p1", "type": "part", "fstype": "vfat", "mountpoints": ["/boot/efi"]},
    {"name": "nvme0n1p2", "type": "part", "fstype": "crypto_LUKS", "mountpoints": [None], "children": [
        {"name": "shani_root", "type": "crypt", "fstype": "btrfs",
         "mountpoints": ["/", "/home", "/data", "/var/cache"]}]}]}]


def test_disk_health_walks_ancestors_for_luks():
    from shani_chronoa.skills import disk_health as d
    found = d.encryption_by_mount(_SHANIOS_TREE)
    assert found["/"] == "nvme0n1p2" and found["/home"] == "nvme0n1p2"
    assert found["/boot/efi"] is None
    lines = "\n".join(d.describe_encryption(_SHANIOS_TREE))
    assert "/home" in lines and "encrypted (LUKS on nvme0n1p2)" in lines
    assert "/boot/efi" in lines and "NOT encrypted" in lines


def test_disk_health_runs_and_an_unreadable_tree_is_unknown(bindir):
    outcome, text = call("disk_health", {})
    assert outcome.ran and "Encryption at rest:" in text and "Drive health:" in text
    fake(bindir, "lsblk", "exit 1\n")
    _, text = call("disk_health", {})
    assert "UNKNOWN" in text and "not the same as the disk being unencrypted" in text


# ── 8. usb_devices ───────────────────────────────────────────────────────────

_BOLT = """\
 ● Dell Thunderbolt Dock
   ├─ type:          peripheral
   ├─ name:          WD19TB
   ├─ status:        connected
   └─ authflags:     none

 ○ OWC Envoy Express
   ├─ type:          peripheral
   ├─ status:        disconnected
"""


def test_usb_devices_flags_an_unauthorised_dock(bindir):
    from shani_chronoa.skills import usb_devices as u
    devices = u.parse_boltctl(_BOLT)
    assert [d["title"] for d in devices] == ["Dell Thunderbolt Dock", "OWC Envoy Express"]
    assert devices[0]["status"] == "connected"
    fake(bindir, "boltctl", "cat <<'EOF'\n" + _BOLT + "EOF\n")
    _, text = call("usb_devices", {})
    assert "NOT authorised" in text and "USB:" in text


# ── 9. driver_info ───────────────────────────────────────────────────────────

def test_driver_info_tells_loaded_from_built_in_from_absent(tmp_path):
    from shani_chronoa.skills import driver_info as d
    proc = tmp_path / "modules"
    proc.write_text("iwlwifi 512000 1 iwlmvm, Live 0x0\nnvidia 61000000 2 nvidia_modeset,nvidia_drm, Live 0x0\n")
    sysmod = tmp_path / "sysmodule"
    (sysmod / "nvidia").mkdir(parents=True)
    (sysmod / "nvidia" / "version").write_text("560.35.03\n")
    (sysmod / "ext4").mkdir()
    mods = d.loaded_modules(proc)
    assert "is loaded" in d.check_module("nvidia", mods, sysmod) and "560.35.03" in d.check_module("nvidia", mods, sysmod)
    assert "built into the kernel" in d.check_module("ext4", mods, sysmod)
    assert "not loaded" in d.check_module("nouveau", mods, sysmod)
    assert "UNKNOWN" in d.check_module("nouveau", None, sysmod)


def test_driver_info_reads_the_device_driver_link(tmp_path):
    from shani_chronoa.skills import driver_info as d
    drv = tmp_path / "drivers" / "iwlwifi"
    drv.mkdir(parents=True)
    dev = tmp_path / "class" / "net" / "wlan0" / "device"
    dev.mkdir(parents=True)
    (dev / "driver").symlink_to(drv)
    (tmp_path / "class" / "net" / "lo").mkdir()
    unbound = tmp_path / "class" / "net" / "eth0" / "device"
    unbound.mkdir(parents=True)
    rows = d.device_drivers(tmp_path / "class")
    assert ("Network", "wlan0", "iwlwifi") in rows
    assert ("Network", "eth0", None) in rows
    assert not any(r[1] == "lo" for r in rows)


def test_driver_info_runs_on_this_machine():
    outcome, text = call("driver_info", {})
    assert outcome.ran and "kernel modules are loaded" in text


# ── 10. list_fonts ───────────────────────────────────────────────────────────

def test_list_fonts_reports_a_substitute_as_one(bindir):
    fake(bindir, "fc-list", "printf 'DejaVu Sans,DejaVu Sans Condensed\\nNoto Sans Mono\\nDejaVu Sans\\n'\n")
    fake(bindir, "fc-match", "echo 'DejaVu Sans|Book|/usr/share/fonts/DejaVuSans.ttf'\n")
    _, text = call("list_fonts", {"query": "Comic Sans"})
    assert "No installed font family contains 'Comic Sans'" in text
    assert "a substitute" in text
    _, text = call("list_fonts", {})
    assert "2 font families installed" in text and "monospace" in text


# ── 11. photo_metadata ───────────────────────────────────────────────────────

def _ifd(entries, base):
    head = 2 + 12 * len(entries) + 4
    body, data = struct.pack("<H", len(entries)), b""
    for tag, typ, count, value in entries:
        if len(value) <= 4:
            body += struct.pack("<HHI", tag, typ, count) + value.ljust(4, b"\0")
        else:
            body += struct.pack("<HHII", tag, typ, count, base + head + len(data))
            data += value + (b"\0" if len(value) % 2 else b"")
    return body + struct.pack("<I", 0) + data


def _ascii(s):
    raw = s.encode() + b"\0"
    return (2, len(raw), raw)


def _rationals(*pairs):
    return (5, len(pairs), b"".join(struct.pack("<II", n, d) for n, d in pairs))


def exif_jpeg(with_gps=True) -> bytes:
    exif = [(0x9003, *_ascii("2026:08:15 18:42:07")), (0x829A, *_rationals((1, 250))),
            (0x829D, *_rationals((18, 10)))]
    gps = [(1, *_ascii("N")), (2, *_rationals((12, 1), (30, 0 + 1), (0, 1))),
           (3, *_ascii("W")), (4, *_rationals((77, 1), (15, 1), (0, 1)))]

    def ifd0(exif_at, gps_at):
        rows = [(0x010F, *_ascii("Fairphone")), (0x0110, *_ascii("FP5")),
                (0x8769, 4, 1, struct.pack("<I", exif_at))]
        if with_gps:
            rows.append((0x8825, 4, 1, struct.pack("<I", gps_at)))
        return rows

    first = len(_ifd(ifd0(0, 0), 8))
    exif_block = _ifd(exif, 8 + first)
    gps_block = _ifd(gps, 8 + first + len(exif_block))
    tiff = b"II*\0" + struct.pack("<I", 8) + _ifd(ifd0(8 + first, 8 + first + len(exif_block)), 8) \
        + exif_block + gps_block
    app1 = b"Exif\0\0" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


def test_photo_metadata_reads_camera_date_and_location(tmp_path):
    photo = tmp_path / "beach.jpg"
    photo.write_bytes(exif_jpeg())
    outcome, text = call("photo_metadata", {"path": str(photo)})
    assert outcome.ran
    assert "Camera make: Fairphone" in text and "Camera model: FP5" in text
    assert "Taken: 2026:08:15 18:42:07" in text
    assert "1/250 s" in text and "f/1.8" in text
    assert "12.500000, -77.250000" in text and "anyone you share this file with" in text


def test_photo_metadata_without_gps_says_none(tmp_path):
    photo = tmp_path / "x.jpg"
    photo.write_bytes(exif_jpeg(with_gps=False))
    _, text = call("photo_metadata", {"path": str(photo)})
    assert "Location: none recorded." in text


def test_photo_metadata_keeps_unsupported_apart_from_empty(tmp_path):
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 40)
    _, text = call("photo_metadata", {"path": str(png)})
    assert "not a JPEG or TIFF" in text and "not the same as it having none" in text
    bare = tmp_path / "bare.jpg"
    bare.write_bytes(b"\xff\xd8\xff\xdb\x00\x04\x00\x00\xff\xd9")
    _, text = call("photo_metadata", {"path": str(bare)})
    assert "carries no EXIF metadata" in text


# ── 13. login_history ────────────────────────────────────────────────────────

def test_login_history_refuses_without_the_sessions_switch():
    outcome, text = call("login_history", {})
    assert "Refusing" in text and "sessions-sense-enabled" in text


def _utmp(typ, line, user, host, ts):
    from shani_chronoa.skills import login_history as lh
    return lh._RECORD.pack(typ, 100, line.encode(), b"", user.encode(), host.encode(),
                           0, 0, 0, ts, 0, b"", b"")


def test_login_history_pairs_logins_with_logouts():
    from shani_chronoa.skills import login_history as lh
    data = (_utmp(2, "~", "reboot", "", 1000) + _utmp(7, "tty2", "asha", "", 1100)
            + _utmp(7, "pts/0", "ravi", "10.0.0.5", 1200) + _utmp(8, "pts/0", "", "", 1500)
            + _utmp(2, "~", "reboot", "", 2000))
    entries = lh.parse_wtmp(data)
    assert entries[0]["user"] == "reboot"
    ravi = next(e for e in entries if e["user"] == "ravi")
    asha = next(e for e in entries if e["user"] == "asha")
    assert ravi["end"] == 1500 and ravi["host"] == "10.0.0.5"
    assert asha["end"] == "crash"  # never logged out before the next boot
    text = "\n".join(lh.describe(entries))
    assert "for 0:05:00 from 10.0.0.5" in text and "ended by a reboot or crash" in text


def test_login_history_granted_reads_wtmp_when_last_is_absent(bindir, tmp_path, monkeypatch):
    from shani_chronoa.skills import login_history as lh
    grant("sessions-sense-enabled")
    hide(bindir, "last")
    wtmp = tmp_path / "wtmp"
    wtmp.write_bytes(_utmp(7, "tty2", "asha", "", 1100))
    monkeypatch.setattr(lh, "_WTMP", wtmp)
    text = lh._run({})
    assert "asha" in text and "still logged in" in text
    monkeypatch.setattr(lh, "_WTMP", tmp_path / "missing")
    assert "UNKNOWN" in lh._run({})


# ── 14. audio_output ─────────────────────────────────────────────────────────

_WPCTL = r"""
state="$(dirname "$0")/default"; [ -f "$state" ] || echo 60 > "$state"
d=$(cat "$state")
mark() { [ "$1" = "$d" ] && printf '*' || printf ' '; }
case "$1" in
  status)
    echo "PipeWire 'pipewire-0' [1.2.7, asha@laptop, cookie:1]"
    echo " └─ Clients:"
    echo ""
    echo "Audio"
    echo " ├─ Devices:"
    echo " │      51. Tiger Lake Audio Controller [alsa]"
    echo " │  "
    echo " ├─ Sinks:"
    echo " │  $(mark 57)   57. HDMI / DisplayPort 1 Output [vol: 1.00]"
    echo " │  $(mark 58)   58. HDMI / DisplayPort 2 Output [vol: 1.00]"
    echo " │  $(mark 60)   60. Speaker [vol: 0.40]"
    echo " │  $(mark 70)   70. Jabra Evolve2 Headset [vol: 0.80]"
    echo " │  "
    echo " ├─ Sources:"
    echo " │  $(mark 62)   62. Digital Microphone [vol: 1.00]"
    echo " └─ Streams:"
    echo ""
    echo "Video"
    echo " ├─ Sinks:"
    echo " │      99. Not audio [vol: 1.00]"
    ;;
  set-default) echo "$2" >> "$(dirname "$0")/calls"; [ -f "$(dirname "$0")/sticky" ] || echo "$2" > "$state";;
esac
"""


def test_audio_output_parses_only_the_audio_section():
    from shani_chronoa.skills import audio_output as a
    import subprocess
    # Render the fake once to get real-shaped text.
    text = subprocess.run(["sh", "-c", _WPCTL.replace("$(dirname \"$0\")", "/tmp/opencode"), "x", "status"],
                          capture_output=True, text=True).stdout
    devs = a.parse_status(text)
    assert [d["id"] for d in devs["Sinks"]] == [57, 58, 60, 70]
    assert devs["Sources"][0]["id"] == 62
    assert any(d["default"] for d in devs["Sinks"])


def test_audio_output_switches_and_verifies(bindir):
    fake(bindir, "wpctl", _WPCTL)
    outcome, text = call("audio_output", {"action": "set", "device": "headset"})
    assert "now Jabra Evolve2 Headset (verified" in text
    assert outcome.verdict is Verdict.VERIFIED


def test_audio_output_refuses_an_ambiguous_name(bindir):
    fake(bindir, "wpctl", _WPCTL)
    _, text = call("audio_output", {"action": "set", "device": "HDMI"})
    assert "matches 2 outputs" in text and "nothing was" in text
    assert not (bindir / "calls").exists(), "an ambiguous name must not reach set-default"


def test_audio_output_control_a_switch_that_does_not_stick_is_not_verified(bindir):
    fake(bindir, "wpctl", _WPCTL)
    (bindir / "sticky").write_text("")
    outcome, text = call("audio_output", {"action": "set", "device": "70"})
    assert "not verified" in text
    assert outcome.verdict is Verdict.FAILED


# ── 15. default_apps ─────────────────────────────────────────────────────────

_XDG_MIME = r"""
db="$(dirname "$0")/mime.db"; touch "$db"
case "$1 $2" in
  "query default") grep "^$3=" "$db" | tail -1 | cut -d= -f2;;
  "query filetype") echo "application/pdf";;
  default*) echo "$3=$2" >> "$db";;
esac
"""


def _apps():
    apps = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / "org.gnome.Evince.desktop").write_text("[Desktop Entry]\nName=Document Viewer\n")
    (apps / "org.videolan.VLC.desktop").write_text("[Desktop Entry]\nName=VLC media player\n")
    (apps / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\n")


def test_default_apps_status_needs_no_consent(bindir):
    fake(bindir, "xdg-mime", _XDG_MIME)
    (bindir / "mime.db").write_text("application/pdf=org.gnome.Evince.desktop\n")
    _apps()
    _, text = call("default_apps", {"target": ".pdf"})
    assert "Files of type application/pdf open with: Document Viewer (org.gnome.Evince.desktop)" in text


def test_default_apps_set_is_refused_without_consent(bindir):
    fake(bindir, "xdg-mime", _XDG_MIME)
    _apps()
    _, text = call("default_apps", {"action": "set", "target": "pdf", "app": "Document Viewer"})
    assert "Refusing" in text and "default-apps-enabled" in text


def test_default_apps_set_resolves_by_name_and_verifies(bindir):
    grant("default-apps-enabled")
    fake(bindir, "xdg-mime", _XDG_MIME)
    _apps()
    outcome, text = call("default_apps", {"action": "set", "target": "mp4", "app": "VLC"})
    assert "Files of type video/mp4 now open with VLC media player (verified" in text
    assert outcome.verdict is Verdict.VERIFIED


def test_default_apps_refuses_an_app_that_does_not_exist(bindir):
    grant("default-apps-enabled")
    fake(bindir, "xdg-mime", _XDG_MIME)
    _apps()
    _, text = call("default_apps", {"action": "set", "target": "pdf", "app": "Acrobat"})
    assert "No installed application matches" in text
    assert "=" not in (bindir / "mime.db").read_text(), "an unknown app must not be recorded"


# ── 16. pdf_pages ────────────────────────────────────────────────────────────

def make_pdf(path: Path, pages: int):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + i} 0 R' for i in range(pages))}] /Count {pages} >>"]
    objs += ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * pages
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


needs_poppler = pytest.mark.skipif(
    not all(__import__("shutil").which(b) for b in ("pdfinfo", "pdfunite", "pdfseparate")),
    reason="poppler is not installed here")


@needs_poppler
def test_pdf_pages_merge_extract_split(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(a, 1)
    make_pdf(b, 3)
    outcome, text = call("pdf_pages", {"action": "merge", "paths": [str(a), str(b)],
                                       "output": str(tmp_path / "all.pdf")})
    assert "(4 pages, verified)" in text and outcome.verdict is Verdict.VERIFIED
    outcome, text = call("pdf_pages", {"action": "extract", "path": str(tmp_path / "all.pdf"),
                                       "pages": "2-3", "output": str(tmp_path / "part")})
    assert "(2 pages, verified)" in text and (tmp_path / "part.pdf").exists()
    assert outcome.verdict is Verdict.VERIFIED
    _, text = call("pdf_pages", {"action": "split", "path": str(b), "output": str(tmp_path / "pages")})
    assert "3 one-page PDF(s)" in text and len(list((tmp_path / "pages").glob("*.pdf"))) == 3
    _, text = call("pdf_pages", {"action": "info", "path": str(b)})
    assert "has 3 page(s)" in text


@needs_poppler
def test_pdf_pages_never_overwrites_and_checks_ranges(tmp_path):
    a = tmp_path / "a.pdf"
    make_pdf(a, 2)
    # The control has to be a file the post-condition would otherwise accept: a
    # real PDF with exactly the page count the merge implies (2 + 2), dated in
    # the past. A non-PDF here fails the page count for an unrelated reason and
    # lets a post-condition with no freshness check pass unnoticed.
    existing = tmp_path / "keep.pdf"
    make_pdf(existing, 4)
    before = existing.read_bytes()
    os.utime(existing, (1_000_000_000, 1_000_000_000))
    outcome, text = call("pdf_pages", {"action": "merge", "paths": [str(a), str(a)], "output": str(existing)})
    assert "never overwrites" in text and existing.read_bytes() == before
    assert outcome.verdict is not Verdict.VERIFIED  # an old file of the same name is not the result
    _, text = call("pdf_pages", {"action": "extract", "path": str(a), "pages": "2-5",
                                 "output": str(tmp_path / "x.pdf")})
    assert "outside pages 1-2" in text and not (tmp_path / "x.pdf").exists()


def test_pdf_pages_parse_pages():
    from shani_chronoa.skills.pdf_pages import parse_pages
    assert parse_pages("1-3, 7", 9) == [1, 2, 3, 7]
    for bad in ("0", "3-1", "x", "", "10"):
        with pytest.raises(ValueError):
            parse_pages(bad, 9)


# ── 17. print_queue ──────────────────────────────────────────────────────────

_LPSTAT = r"""
q="$(dirname "$0")/queue"; [ -f "$q" ] || printf 'Office_Laser-41 asha 20480 Tue 07 Oct 2026 09:10:00\nOffice_Laser-42 asha 1024 Tue 07 Oct 2026 09:12:00\n' > "$q"
case "$1" in
  -o) cat "$q";;
  -d) echo "system default destination: Office_Laser";;
  -p) echo "printer Office_Laser now printing Office_Laser-41.  enabled since Tue";;
esac
"""
_CANCEL = r"""q="$(dirname "$0")/queue"; grep -v "^$1 " "$q" > "$q.new"; mv "$q.new" "$q"
"""


def test_print_queue_lists_and_cancel_needs_consent(bindir):
    fake(bindir, "lpstat", _LPSTAT)
    fake(bindir, "cancel", _CANCEL)
    _, text = call("print_queue", {})
    assert "2 job(s) waiting" in text and "Office_Laser-42" in text and "system default" in text
    _, text = call("print_queue", {"action": "cancel", "job": "Office_Laser-42"})
    assert "Refusing" in text and "print-control-enabled" in text
    assert "Office_Laser-42" in (bindir / "queue").read_text()


def test_print_queue_cancels_and_verifies(bindir):
    grant("print-control-enabled")
    fake(bindir, "lpstat", _LPSTAT)
    fake(bindir, "cancel", _CANCEL)
    outcome, text = call("print_queue", {"action": "cancel", "job": "42"})
    assert "Cancelled Office_Laser-42 (verified" in text and outcome.verdict is Verdict.VERIFIED
    _, text = call("print_queue", {"action": "cancel", "job": "Office_Laser-99"})
    assert "No waiting job" in text


def test_print_queue_control_a_cancel_that_does_not_take_is_not_verified(bindir):
    grant("print-control-enabled")
    fake(bindir, "lpstat", _LPSTAT)
    fake(bindir, "cancel", "exit 0\n")  # accepts, removes nothing
    outcome, text = call("print_queue", {"action": "cancel", "job": "Office_Laser-41"})
    assert "not verified" in text and outcome.verdict is Verdict.FAILED


# ── 18. set_hostname ─────────────────────────────────────────────────────────

_HOSTNAMECTL = r"""
s="$(dirname "$0")/hostname"; [ -f "$s" ] || echo shanios > "$s"
case "$1" in
  hostname) cat "$s";;
  set-hostname) [ -f "$(dirname "$0")/sticky" ] || echo "$2" > "$s";;
esac
"""


def test_set_hostname_validates_before_anything_runs():
    from shani_chronoa.skills.set_hostname import validate
    assert validate("kitchen-laptop") is None
    for bad in ("My Laptop!", "-x", "x-", "a.b", "a_b", "x" * 64, ""):
        assert validate(bad), bad
    assert "lower-case" in validate("Kitchen")


def test_set_hostname_an_invalid_name_never_reaches_hostnamectl(bindir):
    """validate() being correct is not the same as _run() calling it."""
    grant("hostname-control-enabled")
    fake(bindir, "hostnamectl", _HOSTNAMECTL)
    _, text = call("set_hostname", {"action": "set", "name": "My Laptop!"})
    assert "Refusing to use 'My Laptop!'" in text
    assert (bindir / "hostname").read_text().strip() == "shanios"


def test_set_hostname_consent_rename_and_control(bindir):
    fake(bindir, "hostnamectl", _HOSTNAMECTL)
    _, text = call("set_hostname", {})
    assert "named shanios" in text
    _, text = call("set_hostname", {"action": "set", "name": "kitchen-laptop"})
    assert "Refusing" in text and "hostname-control-enabled" in text
    grant("hostname-control-enabled")
    outcome, text = call("set_hostname", {"action": "set", "name": "kitchen-laptop"})
    assert "now named kitchen-laptop (verified" in text and outcome.verdict is Verdict.VERIFIED
    (bindir / "sticky").write_text("")
    outcome, text = call("set_hostname", {"action": "set", "name": "study-pc"})
    assert "not verified" in text and outcome.verdict is Verdict.FAILED


# ── 19. set_locale ───────────────────────────────────────────────────────────

_LOCALECTL = r"""
s="$(dirname "$0")/locale"; [ -f "$s" ] || printf 'LANG=en_US.UTF-8\nLC_TIME=en_GB.UTF-8\n' > "$s"
case "$1" in
  status) first=1; while read -r l; do
            if [ $first = 1 ]; then echo "   System Locale: $l"; first=0; else echo "                  $l"; fi
          done < "$s"; echo "       VC Keymap: us";;
  list-locales) printf 'C.UTF-8\nde_DE.UTF-8\nen_GB.UTF-8\nen_US.UTF-8\n';;
  set-locale) shift; : > "$s"; for kv in "$@"; do echo "$kv" >> "$s"; done;;
esac
"""


def test_set_locale_parses_multi_line_status():
    from shani_chronoa.skills.set_locale import parse_status
    text = "   System Locale: LANG=en_US.UTF-8\n                  LC_TIME=en_GB.UTF-8\n       VC Keymap: us\n"
    assert parse_status(text) == {"LANG": "en_US.UTF-8", "LC_TIME": "en_GB.UTF-8"}


def test_set_locale_keeps_the_other_settings(bindir):
    """localectl set-locale replaces everything; changing LANG must not drop LC_TIME."""
    grant("locale-control-enabled")
    fake(bindir, "localectl", _LOCALECTL)
    outcome, text = call("set_locale", {"action": "set", "locale": "de_DE.UTF-8"})
    assert "now de_DE.UTF-8 (verified" in text and "LC_TIME" in text
    assert outcome.verdict is Verdict.VERIFIED
    stored = (bindir / "locale").read_text()
    assert "LANG=de_DE.UTF-8" in stored and "LC_TIME=en_GB.UTF-8" in stored


def test_set_locale_refuses_ungenerated_and_ungranted(bindir):
    fake(bindir, "localectl", _LOCALECTL)
    _, text = call("set_locale", {"action": "set", "locale": "de_DE.UTF-8"})
    assert "Refusing" in text and "locale-control-enabled" in text
    grant("locale-control-enabled")
    _, text = call("set_locale", {"action": "set", "locale": "fr_FR.UTF-8"})
    assert "not generated on this machine" in text


# ── 20. speed_test ───────────────────────────────────────────────────────────

def _mock_transport(hits):
    import httpx

    def handler(request):
        hits.append((request.method, str(request.url)))
        if request.url.path == "/__down":
            n = int(request.url.params.get("bytes", "0"))
            return httpx.Response(200, content=b"\0" * n)
        return httpx.Response(200, json={})
    return httpx.MockTransport(handler)


def test_speed_test_is_refused_until_granted_and_never_touches_the_wire(monkeypatch):
    from shani_chronoa.skills import speed_test as st
    hits = []
    monkeypatch.setattr(st, "_TRANSPORT", _mock_transport(hits))
    text = st._run({})
    # Key first, so the refusal names the switch a person can turn on - even
    # though privacy mode (default on) would refuse as well.
    assert "Refusing" in text and "speed-test-enabled" in text and hits == []
    _, text = call("speed_test", {})
    assert "speed-test-enabled" in text


def test_speed_test_privacy_mode_wins_over_the_grant(monkeypatch):
    from shani_chronoa.skills import speed_test as st
    grant("speed-test-enabled")
    hits = []
    monkeypatch.setattr(st, "_TRANSPORT", _mock_transport(hits))
    monkeypatch.setattr(st.egress, "privacy_mode_enabled", lambda: True)
    text = st._run({})
    assert "privacy mode is on" in text and hits == []


def test_speed_test_measures_and_logs(monkeypatch):
    from shani_chronoa.skills import speed_test as st
    grant("speed-test-enabled")
    hits, logged = [], []
    monkeypatch.setattr(st, "_TRANSPORT", _mock_transport(hits))
    monkeypatch.setattr(st.egress, "privacy_mode_enabled", lambda: False)
    monkeypatch.setattr(st.egress, "record", lambda *a, **k: logged.append((a, k)))
    text = st._run({"download_mb": 1, "upload_mb": 1})
    assert "Latency:" in text and "Download:" in text and "Upload:" in text
    assert "1.0 MB" in text and "One measurement" in text
    assert any(k.get("bytes_out") == 1_000_000 for _, k in logged), "the upload's bytes must be logged"
    assert sum(1 for m, _ in hits if m == "POST") == 1

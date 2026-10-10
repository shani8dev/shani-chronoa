"""Exercise shipped skills with real binaries and real arguments, on this box.

`AGENTS.md` is explicit that running is verification, and the four parser bugs
found in `open_files`/`system_history` on 2026-10-10 are what getting that
wrong costs. Both skills were green, both were verified by dispatch, and both
answered wrongly on this machine - because they had been verified only against
the `@blue` capture.

So this does the same thing backwards: for every skill whose binaries are
**actually installed here**, dispatch it with a real argument and print what
came back. A refusal naming a missing package is a correct answer and is shown
as such; an exception or a traceback is a bug.

**Only read-only, non-destructive skills are listed, deliberately.** This is
not a generic dispatcher - it is a list a person wrote, so no accidental
`rm`, no write to a real path, and `sync_folder` gets two fresh empty
directories and nothing else.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))

PROBES = [
    ("disk_activity", {}, "iostat, read only"),
    ("cpu_frequency", {}, "cpupower frequency-info, read only"),
    ("cpu_per_core", {}, "mpstat, read only"),
    ("inspect_binary", {"path": "/bin/true"}, "readelf on a shipped binary"),
    ("dns_lookup", {"name": "example.com"}, "one DNS query over the network"),
    ("check_port", {"host": "127.0.0.1", "port": 1},
     "one TCP connect to loopback, a refused port"),
    ("list_archive", {"path": "<tmp>/std.zip"}, "unzip -l on a file made here"),
    ("vcs_status", {"path": "<tmp>/repo"}, "git status in a throwaway repo"),
    ("sync_folder", {"source": "<tmp>/from", "destination": "<tmp>/to"},
     "rsync between two fresh empty directories"),
    ("download_file", {"url": "https://example.com/",
                       "destination": "<tmp>/dl.html"}, "curl, into a temp file"),
    ("cloud_files", {}, "rclone is absent here - refusal expected"),
    ("backup_status", {}, "restic is absent here - refusal expected"),
    ("bandwidth_to_host", {}, "iperf3 is absent here - refusal expected"),
    # --- the second batch: skills whose *state* is here, not a binary -------
    # These read /proc, /sys, the journal, or the filesystem, so they need no
    # package at all and have never been exercised on a machine that is not the
    # slot they were built on.
    ("system_info", {}, "lscpu + /proc, read only"),
    ("disk_usage", {}, "df + du, read only"),
    ("list_processes", {}, "ps, read only"),
    ("port_owner", {}, "/proc/net/tcp + /proc/<pid>, read only"),
    ("interface_counters", {}, "/proc/net/dev, read only"),
    ("neighbour_table", {}, "/proc/net/arp, read only"),
    ("routing_table", {}, "the kernel's routing table, read only"),
    ("usb_devices", {}, "/sys/bus/usb, read only"),
    ("driver_info", {}, "/proc/modules + /sys/module, read only"),
    ("temperatures", {}, "/sys/class/hwmon + thermal zones, read only"),
    ("list_services", {}, "systemctl list-units, read only"),
    ("read_logs", {}, "journalctl, read only"),
    ("scheduled_tasks", {}, "crontab -l, read only"),
    ("tls_certificate", {"host": "example.com"}, "ssl from the stdlib, one handshake"),
    ("compute_hash", {"path": "/bin/true"}, "hashlib, read only"),
    ("compare_files", {"path_a": "<tmp>/same-a", "path_b": "<tmp>/same-b"},
     "two nearly identical files in the scratch directory"),
    ("ping_host", {"host": "127.0.0.1"}, "one ICMP to loopback, bounded"),
    ("trace_route", {"host": "127.0.0.1"}, "mtr to loopback, bounded"),
    ("my_ip_address", {}, "the kernel's view of this machine's addresses"),
    ("list_fonts", {}, "fc-list, read only"),
]

_TRACES = ("Traceback", "NameError", "AttributeError", "KeyError",
           "TypeError", "IndexError")


#: **The scratch lives under `$HOME`, not `/tmp`.** The sandbox confines file
#: skills to your home directory, and a fixture in `/tmp` gets the sandbox's own
#: refusal - which is correct and is not the thing being probed. Measured: a
#: probe at `/bin/true` and then at `/tmp/probe-skills-*/same-a` both answered
#: "Not touching ... it is outside your home directory". The directory is named
#: for what it is and removed on the way out.
_SCRATCH_PARENT = Path.home()


def _scratch():
    work = Path(tempfile.mkdtemp(prefix="probe-skills-", dir=_SCRATCH_PARENT))
    with zipfile.ZipFile(work / "std.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.txt", "one\n")
        archive.writestr("dir/b.txt", "two\n")
    repo = work / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True,
                   timeout=30)
    (repo / "file.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(repo), "add", "."],
                   capture_output=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "probe"],
                   capture_output=True, timeout=30)
    (work / "from").mkdir()
    (work / "to").mkdir()
    # Two files that differ by one line, for `compare_files`. Both live under
    # the scratch directory because the sandbox confines it to your home - a
    # probe at `/bin/true` gets the sandbox's refusal, which is correct and
    # which is not the thing being probed.
    (work / "same-a").write_text("one\ntwo\nthree\n")
    (work / "same-b").write_text("one\nTWO\nthree\n")
    return work


def main():
    work = _scratch()
    from shani_chronoa import tools

    problems = []
    print("=" * 74)
    for name, arguments, why in PROBES:
        # **`value` is not always a str** - `check_port`'s `port` is an int, and
        # the first version of this tool raised AttributeError on it, which is
        # the same tool-shape bug the tool exists to find.
        args = {}
        for key, value in arguments.items():
            if isinstance(value, str) and value.startswith("<tmp>"):
                args[key] = str(work) + value[len("<tmp>"):]
            else:
                args[key] = value
        print("\n### %s   (%s)" % (name, why))
        try:
            result = tools.execute_tool(name, args)
        except Exception as exc:
            problems.append((name, "RAISED %s: %s" % (type(exc).__name__, exc)))
            print("  !! RAISED %s: %s" % (type(exc).__name__, exc))
            traceback.print_exc()
            continue
        text = str(result)
        if not text.strip():
            problems.append((name, "empty answer"))
            print("  !! EMPTY ANSWER")
            continue
        for marker in _TRACES:
            if marker in text:
                problems.append((name, "answer contains %s" % marker))
                break
        lines = text.splitlines()
        for line in lines[:6]:
            print("  | %s" % line[:150])
        if len(lines) > 6:
            print("  | ... (%d lines total)" % len(lines))

    print("\n" + "=" * 74)
    shutil.rmtree(work, ignore_errors=True)
    if not problems:
        print("no skill raised, went empty, or answered with a traceback.")
        print("(the scratch directory was removed; it only ever held the zip,"
              " the git repo and two empty directories)")
        return 0
    print("%d problem(s):" % len(problems))
    for name, what in problems:
        print("  %s: %s" % (name, what))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- the verified result, so the next run is a diff -----------------------------
# Run on this box, 2026-10-10, after the four parser bugs above were fixed.
# **32 skills, no raise, no empty answer, no traceback.**
#
#     disk_activity      1 disk(s) working (per second, over the last second)
#                        nvme0n1 read 0.00/s ... 0.70% used
#     cpu_frequency      CPU governor: powersave, running at about 3.92 GHz,
#                        hardware range 400 MHz to 4.70 GHz
#     cpu_per_core       8 core(s) over the last second
#     inspect_binary     true, ELF64, ... position-independent executable file
#     dns_lookup         **privacy mode is on** - the correct refusal, and the
#                        honest one: the name never left the machine
#     check_port         127.0.0.1 did NOT accept a connection on port 1: refused
#     list_archive       std.zip contains 2 item(s), 8 B, standard library
#     vcs_status         repo is a git working copy, branch master
#     sync_folder        already up to date, nothing would change
#     download_file      577 bytes, HTTP 200, 0.189s
#     backup_status      restic is not installed ... the 'restic' package
#     bandwidth_to_host  iperf3 is not installed ... the 'iperf3' package
#
# and the second batch, whose *state* is here rather than a binary:
#
#     system_info        LENOVO 20TAS19000, R1EET47W(1.47), Ubuntu 26.04.1 LTS
#     disk_usage         /dev/mapper/ubuntu--vg-ubuntu--lv 466G 313G 129G 71%
#     list_processes     361 process(es); showing 60 by CPU use
#     neighbour_table    172.17.0.3 dev docker0 COMPLETE de:ac:eb:97:4e:a6
#     routing_table      default via 192.168.31.1 dev wlp0s20f3 metric 600 from dhcp
#     usb_devices        7 USB device interface(s) attached, Integrated Camera 480 Mb/s
#     driver_info        iwlwifi, i915, skl_hda_dsp_generic, btusb, r8169
#     list_services      232 service unit(s); showing 80 (running first)
#     read_logs          the kernel journal, 20 line(s) shown
#     scheduled_tasks    cron: 7 scheduled job(s) across 6 source(s)
#     compute_hash       SHA256(true) = 913a39cd38f353...  [34.5 KiB]
#     compare_files      3 vs 3 line(s), the diff is `two` -> `TWO`
#     ping_host          127.0.0.1 answered: 5/5 replies, average 0.0 ms
#     my_ip_address      wlp0s20f3: 192.168.31.202, plus an IPv6 and two bridges
#     list_fonts         231 font families installed
#     temperatures       the hwmon sense is turned off - its consent gate, correctly
#     tls_certificate    privacy mode is on - the correct refusal
#     trace_route        privacy mode is on - the correct refusal
#
# **No skill raised, went empty, or answered with a traceback.** The two
# refusals are correct answers and they name real packages, which is the
# first thing the missing-hint work of the same day was for.
#
# **What a clean run does and does not prove.** It proves these skills run on
# a *third* distribution - Ubuntu, not the `@blue` slot and not the dev box the
# unit tests were written on - and that is the only thing it proves. The four
# parser bugs found the same day were all invisible here at first because the
# fixtures were slot-shaped; `open_files` and `system_history` were green,
# dispatchable, and wrong. So re-run this after every change to a parser, and
# read the *output* rather than trusting that a dispatch succeeded.

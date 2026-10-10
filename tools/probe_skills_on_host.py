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
    ("backup_status", {}, "restic is absent here - refusal expected"),
    ("bandwidth_to_host", {}, "iperf3 is absent here - refusal expected"),
]

_TRACES = ("Traceback", "NameError", "AttributeError", "KeyError",
           "TypeError", "IndexError")


def _scratch():
    work = Path(tempfile.mkdtemp(prefix="probe-skills-"))
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
    if not problems:
        print("no skill raised, went empty, or answered with a traceback.")
        return 0
    print("%d problem(s):" % len(problems))
    for name, what in problems:
        print("  %s: %s" % (name, what))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- the verified result, so the next run is a diff -----------------------------
# Run on this box, 2026-10-10, after the four parser bugs below were fixed:
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

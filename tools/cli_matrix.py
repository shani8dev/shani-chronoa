#!/usr/bin/env python3
"""Every command, library and interface on a Shanios install, mapped for building skills.

Run ON a Shanios system (or in a shani-testbed slot), from the checkout:

    python3 tools/cli_matrix.py --out=cli-matrix     # writes .json, .md and .html

For every executable on PATH it records:

* the package that owns it (pacman's own database), why it is installed
  (explicitly, or as a dependency) and whether it comes from the [shani] repo;
* the man page's section and NAME line - from `whatis` when man-db has an
  index, else read straight from the page, so a fresh image works too;
* whether the man page documents machine-readable output (--json and friends),
  which is what makes a command cheap to wrap: a skill parses JSON, not prose;
* a category, a kind (scriptable / interactive / admin / daemon / game), a
  safety guess (read-only / writes-new / mixed / can-change / elevates) and an intent verb
  (inspect, convert, search, monitor, ...) taken from the man summary;
* the desktop app it launches, if any, and which Chronoa skills already call it.

Each command is also mapped to the Chronoa surface it could feed - a
**sense** (read-only machine state, deposited as a percept), a **skill** (an
on-demand read-only tool), an **actuator** (it changes something, so it needs
a consent gate), a **trigger** (a state that transitions, or a command that
can follow/watch) or **memory** (a stable fact about the machine or its user) -
and coverage is tracked per surface: which skills, senses and trigger event
readers already call it. What Chronoa has today (every tool and whether it is
an actuator, gated or allowed unattended; every sense with its lifetime,
sensitivity, polling and default consent; the trigger event types; the memory
kinds) is read from its live registries, not written down here.

Scriptable, documented commands that nothing uses are the candidates;
they are scored (JSON output, read-only, explicitly installed and Shanios'
own packages rank higher; setuid tools and packages a
skill already wraps rank lower) and grouped into skill
ideas by category and intent.

Beside commands, it maps the interfaces a skill can use without a command
line at all: D-Bus services (system and session), GObject-introspection
typelibs, Python modules, systemd units, shared libraries and Flatpak apps.

Reads only: nothing is installed, run or changed (it calls pacman -Q/-Sl,
whatis and flatpak list, all read-only queries).
"""

import argparse
import collections
import configparser
import gzip
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cli_matrix")

#: Modules this tool looked for and did not find. Reported in the output so an
#: empty column is distinguishable from a broken inventory.
NOT_FOUND: "list[str]" = []

PATH_DIRS = ("/usr/bin", "/usr/sbin", "/usr/local/bin")
HERE = Path(__file__).resolve().parent.parent
SKILLS_DIR = HERE / "usr/lib/shani-chronoa/shani_chronoa"
if not SKILLS_DIR.is_dir():
    # Run from an installed system (the harness copies this file into a slot
    # at /usr/lib/shani-chronoa/tools/): read the installed package instead.
    HERE, SKILLS_DIR = Path("/"), Path("/usr/lib/shani-chronoa/shani_chronoa")
PKGBUILDS = HERE.parent / "shani-pkgbuilds"

#: package-name / description keyword -> category, first match wins
CATEGORIES = [
    ("Desktop & apps", r"gnome|kde|plasma|gtk|qt[56]?\b|nautilus|cosmic|xdg|desktop|wayland|x11|xorg"),
    ("Audio & video", r"pipewire|pulse|alsa|wireplumber|ffmpeg|gstreamer|v4l|sox|lame|opus|vorbis|flac|mpv|video|audio|l-smash|amr|srt\b|mp4|mux"),
    ("Images & documents", r"imagemagick|poppler|ghostscript|tesseract|pdf|jpeg|png|tiff|webp|exif|cups|print|sane|scan|font|gif|vips|pixbuf|jbig|image|groff|troff|pango|fits"),
    ("Network", r"network|nm-|wpa|iw\b|wireless|ssh|curl|wget|dns|bind|nmap|iputils|iproute|ethtool|tcpdump|vpn|wireguard|openvpn|tailscale|cloudflared|rsync|samba|nfs|avahi|firewall|bluez|bluetooth|netlink|iptables|nftables|packet|gnutls|sasl"),
    ("Storage & filesystems", r"btrfs|e2fs|xfs|f2fs|dosfs|exfat|ntfs|lvm|mdadm|cryptsetup|smartmon|nvme|udisks|parted|gdisk|squashfs|fuse|mount|quota|zram|mtools|ms-dos|device-mapper|isoburn|libburn|acl\b"),
    ("Security & identity", r"gnupg|gpg|openssl|pam|polkit|sudo|tpm|sbctl|mokutil|apparmor|audit|fprint|pcsc|yubikey|fido|krb|ldap|keyring|secret|pinentry|nettle|sbsign|libcap|capabilit|crypt"),
    ("Containers & VMs", r"podman|docker|buildah|skopeo|distrobox|lxc|lxd|libvirt|qemu|apptainer|waydroid|flatpak|snap|nix"),
    ("Hardware & power", r"upower|power|acpi|cpupower|lm_sensors|sensors|dmidecode|pciutils|usbutils|ddcutil|liquidctl|fwupd|bolt|gpsd|geoclue|iio|mesa|vulkan|nvidia|intel|amd|hwloc|numa|drm\b|firewire|1394|kbd\b|keyboard"),
    ("System & services", r"pacman|package manager|systemd|util-linux|procps|coreutils|shadow|kmod|dbus|cronie|logrotate|htop|lsof|strace|sysstat|psmisc|inxi|fastfetch|glibc|bash|shell|arch-install|glib2|gettext|icu\b|locale"),
    ("Development", r"git|subversion|mercurial|gcc|clang|make|cmake|python|perl|ruby|lua|node|jq|elfutils|gdb|binutils|android-tools|autoconf|pkgconf|libtool|sqlite|guile|scheme|yaml|fftw|ncurses"),
    ("Text & files", r"grep|ripgrep|sed|gawk|diffutils|patch|less|man|texinfo|tealdeer|words|vim|nano|ed\b|findutils|tar|gzip|bzip|xz|zstd|zip|7zip|unrar|lrzip|lzop|file\b|tree|plocate|ncdu|archive|lz4|xxhash|hash|compress"),
    ("Accessibility & input", r"orca|brltty|espeak|ibus|onboard|at-spi"),
]

INTERACTIVE = re.compile(r"(vim?|nano|ed|emacs|less|more|htop|top|btop|ncdu|tmux|screen|mc|ranger|"
                         r"cgdisk|cfdisk|nmtui|alsamixer|w3m|lynx|cmus|mutt|irssi|iftop|nethogs|"
                         r"bandwhich|cgps|micro|dialog|whiptail|fish|bash|zsh|sh|python3?|perl|lua)")
GAME_PKGS = {"bsd-games"}

#: man-summary verb -> intent, first match wins
INTENTS = [
    ("convert", r"\bconvert|transcod|translat|encod|decod|render|to (?:pdf|png|text)"),
    ("compress", r"compress|archiv|\bzip|\bpack(?:s|ed|ing)?\b|extract"),
    ("search", r"\bsearch|\bfind|\blocate|\bmatch|\blook ?up|\bquery"),
    ("monitor", r"\bmonitor|\bwatch|\btrack|\blog(?:s|ging)?\b|statistic|usage|\btop\b"),
    ("transfer", r"download|upload|transfer|\bcopy|\bsync|\bfetch|\bsend|\breceive|mirror"),
    ("check", r"\bcheck(?:s|ed|ing)?\b|verif|\btest|validat|diagnos|benchmark|\bscan"),
    ("calculate", r"calculat|arithmetic|\bmath|checksum|\bhash|digest|\bsum\b|\bcount"),
    ("create", r"\bcreat|\bmake|generat|\bbuild|\bnew\b|\bwrite"),
    ("change", r"\bset\b|\bchange|\bmodif|\bedit|\bremov|\bdelet|\bkill|\bmount|\binstall|\benabl|\bdisabl|"
               r"\badd\b|\bformat (?:a |the )?(?:disk|partition|device|filesystem|volume)|\brenam|\bmov|"
               r"\bsignal|\bwip(?:e|ing)\b|\berase|\bflash|\bhalt|\bpower off|\breboot|\bsuspend|\bhibernat"),
    ("control", r"\bcontrol|\bmanag|configur|\bstart|\bstop|\brun\b|\bexecut|\blaunch|\bopen"),
    ("process", r"\bsort|\bfilter|\bjoin|\bsplit|\bcut\b|\bparse|\bprocess|transform|\bjson|\bmerge|\bconcatenat|"
                r"\bstrip|\bwrap|\bpaste|\bstream editor|\bformat (?:text|lines)"),
    ("inspect", r"\bprob|\bintrospect|\bdetermin|\bidentif|\bretriev|\bdetect|\binterrogat|\bshow|\bdisplay|\bprint|\blist|\breport|\binfo|\bdescri|\bdump|\bview|\bread|\bget\b"),
]
MUTATING = {"change", "create", "control", "transfer"}
DRYRUN_FLAG = re.compile(r"(?:--dry-run\b|--simulate\b|--no-act\b|--pretend\b|--what-if\b|-n,\s*--dry-run|"
                         r"--check\b.{0,60}(?:without|not) (?:chang|modif|writ))", re.I)
FOLLOW_FLAG = re.compile(r"(?:--follow\b|--watch\b|-f,\s*--follow|\bmonitor mode\b|\binotify\b|"
                         r"--monitor\b|\bevents? as they (?:happen|occur)|\bwait for (?:events|changes))", re.I)
JSON_FLAG = re.compile(r"(?:--json\b|-j,\s*--json|--output[= ](?:\\fI)?json|-o\s+json|--format[= ]json|"
                       r"--porcelain|-J\b.{0,40}json|output.{0,30}\bJSON\b)", re.I)


def run(argv, timeout=60) -> str:
    try:
        # C locale: pacman's dates and sizes are parsed below, and a translated "Build Date" is not
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# --- pacman ---------------------------------------------------------------

def _fields(block: str) -> dict:
    """`pacman -Qi` fields, with wrapped continuation lines joined back on."""
    f, last = {}, None
    for line in block.splitlines():
        m = re.match(r"^(\w[\w ]*?)\s*:\s*(.*)$", line)
        if m and not line.startswith(" "):
            last = m.group(1)
            f[last] = m.group(2)
        elif last and line.startswith(" "):
            f[last] += " " + line.strip()
    return f


def _optional_deps(block: str) -> "list[dict]":
    """`Optional Deps` as [{name, why, installed}] - one per line, unlike the other fields."""
    m = re.search(r"^Optional Deps\s*:\s*(.*?)(?=^\S)", block + "\nX", re.M | re.S)
    out = []
    for line in (m.group(1).splitlines() if m else []):
        line = line.strip()
        if not line or line == "None":
            continue
        installed = line.endswith("[installed]")
        line = line.removesuffix("[installed]").strip()
        name, _, why = line.partition(": ")
        name = re.split(r"[<>=]", name.strip().rstrip(":"), maxsplit=1)[0]
        if name:
            out.append({"name": name, "why": why.strip()[:120], "installed": installed})
    return out


def _size(text: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", text.strip())
    if not m:
        return 0
    mult = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}.get(m.group(2), 1)
    return int(float(m.group(1)) * mult)


def _when(text: str) -> int:
    import calendar
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %d %b %Y %I:%M:%S %p %Z"):
        try:
            return calendar.timegm(time.strptime(text.strip(), fmt))
        except ValueError:
            continue
    return 0


def enabled_units() -> "list[dict]":
    """Units enabled by a symlink in a .wants/.requires dir - what starts at boot or login, no boot needed."""
    out = []
    for scope, roots in (("system", ("/etc/systemd/system", "/usr/lib/systemd/system")),
                         ("user", ("/etc/systemd/user", "/usr/lib/systemd/user"))):
        for root in roots:
            for wants in sorted(Path(root).glob("*.wants")) + sorted(Path(root).glob("*.requires")):
                for unit in sorted(wants.iterdir()):
                    out.append({"unit": unit.name, "scope": scope, "by": wants.name.rsplit(".", 1)[0],
                                "vendor": root.startswith("/usr")})
    return out


def service_exposure(units: "list[dict]") -> "list[dict]":
    """`systemd-analyze security --offline` for each enabled system service: 0 (tight) .. 10 (no sandboxing).

    Offline mode reads the unit file, so no boot is needed. The score only
    counts systemd's own sandboxing options - AppArmor and the program's own
    precautions are invisible to it - so it is a list of where hardening would
    apply, not a verdict on the service.
    """
    if shutil.which("systemd-analyze") is None:
        return []
    out, seen = [], set()
    for u in units:
        name = u["unit"]
        if u["scope"] != "system" or not name.endswith(".service") or "@" in name or name in seen:
            continue
        seen.add(name)
        path = next((f"{d}/{name}" for d in ("/etc/systemd/system", "/usr/lib/systemd/system")
                     if os.path.isfile(f"{d}/{name}")), None)
        if not path:
            continue
        text = run(["systemd-analyze", "security", "--offline=true", "--no-pager", path], timeout=20)
        m = re.search(r"Overall exposure level for \S+: ([\d.]+) (\S+)", text)
        if m:
            out.append({"unit": name, "exposure": float(m.group(1)), "rating": m.group(2)})
    return sorted(out, key=lambda x: -x["exposure"])


def packages() -> dict:
    """name -> {description, explicit, version, required_by, groups} from `pacman -Qi`."""
    out = {}
    for block in run(["pacman", "-Qi"], timeout=600).split("\n\n"):
        f = _fields(block)
        if "Name" in f:
            listed = lambda k: [x for x in f.get(k, "").split() if x != "None"]  # noqa: E731
            out[f["Name"]] = {"description": f.get("Description", ""), "version": f.get("Version", ""),
                              "explicit": f.get("Install Reason", "").startswith("Explicitly"),
                              "required_by": listed("Required By"), "groups": listed("Groups"),
                              "provides": [re.split(r"[<>=]", x, maxsplit=1)[0] for x in listed("Provides")],
                              "optional": _optional_deps(block),
                              "depends": [re.split(r"[<>=]", x, maxsplit=1)[0] for x in listed("Depends On")],
                              "size": _size(f.get("Installed Size", "")),
                              "built": _when(f.get("Build Date", ""))}
    return out


def pulled_in_by(pkgs: dict) -> dict:
    """package -> the explicitly installed packages whose dependencies brought it in (nearest first).

    This is "why is this on the image": a library nobody asked for is there
    because shani-desktop-gnome (or a printer driver, or a codec pack) needs it,
    and that ancestor is what says which part of the system a command belongs to.
    """
    out = {}
    for name in pkgs:
        if pkgs[name]["explicit"]:
            out[name] = [name]
            continue
        seen, frontier, found = {name}, [name], []
        for _depth in range(8):
            nxt = []
            for p in frontier:
                for parent in pkgs.get(p, {}).get("required_by", []):
                    if parent in seen:
                        continue
                    seen.add(parent)
                    (found if pkgs.get(parent, {}).get("explicit") else nxt).append(parent)
            if found or not nxt:
                break
            frontier = nxt
        out[name] = sorted(found)[:5]
    return out


def shani_packages() -> set:
    """Packages built by Shanios: the [shani] sync db, else the shani-pkgbuilds directory names."""
    names = {line.split()[1] for line in run(["pacman", "-Sl", "shani"]).splitlines() if len(line.split()) > 1}
    if not names and PKGBUILDS.is_dir():
        names = {p.name for p in PKGBUILDS.iterdir() if (p / "PKGBUILD").is_file()}
    return names


def file_lists() -> dict:
    """package -> [paths] from `pacman -Ql`."""
    out = collections.defaultdict(list)
    for line in run(["pacman", "-Ql"], timeout=600).splitlines():
        pkg, _, path = line.partition(" ")
        if path and not path.endswith("/"):
            out[pkg].append(path)
    return out


# --- man pages ------------------------------------------------------------

_SECTION_RANK = {"1": 0, "8": 1, "6": 2}


def _man_pages() -> dict:
    """name -> (section, file) for every page under /usr/share/man (no man-db needed).

    A command's page wins over a same-named one in another section: `shutdown`
    is shutdown(8) here, not the shutdown(2) syscall that sorts first.
    """
    pages = {}
    for f in sorted(Path("/usr/share/man").glob("man[1-9]*/*")):
        m = re.match(r"^(.+)\.([1-9][a-z0-9]*)(\.(gz|bz2|xz|zst))?$", f.name)
        if not m:
            continue
        rank = _SECTION_RANK.get(m.group(2)[0], 9)
        if m.group(1) not in pages or rank < _SECTION_RANK.get(pages[m.group(1)][0][0], 9):
            pages[m.group(1)] = (m.group(2), f)
    return pages


def _read_page(f: Path, limit=400_000, depth=0) -> str:
    import bz2
    import lzma
    try:
        if f.suffix == ".zst":
            return run(["zstd", "-dc", str(f)], timeout=10)[:limit]
        opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}.get(f.suffix, open)
        with opener(f, "rt", errors="replace") as fh:
            text = fh.read(limit)
    except (OSError, EOFError):
        return ""
    so = re.match(r"^\.so\s+(\S+)", text)  # a page that just includes another
    if so and depth < 3:
        target = Path("/usr/share/man") / so.group(1)
        for cand in [target, *target.parent.glob(target.name + ".*")]:
            if cand.is_file():
                return _read_page(cand, limit, depth + 1)
    return text


def _name_line(text: str) -> str:
    """The NAME section's one-liner, from the roff source."""
    nd = re.search(r"^\.Nd\s+(.+)$", text, re.M)  # mdoc says it outright
    if nd:
        return nd.group(1).strip().strip('"')[:200]
    m = re.search(r'^\.S[Hh]\s+"?(?i:NAME)"?\s*\n(.*?)(?=^\.S[Hh]\s)', text, re.M | re.S)
    if not m:
        return ""
    body = re.sub(r'^\.\s*\\".*$', "", m.group(1), flags=re.M)  # roff comments
    body = re.sub(r"^\.(?:Nm|B|I|BR|IR|PD|nh|ad|na|br|sp)\b ?", "", body, flags=re.M)
    body = re.sub(r"\\f[BIRP]|\\\(em|\\-", lambda x: "-" if x.group(0) in ("\\-", "\\(em") else "", body)
    body = re.sub(r"\\\*?\(Aq", "'", body)
    body = " ".join(body.replace("\\", "").split())
    body = re.sub(r"(^|\s)\.[A-Za-z]{1,3}\b", " ", body).strip(" .")
    return body.split(" - ", 1)[-1].strip()[:200]


def _synopsis(text: str) -> str:
    """The first usage line of SYNOPSIS, roff stripped: where a wrapper's argv starts."""
    m = re.search(r'^\.S[Hh]\s+"?(?i:SYNOPSIS)"?\s*\n(.*?)(?=^\.S[Hh]\s)', text, re.M | re.S)
    if not m:
        return ""
    if re.search(r"^\.(?:Nm|Op|Fl|Ar)\b", m.group(1), re.M):  # mdoc: macros are words
        body = re.sub(r"\bFl\s+", "-", m.group(1))
        body = re.sub(r"(?m)^\.(?=[A-Z][a-z])", "", body)
        words = [w for w in body.split() if w not in ("Nm", "Op", "Ar", "Oo", "Oc", "Ns", "Cm", "Pa", "Ic", "Xo", "Xc",
                                                     "Ek", "Bk", "-words", "Op", "Sx", "Ao", "Ac")]
        return " ".join(words)[:160]
    lines = []
    for line in m.group(1).splitlines():
        if re.match(r"^\.(?:br|sp|PP|P|LP|TP|IP|HP)\b", line) and lines:
            break
        if line.startswith('.\\"'):
            continue
        line = re.sub(r"^\.(?:Nm|Op|Fl|Ar|Oo|Oc|Ek|Bk|B|I|BR|IR|RB|RI|BI|IB|SY|YS|OP|UR|UE|in|nh|ad|HP)\b ?", "", line)
        line = re.sub(r"\\f[BIRP]|\\f\(..|\\&|\\c|\\\|", "", line).replace("\\-", "-").replace("\\ ", " ")
        line = line.replace('"', "").strip()
        if line and not line.startswith("."):
            lines.append(line)
    return " ".join(" ".join(lines).split())[:160]


def _roff_clean(text: str) -> str:
    text = re.sub(r"\\f[BIRP]|\\f\(..|\\&|\\c|\\\||\\\*\(..|\\e|\\,|\\/|\\%|\\:", "", text)
    text = text.replace("\\-", "-").replace("\\ ", " ").replace("\\(em", "-").replace('"', "")
    text = re.sub(r"^\.(?:B|I|BR|IR|RB|RI|BI|IB|SM|SB)\s+", "", text, flags=re.M)
    return " ".join(text.split())


def _options(text: str, limit: int = 30) -> "list[dict]":
    """The flags a man page documents: [{flags, arg, desc}], from .TP/.IP (man) or .It Fl (mdoc).

    What a wrapper needs to build its schema: which flags exist, which take a
    value, and one line of what each does. Only entries whose tag starts with
    a dash are kept, so a .TP used for an exit status or a file is not a flag.
    """
    out = []
    if re.search(r"^\.It Fl\b", text, re.M):  # mdoc
        for m in re.finditer(r"^\.It Fl (\S+)(?: Ar (\S+))?.*\n((?:(?!^\.It\b|^\.Sh\b).*\n){0,4})", text, re.M):
            desc = _roff_clean(re.sub(r"^\.\w+\s?", "", m.group(3), flags=re.M))
            out.append({"flags": ["-" + m.group(1)], "arg": m.group(2) or "", "desc": desc[:100]})
            if len(out) >= limit:
                break
        return out
    stop = r"(?!^\.(?:TP|IP|SH|SS|PP|P|LP|sp)\b)"
    entries = []
    # .TP, then the tag on the next line (classic man(7))
    entries += [(m.start(), m.group(1), m.group(2)) for m in re.finditer(
        rf"^\.TP[^\n]*\n(.*)\n((?:{stop}.*\n){{0,4}})", text, re.M)]
    # .IP "tag" on one line (curl, many hand-written pages)
    entries += [(m.start(), m.group(1), m.group(2)) for m in re.finditer(
        rf'^\.IP\s+"([^"]+)"[^\n]*\n((?:{stop}.*\n){{0,4}})', text, re.M)]
    # asciidoctor/docbook: .sp or .PP, a bold flag line, .RS 4, the description (util-linux, systemd)
    entries += [(m.start(), m.group(1), m.group(2)) for m in re.finditer(
        r"^\.(?:sp|PP)\n(\\fB\\-.*)\n\.RS \d+\n((?:(?!^\.RE\b).*\n){0,4})", text, re.M)]
    for _pos, raw_tag, raw_desc in sorted(entries):
        m = None
        tag = _roff_clean(raw_tag)
        if not tag.startswith("-"):
            continue
        lead = re.match(r"^((?:--?[A-Za-z0-9][\w-]*(?:[= ][^,\s]+)?(?:,\s*|\s+(?=-))?)+)", tag)
        flags = re.findall(r"(?<![\w-])(--?[A-Za-z0-9][\w-]*)", lead.group(1) if lead else "")
        if any(len(f) > 1 and f[1] != "-" and len(f) > 3 for f in flags[1:] if not f.startswith("--")):
            continue  # "-ftps" after "--proto": an example value, not a second spelling
        # A value follows "--flag=VALUE", "--flag[=WHEN]", "--flag=" (docbook leaves the name in the prose)
        # or "-B bind_interface" / "--alt-svc <file name>".
        val = re.search(r"--?[A-Za-z0-9][\w-]*(?:\[?=([^\]\s,]*)|\s+(<[^>]+>|[A-Za-z][\w-]*))", tag)
        arg = None
        if val and not (val.group(2) or "").startswith("-") and (val.group(1) is not None or val.group(2)):
            arg = (val.group(1) or val.group(2) or "VALUE")
        desc = _roff_clean(re.sub(r"^\.\w+\b ?", "", raw_desc, flags=re.M))
        if flags:
            out.append({"flags": flags[:3], "arg": arg or "",
                        "desc": desc[:100]})
        if len(out) >= limit:
            break
    return out


_SUB_READS = re.compile(r"^(?:list|ls|show|status|get|is-|cat|info|inspect|introspect|tree|connectivity|"
                        r"ps|log|logs|top|stats|search|query|"
                        r"find|check|verify|diff|print|dump|describe|history|events|monitor|help|version|"
                        r"whoami|lookup|resolve|ping|test|scan|count|query|blame|grep|shortlog|names?$)")
_SUB_CHANGES = re.compile(r"^(?:start|stop|restart|reload|enable|disable|mask|unmask|kill|set|unset|edit|add|"
                          r"remove|rm|delete|del|create|new|mv|move|rename|install|uninstall|upgrade|update|"
                          r"apply|reset|revert|push|commit|merge|rebase|pull|clone|import|export|load|unload|"
                          r"mount|umount|format|wipe|erase|connect|disconnect|up|down|modify|change|"
                          r"poweroff|reboot|halt|suspend|hibernate|isolate|daemon-|link|clean|prune|"
                          r"switch|activate|deactivate|attach|detach|run|exec|tag|reboot|lock|unlock|trust|pair|"
                          r"call|emit|send|write|restore|replace|patch|terminate|abort|cleanup|record|import|"
                          r"reduce|init|initiali[sz]e|config|configure|prepare|generate|sparse)")


def _whole(rx: "re.Pattern", word: str) -> bool:
    """`rx` matches all of `word`, or a prefix ending at a hyphen: list-units yes, checkout for check no."""
    m = rx.match(word)
    return bool(m) and (m.end() == len(word) or word[m.end()] == "-" or word[m.end() - 1] == "-")


def classify_subcommand(name: str, desc: str) -> str:
    """'reads' or 'changes' for one subcommand.

    Its name first (status, start), then the first word of its description
    ("Apply a series of patches" for git am), then the description's verbs.
    `changes` is the default when nothing reads clearly: a wrapper that offers
    a subcommand as read-only must be sure, and the cost of a wrong 'changes'
    is only that it stays gated.
    """
    low = name.lower()
    words = [w.strip(",.;:()").lower() for w in desc.split()]
    # A getter that turns setter when given an argument ("log-level [LEVEL]":
    # "if an argument is given, changes the current log level") reads by name
    # and changes by description; the description wins, as gating is the safe side.
    says_change = bool(re.search(r"\b(?:chang(?:e|es|ing)|sets?|modif(?:y|ies)|overrid|writ(?:e|es)) (?:the |its |a )?"
                                 r"(?:current |default |new )?\w+", desc.lower()))
    # "container-list" / "container-checkpoint": the verb is the last segment
    for candidate in dict.fromkeys((low, low.rsplit("-", 1)[-1])):
        if _whole(_SUB_CHANGES, candidate) and candidate != low:
            return "changes"
        if _whole(_SUB_READS, candidate):
            return "changes" if says_change else "reads"
        if _whole(_SUB_CHANGES, candidate):
            return "changes"
    if any(_whole(_SUB_CHANGES, w) for w in words) or can_change(desc):
        return "changes"  # "List, create, or delete branches": one changing verb is enough
    if words and _whole(_SUB_READS, words[0]):
        return "reads"
    if intent(desc) in ("inspect", "search", "monitor", "check", "calculate") and not can_change(desc):
        return "reads"
    return "changes"


def _subcommands(text: str, limit: int = 80, command: str = "") -> "list[dict]":
    """Subcommands a page documents under a COMMANDS-like section: [{name, args, desc, effect}]."""
    out, seen = [], set()
    for sec in re.finditer(r'^\.SH\s+"?([^"\n]*COMMANDS?[^"\n]*)"?\s*\n(.*?)(?=^\.SH\s)', text, re.M | re.S | re.I):
        body = sec.group(2)
        tags = re.findall(r"^\.(?:PP|sp|TP|IP)[^\n]*\n(\\fB(?!\\-)[a-z][^\n]*)\n(?:\.RS \d+\n)?((?:(?!^\.(?:PP|TP|IP|RE|SS)\b).*\n){0,12})",
                          body, re.M)
        for raw_tag, raw_desc in tags:
            tag = _roff_clean(raw_tag)
            m = re.match(r"([a-z][\w-]*)(?:\(\d\))?(.*)", tag)
            if not m:
                continue
            sub = m.group(1)
            if command and sub.startswith(command + "-"):
                sub = sub[len(command) + 1:]  # git's own page lists "git-am(1)"
            if sub in seen or sub == command:
                continue
            seen.add(sub)
            # Classified on the whole description, stored shortened: systemctl's
            # service-log-level reads the level "if the argument is not given" and
            # sets it otherwise, and that second clause is past any short cut.
            full = _roff_clean(re.sub(r"^\.\w+\b ?", "", raw_desc, flags=re.M))
            out.append({"name": sub, "args": m.group(2).strip()[:60], "desc": full[:100],
                        "effect": classify_subcommand(sub, full[:600])})
            if len(out) >= limit:
                return out
    return out


def man_info(names: list) -> dict:
    """name -> {section, summary, json}: `whatis` for the summary when indexed, the page for the rest."""
    whatis = {}
    for i in range(0, len(names), 500):
        for line in run(["whatis", "-l", *names[i:i + 500]], timeout=300).splitlines():
            m = re.match(r"^(\S+)\s+\(([^)]+)\)\s+-\s+(.*)$", line)
            if m and m.group(1) not in whatis:
                whatis[m.group(1)] = (m.group(2), m.group(3).strip())
    pages = _man_pages()
    # "git-commit", "podman-ps", "btrfs-subvolume": one page per subcommand, not on PATH
    by_prefix = collections.defaultdict(list)
    for page in pages:
        if "-" in page:
            head, _, tail = page.partition("-")
            if head in names and page not in names and tail:
                by_prefix[head].append(page)
    out = {}
    for name in names:
        if name not in pages and name not in whatis:
            continue
        section, summary = whatis.get(name, ("", ""))
        if name in pages and section and _SECTION_RANK.get(section[0], 9) > _SECTION_RANK.get(pages[name][0][0], 9):
            section, summary = "", ""  # whatis listed shutdown(2) first; the command's page is the answer
        has_json = follows = dry_run = False
        synopsis, options, option_list, subcommands = "", 0, [], []
        if name in pages:
            text = _read_page(pages[name][1])
            section = section or pages[name][0]
            summary = summary or _name_line(text) or "(man page)"
            has_json = bool(JSON_FLAG.search(text))
            follows = bool(FOLLOW_FLAG.search(text))
            dry_run = bool(DRYRUN_FLAG.search(text))
            synopsis = _synopsis(text)
            option_list = _options(text)
            subcommands = _subcommands(text, command=name)
            options = len(option_list) or len(re.findall(r"^\.(?:TP|IP|It Fl)\b", text, re.M))
        if name in by_prefix and len(by_prefix[name]) >= 3:
            known = {x["name"] for x in subcommands}
            for page in sorted(by_prefix[name])[:120]:
                sub = page.split("-", 1)[1]
                if sub in known or len(subcommands) >= 120:
                    continue
                desc = _name_line(_read_page(pages[page][1], limit=6000))[:100]
                subcommands.append({"name": sub, "args": "", "desc": desc, "effect": classify_subcommand(sub, desc)})
        out[name] = {"section": section, "summary": summary, "json": has_json, "follows": follows,
                     "dry_run": dry_run, "synopsis": synopsis, "options": options,
                     "option_list": option_list if name in pages else [],
                     "subcommands": subcommands}
    return out


# --- what Chronoa already uses ---------------------------------------------

_CALLS = re.compile(r"""(?:\[\s*|which\(\s*|_tool\(\s*|tool\s*=\s*)["']([a-z0-9][a-z0-9._+-]{1,40})["']""")


def surface_uses() -> dict:
    """command -> {"skill"|"sense"|"trigger": {module stems}} whose source calls it.

    A call is the first word of an argv list, or a name passed to
    which()/_tool()-style lookups - not any quoted word, which would mark
    `file` as used by every skill that says "file".
    """
    uses = collections.defaultdict(lambda: collections.defaultdict(set))
    sources = [("skill", f) for f in (SKILLS_DIR / "skills").glob("*.py")]
    sources += [("sense", f) for f in (SKILLS_DIR / "senses").glob("*.py")]
    sources += [("trigger", SKILLS_DIR / "triggers.py")]
    for surface, f in sources:
        if f.is_file():
            for cmd in set(_CALLS.findall(f.read_text(errors="replace"))):
                uses[cmd][surface].add(f.stem)
    return uses


_GUARD = re.compile(r"""(?:which|tool_missing|_have|have_tool)\(\s*["']([a-z0-9][\w.+-]{1,40})["']""")
_EXEC = re.compile(r"""(?:\brun|Popen|check_output|check_call|_run_argv|_run_cmd|_run|call)\(\s*\[\s*["']([a-z0-9][\w.+-]{1,40})["']""")
_DESKTOPS = {
    "GNOME": re.compile(r"gsettings|org\.gnome|gnome-|mutter"),
    "KDE": re.compile(r"kwriteconfig|kreadconfig|qdbus|plasma|org\.kde|kscreen|kwin"),
    "COSMIC": re.compile(r"cosmic"),
    "X11 only": re.compile(r"xdotool|xclip|xrandr|setxkbmap|wmctrl|xprop|xsel\b"),
    "Wayland": re.compile(r"wl-copy|wl-paste|ydotool|wlr-|grim\b|swaymsg"),
}


def chronoa_dependencies(commands: dict) -> dict:
    """module -> what it runs, what of that is missing here, and which desktops it knows.

    A command a skill runs that is not on this image is a skill that cannot
    work here; one it checks for first (`shutil.which`, `tool_missing`) at
    least says so, one it does not is a crash or a misleading error. Desktop
    markers say which desktops a module has code for - a GNOME-only actuator on
    a Plasma install answers "I can only switch GNOME's".
    """
    out = {}
    paths = [("skill", f) for f in sorted((SKILLS_DIR / "skills").glob("*.py"))]
    paths += [("sense", f) for f in sorted((SKILLS_DIR / "senses").glob("*.py"))]
    paths += [("trigger", SKILLS_DIR / "triggers.py")]
    # A guard can live in a helper the module imports: close_window, focus_window
    # and press_key all call list_windows.session_problem(), which is where
    # `shutil.which("xdotool")` is. Reading each file alone reported all three
    # as showing a raw error - a confident wrong answer from the audit itself.
    sources = {f"{'skills' if k == 'skill' else 'senses' if k == 'sense' else ''}.{f.stem}".lstrip("."): f
               for k, f in paths if f.is_file()}
    own_guards = {key: set(_GUARD.findall(f.read_text(errors="replace"))) for key, f in sources.items()}

    def imported_guards(key: str, seen: set) -> set:
        text = sources[key].read_text(errors="replace")
        found = set()
        for pkg, mod in re.findall(r"from shani_chronoa\.(skills|senses)\.(\w+) import", text):
            dep = f"{pkg}.{mod}"
            if dep in sources and dep not in seen:
                seen.add(dep)
                found |= own_guards[dep] | imported_guards(dep, seen)
        return found

    for kind_, f in paths:
        if f.stem.startswith("_") or not f.is_file():
            continue
        text = f.read_text(errors="replace")
        key = f"{'skills' if kind_ == 'skill' else 'senses' if kind_ == 'sense' else ''}.{f.stem}".lstrip(".")
        guarded = set(_GUARD.findall(text)) | imported_guards(key, {key})
        runs = guarded | set(_EXEC.findall(text))
        runs.discard("python3")
        missing = sorted(c for c in runs if c not in commands)
        # `shutil.which(binary)` over a list of candidates checks first too, but
        # names no literal the pattern can see; catching OSError at least turns a
        # missing binary into an error string rather than a crash.
        generic = bool(re.search(r"which\(\s*[a-z_][\w\[\]]*\s*\)", text))
        caught = bool(re.search(r"except[^:\n]*(?:FileNotFoundError|OSError)", text))
        handling = {c: ("checked first" if c in guarded or generic else "caught (raw error)" if caught
                        else "unchecked") for c in missing}
        out[f.stem] = {"kind": kind_, "runs": sorted(runs), "missing": missing, "handling": handling,
                       "unguarded_missing": sorted(c for c in missing if handling[c] != "checked first"),
                       "desktops": [d for d, rx in _DESKTOPS.items() if rx.search(text)]}
    return out


def _read_source(name: str, candidates: "tuple[str, ...]" = ()) -> str:
    """The source of `shani_chronoa/<name>`, wherever it lives now.

    Tries the module file, then each of `candidates` inside the package of the
    same name. Returns "" when none of them is there, and records what it looked
    for: a missing file means one column of the matrix is empty, and the reader
    of that matrix has to be able to tell an empty column from a broken tool.
    Raising - which is what this did - turns "one section is missing" into "no
    inventory at all", which is how a tool that only reports on other people's
    rot came to need the same repair.
    """
    tried = [SKILLS_DIR / f"{name}.py"] + [SKILLS_DIR / name / f"{c}.py" for c in candidates]
    for path in tried:
        try:
            return path.read_text(errors="replace")
        except OSError:
            continue
    NOT_FOUND.append(str(name))
    logger.warning("cli_matrix: no source found for %s (looked for %s)", name,
                   ", ".join(str(p) for p in tried))
    return ""


def chronoa_inventory() -> dict:
    """What Chronoa exposes today, read from its own registries (the checkout this file sits in)."""
    sys.path.insert(0, str(SKILLS_DIR.parent))
    try:
        from shani_chronoa import capabilities, senses, skills, triggers
        tools, handlers = skills.discover_skills()
        found = senses.discover_senses()
    except Exception as e:  # noqa: BLE001 - the matrix is still useful without it; say why
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        sys.path.pop(0)
    schema = HERE / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"
    defaults = dict(re.findall(r'<key name="([\w-]+)" type="b">\s*<default>(\w+)</default>',
                               schema.read_text())) if schema.is_file() else {}
    inv_tools = []
    for t in tools:
        fn = t["function"]
        name, desc = fn["name"], fn.get("description", "")
        gate = capabilities.gated_by(name, desc)
        mutating = name in capabilities.MUTATING_TOOLS or bool(gate and name not in capabilities.READ_ONLY_TOOLS)
        inv_tools.append({
            "name": name, "module": getattr(handlers.get(name), "__module__", "").rsplit(".", 1)[-1],
            "role": "actuator" if mutating else "skill", "gate": gate or "",
            "read_only": name in capabilities.READ_ONLY_TOOLS, "open_world": name in capabilities.OPEN_WORLD_TOOLS,
            "unattended_ok": triggers._actuator_problem(name, False) is None
                             and name not in triggers._INPUT_ACTUATORS,
            "summary": re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0][:140]})
    inv_senses = [{"name": n, "kind": x.kind, "ttl_seconds": x.ttl_seconds, "sensitivity": x.sensitivity,
                   "ambient": x.is_ambient(), "poll_interval": x.poll_interval,
                   "default_on": defaults.get(f"{n}-sense-enabled", "false") == "true",
                   "summary": re.split(r"(?<=[.!?])\s", x.schema["function"].get("description", ""), maxsplit=1)[0][:140]}
                  for n, x in sorted(found.items())]
    import importlib
    post = {}
    for t in inv_tools:
        if t["role"] == "actuator" and t["module"] not in post:
            try:
                from shani_chronoa import verification
                post[t["module"]] = verification.post_condition_for(f"shani_chronoa.skills.{t['module']}") is not None
            except Exception:  # noqa: BLE001
                post[t["module"]] = False
    for t in inv_tools:
        t["post_condition"] = post.get(t["module"], False) if t["role"] == "actuator" else None
    # Two paths here went stale with the same renames that broke the testbed
    # slot-test: `app.py` became the package `app/` (application.py inside it)
    # and `triggers.py` became the package `triggers/` (EVENT_TYPES and the
    # per-event documentation are in `triggers/common.py`). Both were read as
    # plain files, so a current checkout died with FileNotFoundError before
    # writing a single row - a tool whose whole job is to inventory a tree that
    # moves was itself pinned to one layout.
    app_src = _read_source("app", ("application", "app"))
    gactions = sorted(set(re.findall(r'SimpleAction\.new(?:_stateful)?\(\s*"([a-z-]+)"', app_src)))
    percept_sense_note = "any sense; a rule matches a percept's text and runs one whitelisted actuator"
    questions = dict(re.findall(r"^#\s{3}(\w+)\s+(did .+\?)$", _read_source("triggers", ("common", "events", "triggers")), re.M))
    return {"tools": inv_tools, "senses": inv_senses,
            "events": [{"type": e, "question": questions.get(e, "")} for e in sorted(triggers.EVENT_TYPES)],
            "memory_kinds": sorted(senses.MEMORY_KINDS),
            "percept_rules": percept_sense_note,
            "entry_points": {"gactions": gactions,
                             "launchers": sorted(p.name for p in (HERE / "usr/bin").glob("shani-chronoa*")),
                             "mcp_tools": len(inv_tools),
                             "user_dropins": ["~/.config/shani-chronoa/skills/", "~/.config/shani-chronoa/senses/"]},
            "attachment_kinds": ["image", "audio", "video", "text", "other mime types (e.g. application/pdf)"]}


def skill_text() -> str:
    """Every skill/sense source, for finding which interfaces (D-Bus names, typelibs) are used."""
    return "\n".join(f.read_text(errors="replace") for d in ("skills", "senses", "")
                     for f in (SKILLS_DIR / d).glob("*.py"))


# --- desktop apps & interfaces ---------------------------------------------

def _missing_python_imports() -> "list[dict]":
    """Top-level modules Chronoa imports that this Python cannot find (an optional feature off)."""
    import ast
    import importlib.util
    seen = {}
    for f in SKILLS_DIR.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):  # real import statements only - a docstring saying "import the" is not one
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
            for full in names:
                mod = full.split(".")[0]
                if mod not in seen and mod not in sys.builtin_module_names and mod != "shani_chronoa":
                    seen[mod] = f.stem
    out = []
    for mod, where in sorted(seen.items()):
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            out.append({"module": mod, "first_used_in": where})
    return out


def broken_references(files: dict) -> "list[dict]":
    """Launchers and units whose program is not there: a menu entry or a unit that cannot start."""
    out = []
    for f in sorted(Path("/usr/share/applications").glob("*.desktop")):
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        try:
            cp.read(f, encoding="utf-8")
            entry = cp["Desktop Entry"]
            exe = (entry.get("TryExec") or entry.get("Exec", "")).split()
        except (configparser.Error, KeyError, UnicodeDecodeError):
            continue
        if entry.get("NoDisplay", "false") == "true" or entry.get("Hidden", "false") == "true":
            continue  # a hidden, D-Bus-activated helper (kiod6) is not a menu launcher
        prog = exe[0] if exe else ""
        if prog in ("env", "/usr/bin/env") and len(exe) > 1:
            prog = next((x for x in exe[1:] if "=" not in x), "")
        if prog and not prog.startswith("flatpak") and not (shutil.which(prog) or os.path.exists(prog)):
            out.append({"kind": "launcher", "file": str(f), "program": prog})
    owner = {p: pkg for pkg, ps in files.items() for p in ps}
    for pkg, paths in files.items():
        for p in paths:
            if not re.match(r"^/usr/lib/systemd/(system|user)/[^/]+\.service$", p):
                continue
            try:
                text = Path(p).read_text(errors="replace")
            except OSError:
                continue
            if re.search(r"^Condition\w*=.*initrd-release|^DefaultDependencies=no.*\n(?:.*\n)*?.*initrd", text, re.M) \
                    or "/etc/initrd-release" in text:
                continue  # runs inside the initramfs, where dracut provides the program
            for m in re.finditer(r"^ExecStart=[-@:+!]*(\S+)", text, re.M):
                prog = m.group(1)
                if prog.startswith("/") and not os.path.exists(prog):
                    out.append({"kind": "unit", "file": p, "program": prog, "package": owner.get(p, pkg)})
                    break
    return out


def desktop_apps() -> "tuple[dict, list]":
    """command -> app name, and the list of apps (system .desktop files plus Flatpak)."""
    by_cmd, apps = {}, []
    for f in sorted(Path("/usr/share/applications").glob("*.desktop")):
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        try:
            cp.read(f, encoding="utf-8")
            e = cp["Desktop Entry"]
        except (configparser.Error, KeyError, UnicodeDecodeError):
            continue
        if e.get("NoDisplay", "false") == "true" or e.get("Type", "Application") != "Application":
            continue
        exe = os.path.basename((e.get("Exec", "").split() or [""])[0])
        app = {"name": e.get("Name", f.stem), "id": f.stem, "command": exe,
               "categories": e.get("Categories", "").strip(";"), "source": "system"}
        apps.append(app)
        by_cmd.setdefault(exe, app["name"])
    for line in run(["flatpak", "list", "--app", "--columns=application,name"]).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            apps.append({"name": parts[1], "id": parts[0], "command": f"flatpak run {parts[0]}",
                         "categories": "", "source": "flatpak"})
    return by_cmd, apps


def interfaces(files: dict, used: str) -> dict:
    """D-Bus services, typelibs, Python modules, systemd units and libraries, by package."""
    owner = {p: pkg for pkg, ps in files.items() for p in ps}
    dbus = []
    for bus, d in (("system", "/usr/share/dbus-1/system-services"), ("session", "/usr/share/dbus-1/services")):
        for f in sorted(Path(d).glob("*.service")):
            m = re.search(r"^Name=(\S+)", f.read_text(errors="replace"), re.M)
            name = m.group(1) if m else f.stem
            dbus.append({"name": name, "bus": bus, "package": owner.get(str(f), "?"), "used": name in used})
    typelibs = [{"name": f.stem, "package": owner.get(str(f), "?"),
                 "used": bool(re.search(rf"""require_version\(\s*["']{re.escape(f.stem.split("-")[0])}["']""", used))}
                for f in sorted(Path("/usr/lib/girepository-1.0").glob("*.typelib"))]
    pymods, units, libs = collections.defaultdict(set), [], collections.defaultdict(list)
    for pkg, paths in files.items():
        for p in paths:
            m = re.match(r"^/usr/lib/python3[.\d]*/site-packages/([A-Za-z_]\w*)(?:/__init__\.py|\.py|\..*\.so)$", p)
            if m:
                pymods[pkg].add(m.group(1))
            m = re.match(r"^/usr/lib/systemd/(system|user)/([^/]+\.(?:service|timer|socket|path))$", p)
            if m and "@" not in m.group(2):
                units.append({"name": m.group(2), "scope": m.group(1), "package": pkg})
            if re.search(r"^/usr/lib/[^/]+\.so(\.\d+)*$", p):
                libs[pkg].append(os.path.basename(p))
    return {
        "dbus": dbus, "typelibs": typelibs,
        "python": [{"package": k, "modules": sorted(v),
                    "used": any(re.search(rf"^\s*(?:import|from) {m}\b", used, re.M) for m in v)}
                   for k, v in sorted(pymods.items())],
        "units": units,
        "libraries": [{"package": p, "count": len(v), "examples": sorted(v)[:4],
                       "python_binding": next((q for q in (f"python-{p}", f"python-{p.removeprefix('lib')}")
                                               if q in files), "")}
                      for p, v in sorted(libs.items(), key=lambda kv: -len(kv[1]))],
    }


def _ini(path: Path, section: str) -> dict:
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
        return dict(cp[section])
    except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
        return {}


#: event source -> (how it is observed, the commands that carry it, the trigger type covering it)
_EVENT_SOURCES = [
    ("D-Bus signals", "busctl monitor / gdbus monitor", ("busctl", "gdbus"), ""),
    ("USB device add/remove", "/sys/bus/usb/devices, udevadm monitor", ("udevadm",), "usbplug"),
    ("journal entries as they arrive", "journalctl -f -o json", ("journalctl",), ""),
    ("NetworkManager state", "nmcli", ("nmcli",), "netstate"),
    ("battery / power changes", "/sys/class/power_supply, upower --monitor", ("upower",), "powerstate"),
    ("logind: lock, unlock, idle", "loginctl show-session", ("loginctl",), "screenlock"),
    ("logind: suspend / resume", "org.freedesktop.login1 PrepareForSleep", ("loginctl",), ""),
    ("PipeWire graph changes", "pw-mon", ("pw-mon",), ""),
    ("file changes", "polling (fswatch), inotifywait -m", ("inotifywait",), "fswatch"),
    ("calendar / time schedule", "local clock", ("date",), "schedule"),
    ("Bluetooth devices", "bluetoothctl devices Connected", ("bluetoothctl",), "btconnect"),
    ("systemd unit health", "systemctl show", ("systemctl",), "unithealth"),
    ("container runs", "docker / podman inspect", ("podman", "docker"), "containerrun"),
]


def os_surfaces(files: dict, used: str, commands: dict, event_types=()) -> dict:
    """What the OS offers beyond commands, each mapped to the Chronoa surfaces it could feed."""
    owner = {p: pkg for pkg, ps in files.items() for p in ps}
    out = {}
    # GSettings: every key is a readable sense and a writable, verifiable actuator.
    gs = []
    for f in sorted(Path("/usr/share/glib-2.0/schemas").glob("*.gschema.xml")):
        text = f.read_text(errors="replace")
        for sid, body in re.findall(r'<schema\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</schema>', text, re.S):
            keys = re.findall(r'<key\b[^>]*\bname="([^"]+)"', body)
            if keys:
                gs.append({"schema": sid, "keys": len(keys), "examples": keys[:5], "package": owner.get(str(f), "?"),
                           "used": sid in used, "feeds": ["sense", "actuator", "verifier"]})
    out["gsettings"] = gs
    # Polkit: privileged actions, and whether an active local user needs a password.
    pk = []
    for f in sorted(Path("/usr/share/polkit-1/actions").glob("*.policy")):
        text = f.read_text(errors="replace")
        for aid, body in re.findall(r'<action id="([^"]+)">(.*?)</action>', text, re.S):
            desc = re.search(r"<description>([^<]*)</description>", body)
            active = re.search(r"<allow_active>([^<]*)</allow_active>", body)
            pk.append({"action": aid, "description": desc.group(1).strip() if desc else "",
                       "allow_active": active.group(1).strip() if active else "?",
                       "package": owner.get(str(f), "?"), "used": aid in used,
                       "feeds": ["actuator"] if (active and active.group(1).strip() == "yes") else ["actuator (needs auth)"]})
    out["polkit"] = pk
    # sysfs: kernel-direct readings, no binary that can be missing.
    sysfs = []
    for c in sorted(Path("/sys/class").iterdir()) if Path("/sys/class").is_dir() else []:
        try:
            n = len(list(c.iterdir()))
        except OSError:
            continue
        if n:
            writable = c.name in ("backlight", "leds", "rfkill")
            sysfs.append({"class": c.name, "devices": n, "used": f"/sys/class/{c.name}" in used,
                          "feeds": ["sense", "trigger"] + (["actuator"] if writable else [])})
    out["sysfs"] = sysfs
    # Varlink: systemd's JSON-native IPC.
    out["varlink"] = [{"service": f.name, "used": f.name in used, "feeds": ["sense", "skill"]}
                      for d in ("/run/systemd", "/run/varlink/registry")
                      for f in sorted(Path(d).glob("io.*")) if Path(d).is_dir()]
    # XDG desktop portals.
    portals = []
    for f in sorted(Path("/usr/share/xdg-desktop-portal/portals").glob("*.portal")):
        ifaces = _ini(f, "portal").get("interfaces", "")
        for i in filter(None, ifaces.split(";")):
            portals.append({"interface": i.strip(), "backend": f.stem, "used": i.strip() in used,
                            "feeds": ["sense", "actuator"]})
    out["portals"] = portals
    # Event sources the trigger engine could gain as new event types.
    out["event_sources"] = [{"source": a, "via": b, "available": any(c in commands for c in cs),
                             "event_type": t if t in event_types else "", "used": t in event_types,
                             "feeds": ["trigger"]} for a, b, cs, t in _EVENT_SOURCES]
    # Desktop entry points Chronoa could register.
    ep = []
    for f in sorted(Path("/usr/share/gnome-shell/search-providers").glob("*.ini")):
        ep.append({"kind": "GNOME Shell search provider", "name": f.stem, "used": "chronoa" in f.stem.lower()})
    for d in ("/usr/share/kio/servicemenus", "/usr/share/kservices5/ServiceMenus"):
        for f in sorted(Path(d).glob("*.desktop")):
            ep.append({"kind": "KDE file-manager service menu", "name": f.stem, "used": "chronoa" in f.stem.lower()})
    for f in sorted(Path("/usr/share/nautilus-python/extensions").glob("*.py")):
        ep.append({"kind": "Nautilus extension", "name": f.stem, "used": "chronoa" in f.stem.lower()})
    for f in sorted(Path("/etc/xdg/autostart").glob("*.desktop")):
        ep.append({"kind": "autostart", "name": f.stem, "used": "chronoa" in f.stem.lower()})
    mime = collections.Counter()
    for f in Path("/usr/share/applications").glob("*.desktop"):
        for m in filter(None, _ini(f, "Desktop Entry").get("mimetype", "").split(";")):
            mime[m.split("/")[0]] += 1
    out["entry_points"] = ep
    out["mime_handlers"] = dict(mime.most_common())
    return out


# --- classification --------------------------------------------------------

def category(pkg: str, desc: str) -> str:
    hay = f"{pkg} {desc}".lower()
    return next((name for name, pattern in CATEGORIES if re.search(pattern, hay)), "Other")


def intent(summary: str) -> str:
    """The intent of the summary's first verb: 'print or set the date' is inspect, not change."""
    s, best = summary.lower(), (10 ** 6, "other")
    for name, pattern in INTENTS:
        m = re.search(pattern, s)
        if m and m.start() < best[0]:
            best = (m.start(), name)
    return best[1]


def can_change(summary: str) -> bool:
    """Whether any verb in the summary changes something (date: 'print or SET')."""
    s = summary.lower()
    return any(re.search(p, s) for n, p in INTENTS if n in MUTATING)


def kind(name: str, pkg: str, section: str, summary: str = "") -> str:
    if pkg in GAME_PKGS:
        return "game"
    if INTERACTIVE.fullmatch(name):
        return "interactive"
    if name.endswith("-config") or name.endswith("-config.sh"):
        return "build-helper"
    if re.search(r"\bdaemon\b|\bserver\b|\bgetty\b", summary.lower()) or (
            name.endswith("d") and section in ("8", "") and not summary):
        return "daemon"
    if section.startswith("8"):
        return "admin"
    return "scriptable"


SAFETY_CHANGES = ("writes-new", "mixed", "can-change", "elevates")
_MIXED = re.compile(r"\b(?:query|monitor|show|list|inspect|introspect|get|read|report)\w* (?:and|or|/) "
                    r"(?:\w+ )?(?:control|manipulat|chang|set|modif|manag|configur)|"
                    r"\bmanage(?:ment|r)?\b|\bcontrol (?:the|utility|tool)|\bcommand[ -]?line (?:tool|client|interface|utility)|"
                    r"\bcommandline tool|\bconfiguration tool|\bclient\b|\binterface utility|introspect the bus|"
                    r"\bcontrol and monitor|\bmonitor and control|\btool for\b|\badministration tool|"
                    r"\bcontrol cli\b|\bcli\b\s*$|\bpackage manager")
_LOCAL_WRITE = re.compile(r"\bfile|\bcopy|\bsync|\bdownload|\bmirror|\bdirector")


def safety(path: str, summary: str, verb: str = "") -> str:
    """read-only, writes-new, mixed, can-change or elevates.

    - **elevates**: setuid - it runs with someone else's (usually root's)
      rights. setgid alone is not that: plocate is setgid only to read its
      database, and calling it elevating hid a plain search tool.
    - **mixed**: one tool whose subcommands both read and change - systemctl,
      podman, smartctl, pacman, "query or control". The honest class for a
      manager: a wrapper may expose the reading subcommands freely and must
      gate the rest. Calling these read-only or can-change was where the
      calibration against Chronoa's own modules disagreed most.
    - **writes-new**: converters and archivers create an output file and leave
      the input alone (ffmpeg, magick, tar -c) - an effect to verify, not a
      destructive one.
    - **can-change**: a summary verb that changes existing state.

    Man section 8 says "administration", not "root only" - lsblk, findmnt and
    ip are section 8 and answer an ordinary user - so it does not decide this.
    """
    try:
        if os.stat(path).st_mode & stat.S_ISUID:
            return "elevates"
    except OSError:
        pass
    low = summary.lower()
    if _MIXED.search(low):
        return "mixed"
    if verb in ("convert", "compress") and re.search(r"standard output|\bstdout|\bprint", low):
        return "read-only"  # base64, xxd: the result goes to the terminal, nothing is written
    if verb in ("convert", "compress") or (verb == "create" and "file" in low):
        return "writes-new"
    if verb == "transfer" and not _LOCAL_WRITE.search(low):
        return "read-only"  # ping "sends" packets; nothing local changes (open-world is flagged separately)
    return "can-change" if can_change(summary) else "read-only"


SURFACES = ("sense", "skill", "actuator", "trigger", "memory", "verifier", "attachment", "voice")
_STATE = re.compile(r"system|\bcpu|processor|memory|\bdisk|device|process|network|interface|usage|statistic|"
                    r"status|state|battery|power|sensor|temperatur|kernel|module|block|mount|session|login|"
                    r"\busers?\b|load|uptime|service|\bunit|socket|connection|wireless|bluetooth|usb|pci|"
                    r"hardware|firmware|smart|health|space|quota|lock|clock|time|routing|link|address|"
                    r"container|image|volume|audio|sound|display|monitor|screen|input|printer|queue|"
                    r"firewall|packet|\bgps|\bi2c|\bchip|logged|\bwho\b|wireplumber|pipewire|package")
_WORLD = re.compile(r"\bfile type|determine file|\bocr\b|optical character|content tracker|version control|"
                    r"\brepositor|\bexif|metadata|\bdocument (?:info|properties)")
_PERSONAL = re.compile(r"\buser|login|session|history|password|passphrase|\bkey|secret|location|clipboard|"
                       r"\bmail|contact|calendar|who\b|last log|finger|keyring|credential|token|account")
_OPEN_WORLD = re.compile(r"\burl\b|remote|download|upload|http|\bftp|\bdns\b|resolv|internet|network|"
                         r"\bssh\b|server|host|mirror|\bmail\b|\bsend")
_VOICE = re.compile(r"speech|speak|synthes|\bvoice|record|\bplay(?:back|s|er)?\b|microphone|\bsound|\baudio")
_FACT = re.compile(r"hostname|locale|identit|\buser|version|release|hardware|\bcpu|machine|dmi|bios|firmware|"
                   r"system information|os-release|time ?zone|keyboard|display|monitor|battery|\bdisk|partition")


def fits(r: dict) -> list:
    """Which Chronoa surfaces this command could feed (heuristic, from intent/safety/man page)."""
    out, i = [], r["intent"]
    ro = r["safety"] == "read-only"
    reads = r["safety"] in ("read-only", "mixed")  # a mixed tool's reading subcommands are sense material
    machine = r["category"] not in ("Development", "Text & files")
    if reads and i in ("inspect", "monitor", "check", "search", "control", "other") and machine \
            and _STATE.search((r["summary"] + " " + r["command"]).lower()):
        out.append("sense")
    elif ro and i in ("inspect", "check", "monitor", "other") and r["category"] in (
            "Text & files", "Images & documents", "Development") and _WORLD.search(
            (r["summary"] + " " + r["package_description"]).lower()):
        out.append("sense")  # a sense of the user's world (file type, OCR, a git tree): personal, off by default
    if (ro or r["safety"] == "mixed") and i in ("inspect", "search", "calculate", "process", "check", "monitor",
                                                 "control", "other"):
        out.append("skill")
    elif r["safety"] == "writes-new" and i in ("convert", "compress"):
        out.append("skill")
    if r["safety"] in ("can-change", "mixed", "writes-new") and i in (
            "change", "control", "create", "transfer", "inspect", "convert", "compress", "search", "other"):
        out.append("actuator")
    if i == "monitor" or r["follows"] or (r["json"] and ro and i in ("inspect", "check") and machine):
        out.append("trigger")
    if ro and i == "inspect" and _FACT.search(r["summary"].lower()):
        out.append("memory")
    if ro and i in ("inspect", "check", "search") and r.get("package_has_changer"):
        out.append("verifier")
    if r["category"] in ("Images & documents", "Audio & video", "Text & files") and \
            i in ("convert", "inspect", "compress", "process", "check", "search"):
        out.append("attachment")
    if r["category"] in ("Audio & video", "Accessibility & input") and _VOICE.search(r["summary"].lower()):
        out.append("voice")
    return out


def properties(r: dict) -> dict:
    """What a wrapper must honour: egress, sensitivity, plan-mode preview, ambient polling."""
    summ = (r["summary"] + " " + r["package_description"]).lower()
    open_world = r["category"] == "Network" and bool(_OPEN_WORLD.search(summ)) or r["intent"] == "transfer"
    sensitivity = "personal" if _PERSONAL.search(summ) else "public"
    return {"open_world": open_world, "sensitivity": sensitivity,
            "plan_preview": r["safety"] in SAFETY_CHANGES and r["dry_run"],
            "ambient": "sense" in r["fits"] and not open_world and not r["follows"]}


def _with_subcommands(base: str, subs: list) -> str:
    """Subcommands outrank the one-line summary: reading and changing ones together is `mixed`."""
    if base == "elevates" or not subs:
        return base
    effects = {x["effect"] for x in subs}
    if effects == {"reads", "changes"}:
        return "mixed"
    if effects == {"reads"} and base in ("can-change", "mixed"):
        return "read-only" if len(subs) >= 3 else base
    return base


def score(r: dict) -> int:
    return (3 * r["json"] + 2 * (r["safety"] == "read-only") + 2 * r["explicit"] + 2 * r["shani"]
            + bool(r["app"]) - 2 * (r["safety"] == "elevates") - 2 * r["package_covered"]
            - (r["intent"] == "other"))


# --- calibration: the heuristics scored against what Chronoa already does ---

def calibration(rows: list) -> dict:
    """How far to trust `safety` and the sense fit, measured on Chronoa's own choices.

    Chronoa's modules are labelled data the heuristics never saw: a command
    only an actuator module runs is one that changes things; one only read-only
    skills or senses run is one that reads. Agreement on those is the nearest
    thing this matrix has to an accuracy figure, and the disagreements are the
    examples worth reading before trusting a classification.
    """
    agree = disagree = 0
    by_truth = {"reads": [0, 0], "changes": [0, 0]}
    wrong = []
    for r in rows:
        u = r["used_by"]
        if "actuator" in u and not ({"skill", "sense"} & set(u)):
            truth = "changes"
        elif ({"skill", "sense"} & set(u)) and "actuator" not in u:
            truth = "reads"
        else:
            continue
        guess = "changes" if r["safety"] in SAFETY_CHANGES else "reads"
        ok = r["safety"] == "mixed" or guess == truth
        by_truth[truth][0 if ok else 1] += 1
        if ok:
            agree += 1
        else:
            disagree += 1
            wrong.append({"command": r["command"], "truth": truth, "guess": r["safety"], "summary": r["summary"][:80]})
    sensed = [r for r in rows if "sense" in r["used_by"]]
    return {"safety_agree": agree, "safety_disagree": disagree,
            "safety_accuracy": round(agree / (agree + disagree), 3) if agree + disagree else None,
            "safety_wrong": wrong[:25],
            # Reported apart because the labels are not equally clean: an actuator
            # module also runs read-only helpers (diff before a write, findmnt
            # before a mount), so "changes" truth includes commands that only read.
            "reads_accuracy": round(by_truth["reads"][0] / sum(by_truth["reads"]), 3) if sum(by_truth["reads"]) else None,
            "changes_accuracy": (round(by_truth["changes"][0] / sum(by_truth["changes"]), 3)
                                 if sum(by_truth["changes"]) else None),
            "sense_recall": round(sum("sense" in r["fits"] for r in sensed) / len(sensed), 3) if sensed else None,
            # **Was `sense_missed`, and that name made it read as a list of
            # capabilities Chronoa lacks.** It is the opposite: every entry is a
            # command a Chronoa sense *does* run, where this file's own
            # heuristic failed to say "sense". I read it as a gap list, checked
            # all six against the tree, and found every one already covered -
            # `iptables`/`nft`/`ufw`/`firewall-cmd` by the `firewall` sense,
            # `link` by `wirelesslink`, `card` by `gpu`, `pkexec` by
            # `thermalgrid`. An instrument that reports its own blind spot under
            # a name that says "missed" will be believed over the tree.
            #
            # **So it carries the sense that runs each one**, which is what makes
            # it checkable in a second instead of by hand.
            "sense_classifier_disagreements": [
                {"command": r["command"],
                 "run_by": sorted(set(r.get("chronoa_skills") or [])),
                 # `.get`, not `r[...]`: `calibration()` is handed rows by its
                 # callers and by the tests, and it must not require fields it
                 # does not own. It raised `KeyError: 'category'` on a fixture
                 # that is perfectly valid for everything else this function
                 # reads.
                 "classified_as": "/".join(
                     str(r.get(k, "")) for k in ("category", "intent")).strip("/")}
                for r in sensed if "sense" not in r["fits"]][:25],
            # Recall alone rewards calling everything a sense; the share of all
            # commands so classified is what shows a loosened rule doing that.
            "sense_fit_share": round(sum("sense" in r["fits"] for r in rows) / len(rows), 3) if rows else None}


# --- scaffold: a skill module from one matrix row ----------------------------

def _ident(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "cmd"


def scaffold(row: dict, allow_actuator: bool = False) -> str:
    """Source for a user drop-in skill wrapping `row`'s command.

    The module follows the skills contract (Ollama's function schema, a `run`
    taking the arguments dict, `SKILLS = [...]`) and is safe as generated:
    a `shutil.which` guard that says what to install, a timeout, operands that
    may not start with '-' (so a model cannot smuggle a flag in as a file name),
    only flags the man page documents, and no shell. A command the matrix
    thinks can change things is generated **disabled** - its `run` refuses
    until a person reads it, sets ENABLED and gives it a consent gate - because
    a scaffold must never be the way an unreviewed actuator reaches a model.
    """
    cmd, name = row["command"], _ident(row["command"])
    changes = row["safety"] not in ("read-only", "writes-new")
    read_subs = [x["name"] for x in row.get("subcommands", [])
                 if x["effect"] == "reads" and x["name"] not in ("help", "version")]
    # A mixed tool with documented reading subcommands becomes a read-only skill
    # over exactly those: `systemctl status` without any way to reach `stop`.
    subset = changes and bool(read_subs) and not allow_actuator
    if subset:
        changes = False
    if changes and not allow_actuator:
        raise ValueError(f"{cmd} is classified {row['safety']}; pass --allow-actuator to scaffold it disabled")
    props, flag_map = {}, {}
    if subset:
        props["subcommand"] = {"type": "string", "enum": read_subs[:60],
                               "description": "Which read-only subcommand to run."}
    for opt in row.get("option_list", [])[:20 if subset else 12]:
        if subset and (can_change(opt["desc"]) or re.search(r"--(?:force|now|yes|assume-yes|no-confirm|noconfirm|"
                                                             r"no-block|kill|delete|remove|write)\b|^-y$",
                                                             " ".join(opt["flags"]))):
            continue  # a reading subcommand with a changing flag is not reading any more
        long_ = next((f for f in opt["flags"] if f.startswith("--")), opt["flags"][0])
        key = _ident(long_.lstrip("-"))
        if not key or key in props or key in ("help", "version", "usage"):
            continue
        flag_map[key] = (long_, bool(opt["arg"]))
        if len(flag_map) >= 12:
            break
        props[key] = ({"type": "string", "description": f"{long_} {opt['arg']}: {opt['desc']}"[:160]} if opt["arg"]
                      else {"type": "boolean", "description": f"{long_}: {opt['desc']}"[:160]})
    props["operands"] = {"type": "array", "items": {"type": "string"},
                         "description": f"Positional arguments, as in: {row.get('synopsis') or cmd}"[:160]}
    desc = (row["summary"] or row["package_description"] or cmd).rstrip(".")
    json_note = " Output is JSON where the command supports it." if row.get("json") else ""
    schema = {"type": "function", "function": {
        "name": name, "description": f"{desc[:1].upper()}{desc[1:]} (runs `{cmd}`).{json_note}",
        "parameters": {"type": "object", "properties": props}}}
    pkg = row.get("package") or "?"
    return f'''"""Skill: {desc} - wraps `{cmd}` ({pkg}).

Generated by tools/cli_matrix.py --scaffold from a matrix row; review before use.
Matrix classification: intent={row["intent"]}, safety={row["safety"]}, surfaces={", ".join(row.get("fits", []))}.
{("Only the subcommands the man page documents as reading are offered (" + str(len(read_subs)) + "); the "
  + str(row.get("change_subcommands", 0)) + " that change things are not reachable from here.") if subset else ""}
Drop it into ~/.config/shani-chronoa/skills/ to load it as a user skill.
"""

import shutil
import subprocess

from shani_chronoa.skills import Skill

TOOL = {cmd!r}
ENABLED = {not changes!r}  # {"a command that can change things: read it, gate it, then enable" if changes else "read-only per the matrix"}
TIMEOUT = 30
MAX_OUTPUT = 4000
FLAGS = {flag_map!r}
SUBCOMMANDS = {tuple(read_subs[:60]) if subset else ()!r}  # the allowlist; empty = the command takes none
QUIET = {{"PAGER": "cat", "SYSTEMD_PAGER": "cat", "GIT_PAGER": "cat", "LESS": "FRX", "SYSTEMD_COLORS": "0"}}

_SCHEMA = {json.dumps(schema, indent=4)}


def build_argv(arguments: dict) -> list:
    argv = [TOOL]
    if SUBCOMMANDS:
        sub = arguments.get("subcommand")
        if sub not in SUBCOMMANDS:
            raise ValueError(f"subcommand must be one of the read-only ones, not {{sub!r}}")
        argv.append(sub)
    for key, (flag, takes_value) in FLAGS.items():
        value = arguments.get(key)
        if takes_value and isinstance(value, str) and value.strip():
            if value.startswith("-"):
                raise ValueError(f"{{key}} may not start with '-'")
            argv += [flag, value]
        elif not takes_value and value is True:
            argv.append(flag)
    for operand in arguments.get("operands") or []:
        if not isinstance(operand, str) or not operand or operand.startswith("-"):
            raise ValueError(f"operand {{operand!r}} is empty or looks like a flag")
        argv.append(operand)
    return argv


def _run(arguments: dict) -> str:
    if not ENABLED:
        return f"{{TOOL}} can change things, so this generated skill is disabled until someone reviews it."
    if shutil.which(TOOL) is None:
        return f"{{TOOL}} is not installed (it comes from the {pkg} package)."
    try:
        argv = build_argv(arguments)
        import os
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, check=False,
                              stdin=subprocess.DEVNULL, env={{**os.environ, **QUIET}})
    except ValueError as e:
        return f"Not run: {{e}}."
    except subprocess.TimeoutExpired:
        return f"{{TOOL}} did not finish within {{TIMEOUT}}s."
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"{{TOOL}} exited {{proc.returncode}}: {{out[:500]}}"
    return out[:MAX_OUTPUT] + ("\\n... (truncated)" if len(out) > MAX_OUTPUT else "")


SKILLS = [Skill(name={name!r}, schema=_SCHEMA, run=_run)]
'''


# --- diff: two matrix runs, what changed ----------------------------------

def diff(old: dict, new: dict) -> str:
    """Markdown of what changed between two matrix JSONs (two images, or before/after an update)."""
    oc = {r["command"]: r for r in old["commands"]}
    nc = {r["command"]: r for r in new["commands"]}
    L = [f"# Matrix diff: {old.get('profile') or old.get('host')} {old.get('generated')} -> "
         f"{new.get('profile') or new.get('host')} {new.get('generated')}", ""]
    for label, names in (("Commands added", sorted(set(nc) - set(oc))), ("Commands removed", sorted(set(oc) - set(nc)))):
        L += [f"## {label} ({len(names)})", "", ", ".join(f"`{n}`" for n in names[:300]) or "*(none)*", ""]
    def inv(d, k):
        return {x.get("name") or x.get("type"): x for x in d.get("chronoa", {}).get(k, [])}
    for k in ("tools", "senses", "events"):
        a, b = inv(old, k), inv(new, k)
        L += [f"## Chronoa {k}: {len(a)} -> {len(b)}", "",
              "Added: " + (", ".join(f"`{n}`" for n in sorted(set(b) - set(a))) or "none"),
              "Removed: " + (", ".join(f"`{n}`" for n in sorted(set(a) - set(b))) or "none"), ""]
    ot, nt = inv(old, "tools"), inv(new, "tools")
    broke = sorted(n for n, t in nt.items() if t.get("missing") and not ot.get(n, {}).get("missing"))
    fixed = sorted(n for n, t in ot.items() if t.get("missing") and n in nt and not nt[n].get("missing"))
    verified = sorted(n for n, t in nt.items() if t.get("post_condition") and not ot.get(n, {}).get("post_condition"))
    L += ["## Chronoa tools", "", "Now missing a command: " + (", ".join(f"`{n}`" for n in broke) or "none"),
          "No longer missing one: " + (", ".join(f"`{n}`" for n in fixed) or "none"),
          "Newly verified: " + (", ".join(f"`{n}`" for n in verified) or "none"), ""]
    os_, ns = old.get("summary", {}), new.get("summary", {})
    L += ["## Summary", "", "| | Before | After |", "|---|---:|---:|"]
    L += [f"| {k} | {os_.get(k)} | {ns.get(k)} |" for k in ("commands", "packages", "with_man", "with_json", "used",
                                                         "candidates", "dbus", "apps")]
    return "\n".join(L) + "\n"


# --- packaging audit: the matrices against shani-pkgbuilds and the image profiles ---

#: command -> the Arch package that ships it, for commands Chronoa runs that an image lacks
COMMAND_PACKAGES = {
    "xdotool": "xdotool", "wtype": "wtype", "dotool": "dotool (AUR)", "ydotool": "ydotool", "xclip": "xclip",
    "xsel": "xsel", "wl-copy": "wl-clipboard", "checkupdates": "pacman-contrib", "tldr": "tealdeer",
    "trans": "translate-shell", "ufw": "ufw", "kscreen-doctor": "libkscreen", "xset": "xorg-xset",
    "kreadconfig5": "kconfig5", "kreadconfig6": "kconfig", "plasma-lookandfeeltool": "plasma-workspace",
    "gpspipe": "gpsd", "plocate": "plocate", "pdftotext": "poppler", "magick": "imagemagick",
    "ddcutil": "ddcutil", "liquidctl": "liquidctl", "tesseract": "tesseract",
}
_DESKTOP_SPECIFIC = re.compile(r"gnome|gtk|mutter|nautilus|gvfs|evolution|tracker|localsearch|gdm|kde|plasma|"
                               r"\bqt|qt\d|kf6|kf5|kwin|kio|dolphin|konsole|breeze|kwallet|akonadi|baloo|sddm|"
                               r"ksystem|kscreen|phonon|polkit-kde|xdg-desktop-portal-|spectacle|ark\b|"
                               r"desktop-gnome|desktop-plasma|libadwaita|adwaita|webkit|appstream-qt|kdeconnect|gsconnect")


def _bash_array(text: str, name: str) -> "list[str]":
    """Every element of every `name=(...)` in a PKGBUILD - quote-aware, comments stripped, versions cut.

    A regex up to the first ')' is wrong here: optdepends descriptions contain
    parentheses ("geoclue: ... (GNOME's location service)"), so the array is
    scanned character by character, honouring quotes and '#' comments.
    """
    out = []
    for m in re.finditer(rf"(?m)^\s*{name}=\(", text):
        i, items, cur, quote = m.end(), [], "", None
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    quote = None
                else:
                    cur += ch
            elif ch in "'\"":
                quote = ch
            elif ch == "#":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            elif ch == ")":
                break
            elif ch.isspace():
                if cur:
                    items.append(cur)
                cur = ""
            else:
                cur += ch
            i += 1
        if cur:
            items.append(cur)
        out += [re.split(r"[<>=:\s]", w.strip(), maxsplit=1)[0] for w in items if w.strip()]
    return [w for w in out if w]


def read_pkgbuilds(root: Path) -> dict:
    """pkgname -> {dir, depends, optdepends} for every PKGBUILD under root (split packages included)."""
    out = {}
    for pb in sorted(root.glob("*/PKGBUILD")):
        text = pb.read_text(errors="replace")
        names = _bash_array(text, "pkgname") or [pb.parent.name]
        deps, opt = _bash_array(text, "depends"), _bash_array(text, "optdepends")
        make = _bash_array(text, "makedepends")
        ver = re.search(r"(?m)^pkgver=(\S+)", text)
        rel = re.search(r"(?m)^pkgrel=(\S+)", text)
        epoch = re.search(r"(?m)^epoch=(\S+)", text)
        version = (f"{epoch.group(1)}:" if epoch else "") + (f"{ver.group(1)}-{rel.group(1)}" if ver and rel else "")
        for n in names:
            if "$" in n:
                continue
            out[n] = {"dir": pb.parent.name, "depends": sorted(set(deps)), "optdepends": sorted(set(opt)),
                      "makedepends": sorted(set(make)), "version": version.strip("'\"")}
    return out


def read_profile(root: Path, profile: str) -> "dict[str, list[str]]":
    """list file -> packages, for shared/ and the profile's own Packages-* files."""
    out = {}
    for prof in ("shared", profile):
        for f in sorted((root / prof).glob("Packages-*")):
            pkgs = [l.split("#", 1)[0].strip() for l in f.read_text(errors="replace").splitlines()]
            out[f"{prof}/{f.name}"] = [p for p in pkgs if p]
    return out


def audit_packaging(matrices: "list[dict]", pkgbuilds: Path, profiles: Path) -> str:
    """Markdown: what the matrices say should change in shani-pkgbuilds and the image profiles."""
    builds = read_pkgbuilds(pkgbuilds) if pkgbuilds.is_dir() else {}
    chronoa = builds.get("shani-chronoa", {"depends": [], "optdepends": []})
    declared = set(chronoa["depends"]) | set(chronoa["optdepends"])
    base = {"systemd", "coreutils", "util-linux", "glibc", "bash", "procps-ng", "findutils", "grep", "sed",
            "gawk", "tar", "gzip", "iproute2", "kmod", "shadow", "file", "diffutils", "pacman", "python"}
    L = ["# Packaging audit from the capability matrices", "",
         "Each finding is measured on the installed images, not read from the lists: "
         + ", ".join(f"**{m.get('profile') or m.get('host')}** ({len(m.get('packages', {}))} packages, "
                     f"{m['generated']})" for m in matrices) + ".", ""]
    # what an image satisfies: package names plus everything they Provide (sh, java-runtime, ...)
    installed = {m.get("profile") or m.get("host"): set(m.get("packages", {}))
                 | {x for v in m.get("packages", {}).values() for x in v.get("provides", [])} for m in matrices}

    # 1. image profiles: listed but not installed, and meta-package deps not installed
    L += ["## 1. Image profiles", ""]
    for m in matrices:
        prof = m.get("profile") or "?"
        have = installed.get(prof, set())
        lists = read_profile(profiles, prof) if profiles.is_dir() else {}
        listed = [p for ps in lists.values() for p in ps]
        absent = sorted(p for p in listed if p not in have)
        dup = sorted({p for p in listed if listed.count(p) > 1})
        L += [f"### {prof}", "",
              f"- {len(listed)} packages listed in {', '.join(lists) or 'no lists'}; "
              f"**{len(absent)} not installed**: " + (", ".join(f"`{p}`" for p in absent) or "none") + ".",
              f"- Listed twice: " + (", ".join(f"`{p}`" for p in dup) or "none") + "."]
        missing_deps = []
        for meta in listed:
            for dep in builds.get(meta, {}).get("depends", []):
                if dep not in have:
                    missing_deps.append((meta, dep))
        if missing_deps:
            L.append("- Meta-package dependencies **not installed** (renamed, provided by another name, "
                     "or the meta package is out of date): "
                     + ", ".join(f"`{d}` (from {meta})" for meta, d in missing_deps[:40]) + ".")
        L.append("")
    # 1b. is each image as new as its profile's lists?
    for m in matrices:
        prof = m.get("profile") or "?"
        ver = m.get("image_version") or ""
        last = run(["git", "-C", str(profiles), "log", "-1", "--format=%cs", "--", f"{prof}/", "shared/"]).strip()
        if ver and last and ver < last.replace("-", ""):
            L += [f"> **{prof}: the image ({ver}) is older than the last change to its lists ({last}).** "
                  "Its 'not installed' lines above measure a stale image, not the lists - rebuild before acting "
                  "on them.", ""]
    # 2a. drift between the current lists, which does not depend on image age
    profs = [m.get("profile") for m in matrices if m.get("profile")]
    if len(profs) >= 2 and profiles.is_dir():
        la = {p for ps in read_profile(profiles, profs[0]).values() for p in ps}
        lb = {p for ps in read_profile(profiles, profs[1]).values() for p in ps}
        neutral = lambda xs: sorted(x for x in xs if not _DESKTOP_SPECIFIC.search(x))  # noqa: E731
        L += ["## 2. Drift between the desktop profiles", "",
              "### In the lists (today's checkout)", "",
              f"- Only in **{profs[0]}**'s lists, not desktop-specific: "
              + (", ".join(f"`{x}`" for x in neutral(la - lb)) or "none"),
              f"- Only in **{profs[1]}**'s lists, not desktop-specific: "
              + (", ".join(f"`{x}`" for x in neutral(lb - la)) or "none"), ""]
    # 2b. drift between images in desktop-neutral packages
    if len(installed) >= 2:
        (a, _), (b, _) = list(installed.items())[:2]
        sa, sb = set(matrices[0].get("packages", {})), set(matrices[1].get("packages", {}))
        def neutral(names, pk):
            return sorted(n for n in names if not _DESKTOP_SPECIFIC.search(f"{n} {pk.get(n, {}).get('description', '')}".lower()))
        pa = matrices[0].get("packages", {}); pb_ = matrices[1].get("packages", {})
        only_a, only_b = neutral(sa - sb, pa), neutral(sb - sa, pb_)
        def why(n, pk):
            req = pk.get(n, {}).get("required_by", [])
            return f"`{n}`" + (f" (via {', '.join(req[:2])})" if req else " (explicit)" if pk.get(n, {}).get("explicit") else "")
        def meta_level(names, pk):
            return [n for n in names if pk.get(n, {}).get("explicit")
                    or any(r.startswith("shani-") for r in pk.get(n, {}).get("required_by", []))]
        only_a, only_b = meta_level(only_a, pa), meta_level(only_b, pb_)
        L += ["### On the installed images", "",
              "Not desktop-specific, installed on only one image, and pulled in directly by a `shani-*` meta "
              "package or the profile itself (libraries further down are left out as noise):", "",
              f"- **Only on {a}** ({len(only_a)}): " + ", ".join(why(n, pa) for n in only_a[:80]),
              f"- **Only on {b}** ({len(only_b)}): " + ", ".join(why(n, pb_) for n in only_b[:80]), ""]
    # 3. shani-chronoa PKGBUILD
    L += ["## 3. shani-chronoa's PKGBUILD", ""]
    for m in matrices:
        prof = m.get("profile") or "?"
        owner = {r["command"]: r["package"] for r in m["commands"]}
        runs = {}
        for x in m.get("chronoa", {}).get("tools", []) + m.get("chronoa", {}).get("senses", []):
            for c in x.get("needs", []):
                runs.setdefault(c, set()).add(x["name"])
        undeclared = sorted({(owner[c], c) for c in runs if c in owner and owner[c] not in declared
                             and owner[c] not in base and owner[c] != "shani-chronoa"})
        missing = sorted(c for c in runs if c not in owner)
        have = installed.get(prof, set())
        dep_missing = sorted(d for d in chronoa["depends"] if d not in have)
        opt_missing = sorted(d for d in chronoa["optdepends"] if d not in have)
        L += [f"### on {prof}", "",
              "- **Used but not declared** (works here only because the image happens to ship it): "
              + (", ".join(f"`{p}` for `{c}` ({', '.join(sorted(runs[c])[:3])})" for p, c in undeclared) or "none") + ".",
              "- **Run but not installed**: "
              + (", ".join(f"`{c}` -> {COMMAND_PACKAGES.get(c, '?')} ({', '.join(sorted(runs[c])[:3])})"
                           for c in missing) or "none") + ".",
              "- **A hard `depends` the image does not have** (the image was built from an older package, or "
              "the dependency never resolved): " + (", ".join(f"`{d}`" for d in dep_missing) or "none") + ".",
              "- Optional (`optdepends`) not installed - expected, listed for completeness: "
              + (", ".join(f"`{d}`" for d in opt_missing) or "none") + ".", ""]
    # 4. shani-pkgbuilds packages on no audited image
    if builds:
        build_only = {d for b in builds.values() for d in b.get("makedepends", [])}
        on_none = sorted(n for n in builds if not any(n in s_ for s_ in installed.values()) and n not in build_only)
        L += ["## 4. shani-pkgbuilds packages installed on none of these images", "",
              ", ".join(f"`{n}`" for n in on_none) or "none", "",
              "Expected for fleet/insights (never in an image) and other profiles' packages; anything else is "
              "either a profile omission or a package nothing uses.", ""]
    # 5. Shanios' own commands without man pages; files on PATH no package owns
    ours = set(builds)  # Shanios' own packages, from the PKGBUILDs - an image slot has no [shani] sync db
    for m in matrices[:1]:
        undoc = sorted({f"{r['command']} ({r['package']})" for r in m["commands"]
                        if (r.get("shani") or r["package"] in ours) and not r["has_man"]})
        orphans = sorted(r["path"] for r in m["commands"] if r["package"] == "?")
        L += ["## 5. Documentation and ownership", "",
              f"- **Shanios' own commands with no man page** ({len(undoc)}) - `whatis`, `man` and this matrix "
              "cannot describe them, so neither can Chronoa: " + ", ".join(f"`{c}`" for c in undoc[:60]),
              f"- **Files on PATH no package owns** ({len(orphans)}) - created by an install script or the image "
              "build, so pacman cannot verify or remove them: " + ", ".join(f"`{p}`" for p in orphans[:40]), ""]
    # 6. the distro beyond packaging
    L += distro_findings(matrices, builds)
    return "\n".join(L) + "\n"


def _vercmp(a: str, b: str) -> int:
    """pacman's vercmp when it is here, else a plain comparison of the number runs."""
    out = run(["vercmp", a, b]).strip()
    if re.fullmatch(r"-?\d+", out):
        return int(out)
    key = lambda v: [int(x) for x in re.findall(r"\d+", v)]  # noqa: E731
    return (key(a) > key(b)) - (key(a) < key(b))


def why_installed(pk: dict, target: str) -> "list[str]":
    """The shortest chain from an explicitly installed package down to `target` (nix why-depends)."""
    from collections import deque
    if pk.get(target, {}).get("explicit"):
        return [target]
    queue, parent = deque([target]), {target: None}
    while queue:
        cur = queue.popleft()
        for up in pk.get(cur, {}).get("required_by", []):
            if up in parent:
                continue
            parent[up] = cur
            if pk.get(up, {}).get("explicit"):
                chain = [up]
                while chain[-1] != target:
                    chain.append(parent[chain[-1]])
                return chain
            queue.append(up)
    return []


def footprint(pk: dict) -> "list[dict]":
    """Per explicitly installed package: its closure, and what only it pulls in (its exclusive cost).

    Removing a package frees exactly its exclusive closure - what no other
    explicit package also needs. For an image-based distro that is the number a
    package-list decision should be made with.
    """
    provider = {}
    for n, v in pk.items():
        provider.setdefault(n, n)
        for x in v.get("provides", []):
            provider.setdefault(x, n)

    def closure(root: str) -> set:
        seen, stack = {root}, [root]
        while stack:
            for d in pk.get(stack.pop(), {}).get("depends", []):
                q = provider.get(d)
                if q and q not in seen:
                    seen.add(q)
                    stack.append(q)
        return seen
    roots = [n for n, v in pk.items() if v.get("explicit")]
    closures = {r: closure(r) for r in roots}
    count = collections.Counter(p for c in closures.values() for p in c)
    out = []
    for r, c in closures.items():
        only = {p for p in c if count[p] == 1}
        out.append({"package": r, "closure": len(c), "exclusive": len(only),
                    "exclusive_bytes": sum(pk.get(p, {}).get("size", 0) for p in only),
                    "biggest": sorted(only, key=lambda p: -pk.get(p, {}).get("size", 0))[:4]})
    return sorted(out, key=lambda x: -x["exclusive_bytes"])


def _mib(n: int) -> str:
    return f"{n / 1024 ** 2:,.0f} MiB" if n >= 1024 ** 2 else f"{n / 1024:,.0f} KiB"


def distro_findings(matrices: "list[dict]", builds: dict) -> "list[str]":
    """Release lag, hygiene, attack surface and documentation, per image."""
    L = ["## 6. The distro beyond packaging", ""]
    for m in matrices:
        prof = m.get("profile") or m.get("host")
        pk = m.get("packages", {})
        lag = []
        for name, b in builds.items():
            have = pk.get(name, {}).get("version")
            if have and b.get("version") and "$" not in b["version"] and _vercmp(have, b["version"]) < 0:
                lag.append(f"`{name}` {have} < {b['version']}")
        orphans = sorted(n for n, v in pk.items() if not v.get("explicit") and not v.get("required_by"))
        setuid = sorted(f"`{r['command']}` ({r['package']})" for r in m["commands"] if r["safety"] == "elevates")
        pol = [x for x in m.get("os_surfaces", {}).get("polkit", []) if x.get("allow_active") == "yes"]
        risky = [x for x in pol if not re.search(r"read|info|\bget|query|list|inhibit|ask|claim|verify|access",
                                                  x["action"].lower() + " " + x.get("description", "").lower())]
        nodoc = collections.Counter(r["package"] for r in m["commands"] if not r["has_man"] and r["kind"] == "scriptable")
        total = sum(v.get("size", 0) for v in pk.values())
        fp = footprint(pk)
        units = m.get("enabled_units", [])
        chosen = sorted({u["unit"] for u in units if not u["vendor"] and u["scope"] == "system"})
        now = max((v.get("built", 0) for v in pk.values()), default=0)
        ours_old = sorted(((now - v["built"]) // 86400, n) for n, v in pk.items()
                          if n in builds and v.get("built") and now - v["built"] > 180 * 86400)
        oldest = sorted(((now - v["built"]) // 86400, n) for n, v in pk.items() if v.get("built"))[::-1][:10]
        L += [f"### {prof} ({m.get('image_version') or 'unknown build'})", "",
              f"- **Installed size**: {_mib(total)} in {len(pk)} packages. **Largest exclusive footprints** "
              "(what removing that one explicit package would free): "
              + ", ".join(f"`{x['package']}` {_mib(x['exclusive_bytes'])} ({x['exclusive']} pkgs; "
                          f"e.g. {', '.join(x['biggest'][:2])})" for x in fp[:10]),
              f"- **Enabled at boot by the image itself** ({len(chosen)} system units enabled in /etc, beyond vendor "
              f"defaults; {len(units)} enablement links in all): " + ", ".join(f"`{u}`" for u in chosen[:50]),
              f"- **Shanios packages not rebuilt in 180 days** ({len(ours_old)}): "
              + (", ".join(f"`{n}` ({d} d)" for d, n in ours_old) or "none")
              + f"; oldest builds overall: " + ", ".join(f"`{n}` ({d} d)" for d, n in oldest),
              f"- **Shanios packages older on the image than in shani-pkgbuilds** ({len(lag)}) - the image lags "
              "the repo; a rebuild (or the repo publish it is waiting on) ships these: " + (", ".join(lag) or "none"),
              f"- **Orphaned packages** ({len(orphans)}) - installed, not explicitly wanted, required by nothing "
              "(left by a removed dependency; `pacman -Qdtq`): " + (", ".join(f"`{o}`" for o in orphans[:40]) or "none"),
              f"- **setuid commands** ({len(setuid)}) - each runs with root's rights for any user; every one is "
              "attack surface worth a reason: " + ", ".join(setuid[:40]),
              f"- **Polkit actions any active local user may take without a password** ({len(pol)}; "
              f"{len(risky)} of them change something rather than read): "
              + ", ".join(f"`{x['action']}`" for x in risky[:30]),
              "- **Packages whose commands have no man page** (top 12, scriptable commands only): "
              + ", ".join(f"`{p}` ({n})" for p, n in nodoc.most_common(12)), ""]
    if len(matrices) >= 2:
        a, b = matrices[0], matrices[1]
        ua = {u["unit"] for u in a.get("enabled_units", []) if not u["vendor"]}
        ub = {u["unit"] for u in b.get("enabled_units", []) if not u["vendor"]}
        neutral = lambda xs: sorted(x for x in xs if not _DESKTOP_SPECIFIC.search(x))  # noqa: E731
        L += ["### Services enabled on one image only (not desktop-specific)", "",
              f"- only **{a.get('profile')}**: " + (", ".join(f"`{x}`" for x in neutral(ua - ub)) or "none"),
              f"- only **{b.get('profile')}**: " + (", ".join(f"`{x}`" for x in neutral(ub - ua)) or "none"), ""]
    return L


def audit_problems(matrices: "list[dict]", builds: dict, profiles: Path) -> "list[str]":
    """The findings that should fail CI. Only measured, unambiguous ones:

    - shani-chronoa (installed) lacks one of its own hard `depends`;
    - a Chronoa tool or sense runs a command whose package shani-chronoa does not
      declare (it works only while some other package keeps pulling it in);
    - an image at least as new as its profile's lists lacks a listed package
      (a stale image is reported as stale, not as a failure of the lists).
    """
    out = []
    chronoa = builds.get("shani-chronoa", {"depends": [], "optdepends": []})
    declared = set(chronoa["depends"]) | set(chronoa["optdepends"])
    base = {"systemd", "systemd-sysvcompat", "coreutils", "util-linux", "glibc", "bash", "procps-ng", "findutils",
            "grep", "sed", "gawk", "tar", "gzip", "iproute2", "kmod", "shadow", "file", "diffutils", "pacman",
            "python", "glib2", "iputils", "shani-chronoa"}
    for m in matrices:
        prof = m.get("profile") or "?"
        pk = m.get("packages", {})
        have = set(pk) | {x for v in pk.values() for x in v.get("provides", [])}
        if "shani-chronoa" in pk:
            out += [f"{prof}: shani-chronoa {pk['shani-chronoa']['version']} is installed without its "
                    f"dependency {d}" for d in chronoa["depends"] if d not in have]
        owner = {r["command"]: r["package"] for r in m["commands"]}
        for x in m.get("chronoa", {}).get("tools", []) + m.get("chronoa", {}).get("senses", []):
            for c in x.get("needs", []):
                pkg = owner.get(c)
                if pkg and pkg not in declared and pkg not in base:
                    out.append(f"shani-chronoa: {x['name']} runs {c} from {pkg}, which the PKGBUILD does not declare")
        ver = m.get("image_version") or ""
        last = run(["git", "-C", str(profiles), "log", "-1", "--format=%cs", "--", f"{prof}/", "shared/"]).strip()
        if profiles.is_dir() and ver and last and ver >= last.replace("-", ""):
            listed = {p for ps in read_profile(profiles, prof).values() for p in ps}
            out += [f"{prof}: {p} is in the profile's lists but not on the image built from them"
                    for p in sorted(listed - have)]
    return sorted(set(out))


# --- suggestions: what to add, fix or review, ranked by evidence ---------------

X11_TOOLS = {"xdotool", "xclip", "xsel", "xset", "xprop", "wmctrl", "setxkbmap"}
#: Wayland input injectors, each with its own precondition - not X11, and not a plain "install it"
INPUT_INJECTORS = {"wtype": "needs the compositor's virtual-keyboard protocol",
                   "dotool": "writes to /dev/uinput, so the user needs uinput access (a udev rule or group)",
                   "ydotool": "writes to /dev/uinput through its daemon, so it needs uinput access"}
KDE_TOOLS = {"kreadconfig6", "kwriteconfig6", "kscreen-doctor", "plasma-apply-colorscheme", "plasma-apply-lookandfeel",
             "qdbus6", "kdeconnect-cli", "baloosearch6", "balooctl6"}
GNOME_TOOLS = {"gsettings", "gnome-extensions", "localsearch", "gdbus"}
PYTHON_PACKAGES = {"yaml": "python-yaml", "PIL": "python-pillow", "cv2": "python-opencv", "sympy": "python-sympy",
                   "mcp": "python-mcp", "numpy": "python-numpy", "httpx": "python-httpx", "gi": "python-gobject",
                   "cairo": "python-cairo", "dbus": "python-dbus", "requests": "python-requests",
                   "onnxruntime": "python-onnxruntime", "openwakeword": "(not in Arch)", "sounddevice": "python-sounddevice"}

#: (capability, packages that provide it - any one counts, why a desktop distro wants it).
#: Opinion, not measurement: reported under its own heading and never in --strict.
CATALOG = [
    ("Spell-checking dictionary (English)", ["hunspell-en_us", "hunspell-en_gb"], "spell checking in GTK/Qt text fields and Chronoa's spell skill"),
    ("Short command examples", ["tealdeer", "tldr"], "explain_command, and people new to a terminal"),
    ("Linux and libc man pages", ["man-pages"], "man 2/3 pages; a matrix of syscalls and libc has nothing without them"),
    ("Pending-updates check without root", ["pacman-contrib"], "checkupdates, used by check_updates"),
    ("Symbolic maths", ["python-sympy"], "Chronoa's solve_math"),
    ("Speech recognition", ["whisper-cpp"], "Chronoa's voice input (a hard depends since pkgrel 15)"),
    ("A natural text-to-speech voice", ["rhvoice-voice-slt", "piper-tts-bin"], "Chronoa's spoken replies beyond espeak-ng"),
    ("Sound firmware for recent laptops", ["sof-firmware"], "built-in speakers and microphones on most 2019+ Intel laptops"),
    ("Firmware updates", ["fwupd"], "LVFS updates for docks, SSDs, touchpads, BIOS"),
    ("Network diagnostics", ["mtr", "traceroute"], "a path trace when 'the internet is slow'"),
    ("DNS lookup tool", ["bind", "ldns", "knot"], "dig/drill/kdig for name resolution problems"),
    ("Emoji font", ["noto-fonts-emoji"], "emoji in every app, including Chronoa's find_emoji"),
    ("CJK fonts", ["noto-fonts-cjk", "adobe-source-han-sans-otc-fonts"], "Chinese, Japanese and Korean text"),
    ("HEIF/AVIF images", ["libheif", "libavif"], "photos from phones; edit_image reads both"),
    ("7z and other archives", ["7zip", "p7zip"], "extract_archive beyond tar and zip"),
    ("Tab completion", ["bash-completion"], "completions for every command that ships them"),
    ("Wayland clipboard tools", ["wl-clipboard"], "clipboard skills on Wayland sessions"),
]


def resolve_owners(commands: "list[str]") -> "dict[str, str]":
    """command -> repo/package that ships usr/bin/<command>, from pacman's files database.

    Needs `pacman -Fy` to have been run (it downloads the files database); with
    none, returns {} and the hand-kept COMMAND_PACKAGES map is all there is.
    """
    out = {}
    for c in commands:
        line = run(["pacman", "-F", "--machinereadable", f"usr/bin/{c}"]).split("\n", 1)[0]
        parts = line.split("\0")
        if len(parts) >= 2 and parts[1]:
            out[c] = f"{parts[1]} ({parts[0]})"
    return out


def repo_leaves(names: "list[str]") -> "dict[str, dict]":
    """name -> {repo, desc, required_by} from the sync database, for names the repos have; {} without pacman.

    What separates an application someone chose from a library pulled in as a
    dependency: in the sync database a library is required by many packages,
    an application by few. pkgstats cannot tell them apart - it counts both.
    """
    if not names or shutil.which("pacman") is None:
        return {}
    out = {}
    for i in range(0, len(names), 200):
        text = run(["pacman", "-Sii", *names[i:i + 200]], timeout=120)
        for block in text.split("\n\n"):
            f = _fields(block)
            if "Name" in f:
                req = [x for x in f.get("Required By", "").split() if x != "None"]
                out[f["Name"]] = {"repo": f.get("Repository", ""), "desc": f.get("Description", ""),
                                  "required_by": len(req), "required_by_names": req,
                                  "size": _size(f.get("Installed Size", "")), "url": f.get("URL", ""),
                                  "provides": [re.split(r"[<>=]", x, maxsplit=1)[0]
                                               for x in f.get("Provides", "").split() if x != "None"],
                                  "depends": [re.split(r"[<>=]", x, maxsplit=1)[0]
                                              for x in f.get("Depends On", "").split() if x != "None"]}
    return out


def _sync_closure(roots: "list[str]") -> set:
    """Everything the sync database says `roots` pull in (for 'is this the toolchain?')."""
    seen, todo = set(), list(roots)
    while todo:
        batch = [n for n in todo if n not in seen][:200]
        todo = [n for n in todo if n not in seen and n not in batch]
        if not batch:
            break
        seen |= set(batch)
        for info in repo_leaves(batch).values():
            todo += [d for d in info["depends"] if d not in seen]
    return seen


def suggestions(matrices: "list[dict]", builds: dict, owners: "Optional[dict]" = None,
                profiles_dir: "Optional[Path]" = None) -> "list[dict]":
    """Every suggestion, each with the evidence for it: {kind, package, why, evidence, images, score}."""
    merged: dict = {}
    toolchain = None
    toolchain_box = [set()]

    def add(kind, package, why, evidence, prof, score):
        key = (kind, package, why)
        item = merged.setdefault(key, {"kind": kind, "package": package, "why": why, "evidence": evidence,
                                       "images": [], "score": score})
        if prof not in item["images"]:
            item["images"].append(prof)
        item["score"] = max(item["score"], score)

    for m in matrices:
        prof = m.get("profile") or m.get("host") or "?"
        pk = m.get("packages", {})
        have = set(pk) | {x for v in pk.values() for x in v.get("provides", [])}
        # 1. optional dependencies of what is installed: a feature of installed software that is off
        wanted = collections.defaultdict(list)
        for name, v in pk.items():
            for o in v.get("optional", []):
                if not o["installed"] and o["name"] not in have:
                    wanted[o["name"]].append((name, o["why"]))
        noise = re.compile(r"binding|ruby|perl|lua|tcl|\btk\b|java|jni|scheme|documentation|\bdocs?\b|"
                           r"develop|devel|build|test|debug|example|demo|legacy|alternative|benchmark|profil")
        for dep, by in wanted.items():
            reasons = sorted({w for _, w in by if w})[:2]
            if all(noise.search(f"{dep} {w}".lower()) for _, w in by):
                add("skip", dep, "optional dependency for bindings, docs or development", "", prof, 0)
                continue
            wanters = sorted({n for n, _ in by})
            add("add", dep, "optional dependency of installed software",
                f"wanted by {len(wanters)}: " + ", ".join(wanters[:5])
                + (f" - for {'; '.join(reasons)}" if reasons else ""), prof, 10 + 5 * len(wanters))
        # 2. broken launchers and units
        for b in m.get("broken", []):
            add("fix", b.get("package") or "?", f"{b['kind']} runs a program that is not installed",
                f"{b['file']} -> {b['program']}", prof, 60)
        # 3. what Chronoa runs and imports
        owner = {r["command"]: r["package"] for r in m["commands"]}
        other_desktop = {"gnome": KDE_TOOLS, "plasma": GNOME_TOOLS}.get(prof, set())
        for x in m.get("chronoa", {}).get("tools", []) + m.get("chronoa", {}).get("senses", []):
            for c in x.get("missing", []):
                pkg = (owners or {}).get(c) or COMMAND_PACKAGES.get(c, "?")
                if c in ("kreadconfig5", "plasma-lookandfeeltool") or c in other_desktop:
                    continue  # a Plasma 5 fallback, or the other desktop's tool
                if c in INPUT_INJECTORS:
                    add("note", pkg, "Wayland input injector a Chronoa skill can use",
                        f"{c} for {x['name']} - {INPUT_INJECTORS[c]}; the input portal (RemoteDesktop) needs neither",
                        prof, 1)
                    continue
                if c in X11_TOOLS:
                    add("note", pkg, "X11-only tool a Chronoa skill can use; Shanios desktops are Wayland",
                        f"{c} for {x['name']} - the Wayland path is the input/clipboard portal, not this package",
                        prof, 1)
                    continue
                add("add", pkg, "a Chronoa skill or sense runs it", f"{c} for {x['name']}", prof, 40)
        for imp in m.get("python_imports", []):
            add("add", PYTHON_PACKAGES.get(imp["module"], f"python-{imp['module'].lower()}"),
                "Chronoa imports a module this Python lacks", f"import {imp['module']} ({imp['first_used_in']})", prof, 35)
        # 4. the image behind the repo
        for name, b in builds.items():
            v = pk.get(name, {}).get("version")
            if v and b.get("version") and "$" not in b["version"] and _vercmp(v, b["version"]) < 0:
                add("rebuild", name, "the image ships an older build than shani-pkgbuilds",
                    f"{v} on the image, {b['version']} in the PKGBUILD", prof, 50)
        # 5. setuid programs: review whether each package needs to be there at all
        for r in m["commands"]:
            if r["safety"] == "elevates":
                req = pk.get(r["package"], {}).get("required_by", [])
                chain = why_installed(pk, r["package"])
                add("review", r["package"], "ships a setuid program (runs as root for any user)",
                    f"{r['command']}; here because " + (" <- ".join(chain[::-1]) if chain else
                                                        (f"required by {', '.join(req[:3])}" if req
                                                         else "nothing requires it"))
                    + (f"; on {(m.get('popularity') or {}).get(r['package']):.1f}% of Arch systems"
                       if (m.get("popularity") or {}).get(r["package"]) is not None else ""),
                    prof, 25 if req else 30)
        # 6. footprint: the biggest single-package costs are where a list decision pays most
        for x in footprint(pk)[:6]:
            if x["exclusive_bytes"] > 50 * 1024 ** 2:
                add("review", x["package"], "large exclusive footprint (freed if it alone were removed)",
                    f"{_mib(x['exclusive_bytes'])} in {x['exclusive']} packages, e.g. {', '.join(x['biggest'][:3])}",
                    prof, 20)
        newest = max((v.get("built", 0) for v in pk.values()), default=0)
        for name in builds:
            v = pk.get(name, {})
            if v.get("built") and newest - v["built"] > 180 * 86400:
                add("rebuild", name, "a Shanios package not rebuilt in over 180 days",
                    f"built {(newest - v['built']) // 86400} days before the newest package on the image", prof, 15)
        # 7. popular on Arch, missing here (pkgstats) - and rare here
        top = m.get("pkgstats_top") or {}
        noise = re.compile(r"^(lib32-|linux(-|$)|.*-debug$|.*-git$|.*-bin$|.*-headers$|base-devel$|gcc|clang|llvm|"
                           r"cmake$|meson$|ninja$|rust$|go$|nodejs|npm|jdk|jre|python-pip|yay|paru|grub|os-prober|"
                           r"pulseaudio|jack2$|xorg-server$|xf86-|lightdm|sddm$|gdm$|virtualbox|mesa-amber|"
                           r"amd-ucode|intel-ucode|nvidia$|nvidia-dkms|steam|wine)")
        other = re.compile({"gnome": r"^(kde|plasma|kf6|qt6|kwin|dolphin|konsole|kate|spectacle|ark|okular|gwenview|"
                                     r"breeze|kio|baloo|akonadi|discover)",
                            "plasma": r"^(gnome|gtk4?|nautilus|evince|eog|gedit|mutter|gvfs|tracker|localsearch|loupe|"
                                      r"papers|totem|adwaita|libadwaita)"}.get(prof, r"^$"))
        alternatives = re.compile(r"^(mkinitcpio|xorg-x|xorg-(?!xwayland)|qt5-|perl-|ruby-|lua-|haskell-|"
                                  r"node-|rust-|go-|ocaml-|r-|vlc|python2)")
        if toolchain is None:
            toolchain_box[0] = _sync_closure(["base-devel"]) if shutil.which("pacman") else set()
        cands = {n: v for n, v in top.items() if n not in have and not noise.search(n) and not other.search(n)
                 and not alternatives.search(n) and n not in toolchain_box[0]}
        leaves = repo_leaves(sorted(cands))
        seen_desc: dict = {}
        other_images = set().union(*[set(o.get("packages", {})) for o in matrices
                                     if (o.get("profile") or "?") != prof]) if len(matrices) > 1 else set()
        flatpaks = []
        if profiles_dir is not None:
            for d in ("shared", prof):
                f = Path(profiles_dir) / d / "flatpak-packages.txt"
                if f.is_file():
                    flatpaks += [l.split("#", 1)[0].strip().lower() for l in f.read_text().splitlines() if l.strip()]
        pacman_tools = re.compile(r"^(reflector|pkgfile|expac|pacutils|pacman-mirrorlist|arch-install-scripts|"
                                  r"rate-mirrors|downgrade|pkgstats|archlinux-keyring)$")
        for name, popv in sorted(cands.items(), key=lambda kv: -kv[1]):
            if name in other_images:
                continue  # the other desktop's choice; desktop parity is reported separately
            if pacman_tools.search(name):
                continue  # Arch maintenance tooling: an image-based system is not maintained with pacman
            # org.gimp.GIMP -> "gimp", org.chromium.Chromium -> "chromium": the app's own last segment,
            # compared whole - a substring test matched "at" and "ant" inside unrelated ids
            flat = next((f for f in flatpaks if f.rsplit(".", 1)[-1] in (name, name.replace("-", ""))), None)
            if flat:
                add("note", name, "popular on Arch; this profile ships it as a Flatpak instead",
                    f"{popv:.1f}% of Arch systems; flatpak {flat}", prof, 1)
                continue
            info = leaves.get(name) if leaves else None
            if leaves and info is None:
                continue  # not in the configured repos (an AUR package) - an image cannot list it
            if info is not None and info["required_by"] > 3:
                continue  # a library or plugin many packages depend on: arrives when something needs it
            if info and any(top.get(r, 0) >= popv * 0.8 for r in info["required_by_names"]):
                continue  # its popularity is borrowed from a popular dependent (libodfgen <- libreoffice)
            if info and re.search(r"\blibrar(y|ies)\b|\bbindings?\b|\bplugins?\b|data files|common files|"
                                  r"\bheaders?\b|\bfonts? metadata|wrapper around", info["desc"].lower()):
                continue  # a building block, not something a person installs for itself
            if name.startswith("python-") and info and not re.search(r"\b(tool|utility|command|cli|application)\b",
                                                                      info["desc"].lower()):
                continue  # a Python library, not something a person installs for itself
            if info and info["desc"] in seen_desc:
                seen_desc[info["desc"]].append(name)
                continue  # a split package of one already listed (vlc-plugin-*): one candidate, not 24
            if info:
                seen_desc[info["desc"]] = [name]
            add("add", name, "installed on many Arch systems, missing here (pkgstats)",
                f"{popv:.1f}% of Arch systems" + (f"; {info['repo']}: {info['desc'][:70]}" if info else
                                                   " (not checked against the repos: no pacman here)"),
                prof, int(popv))
        if top:
            for name, v in pk.items():
                req = [r for r in v.get("required_by", [])]
                if (v.get("explicit") or any(r.startswith("shani-") for r in req)) and name not in builds \
                        and (m.get("popularity") or {}).get(name, 100) < 2:
                    pv = m["popularity"][name]
                    shown = f"{pv:.2f}%" if pv else f"under {m.get('popularity_floor', 0.5)}%"
                    add("review", name, "on this image but rare on Arch (pkgstats under 2%)",
                        f"{shown} of Arch systems; here because "
                        + (" <- ".join(why_installed(pk, name)[::-1]) or "explicit"), prof, 22)
        # 8. known vulnerabilities (Arch security tracker)
        for v in m.get("security", []):
            sev = {"Critical": 90, "High": 80, "Medium": 70, "Low": 60}.get(v["severity"], 55)
            if v["fixed"]:
                add("fix", v["package"], f"{v['severity'] or 'unrated'} {v['type'] or 'vulnerability'} ({v['avg']})",
                    f"{v['installed']} installed, fixed in {v['fixed']}; {', '.join(v['cves'][:3])}", prof, sev)
            else:
                # Open in the tracker with no fixed version recorded. The tracker leaves old
                # issues open (a 2021 kernel CVE against 7.2.x), so this is a question to
                # check, not a known hole.
                add("review", v["package"], f"open in the Arch security tracker, no fix recorded ({v['avg']})",
                    f"{v['severity'] or 'unrated'} {v['type'] or ''}; {v['installed']} installed; "
                    f"{', '.join(v['cves'][:3])} - verify against upstream before acting", prof, sev // 4)
        # 9. services with no systemd sandboxing at all (report only - the policy is light hardening)
        for e in m.get("service_exposure", [])[:8]:
            if e["exposure"] >= 9.0:
                add("review", e["unit"], "enabled service with no systemd sandboxing (exposure >= 9)",
                    f"systemd-analyze security --offline: {e['exposure']} {e['rating']}", prof, 18)
        # 10. the catalog (opinion)
        for capability, pkgs, why in CATALOG:
            if not any(p in have for p in pkgs):
                add("consider", pkgs[0], capability, why, prof, 5)
    order = {"fix": 0, "rebuild": 1, "add": 2, "review": 3, "consider": 4, "note": 5, "skip": 6}
    return sorted(merged.values(), key=lambda x: (order[x["kind"]], -x["score"], x["package"]))


def write_suggestions(items: "list[dict]", matrices: "list[dict]") -> str:
    imgs = ", ".join(f"{m.get('profile') or m.get('host')} {m.get('image_version') or ''}".strip() for m in matrices)
    L = ["# Suggestions for the distro", "", f"From the capability matrices of: {imgs}.", "",
         "Kinds: **fix** (broken now), **rebuild** (the image lags the repo), **add** (measured need: an installed "
         "package's optional dependency, or something Chronoa runs or imports), **review** (attack surface worth a "
         "reason), **consider** (a labelled catalog of what desktop distros usually have - opinion, not measurement).", ""]
    for kind in ("fix", "rebuild", "add", "review", "consider", "note"):
        rows = [x for x in items if x["kind"] == kind]
        if kind == "add":
            # one installed package's optional wish is weak evidence; two or more is a pattern
            strong = [x for x in rows if x["why"] != "optional dependency of installed software" or x["score"] >= 20]
            popular = [x for x in strong if "pkgstats" in x["why"]]
            strong = [x for x in strong if "pkgstats" not in x["why"]] + popular[:40]
            L += [f"*{len(rows) - len(strong)} more optional dependencies wanted by a single package are in the JSON.*", ""] \
                if len(rows) > len(strong) else []
            rows = strong
        if not rows:
            continue
        L += [f"## {kind} ({len(rows)})", "", "| Package | Why | Evidence | Images |", "|---|---|---|---|"]
        L += [f"| `{x['package']}` | {_md_cell(x['why'], 70)} | {_md_cell(x['evidence'], 140)} | "
              f"{', '.join(x['images'])} |" for x in rows[:80]]
        L.append("")
    skipped = [x for x in items if x["kind"] == "skip"]
    if skipped:
        L += [f"*{len(skipped)} optional dependencies for language bindings, documentation or development were left out "
              "(in the JSON as kind=skip).*", ""]
    return "\n".join(L) + "\n"


# --- enrich: what the web knows about these packages (run where the network is) -----

def enrich(data: dict, security: "Optional[list]" = None, tldr_zip: "Optional[Path]" = None,
           pkgstats: "Optional[dict]" = None) -> dict:
    """Add CVEs (Arch security tracker), tldr coverage and pkgstats popularity to a matrix JSON."""
    pk = data.get("packages", {})
    if security is not None:
        vulnerable = []
        for avg in security:
            if avg.get("status") in ("Not affected",):
                continue
            for name in avg.get("packages", []):
                have = pk.get(name, {}).get("version")
                if not have:
                    continue
                fixed = avg.get("fixed")
                if fixed and _vercmp(have, fixed) >= 0:
                    continue  # this build already has the fix
                if avg.get("affected") and _vercmp(have, avg["affected"]) < 0 and not fixed:
                    continue  # older than anything known affected
                vulnerable.append({"package": name, "installed": have, "fixed": fixed or "", "avg": avg["name"],
                                   "severity": avg.get("severity", ""), "status": avg.get("status", ""),
                                   "type": avg.get("type", ""), "cves": avg.get("issues", [])[:6]})
        data["security"] = sorted(vulnerable, key=lambda v: (["Critical", "High", "Medium", "Low"].index(v["severity"])
                                                             if v["severity"] in ("Critical", "High", "Medium", "Low") else 4,
                                                             v["package"]))
    if tldr_zip is not None:
        import zipfile
        names = {Path(n).stem for n in zipfile.ZipFile(tldr_zip).namelist()
                 if n.startswith(("pages/common/", "pages/linux/")) and n.endswith(".md")}
        for r in data["commands"]:
            r["tldr"] = r["command"] in names
        data["tldr_pages"] = len(names)
    if pkgstats is not None:
        # One bulk list (a page per ~500 packages) instead of a request per package:
        # everything at or above its threshold has a number, everything else is below it.
        floor = pkgstats.get("_floor", 0.5)
        data["popularity"] = {n: pkgstats.get(n, 0.0) for n in pk}
        data["popularity_floor"] = floor
        data["pkgstats_top"] = {n: v for n, v in pkgstats.items() if not n.startswith("_") and v >= 5.0}
    return data


def pkgstats_top(min_popularity: float = 5.0, page: int = 500) -> "dict[str, float]":
    """name -> % of reporting Arch systems with it installed, for every package at or above `min_popularity`.

    pkgstats.archlinux.de pages its list in popularity order, so this stops at
    the first page that drops below the threshold.
    """
    import urllib.request
    out, offset = {}, 0
    while True:
        url = f"https://pkgstats.archlinux.de/api/packages?limit={page}&offset={offset}"
        with urllib.request.urlopen(url, timeout=30) as resp:
            batch = json.loads(resp.read()).get("packagePopularities", [])
        if not batch:
            break
        for item in batch:
            if item.get("popularity", 0) >= min_popularity:
                out[item["name"]] = item["popularity"]
        if batch[-1].get("popularity", 0) < min_popularity:
            break
        offset += page
    return out


# --- the detailed report: cost of each addition, gain of each removal ----------------

def _add_cost(name: str, pk: dict, cache: dict) -> "tuple[list[str], int]":
    """What installing `name` would bring onto an image with packages `pk`: new packages and their size."""
    have = set(pk) | {x for v in pk.values() for x in v.get("provides", [])}
    new, todo = [], [name]
    while todo:
        cur = todo.pop()
        if cur in have or cur in new:
            continue
        if cur not in cache:
            cache.update(repo_leaves([cur]))
        info = cache.get(cur)
        if info is None:
            # a virtual name: satisfied if any provider is installed, else unknown
            continue
        new.append(cur)
        have.update(info.get("provides", []))
        todo += info.get("depends", [])
    return new, sum(cache.get(n, {}).get("size", 0) for n in new)


def _owning_meta(name: str, pk: dict, builds: dict) -> "list[str]":
    """Which Shanios meta packages (or profile list entries) bring `name` in, directly or not."""
    return [x for x in why_installed(pk, name) if x in builds or x.startswith("shani-")][:1] or why_installed(pk, name)[:1]


def package_report(matrices: "list[dict]", items: "list[dict]", builds: dict) -> str:
    cache: dict = {}
    L = ["# Packages to add and the profile review, in detail", "",
         "Measured on: " + ", ".join(f"**{m.get('profile')}** {m.get('image_version')} "
                                     f"({len(m.get('packages', {}))} packages, "
                                     f"{_mib(sum(v.get('size', 0) for v in m.get('packages', {}).values()))})"
                                     for m in matrices) + ".",
         "Cost = what the package and its new dependencies add to that image (sync database sizes); "
         "popularity = share of Arch systems with it installed (pkgstats).", ""]
    by_prof = {m.get("profile"): m for m in matrices}
    def cost_cells(name):
        cells = []
        for prof, m in by_prof.items():
            new, size = _add_cost(name, m.get("packages", {}), cache)
            cells.append(f"{prof}: +{len(new)} pkgs, {_mib(size)}" if new else f"{prof}: present")
        return "; ".join(cells)
    groups = [
        ("A. Measured needs - Chronoa or installed software uses it", lambda x: x["kind"] == "add" and "pkgstats" not in x["why"]),
        ("B. Popular on Arch and missing (pkgstats), top 40 after filtering", lambda x: x["kind"] == "add" and "pkgstats" in x["why"]),
        ("C. Catalog - what desktop distros usually have (opinion)", lambda x: x["kind"] == "consider"),
    ]
    for title, pred in groups:
        rows = [x for x in items if pred(x)]
        if title.startswith("B"):
            rows = rows[:40]
        if title.startswith("A"):
            rows = [x for x in rows if x["why"] != "optional dependency of installed software" or x["score"] >= 20]
        L += [f"## {title} ({len(rows)})", "", "| Package | Why | Evidence | Cost to add | Popularity |",
              "|---|---|---|---|---|"]
        for x in rows:
            name = x["package"].split(" ")[0]
            pop = next((m.get("popularity", {}).get(name) for m in matrices if m.get("popularity", {}).get(name)), None)
            pop = pop or next((m.get("pkgstats_top", {}).get(name) for m in matrices if m.get("pkgstats_top", {}).get(name)), None)
            L.append(f"| `{x['package']}` | {_md_cell(x['why'], 60)} | {_md_cell(x['evidence'], 110)} | "
                     f"{cost_cells(name)} | {f'{pop:.1f}%' if pop else ''} |")
        L.append("")
    # removals and review
    L += ["## D. Review: what each removal would free, and who would notice", "",
          "| Package | Finding | Brought in by | Frees (that image) | On Arch | Also required by |", "|---|---|---|---|---|---|"]
    done = set()
    for x in items:
        if x["kind"] != "review" or x["package"] in done or x["package"].endswith((".service", ".socket")):
            continue
        name = x["package"]
        done.add(name)
        cells, req_cells, by_cells = [], [], []
        for prof, m in by_prof.items():
            pk = m.get("packages", {})
            if name not in pk:
                continue
            fp = {f["package"]: f for f in footprint(pk)}
            chain = why_installed(pk, name)
            by_cells.append(" <- ".join(chain[::-1][1:]) or "explicit")
            freed = fp.get(name, {}).get("exclusive_bytes") or pk[name].get("size", 0)
            cells.append(f"{prof}: {_mib(freed)}")
            req_cells.append(", ".join(pk[name].get("required_by", [])[:4]) or "-")
        pop = next((m.get("popularity", {}).get(name) for m in matrices if name in m.get("popularity", {})), None)
        L.append(f"| `{name}` | {_md_cell(x['why'], 60)}: {_md_cell(x['evidence'], 80)} | {'; '.join(dict.fromkeys(by_cells))} | "
                 f"{'; '.join(cells)} | {(f'{pop:.2f}%' if pop else 'under 0.5%') if pop is not None else ''} | "
                 f"{'; '.join(dict.fromkeys(req_cells))} |")
    L += ["", "## E. Services worth a second look", "", "| Unit | Finding | Images |", "|---|---|---|"]
    L += [f"| `{x['package']}` | {_md_cell(x['evidence'], 100)} | {', '.join(x['images'])} |" for x in items
          if x["kind"] == "review" and x["package"].endswith((".service", ".socket"))]
    L += ["", "## F. Rebuilds the images are waiting on", "", "| Package | Evidence | Images |", "|---|---|---|"]
    L += [f"| `{x['package']}` | {_md_cell(x['evidence'], 100)} | {', '.join(x['images'])} |" for x in items
          if x["kind"] == "rebuild"]
    return "\n".join(L) + "\n"


def check(data: dict) -> "list[str]":
    """Problems that should fail a test: Chronoa code needing a command it cannot explain the absence of."""
    inv = data.get("chronoa", {})
    if "error" in inv:
        return [f"Chronoa's registries did not load: {inv['error']}"]
    out = []
    for x in inv.get("tools", []) + inv.get("senses", []):
        for c, how in (x.get("handling") or {}).items():
            if how != "checked first":
                out.append(f"{x['name']}: runs {c}, which is not installed here, and {how}")
    return out


# --- output ----------------------------------------------------------------

def _md_cell(text: str, n=110) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")[:n]


def _yes(v) -> str:
    return "yes" if v else ""


def write_markdown(path: str, data: dict) -> None:
    rows, s, inv, osx = data["commands"], data["summary"], data["chronoa"], data["os_surfaces"]
    L = ["# Shanios capability matrix for Chronoa", "",
         f"Generated {data['generated']} on {data['host']}" + (f" ({data['profile']} profile)" if data.get("profile") else "")
         + ". "
         f"{s['commands']} commands from {s['packages']} packages; {s['with_man']} have a man page, "
         f"{s['with_json']} document JSON output; {s['used']} are already called by Chronoa; "
         f"**{s['candidates']} candidates**.", "",
         "Surfaces: **sense** (read-only machine state -> percept), **skill** (on-demand read-only tool), "
         "**actuator** (changes something; needs a consent gate), **trigger** (a state that transitions, or a "
         "command that can follow/watch), **memory** (a stable fact), **verifier** (a read-only check that an "
         "actuator's effect happened - verification.py POST_CONDITION), **attachment** (acts on a dropped file), "
         "**voice** (capture/playback/speech engines). Wrapper properties: open-world (must go through the "
         "egress log; off in privacy mode), sensitivity, plan-mode preview (documents --dry-run), ambient "
         "(cheap enough to poll).", "",
         "Score = 3·JSON + 2·read-only + 2·explicit + 2·Shanios package + 1·has app − 2·elevates "
         "− 2·package already used − 1·no clear intent. ★ = built by Shanios. Every classification is a "
         "heuristic from the man summary: a starting point for a skill, not a verdict.", ""]

    # ---- Chronoa today
    L += ["## Chronoa today (read from its registries)", ""]
    if "error" in inv:
        L += [f"*Registries could not be loaded here: {inv['error']}*", ""]
    else:
        tools = inv["tools"]
        act = [t for t in tools if t["role"] == "actuator"]
        L += [f"- **Tools**: {len(tools)} ({len(tools) - len(act)} skills, {len(act)} actuators); "
              f"{sum(bool(t['gate']) for t in tools)} consent-gated; {sum(t['open_world'] for t in tools)} open-world; "
              f"all {inv['entry_points']['mcp_tools']} exposed over MCP.",
              f"- **Actuators with a verification post-condition**: "
              f"{sum(bool(t['post_condition']) for t in act)} of {len(act)} - the rest report success unverified.",
              f"- **Actuators an unattended trigger may drive**: {sum(t['unattended_ok'] for t in act)} of {len(act)}.",
              f"- **Senses**: {len(inv['senses'])} ({sum(x['ambient'] for x in inv['senses'])} ambient, "
              f"{sum(x['default_on'] for x in inv['senses'])} on by default; sensitivity "
              + ", ".join(f"{k} {v}" for k, v in collections.Counter(x["sensitivity"] for x in inv["senses"]).items())
              + ").",
              f"- **Trigger event types**: " + "; ".join(f"`{e['type']}` ({e['question']})" for e in inv["events"]),
              f"- **Percept rules**: {inv['percept_rules']}.",
              f"- **Memory kinds**: {', '.join(inv['memory_kinds'])}.",
              f"- **Entry points**: launchers {', '.join(inv['entry_points']['launchers'])}; "
              f"{len(inv['entry_points']['gactions'])} GActions ({', '.join(inv['entry_points']['gactions'])}); "
              f"user drop-ins {', '.join(inv['entry_points']['user_dropins'])}.",
              f"- **Attachment kinds**: {', '.join(inv['attachment_kinds'])}.", ""]
        broken = [(k, x) for k, xs in (("tool", tools), ("sense", inv["senses"])) for x in xs if x.get("missing")]
        desk = collections.Counter(d for t in act for d in t.get("desktops", []))
        L += ["### Needs a command this image does not have", "",
              "A skill or sense that runs a command missing here cannot do that part of its job on this "
              "image. Handling: **checked first** (it explains what is missing), **caught** (the user sees "
              "a raw error), **unchecked** (it may crash). A module with several backends may still work "
              "through one that is installed.", "",
              "| Kind | Name | Missing here | Handling |", "|---|---|---|---|"]
        L += [f"| {k} | `{x['name']}` | {', '.join(f'`{c}`' for c in x['missing'])} | "
              + "; ".join(f"`{c}` {h}" for c, h in x.get("handling", {}).items()) + " |"
              for k, x in broken] or ["| | *(none)* | | |"]
        L += ["", "### Desktop coverage of actuators", "",
              f"{len(act)} actuators; with desktop-specific code: " + ", ".join(f"{d} {n}" for d, n in desk.most_common())
              + ". Desktop-specific code only: a module may also have a generic path (loginctl, portals, D-Bus) "
              "that works everywhere, so read the module before calling one desktop-only.", "",
              "| Actuator | Desktops it has code for |", "|---|---|"]
        L += [f"| `{t['name']}` | {', '.join(t['desktops'])} |" for t in sorted(act, key=lambda t: t["name"])
              if t.get("desktops")]
        L += ["", "### Tools", "",
              "| Tool | Module | Role | Gate | Read-only | Open-world | Unattended | Verified | Runs | Desktops |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for t in sorted(tools, key=lambda t: (t["role"], t["name"])):
            runs = ", ".join(f"~~{c}~~" if c in t.get("missing", []) else c for c in t.get("needs", [])[:6])
            L.append(f"| `{t['name']}` | {t['module']} | {t['role']} | {t['gate']} | {_yes(t['read_only'])} | "
                     f"{_yes(t['open_world'])} | {_yes(t['unattended_ok'])} | "
                     f"{'' if t['post_condition'] is None else _yes(t['post_condition']) or '**no**'} | "
                     f"{runs} | {', '.join(t.get('desktops', []))} |")
        L += ["", "### Senses", "", "| Sense | Kind | TTL s | Sensitivity | Ambient (poll s) | Default on | Runs | What |",
              "|---|---|---:|---|---|---|---|---|"]
        for x in inv["senses"]:
            runs = ", ".join(f"~~{c}~~" if c in x.get("missing", []) else c for c in x.get("needs", [])[:6])
            L.append(f"| `{x['name']}` | {x['kind']} | {x['ttl_seconds'] or ''} | {x['sensitivity']} | "
                     f"{x['poll_interval'] or ''} | {_yes(x['default_on'])} | {runs} | {_md_cell(x['summary'], 90)} |")
        L += ["", "~~struck~~ = not installed on this image.", ""]

    # ---- calibration
    cal = data.get("calibration") or {}
    if cal:
        L += ["## How far to trust the classifications", "",
              "Scored against Chronoa's own modules, which the heuristics never saw: a command only an actuator "
              "runs is one that changes things, one only read-only skills or senses run is one that reads.", "",
              f"- **Safety** (reads vs changes): {cal['safety_agree']} agree, {cal['safety_disagree']} disagree"
              + (f" - **{cal['safety_accuracy']:.0%}** (commands Chronoa only reads with: "
                 f"{cal['reads_accuracy']:.0%}; commands actuator modules run: {cal['changes_accuracy']:.0%} - "
                 "noisier, since an actuator also runs read-only helpers)."
                 if cal.get("safety_accuracy") is not None else "."),
              f"- **Sense fit**: {cal['sense_recall']:.0%} of commands Chronoa's senses run are classified as "
              "sense material." if cal.get("sense_recall") is not None else "- **Sense fit**: no data.", ""]
        if cal.get("safety_wrong"):
            L += ["| Command | Chronoa uses it to | Matrix says | Man summary |", "|---|---|---|---|"]
            L += [f"| `{w['command']}` | {w['truth']} | {w['guess']} | {_md_cell(w['summary'], 80)} |"
                  for w in cal["safety_wrong"]]
        if cal.get("sense_classifier_disagreements"):
            L += ["", "**Where this file's own heuristic is wrong, not where Chronoa is "
                  "short.** Each of these is run by a Chronoa sense already; the "
                  "classifier did not say \"sense\", so it is listed here as a "
                  "disagreement to be fixed in the heuristics rather than as a "
                  "capability to be built:", ""]
            L += [f"- `{d['command']}` — run by "
                  + (", ".join(f"`{m}`" for m in d["run_by"]) if d["run_by"] else "a sense")
                  + f", classified {d['classified_as']}"
                  for d in cal["sense_classifier_disagreements"]]
            L += ["", "Read that list the other way round before acting on it: every entry "
                  "is already covered, and each was checked against the tree rather than "
                  "taken from here. This section exists so the heuristic's blind spot is "
                  "visible, not so it can be mistaken for a work list."]
        L.append("")

    # ---- surface matrix
    by_cat = collections.defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    order = sorted(by_cat, key=lambda c: -len(by_cat[c]))
    L += ["## Surface × category (fits / not yet covered)", "",
          "| Category | Commands | " + " | ".join(SURFACES) + " |", "|---|---:|" + "---:|" * len(SURFACES)]
    for c in order:
        rs = by_cat[c]
        L.append(f"| {c} | {len(rs)} | " + " | ".join(
            f"{sum(f in r['fits'] for r in rs)} / {sum(f in r['open_surfaces'] and r['candidate'] for r in rs)}"
            for f in SURFACES) + " |")
    L += [f"| **All** | {len(rows)} | " + " | ".join(f"**{s['fits'][f]} / {s['open'][f]}**" for f in SURFACES) + " |",
          "", "Wrapper properties: "
          f"{sum(r['open_world'] for r in rows)} open-world, {sum(r['sensitivity'] == 'personal' for r in rows)} personal, "
          f"{sum(r['plan_preview'] for r in rows)} can preview in plan mode (--dry-run), "
          f"{sum(r['ambient'] for r in rows)} cheap enough to poll.", ""]

    # ---- ideas by surface
    L += ["## Ideas by surface (best first)", ""]
    for f in SURFACES:
        ideas = [i for i in data["ideas"] if i["surface"] == f][:8]
        if ideas:
            L += [f"### {f}", ""] + [f"- **{i['category']} / {i['intent']}** (score {i['score']}): "
                                      + ", ".join(f"`{c}`" for c in i["commands"][:10]) for i in ideas] + [""]

    L += ["## Top candidates", "",
          "| Score | Command | Package (pulled in by) | What it does | Usage | Subcommands (read/change) | Surfaces | Safety | JSON | Props |",
          "|---:|---|---|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["candidate"]), key=lambda r: (-r["score"], r["command"]))[:150]:
        props = " ".join(p for p, on in (("open-world", r["open_world"]), ("personal", r["sensitivity"] == "personal"),
                                          ("dry-run", r["plan_preview"]), ("ambient", r["ambient"]),
                                          ("follows", r["follows"])) if on)
        via = [a for a in r["pulled_in_by"] if a != r["package"]]
        L.append(f"| {r['score']} | `{r['command']}` | {r['package']}{' ★' if r['shani'] else ''}"
                 f"{(' (' + ', '.join(via[:2]) + ')') if via else ''} | {_md_cell(r['summary'], 80)} | "
                 f"{('`' + _md_cell(r['synopsis'], 70) + '`') if r['synopsis'] else ''} | "
                 f"{(str(r['read_subcommands']) + ' / ' + str(r['change_subcommands'])) if r['subcommands'] else ''} | "
                 f"{', '.join(r['open_surfaces'] or r['fits'])} | {r['safety']} | {_yes(r['json'])} | {props} |")

    # ---- OS surfaces beyond commands
    L += ["", "## OS surfaces beyond commands", "",
          "| Surface | Count | Feeds | Used by Chronoa |", "|---|---:|---|---:|"]
    for k, label, feeds in (("gsettings", "GSettings schemas (desktop settings)", "sense, actuator, verifier"),
                            ("polkit", "Polkit actions (privileged)", "actuator"),
                            ("sysfs", "sysfs classes (kernel-direct)", "sense, trigger, actuator for backlight/leds/rfkill"),
                            ("varlink", "Varlink services (JSON IPC)", "sense, skill"),
                            ("portals", "XDG portal interfaces", "sense, actuator"),
                            ("event_sources", "Event sources for new trigger types", "trigger"),
                            ("entry_points", "Desktop entry points", "entry point")):
        L.append(f"| {label} | {len(osx[k])} | {feeds} | {sum(bool(x.get('used')) for x in osx[k])} |")
    L += ["", "### Event sources and the trigger types that cover them", "",
          "| Source | Observed via | Available here | Trigger type |", "|---|---|---|---|"]
    L += [f"| {e['source']} | `{e['via']}` | {_yes(e['available'])} | "
          f"{('`' + e['event_type'] + '`') if e['event_type'] else '**not yet**'} |" for e in osx["event_sources"]]
    L += ["", "### GSettings schemas", "", "| Schema | Keys | Examples | Package | Chronoa |", "|---|---:|---|---|---|"]
    L += [f"| `{g['schema']}` | {g['keys']} | {', '.join(g['examples'][:4])} | {g['package']} | {_yes(g['used'])} |"
          for g in sorted(osx["gsettings"], key=lambda g: -g["keys"])[:120]]
    L += ["", "### Polkit actions", "", "| Action | Active user | Description | Package |", "|---|---|---|---|"]
    L += [f"| `{x['action']}` | {x['allow_active']} | {_md_cell(x['description'], 80)} | {x['package']} |" for x in osx["polkit"]]
    L += ["", "### sysfs classes", "", ", ".join(f"`{x['class']}` ({x['devices']}){' ✓' if x['used'] else ''}"
                                             for x in osx["sysfs"])]
    L += ["", "### Portals and Varlink", "", ", ".join(f"`{x['interface']}` ({x['backend']})" for x in osx["portals"]),
          "", ", ".join(f"`{x['service']}`" for x in osx["varlink"]) or "*(no Varlink sockets visible)*"]
    L += ["", "### Desktop entry points", ""]
    L += [f"- {e['kind']}: {e['name']}" for e in osx["entry_points"]] or ["*(none)*"]
    L += ["", "MIME handlers by top-level type: " + ", ".join(f"{k} {v}" for k, v in osx["mime_handlers"].items())]

    # ---- per category command tables
    for c in order:
        L += ["", f"## {c}", "", "| Command | Package | What it does (man) | Kind | Intent | Safety | Fits | Used by |",
              "|---|---|---|---|---|---|---|---|"]
        for r in sorted(by_cat[c], key=lambda r: (not r["candidate"], -r["score"], r["command"])):
            used = "; ".join(f"{k}: {', '.join(v)}" for k, v in r["used_by"].items()) or (
                "**candidate**" if r["candidate"] else "")
            L.append(f"| `{r['command']}` | {r['package']}{' ★' if r['shani'] else ''} | "
                     f"{_md_cell(r['summary'] or '*(no man page)*')} | {r['kind']} | {r['intent']} | {r['safety']} | "
                     f"{', '.join(r['fits'])} | {used} |")
    i = data["interfaces"]
    L += ["", "## D-Bus services", "", "| Name | Bus | Package | Chronoa uses |", "|---|---|---|---|"]
    L += [f"| `{d['name']}` | {d['bus']} | {d['package']} | {_yes(d['used'])} |" for d in i["dbus"]]
    L += ["", "## GObject typelibs (✓ = Chronoa uses)", "",
          ", ".join(f"`{t['name']}`{' ✓' if t['used'] else ''}" for t in i["typelibs"])]
    L += ["", "## Python packages", "", "| Package | Modules | Chronoa uses |", "|---|---|---|"]
    L += [f"| {p['package']} | {', '.join(p['modules'][:6])} | {_yes(p['used'])} |" for p in i["python"]]
    L += ["", "## systemd units", "", "| Unit | Scope | Package |", "|---|---|---|"]
    L += [f"| `{u['name']}` | {u['scope']} | {u['package']} |" for u in i["units"]]
    L += ["", "## Desktop apps", "", "| App | Command | Source | Categories |", "|---|---|---|---|"]
    L += [f"| {a['name']} | `{a['command']}` | {a['source']} | {a['categories']} |" for a in data["apps"]]
    L += ["", "## Libraries (per package)", "", "| Package | Libraries | Python binding | Examples |", "|---|---:|---|---|"]
    L += [f"| {lr['package']} | {lr['count']} | {lr['python_binding']} | {', '.join(lr['examples'][:3])} |"
          for lr in i["libraries"][:150]]
    Path(path).write_text("\n".join(L) + "\n")


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Shanios Capability Matrix</title>
<style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e3df;--card:#fff;--hi:#e7f5ec;--hifg:#1f6b3a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161615;--fg:#ecebe7;--mut:#9c9b95;--line:#2c2c2a;--card:#1e1e1c;--hi:#1c3325;--hifg:#8fd6a6}}
:root[data-theme=dark]{--bg:#161615;--fg:#ecebe7;--mut:#9c9b95;--line:#2c2c2a;--card:#1e1e1c;--hi:#1c3325;--hifg:#8fd6a6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1280px;margin:0 auto;padding:20px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:22px 0 8px}
p.s{color:var(--mut);margin:0 0 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.card b{display:block;font-size:20px}
.card span{color:var(--mut);font-size:12px}.bar{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 10px}
input,select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
input[type=search]{flex:1;min-width:180px}.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;background:var(--card);cursor:pointer;font-size:12px;color:var(--mut);white-space:nowrap}
tr.c td:first-child{box-shadow:inset 3px 0 var(--hifg)}.tag{font-size:11px;padding:1px 6px;border-radius:10px;background:var(--hi);color:var(--hifg)}
.mut{color:var(--mut)}#n{color:var(--mut);align-self:center}.ideas{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px}
.tabs{display:flex;gap:6px;margin:4px 0 14px;flex-wrap:wrap}.tabs button{font:inherit;padding:6px 12px;border:1px solid var(--line);
border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}.tabs button[aria-selected=true]{border-color:var(--hifg);color:var(--hifg);font-weight:600}
s{color:var(--mut)}.ideas div{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;cursor:pointer}.ideas b{font-size:13px}
</style></head><body><main>
<h1>Shanios capability matrix for Chronoa</h1><p class="s" id="sum"></p><div class="cards" id="cards"></div>
<div class="tabs" role="tablist"><button role="tab" data-t="cmd" aria-selected="true">Commands</button>
<button role="tab" data-t="chr" aria-selected="false">Chronoa today</button><button role="tab" data-t="os" aria-selected="false">OS surfaces</button></div>
<section id="t-chr" hidden><div id="chr"></div></section><section id="t-os" hidden><div id="os"></div></section>
<section id="t-cmd">
<h2>Skill ideas</h2><div class="ideas" id="ideas"></div>
<h2>Commands</h2>
<div class="bar"><input type="search" id="q" placeholder="Search command, package, description" aria-label="Search">
<select id="cat" aria-label="Category"><option value="">All categories</option></select>
<select id="int" aria-label="Intent"><option value="">All intents</option></select>
<select id="saf" aria-label="Safety"><option value="">Any safety</option></select>
<select id="srf" aria-label="Surface"><option value="">Any surface</option></select>
<label><input type="checkbox" id="cand"> Candidates only</label><label><input type="checkbox" id="js"> JSON output</label><span id="n"></span></div>
<div class="wrap"><table><thead><tr><th data-k="command">Command</th><th data-k="package">Package</th><th data-k="summary">What it does</th>
<th data-k="category">Category</th><th data-k="fits">Fits</th><th data-k="intent">Intent</th><th data-k="safety">Safety</th><th data-k="score">Score</th><th data-k="chronoa">Chronoa</th></tr></thead>
<tbody id="tb"></tbody></table></div></section></main>
<script>
const D=__DATA__;const R=D.commands,$=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const S=D.summary;$("sum").textContent=`${S.commands} commands from ${S.packages} packages on ${D.host}, generated ${D.generated}.`;
const K=D.calibration||{};
[["Commands",S.commands],["Man pages",S.with_man],["JSON output",S.with_json],["Used by Chronoa",S.used],["Candidates",S.candidates],["D-Bus services",S.dbus],["Desktop apps",S.apps],
 ["Safety heuristic agrees with Chronoa",K.safety_accuracy==null?"–":Math.round(K.safety_accuracy*100)+"%"],["Sense recall",K.sense_recall==null?"–":Math.round(K.sense_recall*100)+"%"]]
.forEach(([k,v])=>$("cards").insertAdjacentHTML("beforeend",`<div class="card"><b>${v}</b><span>${k}</span></div>`));
const fill=(id,k)=>[...new Set(R.map(r=>r[k]))].sort().forEach(v=>$(id).insertAdjacentHTML("beforeend",`<option>${esc(v)}</option>`));
fill("cat","category");fill("int","intent");fill("saf","safety");
[...new Set(R.flatMap(r=>r.fits))].sort().forEach(v=>$("srf").insertAdjacentHTML("beforeend",`<option>${esc(v)}</option>`));
D.ideas.slice(0,24).forEach(i=>{const el=document.createElement("div");el.innerHTML=`<b>${esc(i.surface)}: ${esc(i.category)} · ${esc(i.intent)}</b> <span class="mut">score ${i.score}</span><br><span class="mut">${esc(i.commands.slice(0,8).join(", "))}</span>`;
el.onclick=()=>{$("srf").value=i.surface;$("cat").value=i.category;$("int").value=i.intent;$("cand").checked=true;draw();$("q").scrollIntoView({behavior:"smooth"})};$("ideas").append(el)});
let sk="score",dir=-1;
function draw(){const q=$("q").value.toLowerCase(),f=$("srf").value,c=$("cat").value,i=$("int").value,s=$("saf").value,cd=$("cand").checked,j=$("js").checked;
const rs=R.filter(r=>(!q||(r.command+" "+r.package+" "+r.summary+" "+r.package_description).toLowerCase().includes(q))&&(!f||r.open_surfaces.includes(f)||r.fits.includes(f)&&!cd)&&(!c||r.category==c)&&(!i||r.intent==i)&&(!s||r.safety==s)&&(!cd||r.candidate)&&(!j||r.json))
.sort((a,b)=>{const g=(o)=>sk=="chronoa"?o.chronoa_skills.length:sk=="fits"?o.fits.join():o[sk],x=g(a),y=g(b);return (x>y?1:x<y?-1:0)*dir||a.command.localeCompare(b.command)});
$("n").textContent=rs.length+" shown"+(rs.length>1500?" (first 1500 listed)":"");
$("tb").innerHTML=rs.slice(0,1500).map(r=>`<tr class="${r.candidate?"c":""}"><td><code>${esc(r.command)}</code>${r.json?' <span class="tag">json</span>':""}</td>
<td>${esc(r.package)}${r.shani?" ★":""}</td><td>${esc(r.summary)||'<span class="mut">no man page</span>'}${r.app?` <span class="mut">· app: ${esc(r.app)}</span>`:""}${r.synopsis?`<br><code class="mut">${esc(r.synopsis)}</code>`:""}${(r.read_subcommands+r.change_subcommands)?`<br><span class="mut">subcommands: ${r.read_subcommands} read, ${r.change_subcommands} change</span>`:""}</td>
<td>${esc(r.category)}</td><td>${esc(r.fits.join(", "))}</td><td>${esc(r.intent)}</td><td>${esc(r.safety)}</td><td>${r.score}</td>
<td>${r.chronoa_skills.length?esc(Object.entries(r.used_by).map(([k,v])=>k+": "+v.join(", ")).join("; ")):r.candidate?'<span class="tag">candidate</span>':""}</td></tr>`).join("")}
document.querySelectorAll("th").forEach(th=>th.onclick=()=>{const k=th.dataset.k;dir=sk==k?-dir:(k=="score"?-1:1);sk=k;draw()});
["q","srf","cat","int","saf","cand","js"].forEach(id=>$(id).addEventListener("input",draw));draw();
const tbl=(rows,cols)=>`<div class="wrap"><table><thead><tr>${cols.map(c=>`<th>${esc(c[0])}</th>`).join("")}</tr></thead><tbody>${
 rows.map(r=>`<tr>${cols.map(c=>`<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
const runs=x=>(x.needs||[]).map(c=>(x.missing||[]).includes(c)?`<s>${esc(c)}</s>`:esc(c)).join(", ");
const C=D.chronoa||{};
if(C.error){$("chr").innerHTML=`<p class="mut">Registries could not be loaded: ${esc(C.error)}</p>`}else{
 const broken=[...C.tools,...C.senses].filter(x=>(x.missing||[]).length);
 $("chr").innerHTML=`<h2>Needs a command this image does not have (${broken.length})</h2>`+tbl(broken,[["Name",x=>`<code>${esc(x.name)}</code>`],
  ["Missing",x=>esc(x.missing.join(", "))],["Handling",x=>esc(Object.entries(x.handling||{}).map(([c,h])=>c+": "+h).join("; "))]])+
 `<h2>Tools (${C.tools.length})</h2>`+tbl(C.tools,[["Tool",t=>`<code>${esc(t.name)}</code>`],["Role",t=>esc(t.role)],["Gate",t=>esc(t.gate)],
  ["Verified",t=>t.post_condition==null?"":t.post_condition?"yes":"<b>no</b>"],["Unattended",t=>t.unattended_ok?"yes":""],
  ["Runs (struck = missing)",runs],["Desktops",t=>esc((t.desktops||[]).join(", "))],["What",t=>esc(t.summary)]])+
 `<h2>Senses (${C.senses.length})</h2>`+tbl(C.senses,[["Sense",x=>`<code>${esc(x.name)}</code>`],["Sensitivity",x=>esc(x.sensitivity)],
  ["Poll s",x=>x.poll_interval??""],["Default on",x=>x.default_on?"yes":""],["Runs",runs],["What",x=>esc(x.summary)]])+
 `<h2>Trigger event types (${C.events.length})</h2>`+tbl(C.events,[["Type",e=>`<code>${esc(e.type)}</code>`],["Question",e=>esc(e.question)]])}
const O=D.os_surfaces||{};
$("os").innerHTML=`<h2>Event sources</h2>`+tbl(O.event_sources||[],[["Source",e=>esc(e.source)],["Via",e=>`<code>${esc(e.via)}</code>`],
  ["Here",e=>e.available?"yes":""],["Trigger type",e=>e.event_type?`<code>${esc(e.event_type)}</code>`:"<b>not yet</b>"]])+
 `<h2>GSettings schemas (${(O.gsettings||[]).length})</h2>`+tbl((O.gsettings||[]).slice().sort((a,b)=>b.keys-a.keys),[["Schema",g=>`<code>${esc(g.schema)}</code>`],
  ["Keys",g=>g.keys],["Examples",g=>esc(g.examples.join(", "))],["Chronoa",g=>g.used?"yes":""]])+
 `<h2>Polkit actions (${(O.polkit||[]).length})</h2>`+tbl((O.polkit||[]).slice().sort((a,b)=>(a.allow_active!="yes")-(b.allow_active!="yes")),
  [["Action",x=>`<code>${esc(x.action)}</code>`],["Active user",x=>esc(x.allow_active)],["Description",x=>esc(x.description)]])+
 `<h2>sysfs classes, portals, entry points</h2><p>${(O.sysfs||[]).map(x=>`<code>${esc(x.class)}</code> (${x.devices})${x.used?" ✓":""}`).join(", ")}</p>
 <p>${(O.portals||[]).map(x=>`<code>${esc(x.interface)}</code>`).join(", ")}</p><p>${(O.entry_points||[]).map(x=>esc(x.kind+": "+x.name)).join("; ")}</p>`;
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x==b));
 ["cmd","chr","os"].forEach(t=>$("t-"+t).hidden=t!=b.dataset.t)});
</script></body></html>
"""


def write_html(path: str, data: dict) -> None:
    # option_list stays in the JSON (for --scaffold); the page does not need it and it is most of the size
    slim = [{k: v for k, v in r.items() if k not in ("option_list", "subcommands")} for r in data["commands"]]
    payload = json.dumps({"commands": slim, **{k: data.get(k) for k in ("summary", "generated", "host", "ideas", "chronoa",
                                                                       "os_surfaces", "calibration")}})
    Path(path).write_text(HTML.replace("__DATA__", payload.replace("</", "<\\/")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="cli-matrix", help="output path without extension")
    ap.add_argument("--scaffold", metavar="COMMAND", help="write a user skill module for COMMAND (needs --from-json)")
    ap.add_argument("--from-json", metavar="FILE", help="a matrix JSON from an earlier run")
    ap.add_argument("--scaffold-dir", default=".", help="where --scaffold writes the module")
    ap.add_argument("--allow-actuator", action="store_true", help="scaffold a command that can change things (disabled)")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="print what changed between two matrix JSONs")
    ap.add_argument("--audit-packaging", nargs="+", metavar="MATRIX_JSON",
                    help="compare matrix JSONs against shani-pkgbuilds and the image profiles")
    ap.add_argument("--pkgbuilds", default=str(PKGBUILDS), help="shani-pkgbuilds checkout")
    ap.add_argument("--strict", action="store_true",
                    help="with --audit-packaging: exit 1 on hard findings (for CI and the harness)")
    ap.add_argument("--profiles", default=str(HERE.parent / "shani-install-media/image_profiles"),
                    help="shani-install-media/image_profiles")
    ap.add_argument("--suggest", nargs="+", metavar="MATRIX_JSON",
                    help="ranked add/fix/rebuild/review suggestions from matrix JSONs")
    ap.add_argument("--package-report", nargs="+", metavar="MATRIX_JSON",
                    help="detailed add/remove report: cost of each addition, gain of each removal (needs pacman)")
    ap.add_argument("--suggest-json", metavar="FILE", help="with --suggest: also write the suggestions as JSON")
    ap.add_argument("--resolve-files", action="store_true",
                    help="with --suggest: look missing commands up in pacman's files database (run pacman -Fy first)")
    ap.add_argument("--enrich", metavar="MATRIX_JSON", help="add web data to a matrix JSON in place")
    ap.add_argument("--security-json", metavar="FILE", help="with --enrich: security.archlinux.org/issues/all.json")
    ap.add_argument("--tldr-zip", metavar="FILE", help="with --enrich: tldr-pages release tldr.zip")
    ap.add_argument("--pkgstats-cache", metavar="FILE",
                    help="with --enrich: pkgstats popularity list - fetched once into FILE (down to 0.5%%), reused after")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if a Chronoa tool or sense runs a missing command without checking for it first")
    args = ap.parse_args()
    if args.enrich:
        data = json.loads(Path(args.enrich).read_text())
        stats = None
        if args.pkgstats_cache:
            cache = Path(args.pkgstats_cache)
            if not cache.is_file():
                fetched = pkgstats_top(0.5)
                cache.write_text(json.dumps({**fetched, "_floor": 0.5}))
            stats = json.loads(cache.read_text())
        enrich(data, json.loads(Path(args.security_json).read_text()) if args.security_json else None,
               Path(args.tldr_zip) if args.tldr_zip else None, stats)
        Path(args.enrich).write_text(json.dumps(data, indent=1))
        print(f"enriched {args.enrich}: " + ", ".join(
            f"{k}={len(data[k]) if isinstance(data.get(k), (list, dict)) else data.get(k)}"
            for k in ("security", "tldr_pages", "popularity", "pkgstats_top") if k in data))
        return 0
    if args.package_report:
        mats = [json.loads(Path(f).read_text()) for f in args.package_report]
        builds = read_pkgbuilds(Path(args.pkgbuilds)) if Path(args.pkgbuilds).is_dir() else {}
        items = suggestions(mats, builds, None, Path(args.profiles))
        print(package_report(mats, items, builds), end="")
        return 0
    if args.suggest:
        mats = [json.loads(Path(f).read_text()) for f in args.suggest]
        owners = None
        if args.resolve_files:
            wanted = sorted({c for m in mats for x in m.get("chronoa", {}).get("tools", []) + m.get("chronoa", {}).get("senses", [])
                             for c in x.get("missing", [])})
            owners = resolve_owners(wanted)
            if not owners:
                print("--resolve-files: pacman's files database answered nothing (no pacman here, or run pacman -Fy)",
                      file=sys.stderr)
        items = suggestions(mats, read_pkgbuilds(Path(args.pkgbuilds)) if Path(args.pkgbuilds).is_dir() else {}, owners,
                            Path(args.profiles))
        if args.suggest_json:
            Path(args.suggest_json).write_text(json.dumps(items, indent=1))
        print(write_suggestions(items, mats), end="")
        return 0
    if args.audit_packaging:
        mats = [json.loads(Path(f).read_text()) for f in args.audit_packaging]
        print(audit_packaging(mats, Path(args.pkgbuilds), Path(args.profiles)), end="")
        if args.strict:
            problems = audit_problems(mats, read_pkgbuilds(Path(args.pkgbuilds)), Path(args.profiles))
            for p in problems:
                print("PROBLEM:", p, file=sys.stderr)
            return 1 if problems else 0
        return 0
    if args.diff:
        print(diff(json.loads(Path(args.diff[0]).read_text()), json.loads(Path(args.diff[1]).read_text())), end="")
        return 0
    if args.scaffold:
        if not args.from_json:
            print("--scaffold needs --from-json=MATRIX.json", file=sys.stderr)
            return 2
        rows = {r["command"]: r for r in json.loads(Path(args.from_json).read_text())["commands"]}
        row = rows.get(args.scaffold)
        if row is None:
            print(f"{args.scaffold} is not in that matrix", file=sys.stderr)
            return 2
        try:
            source = scaffold(row, allow_actuator=args.allow_actuator)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        dest = Path(args.scaffold_dir) / f"{_ident(args.scaffold)}.py"
        dest.write_text(source)
        print(f"wrote {dest}")
        return 0
    if not Path("/var/lib/pacman/local").is_dir():
        print("No pacman database here: run this on a Shanios/Arch system or in a testbed slot.", file=sys.stderr)
        return 2
    pkgs, shani, files = packages(), shani_packages(), file_lists()
    via = pulled_in_by(pkgs)
    path_pkg = {p: pkg for pkg, ps in files.items() for p in ps if p.startswith(PATH_DIRS)}
    commands = {}
    for d in PATH_DIRS:
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                if name not in commands and os.path.isfile(path) and os.access(path, os.X_OK):
                    commands[name] = path
    man, uses, used_text = man_info(sorted(commands)), surface_uses(), skill_text()
    inventory = chronoa_inventory()
    deps = chronoa_dependencies(commands)
    for t in inventory.get("tools", []):
        d = deps.get(t["module"], {})
        t["needs"], t["missing"], t["desktops"] = d.get("runs", []), d.get("missing", []), d.get("desktops", [])
        t["unguarded_missing"] = d.get("unguarded_missing", [])
        t["handling"] = d.get("handling", {})
    for x in inventory.get("senses", []):
        d = deps.get(x["name"], {})
        x["needs"], x["missing"], x["desktops"] = d.get("runs", []), d.get("missing", []), d.get("desktops", [])
        x["unguarded_missing"] = d.get("unguarded_missing", [])
        x["handling"] = d.get("handling", {})
    actuator_modules = {t["module"] for t in inventory.get("tools", []) if t["role"] == "actuator"}
    app_by_cmd, apps = desktop_apps()
    rows = []
    for name, path in commands.items():
        pkg = path_pkg.get(path) or path_pkg.get(os.path.realpath(path)) or "?"
        info = pkgs.get(pkg, {})
        m = man.get(name, {"section": "", "summary": "", "json": False, "follows": False, "dry_run": False,
                           "synopsis": "", "options": 0, "option_list": [], "subcommands": []})
        verb = intent(m["summary"] or info.get("description", ""))
        r = {"command": name, "path": path, "package": pkg, "package_description": info.get("description", ""),
             "explicit": info.get("explicit", False), "shani": pkg in shani,
             "category": category(pkg, info.get("description", "")), "kind": kind(name, pkg, m["section"], m["summary"]),
             "intent": verb, "safety": _with_subcommands(safety(path, m["summary"], verb), m["subcommands"]),
             "man_section": m["section"], "summary": m["summary"], "has_man": bool(m["summary"]),
             "json": m["json"], "follows": m["follows"], "dry_run": m["dry_run"], "app": app_by_cmd.get(name, ""),
             "synopsis": m["synopsis"], "options": m["options"], "option_list": m["option_list"],
             "subcommands": m["subcommands"],
             "read_subcommands": sum(x["effect"] == "reads" for x in m["subcommands"]),
             "change_subcommands": sum(x["effect"] == "changes" for x in m["subcommands"]),
             "pulled_in_by": via.get(pkg, [])}
        if r["category"] == "Other":
            # A package with no telling name or description takes the category of
            # what pulled it in - a codec library under shani-multimedia is media.
            for anc in r["pulled_in_by"]:
                c = category(anc, pkgs.get(anc, {}).get("description", ""))
                if c != "Other":
                    r["category"], r["category_via"] = c, anc
                    break
        u = uses.get(name, {})
        skill_mods = u.get("skill", set())
        r["used_by"] = {"skill": sorted(skill_mods - actuator_modules), "actuator": sorted(skill_mods & actuator_modules),
                        "sense": sorted(u.get("sense", ())), "trigger": sorted(u.get("trigger", ()))}
        r["used_by"] = {k: v for k, v in r["used_by"].items() if v}
        r["chronoa_skills"] = sorted({m_ for v in r["used_by"].values() for m_ in v})
        rows.append(r)
    changers = {r["package"] for r in rows if r["safety"] in ("can-change", "mixed") or "actuator" in r["used_by"]}
    for r in rows:
        r["package_has_changer"] = r["package"] in changers and r["safety"] == "read-only"
        r["fits"] = fits(r)
        r.update(properties(r))
        r["open_surfaces"] = [f for f in r["fits"] if f not in r["used_by"]
                              and not (f == "memory" and "sense" in r["used_by"])]
        r["candidate"] = (r["kind"] in ("scriptable", "admin") and r["has_man"] and not r["chronoa_skills"]
                          and r["man_section"][:1] in ("1", "8") and r["safety"] != "elevates")
    covered_pkgs = {r["package"] for r in rows if r["chronoa_skills"]}
    for r in rows:
        r["package_covered"] = r["package"] in covered_pkgs and not r["chronoa_skills"]
        r["score"] = score(r)
    groups = collections.defaultdict(list)
    for r in rows:
        if r["candidate"]:
            for f in r["open_surfaces"] or ["skill"]:
                groups[(f, r["category"], r["intent"])].append(r)
    ideas = sorted(({"surface": f, "category": c, "intent": i,
                     "score": sum(sorted((r["score"] for r in rs), reverse=True)[:5]) // (2 if i == "other" else 1),
                     "commands": [r["command"] for r in sorted(rs, key=lambda r: (-r["score"], r["command"]))]}
                    for (f, c, i), rs in groups.items()), key=lambda x: -x["score"])
    ifaces = interfaces(files, used_text)
    ossurf = os_surfaces(files, used_text, commands, [e["type"] for e in inventory.get("events", [])])
    profile = ""
    for src in ("/etc/shani-profile",):
        try:
            profile = Path(src).read_text().strip().splitlines()[0]
        except (OSError, IndexError):
            pass
    if not profile:
        m = re.search(r'^VARIANT_ID="?([^"\n]+)', Path("/etc/os-release").read_text() if Path("/etc/os-release").exists() else "", re.M)
        profile = m.group(1) if m else ""
    try:
        image_version = Path("/etc/shani-version").read_text().strip()
    except OSError:
        image_version = ""
    data = {"generated": run(["date", "-Iseconds"]).strip(), "host": os.uname().nodename, "profile": profile,
            "image_version": image_version,
            "commands": rows, "ideas": ideas, "interfaces": ifaces, "apps": apps, "chronoa": inventory,
            # Anything the inventory could not read. A reader comparing two runs
            # needs to know that a section shrank because a path moved, not
            # because a capability was removed.
            "chronoa_sources_not_found": sorted(set(NOT_FOUND)),
            "os_surfaces": ossurf, "calibration": calibration(rows), "broken": broken_references(files),
            "enabled_units": enabled_units(),
            "service_exposure": service_exposure(enabled_units()),
            "python_imports": _missing_python_imports(),
            "packages": {n: {"version": v["version"], "explicit": v["explicit"], "groups": v["groups"],
                             "required_by": v["required_by"][:12], "shani": n in shani,
                             "description": v["description"][:120], "provides": v["provides"],
                             "optional": v["optional"], "depends": v["depends"], "size": v["size"],
                             "built": v["built"]}
                         for n, v in pkgs.items()}}
    data["summary"] = {"commands": len(rows), "packages": len({r["package"] for r in rows}),
                       "with_man": sum(r["has_man"] for r in rows), "with_json": sum(r["json"] for r in rows),
                       "used": sum(bool(r["chronoa_skills"]) for r in rows),
                       "candidates": sum(r["candidate"] for r in rows), "dbus": len(ifaces["dbus"]),
                       "typelibs": len(ifaces["typelibs"]), "python": len(ifaces["python"]),
                       "units": len(ifaces["units"]), "apps": len(apps),
                       "fits": {f: sum(f in r["fits"] for r in rows) for f in SURFACES},
                       "open": {f: sum(f in r["open_surfaces"] and r["candidate"] for r in rows) for f in SURFACES},
                       "chronoa": {k: len(v) for k, v in inventory.items() if isinstance(v, list)},
                       "os": {k: len(v) for k, v in ossurf.items()}}
    Path(args.out + ".json").write_text(json.dumps(data, indent=1))
    write_markdown(args.out + ".md", data)
    write_html(args.out + ".html", data)
    print(json.dumps(data["summary"]), f"-> {args.out}.json/.md/.html")
    if args.check:
        problems = check(data)
        for p in problems:
            print("CHECK:", p)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

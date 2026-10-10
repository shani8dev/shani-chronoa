"""Which of the tools the three shani-tools packages ship does Chronoa invoke?

The first attempt tokenised the source with `tr`, which left `` `tldr` `` and
`[usb-devices,` as tokens - so a tool that IS wired up read as a gap. This
parses the Python properly (ast, so only real string literals) and keeps any
literal that is a bare command name, which is what a subprocess argv element
or a shutil.which() argument looks like.

Run from the repo root:  python3 tools/audit_tool_packages.py .
"""
from __future__ import annotations

import ast
import pathlib
import sys

PACKAGES = {
    "shani-tools": """sudo which diff patch time git gpg man apropos plocate vi vim
        tmux nano words lynx ed bc bsd-games""",
    "shani-tools-extra": """inxi htop fastfetch tree dmidecode strace lsof ncdu
        sysstat smartmontools lm_sensors ddcutil liquidctl gptfdisk cpupower btop nvtop
        iotop cronie logrotate expect dialog pv jq micro texinfo tealdeer ripgrep
        man-pages subversion mercurial playerctl android-tools 7zip arj unrar
        unarchiver unzip unace zip lrzip lzop elfutils cabextract brightnessctl
        wl-clipboard ydotool wtype grim slurp vulkan-tools mesa-utils clinfo hdparm""",
    "shani-tools-network": """iftop bandwhich ethtool net-tools nmap traceroute whois
        wireless_tools tcpdump vnstat arpwatch inetutils usbutils bind openbsd-netcat
        curl wget aria2 nbd rsync zsync iperf3 mtr socat ngrep nethogs rclone restic
        tailscale cloudflared caddy wireshark-cli arp-scan gobuster""",
}

#: A command is not always the command: bind ships dig/delv/nslookup/host,
#: net-tools ships ifconfig/netstat, elfutils ships readelf, sysstat ships
#: iostat/mpstat/sar, and so on. Only commands can be *run*, so only commands
#: are checked, and these are the real binary names each package provides.
BINARIES = {
    "sudo": ["sudo"], "which": ["which"], "diffutils": ["diff"], "patch": ["patch"],
    "time": ["time"], "git": ["git"], "gnupg": ["gpg"], "man-db": ["man", "apropos"],
    "plocate": ["plocate"], "vi": ["vi"], "vim": ["vim"], "tmux": ["tmux"],
    "nano": ["nano"], "words": ["words"], "lynx": ["lynx"], "ed": ["ed"], "bc": ["bc"],
    "bsd-games": ["bsdgames"],
    "inxi": ["inxi"], "htop": ["htop"], "fastfetch": ["fastfetch"], "tree": ["tree"],
    "dmidecode": ["dmidecode"], "strace": ["strace"], "lsof": ["lsof"], "ncdu": ["ncdu"],
    "sysstat": ["iostat", "mpstat", "sar"], "smartmontools": ["smartctl"],
    "lm_sensors": ["sensors"], "ddcutil": ["ddcutil"], "liquidctl": ["liquidctl"],
    "gptfdisk": ["sgdisk", "gdisk", "cgdisk"], "cpupower": ["cpupower"], "btop": ["btop"],
    "nvtop": ["nvtop"], "iotop": ["iotop"], "cronie": ["crontab"],
    "logrotate": ["logrotate"], "expect": ["expect"], "dialog": ["dialog"],
    "pv": ["pv"], "jq": ["jq"], "micro": ["micro"], "texinfo": ["info"],
    "tealdeer": ["tldr"], "ripgrep": ["rg"], "man-pages": [],
    "subversion": ["svn"], "mercurial": ["hg"], "playerctl": ["playerctl"],
    "android-tools": ["adb"], "7zip": ["7z", "7za", "7zr"],
    "arj": ["arj"], "unrar": ["unrar"], "unarchiver": ["unar", "lsar"],
    "unzip": ["unzip"], "unace": ["unace"], "zip": ["zip"], "lrzip": ["lrzip", "lrzcat"],
    "lzop": ["lzop", "lzopcat"], "elfutils": ["readelf", "eu-readelf", "eu-elflint"],
    "cabextract": ["cabextract"], "brightnessctl": ["brightnessctl"],
    "wl-clipboard": ["wl-copy", "wl-paste"], "ydotool": ["ydotool"], "wtype": ["wtype"],
    "grim": ["grim"], "slurp": ["slurp"], "vulkan-tools": ["vulkaninfo"],
    "mesa-utils": ["glxinfo", "eglinfo"], "clinfo": ["clinfo"], "hdparm": ["hdparm"],
    "iftop": ["iftop"], "bandwhich": ["bandwhich"], "ethtool": ["ethtool"],
    "net-tools": ["ifconfig", "netstat", "route", "arp"], "nmap": ["nmap"],
    "traceroute": ["traceroute", "traceroute6"], "whois": ["whois"],
    "wireless_tools": ["iwconfig", "iw", "iwlist"], "tcpdump": ["tcpdump"],
    "vnstat": ["vnstat"], "arpwatch": ["arpwatch"], "inetutils": ["telnet", "ftp"],
    "usbutils": ["lsusb", "usb-devices"], "bind": ["dig", "delv", "nslookup", "host"],
    "openbsd-netcat": ["nc", "ncat"], "curl": ["curl"], "wget": ["wget"],
    "aria2": ["aria2c"], "nbd": ["nbdkit"], "rsync": ["rsync"], "zsync": ["zsync"],
    "iperf3": ["iperf3"], "mtr": ["mtr"], "socat": ["socat"], "ngrep": ["ngrep"],
    "nethogs": ["nethogs"], "rclone": ["rclone"], "restic": ["restic"],
    "tailscale": ["tailscale"], "cloudflared": ["cloudflared"], "caddy": ["caddy"],
    "wireshark-cli": ["tshark", "capinfos", "editcap"], "arp-scan": ["arp-scan"],
    "gobuster": ["gobuster"],
}


_EXEC_FUNCS = {"run", "Popen", "check_call", "check_output", "call"}
_WHICH = {"which", "which_command"}


def _executables(root: pathlib.Path) -> set[str]:
    """Commands that are actually EXECUTED, not merely named.

    Three earlier versions of this audit were wrong in opposite directions. The
    first tokenised source text with `tr`, which left `` `tldr` `` as a token
    and reported a wired-up tool as a gap. The second parsed every string
    literal, which reported `rclone`/`curl` as used because `capability.py`
    keeps a list of commands a skill is *allowed* to run - a permission table,
    not an invocation. The third resolved only literal argv lists, which
    reported `whois`/`dig`/`tcpdump` as unused when this codebase holds them in
    module constants: `_WHOIS = "whois"`.

    So argv[0] may be a literal, or a Name resolved against module-level and
    local string assignments. Those are the two places a command name turns
    into a process. Anything else is prose or policy.
    """
    executed: set[str] = set()
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        # Pass 1: every `NAME = "literal"` in the file, so `_DIG = "dig"` can
        # be resolved when argv[0] is the bare name `_DIG`.
        bindings: dict[str, str] = {}

        def bind(node: ast.AST) -> None:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if (isinstance(target, ast.Name)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    bindings[target.id] = node.value.value.strip()

        for node in ast.walk(tree):
            bind(node)

        def resolve(node: ast.AST) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value.strip()
            if isinstance(node, ast.Name):
                return bindings.get(node.id)
            if isinstance(node, ast.Subscript):
                return resolve(node.value)
            return None

        # Pass 2: the two places a name becomes a process.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _EXEC_FUNCS:
                if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                    for element in node.args[0].elts:
                        name = resolve(element)
                        if name:
                            executed.add(name)
            elif isinstance(func, ast.Attribute) and func.attr in _WHICH:
                for arg in node.args:
                    name = resolve(arg)
                    if name:
                        executed.add(name)
    return {name for name in executed
            if name and name.replace("-", "").replace("_", "").isalnum()}


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
    used = _executables(root)
    total_gap = 0
    for package, listing in PACKAGES.items():
        packages = listing.split()
        gaps, covered = [], []
        for pkg in packages:
            for binary in BINARIES.get(pkg, []):
                (covered if binary in used else gaps).append(binary)
        print(f"=== {package}: {len(covered)} used, {len(gaps)} unused ===")
        print(f"  USED:   {' '.join(covered)}\n")
        print(f"  UNUSED: {' '.join(gaps)}\n")
        total_gap += len(gaps)
    print(f"{total_gap} shipped commands no skill or sense invokes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
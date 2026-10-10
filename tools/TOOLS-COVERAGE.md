# Tool coverage: what `shani-tools*` ships that nothing calls

The answer to "which of the tools these three packages ship does no skill
or sense invoke", computed by `tools/audit_tool_packages.py` rather than by
reading a package list.

Run it from the repo root:

```bash
python3 tools/audit_tool_packages.py .
```

It resolves argv[0] and `shutil.which()` arguments out of the **AST**, not
by tokenising text. Three earlier versions of this were wrong and each
reported a real tool as a gap: a `tr`-based tokenizer left `` `tldr` `` and
`[usb-devices,` as tokens; matching all string literals matched the
`capability.py` allow-list; and argv-only missed module constants like
`_WHOIS = "whois"`. It errs toward a false gap rather than a false "used",
because a false gap costs one look and a false "used" costs a shipped
reader that never runs.

**As of 2026-10-10: 86 shipped commands that nothing invokes.** Most of them
are *correctly* unused. The point of this file is that the next survey
should be a diff against it rather than a re-derivation, so the verdicts
below are the record.

## Measured as duplicates — leave them unused

Each of these is on the image, and each is answered by something that reads
the kernel directly and is therefore both faster and more informative. The
measurements are from a real `@blue` slot and this dev box.

| command | answered by | why the command is the worse reader |
|---|---|---|
| `sensors` | `temperatures` → the `hwmon` sense | the sense read **two** fans at 3100 RPM with a per-fan *stopped* flag and the PWM enable meaning (`pwm1: 100% (255/255), enable 2 - automatic`); `sensors -j` returned **one** fan (`{'fan1_input': 3300.0}`) and nothing about PWM |
| `ethtool` | the `network` sense | reads `speed` and `duplex` from `/sys/class/net/<if>/`, so no process spawn; `interface_counters`' own docstring already points at the sense for link-vs-traffic |
| `vnstat` | `data_usage` | already invoked |
| `nmap` | `scan_network` | already invoked |
| `tree`, `inxi`, `fastfetch`, `btop`, `nvtop`, `ncdu` | `directory_tree`, `system_info`, `disk_usage` | `/proc` and `df`/`du`, on purpose, because these binaries can be absent on a minimal image |

`sensors` is the sharpest of these: it is the one command whose *own* output
looks strictly better than what we use, and it is not.

## Deliberately not built

| command | why |
|---|---|
| `strace` | tracing a process is not the fixed-whitelist's job. Used once for measurement (`strace -p`) and never by a skill |
| `hdparm` | needs root, and its answers are SMART's job — `disk_health` reads that |
| `telnet`, `ftp` | cleartext credentials over a socket |
| `cloudflared`, `caddy` | they open an **inbound** connection from the internet to this machine. Nothing here should do that |
| `zsync`, `nbdkit`, `gobuster` | no question a desktop user asks |
| `socat` port-forward | every skill here is a bounded subprocess with a post-condition; a listener is a *persistent process*, which is a lifecycle decision belonging to a person — the same reasoning that left `sing` without a surface |

## Wiring traps found while measuring, worth keeping

- **`unar` is not the universal reader its packaging suggests.** `arj`,
  `unar` and `lsar` all have `_PACKAGE_HINTS` entries and **no caller**. On a
  real `.arj` created by `arj`: `7z l` 0, `arj l` 0, `lsar` 0, **`unar -l` 1**.
  A routing rule built on `unar` fails on exactly the inputs it was added for.
- **Every reader takes a different listing flag**, and the wrong spelling
  exits non-zero with *no output* — indistinguishable from "unreadable":
  `unzip -l`, `7z l` (bare subcommand), `bsdtar -tf`.
- **Only `zcat` reads gzip** in the decompressor family on the image:
  `lzcat`, `unlzma` and `xzcat` all exit 1 on a `.gz` and `bzcat` exits 2 —
  correctly, because it is the wrong format for them.
- **`inxi` is not getopt-style**: `--no-color` exits 22 with "Unsupported
  option"; the flag is `-C`.
- **`capinfos` is not on either image.** It comes from `wireshark-cli`, and
  the presence check reports it absent, so no reader should route to it.

## The asymmetry no slot run sees

**Three of the four parser bugs found on 2026-10-10 came from reading lsof's
output as an unprivileged user, which a slot never does.** A slot runs as root
inside `systemd-nspawn`, so every `lsof` there returns readable paths and rc=0
with no warnings - measured, 3,893 lines and an empty stderr. Here, as a user:

    COMMAND PID USER   FD      TYPE DEVICE SIZE/OFF NODE NAME
    systemd   1 root  cwd   unknown        /proc/1/cwd (readlink: Permission denied)

Four rows, every one with the real name replaced by the unreadable path, and
**the gap between `PID` and `USER` collapsed to a single space** because pid 1
is short. Two shapes the slot cannot produce, and both changed the answer.

The same asymmetry is why `timestamps` differ: the `@blue` `sar` header is
24-hour (`06:40:39 CPU`), this box is 12-hour (`12:55:13 PM CPU`), so a column
alignment fixed on one distribution broke on the other while the slot-shaped
test suite stayed green. **`tools/probe_skills_on_host.py` is the thing that
closes this gap** - it dispatches each shipped skill here, as a user, and a
green run there is not evidence about here.

Rule worth keeping: **a test fixture captured from a root session is a fixture
that cannot see what an unprivileged read produces.**

## Built from this survey

| skill | command | what the current one could not answer |
|---|---|---|
| `backup_status` | `restic` | whether there is an off-machine backup at all |
| `bandwidth_to_host` | `iperf3` | speed to *another* machine, not the internet |
| `open_files` | `lsof` | which program has a **file** open — `/proc/net/tcp` has no answer for a file |
| `system_history` | `sar` | what the machine was doing *earlier*, which no live reading can say |
| `dns_lookup` | `delv` | the DNSSEC verdict (", fully validated") |
| `disk_activity`, `cpu_per_core`, `cpu_frequency`, `inspect_binary`, `process_detail`, `download_file`, `sync_folder`, `check_port`, `list_archive`, `extract_archive`, `vcs_status` | `iostat`, `mpstat`, `cpupower`, `readelf`, `/proc`, `curl`, `rsync`, `nc`, stdlib+7z, stdlib+7z, `git`/`svn`/`hg` | — |
## The matrices predate the last depends change (measured 2026-10-10)

**Five commands in `shani-tools-extra` are declared by the PKGBUILD but the
matrices on disk cannot say whether they land, because the images they describe
were built before they were added.**

| image | `image_version` | matrix generated |
|---|---|---|
| GNOME  | `20260925` | 2026-10-09T17:30:53Z |
| Plasma | `20260922` | 2026-10-09T18:21:55Z |

`shani-pkgbuilds` commit `173a759` landed at **2026-10-08T23:22Z** - roughly two
weeks *after* both images - and its diff to `shani-tools-extra/PKGBUILD` is:

    -pkgrel=13
    +pkgrel=15
    +    playerctl
    +    brightnessctl
    +    wl-clipboard
    +    grim
    +    slurp

So the matrix is recent in *file* mtime and stale in *content*: it was copied on
2026-10-09 but generated from an image whose package set predates the commit.
**A matrix's `generated` timestamp is not evidence about which packages the
current PKGBUILDs ship - `image_version` is.**

The 16 packages absent from both matrices, and what each case actually is:

| package | declared in | verdict |
|---|---|---|
| `playerctl`, `brightnessctl`, `wl-clipboard`, `grim`, `slurp` | `shani-tools-extra` (added 2026-10-08) | **unmeasurable from these matrices** - added after both images were built |
| `gptfdisk`, `btop`, `NVTOP`, `iotop`, `hdparm`, `cabextract` | `shani-tools-extra` | absent from images older than the current PKGBUILD; needs a fresh image to judge |
| `bsd-games` | `shani-tools` | not a question a desktop user asks |
| `nbd`, `wireshark-cli`, `gobuster` | `shani-tools-network` | `capinfos`/`editcap` absent for this reason - already recorded above |

**What survives the staleness: `grim` is a downgrade regardless of whether it
ships, and the reason is a fact about compositors rather than about which box it
was measured on.** `grim` implements `wlr-screencopy-unstable-v1`, which only
wlroots compositors (Sway, Hyprland, river) provide. Neither GNOME's Mutter nor
KDE's KWin is wlroots, so `grim` fails on both images by construction - measured
here as `compositor doesn't support wlr-screencopy-unstable-v1` (rc=1), which is
the same answer a real desktop gives. **The verdict holds on a stronger basis
than the matrix that was first used to reach it.**

The GNOME screenshot path was measured properly rather than assumed, and it is
the interesting half of this:

- `gnome-screenshot -f out.png` - **rc=124, no file**, after
  `Unable to use GNOME Shell's builtin screenshot interface, resorting to
  fallback X11` and `Unable to capture a screenshot of any window`.
- `org.gnome.Shell.Screenshot` on the session bus - **`AccessDenied: Screenshot
  is not allowed`**, because that service is behind a *bus-name allowlist*
  (`checkInvocation` throws `ACCESS_DENIED` for a sender not in it). Read out of
  `libshell-18.so`; the guard is a `_isSenderAllowed` check against watched
  well-known names, so a shell spawned by an assistant is not on it.
- `org.freedesktop.portal.Desktop` Screenshot - **works**: response
  `(0, {'uri': 'file:///.../Pictures/Screenshot.png'})`, and the file is a real
  1920x1080 RGBA PNG of 207,949 bytes. It is asynchronous (a request handle,
  then the `Request::Response` signal), so a caller must run a main loop.
- **But `xdg-desktop-portal` is on neither matrix either**, so this cannot be
  assumed to ship.

`screenshot` and `qr_code` already delegate to `screengrab.capture_screen()`,
which has its own backend chain (`grim`, then `gnome-screenshot`, then
`spectacle`, then XWD) - so there is **no gap to fill here** and the measurement
above is the reason to leave it alone. Building a second capture skill against
a portal that is not on the image would have been the confidently-wrong answer.

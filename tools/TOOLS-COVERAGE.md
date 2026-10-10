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

## Built from this survey

| skill | command | what the current one could not answer |
|---|---|---|
| `backup_status` | `restic` | whether there is an off-machine backup at all |
| `bandwidth_to_host` | `iperf3` | speed to *another* machine, not the internet |
| `open_files` | `lsof` | which program has a **file** open — `/proc/net/tcp` has no answer for a file |
| `system_history` | `sar` | what the machine was doing *earlier*, which no live reading can say |
| `dns_lookup` | `delv` | the DNSSEC verdict (", fully validated") |
| `disk_activity`, `cpu_per_core`, `cpu_frequency`, `inspect_binary`, `process_detail`, `download_file`, `sync_folder`, `check_port`, `list_archive`, `extract_archive`, `vcs_status` | `iostat`, `mpstat`, `cpupower`, `readelf`, `/proc`, `curl`, `rsync`, `nc`, stdlib+7z, stdlib+7z, `git`/`svn`/`hg` | — |
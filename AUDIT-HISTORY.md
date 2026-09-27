# shani-chronoa — audit history

Dated, full-methodology record of verification passes. The per-repo `AGENTS.md`
holds only current-state notes and points here for the full history, following
the same split already used in `shani-docs`, `shani-install-media`,
`shani-deploy` and `shani-builder`.

---

## 2026-09-28 — Machine-state senses, and three answers that were confidently wrong

Commits `4373d82`, `33486aa`, `6b1fdaa`. Adds nine senses (`contention`,
`privilege`, `thermal`, `display`, `network`, `bluetooth`, `camera`, `rfsense`,
`thermalgrid`), a re-arm latch for polled senses, and de-duplication in the
scheduler so a steady condition is not deposited 1440 times a day.

### The lesson, stated first because it is the transferable part

**Three of the nine senses were written, unit-tested, and green while
returning a confident wrong answer on the distribution this actually ships
to.** Every one of them failed by looking plausible rather than by erroring,
and 584 passing unit tests did not catch a single one.

ShaniOS is Arch-based (`pacman`). The development box is Ubuntu 24.04
(`dpkg-query`, `apt`, `psmisc` present). Four of the nine senses shell out to
a binary, and one asks the package manager a question whose answer is
distro-specific:

| Sense | External dependency | Failure on Arch |
|---|---|---|
| `contention` | `fuser` (psmisc) | OSError swallowed into "no holders" → **every camera and microphone reported FREE** — the exact inverse of its purpose |
| `privilege` | package ownership | `dpkg-query` does not exist → lookup returns nothing → **every process reported as unmanaged third-party software** |
| `bluetooth` | `bluetoothctl` (bluez) | **"0 devices"** — a confident answer to a question never asked |
| `thermalgrid` | `i2cdetect`, `pkexec` | handled honestly (UNKNOWN) |

The `privilege` one is the worst: it is not a degraded mode, it is the sense
crying wolf on its own operating system, and it buries the single real finding
under every distro daemon in the image. Ownership now goes through `pacman`,
`dpkg-query`, `rpm` or `zypper` — whichever answers — and when none does,
provenance is reported undetermined rather than assumed.

**Rule this establishes: a sense whose failure mode is a plausible-looking
wrong answer is worse than a sense that fails. Every "I could not determine X"
must be a state the code can actually represent, distinct from "X is false."**

### Verification status — read this before trusting the above

- **Unit suite on Ubuntu 24.04: 584 passed, 3 skipped.** Real, but proves
  little about ShaniOS, per the table above.
- **Real ShaniOS slot test: 35 pass, 0 fail, rc=0** (2026-09-28). Full
  sequence run on a genuinely installed slot:
  `./run_in_container.sh build.sh test bootstrap -p gnome -d latest`
  (completed: "Bootstrap complete (via real install.sh + configure.sh)"),
  then `testbed slot-test blue chronoa-machine-state --local-src-chronoa=…`.

  The decisive line, and the whole reason the test exists:

  ```
  RESULT privilege-uses-package-manager  PASS (18 further holder attributed
    to distribution packages, so the ownership lookup is actually working)
  ```

  On Arch the lookup resolves through `pacman -Qo` and 18 processes are
  correctly attributed to the packages that own them. Before the fix,
  `dpkg-query` did not exist on that slot, nothing was attributed, and the
  sense reported *every* process as unmanaged third-party software.
  `contention-fuser-respected` also passed — `fuser` present, real holder
  state, not the missing-binary path. All nine senses registered, all nine
  consent keys were in the *running compiled* schema, and consent genuinely
  refused (exit 4) for each of the nine.

  Real readings from the booted slot: `eth0: up, carrier`, `DNS:
  192.168.31.1`; `INT3400 Thermal: 20.0C, SEN1: 72.0C, SEN2: 90.0C`;
  `intel_backlight: 9514/96000 (10%)`; `hci0 (unblocked)`; and `privilege`
  reporting "No unmanaged software holds a dangerous capability" — correct
  for a clean image, in contrast to the Workpuls finding on the dev box.

  Two environment notes for anyone repeating this: `systemd-nspawn` needs
  cgroup delegation that a nested terminal scope does not provide, so the
  container must run with `--cgroupns=host`; and `run_in_container.sh` does
  not mount `shani-chronoa` at `/opt/shani-chronoa`, so the chronoa overlay
  has to be run with a hand-rolled `docker run` that adds that one mount.

### Bugs found by running, not by reading

Every one of these was invisible to a correct-looking diff.

1. **Schema in the wrong shape** — the senses loader validates an Ollama-style
   `{"type": "function", "function": {...}}` wrapper. A raw JSON-Schema dict is
   skipped at load with only a log line, so a registered-but-unusable sense
   looks merely absent.
2. **`/dev`-only device scan** — ALSA nodes live in `/dev/snd/`, so
   `contention` found the webcams and **no audio at all, including
   `pcmC0D0c`, the microphone**. It still loaded, still validated, still
   reported a plausible answer.
3. **Absolute-pattern `Path().glob`** — `Path().glob("/sys/...")` raises
   `NotImplementedError` rather than returning nothing, so `thermal` crashed
   on first use.
4. **Fixed-width proc format** — `/proc/net/wireless` writes `70.` and `-40.`,
   not `70` and `-40`. `int()` rejects both, so every line was skipped and
   `rfsense` reported "no wireless interface" on a machine that had one.
5. **Address format mismatch** — `i2cdetect` prints bare hex `33`; the table
   was keyed `0x33`. The comparison could never match, so `thermalgrid` would
   report "no array" **with one attached**.
6. **Permission refusal read as absence** — `i2cdetect` prints "Permission
   denied" to stderr and **exits 0**, so a scan that read nothing was
   indistinguishable from a clean scan that found nothing. Unprivileged, the
   sense reported "no thermal array is attached" — wrong precisely on a
   machine that has one. The probe is now three-way: found / read-and-empty /
   could-not-read.
7. **Re-arm measured from the wrong event** — the quiet window was measured
   from the last *observation* rather than the last *emission*, so any poll
   more frequent than the window reset the countdown every time and the re-arm
   could never arrive. The normal case (60s poll, 900s window) reset forever.
8. **An underscore in a sense name destroys the entire schema** —
   `glib-compile-schemas` rejects `_` in key names and, on hitting one,
   discards the **whole file**: *"This entire file has been ignored."* A
   sense named `thermal_array` therefore made its own consent key illegal and
   silently killed all nine keys at once. It is `thermalgrid` now, and a test
   asserts no sense name could produce an illegal key.

### Research correction

An earlier note in this session dismissed the idea that WiFi can produce a
picture or a body outline. **That was wrong**, and the user was right to push
back twice. The literature is real: RF-Pose (CVPR 2018) estimates skeletons
through walls at AP 58.1 where cameras fail outright and identifies people
from RF skeletons at 83%; RF-Avatar (MIT CSAIL) reconstructs 3D body meshes;
arXiv 2401.17417 synthesises images directly from CSI via a multimodal VAE;
Wi-Vi is a 3-antenna MIMO see-through-wall device.

The real constraint is hardware, not physics, and it is stated in
`senses/rfsense.py`: all of that runs on **Channel State Information** (52+
per-subcarrier amplitude/phase values), and this machine's card is an Intel
AX201 on `iwlwifi`, which exposes no CSI to userspace. The literature's Intel
5300 is a different part; ESP32-S3 rigs need a USB serial device that is not
attached. So RSSI — one aggregate number — is the coarser half: it answers
*is something moving*, not *what* or *who*. `sensing_ceiling()` reports this
as a first-class fact rather than letting a user assume otherwise.

Verified live: `link=70/70 level=-40dBm`, plus the ceiling naming both gaps.

Thermal imaging is real and cheap (MLX90640 32×24, AMG8833 8×8, both I2C,
both on `i2cdetect` addresses `0x33`/`0x60`) and needs the part wired up.
Escalation to read I2C is opt-in via `escalate=true` through `pkexec`: argv-only
with the bus number digits-validated, because a sense the model can call
should not silently request a password. Verified live with escalation: **17 of
17 buses read, no array present.**

### Security finding, unrelated to the above

`privilege`'s first real run surfaced `com.Workpuls.service` — **running, as
root (Uid 0)**, from `/usr/lib/Workpuls/WorkpulsService`, holding `CAP_NET_RAW`,
**`CAP_SYS_MODULE`**, `CAP_SYS_RAWIO`, `CAP_SYS_CHROOT` and `CAP_SYS_ADMIN`.
`dpkg -S` finds no package owning it, so it did not arrive with the OS.

This is what matching on a capability bit rather than a program name buys: it
was found by a general rule, not a vendor list. The sense is read-only and
takes no action on what it finds, and no evasion of it was built or attempted.

The same run showed why the first, hand-written daemon allowlist was wrong: it
produced 40 lines of expected hits (gdm3, cron, catatonit, systemd-udevd,
switcheroo-control) and buried the one line that mattered. A reader who sees
that hourly stops reading. Replacing it with package provenance collapsed the
output to a single finding, needs no vendor list, and does not rot when a
package is renamed.

### Test-hygiene notes

- `tests/test_sense_scheduler.py` asserted "no builtin sense is ambient" — true
  when written, false by design once the first ambient sense landed. Those
  assertions now derive from the registry rather than naming a sense, and the
  empty-registry branch is built from an explicit empty mapping so it keeps
  testing that branch however many senses are added.
- Mutation checking found two of its own mutations were **not** applying
  (wrong indentation; a regex matching a literal `\S`). A mutation that did
  not change the code cannot fail the suite, so "all mutations caught" is only
  true once each mutation is asserted to have applied.
- The permission branch in `thermalgrid` initially looked untested because
  removing it left the suite green. It was not redundant: the test only ever
  supplied empty stdout, which the fallback check also catches. A test that
  refuses *and* receives a table anyway is what separates the two.

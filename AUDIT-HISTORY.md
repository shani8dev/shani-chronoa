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

---

## 2026-09-28 — The verdict was computed and then thrown away

Commits `7c711a4` (settings window), `d6b0362` (verdict propagation), on top of
`2302690` (model resolver). This entry is about a bug class the previous entry
in this file already named — a plausible-looking answer that no assertion was
asking about — appearing in a place that had been fixed once and stayed fixed
only for the reader.

### `872a990` shipped verification and dropped the answer

The earlier pass made every skill's post-condition checked, and appended the
verdict to the string `execute_tool` returns. Both consumers of that answer
could not see it:

- **The audit trail.** `_TRACKER.record_call(...)` was called *before*
  `verification.verify()` ran, so the record was written at a moment when the
  verdict did not exist and had nowhere to put it. `tool_calls.log` had
  `origin` and `duration_ms` and nothing at all for whether the action worked.
- **The trigger engine.** `TriggerEngine.evaluate` treated "did not raise" as
  success, so a rule whose actuator ran and whose post-condition said the
  effect was absent was reported `fired=True`, told the user it had acted, and
  started its cooldown — for up to `MAX_COOLDOWN_SECONDS`, once per rule, with
  nothing in the `FireResult` saying otherwise.

Both consumers had inferred success from the *absence of an exception*, which
is the same substitution as `contention` reporting a device free when `fuser`
raised. The verdict existed; it was in the wrong shape to be used.

Fixed by making the verdict data: `ToolCallRecord` carries `verdict` **and**
`evidence` (the verdict alone is half a record — `result` holds the skill's own
account, so without the evidence the log reads `All done successfully.` next
to `verdict=failed`), the record is written *after* verification, and a FAILED
verdict leaves `last_fired_at` at `None` so the rule stays due and retries on
the next matching percept. `execute_tool`'s signature is unchanged;
`execute_tool_outcome` is the structured accessor.

`None` is reserved for a call that never reached verification at all. It is not
a synonym for `unverified`, and inventing a verdict there would be worse than
recording none.

### Two of my own tests were wrong before the code was

Both are the same mistake as above, pointed the other way, and both are worth
recording because the green run hid them:

- `record_call` takes a verdict, and I had added the field to
  `ToolCallRecord` without adding it to the method that constructs one. The
  suite caught it immediately.
- The new tests' throwaway skill modules were named `boom` and `liar`, which
  `tests/test_verification.py` also uses. `importlib` checks `sys.modules`
  before `sys.path`, so whichever file ran first won and the other silently
  asserted against a **different module body** — two files disagreeing about
  one module. Names are namespaced (`tt_*`) and the helper now purges
  `sys.modules`. This was a latent order-dependency in `test_verification.py`
  too, not only in the new file.

Suite 645 passed, 3 skipped. Five mutations fail it: moving the record back
before verification, dropping the evidence, restoring the unconditional
cooldown, making `verdict_from_text` always return `UNVERIFIED`, and blanking
the verdict column.

### The settings window: a defect that was not cosmetic

`settings_window.py` shipped **zero** controls for the seventeen
`*-sense-enabled` keys. A user could be told a sense was off and have no way
to turn it on except `gsettings set` from a terminal — the consent model was
unreachable from the GUI. It also hardcoded `background-color: #1a1a2e` and
hand-rolled its rows out of `Gtk.Box`, so it fought Adwaita instead of joining
it. Now `Adw.SwitchRow`/`EntryRow`/`PreferencesGroup` in eight groups, switches
generated from the registry, and no colours at all.

Verified by constructing it, not reading it. Three real bugs, none of which a
syntax check or a diff review would have shown:

- the `Adw.HeaderBar` was set as the window titlebar **and** appended to the
  content box — a second parent, which GTK rejects at construction with
  `gtk_widget_get_parent (child) == NULL`;
- `set_text()` on a `Gtk.SearchEntry` does **not** emit `search-changed`, so my
  first check appeared to show search was broken. It is not; it needs the
  signal a person typing produces. The test now types into the real entry;
- the completeness test failed on my own machine because the harness inherited
  my dconf, where I had enabled a sense by hand minutes earlier. A test whose
  claim is "only memory starts enabled" cannot run against the developer's real
  config, so the harness gets its own.

### Slot coverage caught up — and the test that judged it was wrong twice

`hwmon` and `modelfit` existed in the registry and were absent from the
testbed's `SENSES=(...)` list. The same rot as the overlay banner's hardcoded
key count: a literal that adding a sense does not update, in the one file whose
entire purpose is coverage of the wrong-distro case.

Adding them produced **42 pass / 0 fail on a real booted `@blue` slot** — but
getting there took three attempts, and two of them were my own bad assertions
rather than findings:

1. The generic step-5 branch explained an UNKNOWN from a sense with no external
   dependency as *"it found nothing to read"*. False: `modelfit` had read
   `/proc/meminfo` fine, and its UNKNOWN comes from `installed_models()`
   returning `None` because **Ollama did not answer**. A confident explanation
   for something nobody checked — the exact failure shape of the three bugs in
   the previous entry of this file, one level up.
2. Replaced it with a heuristic requiring a 3+ digit number as proof the sense
   had read something. Also wrong: `rfsense` legitimately reports
   `link=70/70 level=-29dBm`, all one or two digits, so the heuristic fails a
   real reading. This one is what produced a **40 pass / 1 fail** run in which
   no failing assertion could be found — the tell being the absence.
3. The correct answer is that the generic loop **cannot adjudicate** an UNKNOWN
   for a sense with no dependency, so it reports SKIP, and `modelfit` gets a
   dedicated check: say UNKNOWN rather than claim a model count, and keep the
   `/proc/meminfo` half real. On the slot that reads
   `31795 MB RAM total, 25656 MB available` with Ollama unreachable.

My placement of that check was also wrong twice: it landed inside the
`! -x $CLI` early-exit branch, before its `exit 0` (the file has two
`echo "== probe done"` lines and I replaced the first), so the 41-check run
that "passed" did not contain it at all.

### Packaging

`shani-pkgbuilds` had drifted three commits behind. Repinned to `d6b0362`,
`pkgrel` 4 → 6 (skipping 5, which was a repin staged locally and never
published). Built and verified **from the artifact rather than the PKGBUILD**:
`0.1.0-6`, 17 sense modules, 17 consent keys in the schema inside the tarball,
and all three changes physically present in the shipped Python.

### Not verified here

- No screenshot: no Xvfb, Weston or other headless display on the dev host. The
  settings window was verified by constructing the real widget tree and walking
  it, which catches the construction bugs but says nothing about how it looks.
- `modelfit`'s populated branch — Ollama installed and answering — has never
  run. Only the honest-UNKNOWN branch is proven, on a slot with no Ollama.
- No CSI-capable Wi-Fi, thermal array, mmWave, sonar or IR camera attached, so
  `rfsense`'s RSSI ceiling and `thermalgrid`'s I2C reads remain
  capability-only.

---

## 2026-09-28 — Looking at the window, and correcting what I believed about it

Commits `da09897`, `0588b2d`, `7ba022c`. This entry is mostly a correction to
the previous one, and the correction is the point.

### Rendering the window found what no test could

`DISPLAY=:1` turned out to be a real X11 session on seat0, and GTK can render
its own widget tree to a PNG through `Gsk.CairoRenderer` without a compositor,
a window manager, or any input. So for the first time in this repo's history the
UI was *looked at* rather than asserted about. It immediately showed two things
no test was asking:

- The sense rows were titled `hwmon`, `modelfit`, `thermalgrid` — module names,
  in front of a user with no reason to know them — with subtitles drawn from
  `description.split(". ")[0]`, which is not a sentence extractor and produced
  anything from a 50-character clause to a 300-character run-on. A sense's
  schema `description` is the LLM's **tool documentation**; it had been used as
  interface copy.
- Replies rendered as `Root has **38G** free` and `- / is 32% full` — the most
  visibly unfinished thing about a chat window.

Both are now fixed, and the markdown is the narrow subset documented in
`markdown_lite.py`: escape first, then introduce a fixed set of tags, so the
model cannot inject one. `[text](url)` is left as literal text on purpose.

### The `da09897` commit message named the wrong cause

It said the hardcoded `window.cajita-window { background-color: #14141f }` made
a light theme unusable. **That rule was dead CSS.** Nothing anywhere in the
tree ever added the `cajita-window` class, so it matched no widget and the
window background always followed the theme.

The real defect was the other half of the same stylesheet: near-white
*foregrounds* — `color: #e6e6ef` on the header, the state line and the detail
line. Measured by sampling the rendered header band on a light theme:

| version | darkest px | lightest px | contrast |
|---|---|---|---|
| before, Adwaita light | `(146,148,149)` | `(246,245,244)` | **2.80:1** |
| after, Adwaita light | `(46,52,54)` | `(246,245,244)` | **11.61:1** |

2.80:1 is well under WCAG AA's 4.5:1, and the lightest pixel in the band is
`(246,245,244)` against a `(246,245,244)` background — the glyphs were the
same lightness as the page. So the fix was needed and the improvement is real
and 4x; the explanation attached to it was wrong in its main clause.

**Two of the three things believed about this were wrong**, and only measuring
found it. I inferred the mechanism from reading the stylesheet. A description
of the before-screenshot then called the old header "black text, clearly
readable" — which the pixel sample contradicts outright. The rule the repo has
kept learning this session holds here too: a green signal, a passing test, and
a plausible description of a picture are three different kinds of evidence, and
this time two of them agreed with each other and were both wrong.

`test_window_theming.py` now asserts the *principle* rather than the old hex
list — no near-white literal in any `color` or `border-color` rule, no
near-black literal as a `background-color` — because the principle is what was
violated and a list of eight hexes would only catch those eight.

# The Chronoa AppImage

A single-file build of the assistant: GTK4, libadwaita, PyGObject, CPython 3.14,
the `httpx` transport, tesseract and bubblewrap, with nothing to install first.

```bash
bash tools/build-appimage.sh --squash     # build/.shani-chronoa-0.1.0-x86_64.AppImage
bash tools/build-appimage.sh              # AppDir only, for debugging
build/AppDir/AppRun                       # run it without squashing
```

The build is idempotent (it recreates `build/AppDir` every time) and takes about
a minute, most of which is `pip install mcp` and `appimagetool`'s runtime fetch.

**What is verified inside the finished squashfs image, and what is not, is written
down below rather than assumed.** The summary is uncomfortable in one place and
that is the honest result.

---

## What is inside

| | |
|---|---|
| `python3` + stdlib | the whole application is Python; nothing to compile |
| `gi` (PyGObject) | every window is GTK4 via introspection |
| typelibs | the introspected namespaces, which are separate `.typelib` files |
| GTK4 / libadwaita | pulled in transitively by the library walker (64 shared libraries) |
| `httpx` stack | the LLM transport — unlike numpy/PIL this is not optional |
| **bubblewrap** | **a hard depends.** Chronoa refuses to run a skill it cannot confine |
| **tesseract + `eng`** | **a hard depends.** The binary without language data reads nothing |
| `mcp` | optional second entry point; a fetch failure does not fail the build |
| `numpy` `PIL` `gguf` `cairo` `yaml` | what the skill modules import unconditionally |
| the `usr/` payload | copied verbatim, minus `__pycache__` |

**Deliberately not bundled:** `cv2`, `matplotlib`, `sklearn`, `onnxruntime`,
`symengine`, `pydantic`. Each sits behind a guarded import in the package and the
app degrades to an honest refusal when it is missing.

---

## What was actually verified, and how

Everything below was **run from inside the extracted squashfs**, not from the
build directory and not from the source checkout — an AppDir can work and the
image can still be broken, and only the second one is the deliverable.

The harness matters, so it is stated: extract with `--appimage-extract`, then run
commands **from inside `squashfs-root`** through a copy of `AppRun` whose final
`exec` line is replaced with `exec "$@"`. Running that modified runner from
outside the bundle silently mis-resolves every path, because `AppRun` derives
`$HERE` from its own location. A verification that "passes" that way proves
nothing.

| subsystem | result |
|---|---|
| tesseract OCR | **PASS** — reads real text with the bundled language data |
| bubblewrap sandbox | **PASS** — a confined child runs |
| sandboxed skill dispatch | **PASS** — `get_datetime` through the real executor, `ran=True` |
| MCP server | **PASS** — `MCPServer` (2.x) with **260 tools** registered |
| GSettings | **PASS** — compiled schema present, **222** keys readable on `org.shani.chronoa` |
| full application start | **PASS** — reaches "startup complete", reaches the local llama.cpp at `127.0.0.1:8765` (HTTP 200), loads the WebKitGTK 6.0 typelib, and restores the saved conversations it finds |
| consent gate | **PASS** — `delete_file` on a real file returns its own refusal (`ran=True`, i.e. "ran and refused", which is a legitimate fourth state) and **the file survives**; the post-condition honestly reports `FAILED` because the effect is absent |

Three real packaging bugs were found by running it, not by reading it:

1. **A self-referential root icon.** `ln -sf shani-chronoa.svg` created a symlink
   pointing at its own name, and `appimagetool` refused to emit the file at all.
2. **The wrong root desktop-file name.** The entry was `shani-chronoa.desktop`,
   but `Gio.Notification` is surfaced by GNOME only when a desktop file carries
   the app id's name. The app id is `dev.shani.chronoa`, so the root entry is now
   named after it.
3. **`TESSDATA_PREFIX` pointed one level too high**, which makes tesseract look
   for `.../5/eng.traineddata` and report *"couldn't load any languages"* — a
   sentence that reads as a broken build rather than a wrong path.

---

## Desktop integration: what works and what does not

The GNOME Shell extensions, the SearchProvider, the KRunner plugin and the systemd
user units are **all shipped inside the image** and exported through
`XDG_DATA_DIRS` by `AppRun`. None of them is adopted by a running desktop, and the
reasons are concrete rather than "probably".

Everything in this section was measured on a live GNOME session.

### systemd user units — will not load, and cannot from a squashfs

Pointing `SYSTEMD_UNIT_PATH` **directly at the AppDir's unit directory** and
asking systemd itself:

```
SYSTEMD_UNIT_PATH=build/AppDir/usr/lib/systemd/user \
  systemctl --user cat shani-chronoa-daemon.service
→ No files found for shani-chronoa-daemon.service.
```

The `@`-template is not listed either. (`shani-chronoa-llm.service` *does* appear
in that same command — because the **host already has it installed**, not because
the AppDir supplied it. That one is the exact shape of a result that looks like a
pass and measures nothing.)

All four units ship: `shani-chronoa-daemon`, `-lan-bridge`, `-llm`, `-model@`.

### D-Bus activation — busd uses its own environment, not the caller's

Starting the SearchProvider from a process launched with the AppImage's
`XDG_DATA_DIRS` already set:

```
StartServiceByName("dev.shani.chronoa.SearchProvider")
→ ServiceUnknown: The name ... was not provided by any .service files
```

`AppRun`'s export only reaches its own process tree. `busd` was started at login
and resolves service files against the `XDG_DATA_DIRS` **it** inherited. The file
also names `Exec=/usr/bin/shani-chronoa-search`, an absolute path that does not
exist on this host and does not exist inside the image at that location.

And the provider is demonstrably **not running**: of the 233 names currently on
the session bus (12 of them `org.gnome.Shell`), **none** is
`dev.shani.chronoa.*`. So the SearchProvider is not merely unactivated — it is
absent, and its `.ini` additionally names `DesktopId=shani-chronoa.desktop`, an
entry the desktop cannot resolve for the reason in the next section.

### The desktop entry — reachable, but not adoptable as shipped

This one is more interesting, because the mechanism itself works. Measured with a
positive control so that "nothing was found" cannot be mistaken for "the search
does not run here":

| probe | result |
|---|---|
| known-good entry on a synthetic `XDG_DATA_DIRS` | **visible** (96 entries vs 95 baseline) — the mechanism works |
| our entry, `Exec=shani-chronoa`, AppDir on `XDG_DATA_DIRS` only | **not visible** |
| same, **plus `build/AppDir/usr/bin` on `PATH`** | **visible** |
| same file but `Exec` naming a binary that does not exist | **not visible** |
| same file rewritten to an **absolute** `Exec` | **visible**, and `list_actions()` still returns the three Desktop Actions |

So the cause is exact: **GLib will not list a desktop entry whose `Exec` program
it cannot resolve**, and `appimagetool` left `Exec=shani-chronoa %U` verbatim
inside the finished image (verified by extracting `*.desktop` from the AppImage).
`AppRun` puts `$HERE/usr/bin` on `PATH` for itself only. A session launcher never
receives that, so the entry is unreachable from outside the running app.

With an absolute `Exec` the entry resolves and the desktop actions survive — which
is the whole of the fix direction, and why it is not simply *impossible*: it is
one condition that a self-registering entry at launch can satisfy, since `AppRun`
knows its own mounted path. **That is not implemented.** It is recorded because it
is a real lever and the measurement above is what makes it one.

### GNOME Shell extensions — not adopted

The running shell answers `ListExtensions` (so the probe is sound) and lists no
`chronoa-*` or `shani-*` extension. The shell builds its extension list from the
data directories **present when it started** and caches it; an `XDG_DATA_DIRS`
entry added later does not cause a rescan. All three extensions ship with their
`metadata.json`.

Enabling one would additionally need `~/.local/share/gnome-shell/extensions` plus
a `dconf` write — a root-free but user-visible change, so it is not done
implicitly.

### Auto-start and the "installed?" check degrade honestly

`desktop_integration.py:175` and `desktop.py:128` both name
`/usr/share/applications/shani-chronoa.desktop` absolutely. On this host that path
does not exist, and the app reports "not installed" rather than guessing. That is
the correct behaviour for a path it cannot reach, and it is worth saying
explicitly: **the failure mode is a refusal, not a wrong claim.**

### Summary

| integration | shipped in the image | adopted by a live desktop | why |
|---|---|---|---|
| Desktop entry / launcher icon | yes | **no** | `Exec` is not resolvable outside `AppRun`'s `PATH` |
| GNOME Shell extensions (3) | yes | **no** | the shell caches its list from startup-time data dirs |
| SearchProvider | yes | **no** | `busd` resolves service files from *its own* env; `Exec` is a host absolute path; no `dev.shani.chronoa.*` name is owned on the session bus |
| KRunner plugin | yes | **no** | same desktop-entry reason, via `DesktopId` |
| systemd user units (4) | yes | **no** | systemd does not read units out of the AppDir, verified against a unit that exists nowhere else |

**"Reachable" is not "adopted", and the difference is the whole of this table.**
The build ships all of it because that was the explicit instruction; what is
reported here is what a running session actually does with it.

---

## bubblewrap on Ubuntu, and why `AppRun` has a shim

The bundled `bwrap` **does not work on Ubuntu**, and no permission bit fixes it.

Ubuntu ships an AppArmor profile (`bwrap-userns-restrict`) that grants unprivileged
user namespaces **by binary path**. A copy at any other path is refused:

```
bwrap: setting up uid map: Permission denied
```

This is not squashfs-specific and not fixable with setuid or capabilities. The
decisive test: a plain-directory copy of the *host's own* `bwrap` fails
identically, while `/usr/bin/bwrap` works — so the profile simply does not match
the new path, and installing a policy for it needs root.

So `AppRun` **prefers the host's `bwrap`** — `/usr/bin/bwrap` or `/bin/bwrap` —
by creating a small `tmpdir` shim directory and prepending it to `PATH`, falling
back to the bundled copy on systems with no such restriction. The distribution's
own `bwrap` is the binary that distribution supports and is what the Debian/Arch
packages use anyway.

Without either, Chronoa **refuses to run a skill it cannot confine** rather than
pretending it was isolated. That is the honest outcome and it is the app's
documented behaviour.

One side effect: because `AppRun` `exec`s, the shim directory cannot be cleaned up
and a small symlink directory is left under `$TMPDIR` per launch. Harmless, and
unavoidable without a wrapper process.

---

## Other notes

- **`PYTHONHOME` is load-bearing.** `AppRun` sets `PYTHONHOME="$HERE/usr"`.
  Without it CPython resolves its prefix from the real `/usr` and imports the
  **host's** stdlib rather than the bundled one — a failure that looks like a
  missing-dependency error.
- **`PYTHONPATH` is absolute by design** and discards the caller's. That is
  intentional: a `PYTHONPATH` inherited from the caller's shell would otherwise
  put host packages ahead of the bundled ones.
- **`TESSDATA_PREFIX` points *at* `tessdata`**, not at its parent.
- **`GSETTINGS_SCHEMA_DIR`** is exported so the bundled compiled schema wins.
- **`appimagetool` warns** about `Categories` carrying two main categories
  (`Utility;Accessibility;...`) and about missing AppStream metadata. Both are
  cosmetic and neither was changed here; the desktop entry is copied verbatim on
  purpose so the Desktop Actions and the single-instance hint survive.
- **`appimagetool` is cached** in `build/` rather than `/tmp`, deliberately: a
  build that depends on a scratch file is a build that stops being reproducible
  when the machine reboots.
- **The build log's "bundled 66 shared libraries" counts two binaries.** The
  walker seeds on `bwrap` and `tesseract` as well as `python3`, and copies
  everything it walks into `usr/lib/` — so 64 libraries plus those two binaries
  makes 66. The copies in `usr/lib/` are redundant (the launcher resolves both
  from `usr/bin/` first) and harmless, but the number in the log is not a count of
  libraries. Read **64** from `find build/AppDir/usr/lib -maxdepth 1 -name '*.so*'
  | wc -l`.
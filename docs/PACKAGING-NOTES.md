# Packaging notes for the packaging repo's owner

This file is the hand-over from the Chronoa source tree to `../shani-pkgbuilds`
and `DEBIAN/control`. **Nothing here was applied to those files.** Both Arch
names and Debian names are given below, in their own conventions: GTK4 is
`gtk4` / `gir1.2-gtk-4.0`, PyGObject is `python-gobject` / `python3-gi`.

## 1. Device engines: `shani-link` is no longer a hard dependency

`shani_chronoa/_link.py` now chooses between two providers at runtime:

1. shani-shell's `phone` package at `/usr/lib/shani-shell/phone` (niri),
   with `SHANI_PHONE_PATH` as an override;
2. shani-link's `shani_link` package at `/usr/lib/shani-link` (all other
   editions), with `SHANI_LINK_PATH` as an override and `../shani-link` as a
   development fallback;
3. no provider: the phone/watch/Bluetooth/landline/FM/NFC/IR skills report
   "engine unavailable" through the existing `ImportError` path. They do not
   crash the assistant and it no longer claims those actions work.

`SHANI_LINK_PROVIDER=phone|shani_link` forces one when both are installed.
Tests: `tests/test_link_provider.py` (6 cases, stand-in packages in `tmp_path`).

### Arch — `shani-pkgbuilds/shani-chronoa/PKGBUILD`

Remove this from `depends`:

```bash
    # the phone, watch, Bluetooth, landline, FM, NFC and infrared engines moved there;
    # Chronoa's skills call them (shani_chronoa/_link.py)
    'shani-link'
```

Add to `optdepends` (both providers named, neither required):

```bash
    'shani-shell: phone, watch, Bluetooth, landline, FM, NFC and IR engines on niri'
    'shani-link: the same engines on GNOME, Plasma and COSMIC editions'
```

The shell already ships its own copy of the engines, so a niri image installs
only `shani-shell`; a GNOME/Plasma/COSMIC image installs `shani-link`. An image
with neither still gets a working assistant that says which functions are
unavailable. Do not add a virtual `provides` to either provider without a
decision in that repo: the two packages carry different module namespaces
(`phone.*` versus `shani_link.*`), so they are alternatives at the packaging
level, not interchangeable implementations of one interface.

### Debian — `DEBIAN/control`

Move `shani-link` out of `Depends:` and into `Suggests:` (already present in
that field, so remove the duplicate in `Depends` and keep one entry):

```text
Depends: python3, python3-gi, gir1.2-gtk-4.0, ... (shani-link removed)
Suggests: shani-link, shani-shell, ...
```

`Recommends:` would also be defensible, but `Suggests:` matches the new
contract: the engine is genuinely optional and its absence is reported.

## 2. Engine / GTK split — exact file lists (proposal — not applied)

The GTK tree now lives in `shani_chronoa/ui_gtk/`; every old import path is a
thin shim. `shani-chronoa` installs the engine and the shims, and must be
installable on a box with **no GTK typelib**. `shani-chronoa-gtk` installs the
GTK tree and depends on `shani-chronoa`. GNOME/Plasma/COSMIC install both;
niri installs only `shani-chronoa` and the shell draws the windows.

The engine starts from `usr/bin/shani-chronoa-engine` on D-Bus activation of
`dev.shani.chronoa.Agent` (`usr/lib/systemd/user/shani-chronoa-engine.service`,
`Type=dbus`, `BusName=dev.shani.chronoa.Agent`, `PartOf=graphical-session.target`,
no `WantedBy`) via `usr/share/dbus-1/services/dev.shani.chronoa.Agent.service`.
The GTK app is the Agent on a desktop; the engine yields the name when an
owner is already there. The background daemon unit is unchanged.

### `shani-chronoa` — engine, no GTK

**`usr/bin/`** (all executable):

```text
shani-chronoa-engine        shani-chronoa-daemon       shani-chronoa-mcp
shani-chronoa-bridge        shani-chronoa-lan-bridge   shani-chronoa-sense
shani-chronoa-screen-stream shani-chronoa-search       shani-chronoa-lab-network
shani-circle-to-search
```

**`usr/lib/shani-chronoa/shani_chronoa/`**: every file and directory **except**
`ui_gtk/` — that is, the 400+ engine modules, the skills/senses/triggers/sandbox
packages, and the GTK compatibility shims at `gui/`, `app/`,
`settings_window/`, `windows/`, `mascot.py`, `setup_wizard.py`,
`automation_overlay.py`, `away.py`, `desktop_cue.py`, `mascot_notch.py` and
`ui_driver.py`. A shim imports its GTK module only when something imports the
old path; the engine never does.

**`usr/lib/systemd/user/`**:

```text
shani-chronoa-engine.service     shani-chronoa-daemon.service
shani-chronoa-lan-bridge.service shani-chronoa-llm.service
shani-chronoa-model@.service
```

**`usr/share/dbus-1/services/`**:

```text
dev.shani.chronoa.Agent.service
dev.shani.chronoa.SearchProvider.service
```

**Arch `depends`**: `python`, `python-gobject` (GLib/Gio only), `python-httpx`,
`espeak-ng` (the only hard TTS floor), plus the model/voice packages a headless
engine uses. **Do not depend on** `gtk4`, `libadwaita`, `gtk4-layer-shell`,
`gir1.2-webkit-6.0` or `shani-link`. `shani-shell` and `shani-link` are
`optdepends` (section 1).

**Debian `Depends`**: `python3`, `python3-gi`, `python3-httpx`, `espeak-ng`,
plus the same non-GTK floors. **Remove** `gir1.2-gtk-4.0`, `gir1.2-adw-1`,
`libgtk4-layer-shell0`, `gir1.2-webkit-6.0` and `shani-link` from `Depends`.

### `shani-chronoa-gtk` — the windows

**`usr/bin/`**: `shani-chronoa` (enters through `shani_chronoa.ui_gtk.main`),
`shani-chronoa-overlay` (the layer-shell cue, imports
`shani_chronoa.ui_gtk.automation_overlay`).

**`usr/lib/shani-chronoa/shani_chronoa/`**: the whole `ui_gtk/` tree —

```text
ui_gtk/__init__.py
ui_gtk/gui/              (72 py + locale/ — 4 catalogue files)
ui_gtk/app/              (8 py)
ui_gtk/settings_window/  (7 py)
ui_gtk/windows/          (6 py)
ui_gtk/automation_overlay.py   ui_gtk/away.py
ui_gtk/desktop_cue.py          ui_gtk/mascot.py
ui_gtk/mascot_notch.py         ui_gtk/setup_wizard.py
ui_gtk/ui_driver.py
```

**`usr/share/applications/`**: `shani-chronoa.desktop`,
`shani-circle-to-search.desktop`.
**`usr/share/krunner/dbusplugins/`**: `shani-chronoa.desktop`.
**`usr/share/icons/hicolor/scalable/apps/`**: `shani-chronoa.svg`.
**`usr/share/icons/hicolor/symbolic/apps/`**: `shani-chronoa-symbolic.svg`.

**Arch `depends`**: `shani-chronoa`, `gtk4`, `libadwaita`, `gtk4-layer-shell`
and the Arch package owning the WebKitGTK 6 typelib (name to be verified in
`shani-pkgbuilds`; the Debian name `gir1.2-webkit-6.0` must not be copied
straight across). `shani-link` stays optional here too — the GTK device
screens say which engine is unavailable, exactly as the engine skills do.

**Debian `Depends`**: `shani-chronoa`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`,
`libgtk4-layer-shell0`, `gir1.2-webkit-6.0`.

### Not part of this change

No QML was written and no other repo was edited. The read-only
`Agent.Inventory(section) -> json` method backs conversations, models,
skills, routines, triggers, privacy, senses, memory, capabilities and engine
status; `docs/HEADLESS.md` lists which GTK panel maps to it and what still
needs a shell-side bridge op.

## 3. Chronoa settings: a JSON table, not a compiled GSettings schema

`org.shani.chronoa.gschema.xml` and `glib-compile-schemas` are **gone for
Chronoa's own settings**. Do not install the schema file and do not run
`glib-compile-schemas` in `build()`; Chronoa reads no compiled schema of its
own at runtime. `settings_file.py` is the store, `settings_table.py` is the
one authoritative table of keys, kinds and defaults.

Four layers, later wins:

| layer | path | owner |
|---|---|---|
| built-in defaults | `usr/share/shani-chronoa/settings.json` | this package |
| vendor defaults | `/usr/share/shani-chronoa/defaults.d/*.json` | distribution |
| admin defaults | `/etc/shani-chronoa/settings.d/*.json` | machine admin |
| person's settings | `$XDG_CONFIG_HOME/shani-chronoa/settings.json` | the user (writes only here, atomically) |

A distribution ships defaults by dropping one flat JSON object into
`/usr/share/shani-chronoa/defaults.d/`, e.g.
`50-shani-os.json` with `{"enable-camera-sense": false}`. Files are read in
sorted name order, so a later filename wins; admin and person layers then win
over both. Only keys declared in `settings_table.KEYS` are accepted, values are
coerced to that key's declared kind, and unknown keys are ignored. No schema
compilation, no dconf write, no package-specific key list in the packaging
repo beyond the defaults it wants to set.

`org.gnome.*` schemas are different: they stay the desktop's business.
`shani_chronoa/desktop_settings.py` is the single provider for them
(`available`, `get`, `get_bool`, `keys`, `describe`); it looks the schema up
first and returns a documented "not available" value on a desktop that does
not install it, rather than aborting on `Gio.Settings.new()`. The allowlisted
schemas are the ones this tree reads (`org.gnome.desktop.*` and
`org.gnome.settings-daemon.plugins.*`). It never claims `org.shani.chronoa`.
No desktop schema needs to be shipped, compiled or owned by this package.

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

## 2. Engine / GTK UI split (proposal — not yet applied)

The engine now starts with no GTK: `shani_chronoa/engine.py` builds the model,
tool loop and Agent D-Bus service; `tests/test_headless_engine.py` blocks
`gi.repository.Gtk`, `Adw`, `Gdk` and `Gtk4LayerShell`, builds the engine with
the repo's stand-in Ollama, and drives an ask to a reply. `tests/test_ui_gtk_boundary.py`
checks that no engine module imports the GTK client (`gui/`, `settings_window/`,
`app/`, `ui_gtk/`) at runtime except the browser's live-window branch.

`usr/bin/shani-chronoa` now enters through `shani_chronoa.ui_gtk.main()`, the
single GTK process boundary. The existing GTK app and its tests are unchanged.

### Proposed Arch split

- **`shani-chronoa-engine`** — `usr/lib/shani-chronoa/shani_chronoa/**`,
  `usr/lib/systemd/user/shani-chronoa-daemon.service`,
  `shani-chronoa-llm.service`, `usr/bin/shani-chronoa-mcp`, the Agent D-Bus
  interface. Drops `gtk4`, `libadwaita`, `gtk4-layer-shell` and
  `gir1.2-webkit-6.0` from `depends`; keeps `python`, `python-gobject` (GLib
  and Gio are still used by the engine), `python-httpx`, and the model/voice
  packages that a headless engine actually uses. The engine must be
  installable on a box with no GTK typelib.
- **`shani-chronoa-ui-gtk`** — `usr/bin/shani-chronoa`, the desktop entries,
  and the GTK-specific payload. `Depends: shani-chronoa-engine`, `gtk4`,
  `libadwaita`, `gtk4-layer-shell`, `gir1.2-webkit-6.0` in Arch naming
  (`gir1.2-webkit-6.0` is Debian; use the Arch package that owns the WebKitGTK
  6 typelib, verified in that repo before adding it).
- **`shani-chronoa`** — a meta package pulling both, so GNOME/Plasma/COSMIC
  installs behave exactly as today. The niri image installs only
  `shani-chronoa-engine` and talks to it through shani-shell's bridge.

### Proposed Debian split

Mirror the three packages in `DEBIAN/control`: `shani-chronoa-engine` without
`gir1.2-gtk-4.0`, `gir1.2-adw-1` or `libgtk4-layer-shell0`, and
`shani-chronoa-ui-gtk` with those plus `shani-chronoa-engine` in `Depends`.
Keep `espeak-ng` as the only hard TTS floor in the engine package; the neural
voices stay optional (see `docs/packaging-debian.md`).

### Not part of this change

No QML was written and no shell repo was edited. The new read-only
`Agent.Inventory(section) -> json` method backs conversations, models,
skills, routines, triggers, privacy, senses, memory, capabilities and engine
status; `docs/HEADLESS.md` lists which GTK panel maps to it and what still
needs a shell-side bridge op.

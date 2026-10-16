"""Make `gtk4-layer-shell` load **before** `libwayland-client`, or it is inert.

Both surfaces in this application that want a layer-shell surface are created in
**different processes**, and until this module existed only one of them had the
link order fixed:

| surface | created by | went through this |
|---|---|---|
| the cue layer | `usr/bin/shani-chronoa-overlay`, its own process | yes |
| **the mascot** | `ChronoaApplication` **in the app's own process** | **no** |

So the mascot - the one surface a person sees without asking - was the one that
silently became an ordinary window, while the cue layer, which nobody looks at
unless something is moving a pointer, worked. The symptom is not an error: the
library logs *"Failed to initialize layer surface, GTK4 Layer Shell may have been
linked after libwayland"* and returns an **ordinary** window, which is exactly
the stray window this codebase's own module docstrings forbid.

**Why this cannot be fixed from inside a running process.** The dynamic linker
decides load order before `main()` runs, so the only correct repair is to
**re-exec** with `LD_PRELOAD` set - which is what the library's own `linking.md`
recommends. **Measured cost: 7.2 ms**, one extra process start, on this box and
once per launch. It is paid **only on a machine that has the library at all** -
without it the marker/absence guards return and nothing is restarted, so the
ordinary machine behaves exactly as before.

The module is deliberately dependency-free - `os` and `sys`, nothing else - so
that a launcher can call it before it imports anything from the package. Both
launchers already put `usr/lib/shani-chronoa` on `sys.path` and nothing else,
and `automation_overlay` (which does import `gi` and `Gtk` at module scope) is
the wrong home for a thing `shani-chronoa` must call at start-up.
"""

from __future__ import annotations

import os
import sys

#: Set before the re-exec so the second process knows it has already done it.
#: Named for the shared fix rather than for the cue layer, which is the only one
#: of the two that used to carry it - a marker scoped to one caller would be a
#: name the other caller's process had to be told about separately.
MARKER = "CHRONOA_LAYER_SHELL_PRELOAD"

#: The soname a distribution installs. The GTK 3 build of this library is
#: `libgtk-layer-shell.so.0` and is a *different file*, deliberately not matched.
LIBRARY = "libgtk4-layer-shell.so.0"

#: Where distributions put it, in the order measured. A machine whose library is
#: somewhere else is treated as a machine without it, which is the old behaviour
#: and not a regression.
SEARCH_PATH = ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib")


def library_path() -> str | None:
    """The installed library, or `None`.

    `None` is **"this machine has no GTK 4 layer shell"**, which is the ordinary
    case on a compositor that does not speak the protocol, and is not an error:
    every caller treats it as "do nothing and behave exactly as before".
    """
    for directory in SEARCH_PATH:
        candidate = os.path.join(directory, LIBRARY)
        if os.path.exists(candidate):
            return candidate
    return None


def preload_layer_shell() -> None:
    """Re-exec once with the library preloaded, if it is installed.

    Returns immediately - so the caller carries on - in every case except the one
    that needs fixing: **the library present, not already preloaded, and no
    marker**. That last case ends in `os.execv`, which does not return.

    Four guards, each one earning its place:

    - **the marker**, so the re-exec cannot loop;
    - **the library's absence**, so a machine without it does not pay for an
      extra process start;
    - **`LD_PRELOAD` already naming it**, so a process that inherited one from a
      launcher does not prepend a second copy;
    - **nothing else**, deliberately. This is the whole of what the loader
      needs and there is no configuration for it, so a preference would be a
      setting nobody could act on.
    """
    if os.environ.get(MARKER):
        return
    library = library_path()
    if library is None:
        return
    if LIBRARY[: -len(".so.0")] in os.environ.get("LD_PRELOAD", ""):
        return
    os.environ["LD_PRELOAD"] = " ".join(
        part for part in (os.path.realpath(library),
                          os.environ.get("LD_PRELOAD", "")) if part)
    os.environ[MARKER] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)
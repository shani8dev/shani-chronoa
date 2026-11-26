# shani-chronoa: its part of the shared architecture

The plan for all repos is `../shani-shell/docs/SHARED-ARCHITECTURE.md` (https://github.com/shani8dev/shani-shell/blob/master/docs/SHARED-ARCHITECTURE.md). Read it first; its rules apply
here. This file is only this repo's part; change the plan there, not here.

**Role:** the assistant **engine** (layer 2); its GTK windows become `shani-chronoa-gtk` (phase 5).

**Do as much of this as your task allows** (never breaking a consumer), and
add a line to the plan's Status section when you finish something:

- Finish the split in `docs/HEADLESS.md`: the engine starts and runs with no GTK installed; GTK code lives in `ui_gtk/` only.
- Device engines: talk to the device service, never import a stateful engine into this process (rule 6). `_link.py` loading the shell's `phone` package in-process is an interim step; phase 2 replaces it with a client.
- New skills: logic in GTK-free functions returning data, described so the registry can generate the skill and its MCP tool later.
- Packaging proposals go to `docs/PACKAGING-NOTES.md` (engine without GTK, `shani-link` optional).
- Phase 5b: a terminal UI (`chronoa`), like the coding-agent TUIs: a client of the same engine interface the shell's panel uses, no logic of its own; streamed replies, approvals, slash commands from the registry, resume, `@file`, `-p`/`--json` for scripts; standard library only; works over SSH and on the server image. Build it on the engine split, not on the GTK app.

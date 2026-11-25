# GUI message catalogs

`messages.pot` is the source template: every user-facing string marked `_()` in
`gui/` and `gui/surfaces/`, including all panel titles and subtitles. Copy it to
`<code>/LC_MESSAGES/shani-chronoa.po`, fill in `msgstr` values, then compile:

    msgfmt -o hi/LC_MESSAGES/shani-chronoa.mo hi/LC_MESSAGES/shani-chronoa.po

A locale appears in `translated_locales()` only when its compiled `.mo` exists
and at least one `msgstr` is filled. An empty skeleton is a catalog to work on,
not a translated language; it is never advertised as one.

| Locale | State | Notes |
| --- | --- | --- |
| en | source | msgids are English; no catalog needed |
| hi | partial | 54 of 89 strings before the GUI-wide extraction; the template now has 140 entries, of which 54 are translated. Every panel title, shared status word and control is translated. Remaining `msgstr ""` entries are intentional; they are not compiled into the `.mo`, so those labels show the English msgid rather than a blank. |
| others | not started | Copy `messages.pot`; nothing is marked translated until a compiled catalog has real messages. |

The UI follows the **system** locale (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`,
`LANG`), not the `language` speech setting and not the `set_locale` skill, which
keeps its meaning: changing the system locale. A missing translation shows the
English msgid in normal mode, with a warning in the log. With
`SHANI_CHRONOA_DEV_LOCALE=1` (or `debug-mode`), missing translations are shown as
`[untranslated:<code>] <msg>` so they cannot pass as finished work.

Layout is left to GTK: strings are whole messages, never fragments joined with
`+`, so a translator can reorder words for Hindi or an RTL language, and the
widgets use `margin-start`/`margin-end`, `halign=START/END` and GTK's own text
shaping rather than left/right padding.

# GUI message catalogs

`messages.pot` is the source template: every user-facing string marked `_()` in
`gui/` and `gui/surfaces/`, including all panel titles and subtitles. Copy it to
`<code>/LC_MESSAGES/shani-chronoa.po`, fill in `msgstr` values, then compile:

    msgfmt -o hi/LC_MESSAGES/shani-chronoa.mo hi/LC_MESSAGES/shani-chronoa.po

A locale appears in `translated_locales()` only when its compiled `.mo` exists
and at least `MIN_TRANSLATION_COVERAGE` (90%) of the template's msgids have a
filled `msgstr`. An empty skeleton, or a stub with a single translation, is a
catalog to work on, not a translated language; it is never advertised as one.

| Locale | State | Notes |
| --- | --- | --- |
| en | source | msgids are English; no catalog needed |
| hi | complete | 140 of 140 strings translated; 100% coverage, compiled `.mo` present. Panel titles, subtitles, shared status words and controls are all Hindi. |
| others | not started | Copy `messages.pot`; nothing is marked translated until a compiled catalog clears the coverage floor. |

The UI follows the **system** locale (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`,
`LANG`), not the `language` speech setting and not the `set_locale` skill, which
keeps its meaning: changing the system locale. A missing translation shows the
English msgid in normal mode, with a warning in the log. With
`SHANI_CHRONOA_DEV_LOCALE=1` (or `debug-mode`), missing translations are shown as
`[untranslated:<code>] <msg>` so they cannot pass as finished work.

Proper nouns, setting names and accelerators (`Chronoa`, `Diff`, `Settings`,
`Ctrl+K`, `F9`) keep their spelling inside a Hindi sentence; they are labels a
person sees on a control, not words to translate.

Layout is left to GTK: strings are whole messages, never fragments joined with
`+`, so a translator can reorder words for Hindi or an RTL language, and the
widgets use `margin-start`/`margin-end`, `halign=START/END` and GTK's own text
shaping rather than left/right padding.

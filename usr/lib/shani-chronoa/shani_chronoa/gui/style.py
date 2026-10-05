"""How the window looks: the stylesheet, and reduced motion."""


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gtk, Gdk






class StyleMixin:
    """How the window looks: the stylesheet, and reduced motion. - a part of ChronoaWindow, which mixes it in."""


    def _apply_css(self) -> None:
        """Layout, shape and the orb's state palette - but no theme colours.

        This used to hardcode a dark window (`background-color: #14141f`) and
        hand-pick every foreground to match, so a user on a light GTK theme got
        a dark dialog with white borders that were invisible against it. The
        theme is the user's, and it already knows the answer: every colour here
        that describes *chrome* is now a named palette entry
        (`@theme_fg_color`, `@theme_base_color`, `alpha(...)`), which follows
        light, dark and high-contrast without this file knowing they exist.

        What stays hardcoded is the orb's state palette, and that is deliberate:
        green/amber/blue/violet/red are not decoration, they are how the
        assistant says what it is doing. A theme cannot supply "thinking", and
        diluting them into theme greys would cost the one signal the window
        depends on. The state is also carried by the icon and the state text, so
        colour is reinforcement rather than the only channel.
        """
        css_data = b"""
        /* The window takes the desktop's background. Naming a colour here at
           all is what made a light theme unusable. */
        .cajita-header {
            color: @theme_fg_color;
            font-size: 17px;
            font-weight: 600;
        }
        .cajita-state {
            color: @theme_fg_color;
            font-size: 14px;
            font-weight: 600;
        }
        .cajita-detail {
            color: alpha(@theme_fg_color, 0.65);
            font-size: 12px;
        }
        .transcript-turn {
            font-size: 14px;
            color: @theme_fg_color;
        }
        /* Bubbles are a tint of the foreground rather than a fixed dark grey,
           so they read as raised on a light theme and on a dark one without a
           second rule. */
        .transcript-user {
            color: @theme_fg_color;
            background-color: alpha(@theme_fg_color, 0.08);
            border-radius: 10px;
            padding: 8px 12px;
        }
        .transcript-assistant {
            color: @theme_fg_color;
            background-color: alpha(@theme_fg_color, 0.14);
            border-radius: 10px;
            padding: 8px 12px;
        }
        .transcript-placeholder {
            color: alpha(@theme_fg_color, 0.5);
            font-size: 13px;
        }

        /* A search hit. A tinted edge rather than a recoloured text: the text is
           model output that has already been through the escaping, and the only
           thing a search should change is how the turn looks. */
        .transcript-match {
            background-color: alpha(@accent_bg_color, 0.22);
            border-left: 3px solid @accent_bg_color;
            border-radius: 8px;
        }

        /* A reply's blocks.
           `card` is a libadwaita style name and this window is plain GTK4, so
           the blocks started life with a class that does nothing here: they
           rendered as unlabelled boxes of text, indistinguishable from prose.
           These rules are what give a command, a table, an equation and a tool
           call their shape - a tint of the foreground, a hairline border, a
           radius - so all of it follows the theme and none of it is a fixed
           colour. */
        .reply-block {
            background-color: alpha(@theme_fg_color, 0.06);
            border: 1px solid alpha(@theme_fg_color, 0.14);
            border-radius: 8px;
            padding: 8px 10px;
        }
        .reply-block-heading {
            font-weight: 600;
            font-size: 13px;
        }
        /* The code itself. Monospace at one size down from the prose, because a
           command in a 14px proportional face is not a command. */
        .reply-code {
            font-family: monospace;
            font-size: 13px;
        }
        /* Numbers in a table line up by character, so a right-aligned column of
           figures needs the same fixed-width face as code. */
        .reply-table-cell {
            font-family: monospace;
            font-size: 13px;
        }
        /* A tool call is a record, not prose: dimmed body, and a stripe down
           the leading edge that is the one thing a user scanning a transcript
           looks for. Failure gets the warning colour - the same signal the orb
           uses, because it is the same event. */
        .tool-card {
            background-color: alpha(@theme_fg_color, 0.05);
            border: 1px solid alpha(@theme_fg_color, 0.12);
            border-left: 3px solid @accent_bg_color;
            border-radius: 6px;
            padding: 6px 10px;
        }
        .tool-card.failed {
            border-left-color: #f59e0b;
        }
        .tool-card-body {
            font-family: monospace;
            font-size: 12px;
        }
        /* The header row's own buttons sit in the block's padding, so they get
           the dim treatment rather than competing with the code. */
        .reply-block button {
            opacity: 0.75;
        }
        .reply-block button:hover, .reply-block button:focus-visible {
            opacity: 1.0;
        }

        /* Per-state orb appearance. Idle is deliberately flat and drawn from
           the theme, since "nothing is happening" is not a colour anyone
           picked. Every active state is a real signal, so those stay fixed.
           All active states glow; idle is the only one with no glow, so a
           single-frame transition still reads as active. */
        .chronoa-orb {
            background-color: alpha(@theme_fg_color, 0.45);
            border-radius: 50%;
            min-width: 80px;
            min-height: 80px;
            box-shadow: none;
            transition: background-color 0.25s ease, box-shadow 0.25s ease;
        }
        .chronoa-orb:hover { box-shadow: 0 0 12px alpha(@theme_fg_color, 0.18); }
        .chronoa-orb:focus-visible {
            box-shadow: 0 0 0 3px alpha(@accent_bg_color, 0.9);
        }
        .chronoa-orb.state-listening {
            background-color: #22c55e;
            box-shadow: 0 0 22px rgba(34,197,94,0.55);
            animation: chronoa-listen 1.4s ease-in-out infinite;
        }
        /* Waiting for the model before transcribing. Listening's green with the
           slower pulse, because the microphone is open and has heard something -
           what is missing is not the mic but the accelerator. */
        .chronoa-orb.state-queued {
            background-color: #14b8a6;
            box-shadow: 0 0 18px rgba(20,184,166,0.4);
            animation: chronoa-listen 2.6s ease-in-out infinite;
        }
        .chronoa-orb.state-thinking {
            background-color: #f59e0b;
            box-shadow: 0 0 18px rgba(245,158,11,0.5);
        }
        .chronoa-orb.state-speaking {
            background-color: #3b82f6;
            box-shadow: 0 0 22px rgba(59,130,246,0.55);
        }
        .chronoa-orb.state-interrupting {
            background-color: #a855f7;
            box-shadow: 0 0 20px rgba(168,85,247,0.6);
        }
        .chronoa-orb.state-error {
            background-color: #ef4444;
            box-shadow: 0 0 20px rgba(239,68,68,0.55);
        }
        @keyframes chronoa-listen {
            0%   { box-shadow: 0 0 14px rgba(34,197,94,0.35); }
            50%  { box-shadow: 0 0 30px rgba(34,197,94,0.75); }
            100% { box-shadow: 0 0 14px rgba(34,197,94,0.35); }
        }

        /* The level halo. Its size comes from the recorder's RMS, so all CSS
           has to supply is the ring; the border colour tracks the orb's state
           so the two never disagree. The idle ring is drawn from the theme,
           because white-on-white is invisible and this used to be exactly
           that on a light theme. */
        .chronoa-halo {
            border-radius: 50%;
            border: 3px solid alpha(@theme_fg_color, 0.28);
            background-color: transparent;
        }
        .chronoa-halo.halo-idle { border-color: alpha(@theme_fg_color, 0.18); }
        .chronoa-halo.halo-listening { border-color: rgba(34,197,94,0.65); }
        .chronoa-halo.halo-queued { border-color: #14b8a6; }
        .chronoa-halo.halo-thinking { border-color: rgba(245,158,11,0.6); }
        .chronoa-halo.halo-speaking { border-color: rgba(59,130,246,0.6); }
        .chronoa-halo.halo-interrupting { border-color: rgba(168,85,247,0.7); }
        .chronoa-halo.halo-error { border-color: rgba(239,68,68,0.6); }

        /* Reduce-motion also stops the halo easing, since a ring that keeps
           changing size is motion too. */
        .reduce-motion .chronoa-halo { border-width: 2px; }

        /* Honour the desktop's reduce-motion setting by dropping the pulse
           while keeping the colour and icon that carry the same meaning. */
        .reduce-motion .chronoa-orb.state-listening {
            animation: none;
            box-shadow: 0 0 20px rgba(34,197,94,0.6);
        }
        .reduce-motion .chronoa-orb.state-queued {
            animation: none;
            box-shadow: 0 0 16px rgba(20,184,166,0.5);
        }
        .cajita-input {
            color: @theme_fg_color;
            font-size: 14px;
            padding: 8px 10px;
            background-color: @theme_base_color;
            border-radius: 8px;
            border: 1px solid alpha(@theme_fg_color, 0.2);
        }
        .cajita-input:focus {
            border-color: @accent_color;
        }
        .mic-off { color: #ef4444; }

        /* Suggestion chips. Outlined rather than filled so a screen full of
           them does not compete with the transcript for attention, and so the
           default-action styling stays reserved for Send. */
        .suggestion-chip {
            color: @theme_fg_color;
            font-size: 13px;
            padding: 6px 12px;
            border-radius: 15px;
            border: 1px solid alpha(@theme_fg_color, 0.25);
            background-color: alpha(@theme_fg_color, 0.04);
            transition: background-color 0.18s ease,
                        border-color 0.18s ease,
                        color 0.18s ease;
        }
        .suggestion-chip:hover {
            background-color: alpha(@accent_color, 0.16);
            border-color: alpha(@accent_color, 0.55);
        }
        .suggestion-chip:focus-visible {
            border-color: @accent_color;
        }

        /* Entrance for the chip row. Staggering is not expressible in GTK CSS,
           so the whole row fades together - a row that appeared one chip at a
           time would read as content still loading. */
        .suggestion-bar.entering {
            animation: chronoa-reveal 0.32s ease-out;
        }
        @keyframes chronoa-reveal {
            from { opacity: 0; }
            to   { opacity: 1; }
        }

        /* A new turn arriving, so the transcript does not jump. */
        .transcript-turn {
            animation: chronoa-turn-in 0.22s ease-out;
        }
        @keyframes chronoa-turn-in {
            from { opacity: 0; }
            to   { opacity: 1; }
        }

        /* Reduce-motion drops the entrances but keeps every colour, border and
           state cue - the animations here are decoration on top of a layout
           that is already complete, so removing them loses no information. */
        .reduce-motion .suggestion-bar.entering,
        .reduce-motion .suggestion-bar,
        .reduce-motion .transcript-turn {
            animation: none;
        }
        .reduce-motion .suggestion-chip {
            transition: none;
        }

        .help-group-heading {
            color: @theme_fg_color;
            font-size: 13px;
            font-weight: 700;
            padding: 14px 14px 4px 14px;
        }
        .help-row {
            padding: 6px 14px;
            border-bottom: 1px solid alpha(@theme_fg_color, 0.07);
        }
        .help-row-label {
            color: @theme_fg_color;
            font-size: 14px;
            font-weight: 600;
        }
        .help-row-detail {
            color: alpha(@theme_fg_color, 0.72);
            font-size: 12px;
        }
        /* The open panel, in the sidebar.
           `SidebarPage.select()` stamps `selected` on the row that is open and
           removes it from every other. Until this rule existed the class was set
           by code that nothing read, so the sidebar stopped saying where you
           were - which is the exact regression `select()`'s own docstring says
           it was written to fix, and it was invisible for the same reason: the
           method ran, and marking nothing looks identical to marking something
           that is styled like every other row.
           The accent left edge is the part that survives a long list: the row's
           own background is easy to miss when the panel has scrolled the list. */
        .sidebar-row.selected {
            background-color: alpha(@accent_bg_color, 0.16);
            box-shadow: inset 3px 0 0 0 @accent_bg_color;
        }

        /* A diagnostics finding, by verdict. The three-word vocabulary the
           diagnostics panel already uses - working, not working, could not
           determine - was stamped onto each row as a CSS class and read by
           nothing, so the panel's entire health language was body-text grey.
           These are the orb's own colours, because it is the same event: a thing
           that is not working is what the error state is for. */
        .diagnostics-row-working { color: rgba(34,197,94,0.9); }
        .diagnostics-row-not-working { color: #ef4444; }
        .diagnostics-row-could-not-determine { color: rgba(245,158,11,0.95); }

        /* A panel's own health, on the row that says it. The colours are the
           diagnostics rows' three, reused rather than chosen again, so the app
           has one visual language for health: green means working, red means
           not working, amber means the panel could not tell.

           Scoped to `.status-row` and to `.status-dot`/`.status-word` rather
           than to the bare state names on purpose, and this was found by
           looking at a render rather than by reading the CSS: the state class is
           applied to the row as well as to the dot and the word, so a bare
           `.status-attention { color: ... }` inherits into every descendant and
           turned the entire summary sentence red because the dot beside it was
           red. Only the dot and the one word carry the colour; the explanation
           stays body text. */
        .status-row.status-ok .status-dot,
        .status-row.status-ok .status-word { color: rgba(34,197,94,0.9); }
        .status-row.status-attention .status-dot,
        .status-row.status-attention .status-word { color: #ef4444; }
        .status-row.status-unknown .status-dot,
        .status-row.status-unknown .status-word { color: rgba(245,158,11,0.95); }

        /* A sidebar row's health dot. The state class is applied to the dot
           itself rather than to a `.status-row` ancestor - a sidebar row is not
           a status row - so the colours are named again here, from the same
           three values, rather than reaching for a selector that cannot match. */
        .status-dot.status-ok { color: rgba(34,197,94,0.9); }
        .status-dot.status-attention { color: #ef4444; }
        .status-dot.status-unknown { color: rgba(245,158,11,0.95); }

        /* The dot. A round swatch rather than a glyph, because a glyph has to
           come from the icon theme and an icon theme is free not to have it -
           the whole class of bug where a missing name renders as an empty box.
           10px, centred vertically against the row's text.

           `currentColor` is the dot's own colour, resolved from the state rule
           above; `background-color` takes it as a paint, so the label's text
           colour is never what paints the circle. */
        .status-dot {
            background-color: currentColor;
            border-radius: 5px;
            min-width: 10px;
            min-height: 10px;
        }

        /* The word. Fixed weight, so a column of panels can be read down its
           left edge instead of each sentence being read in full. */
        .status-word { font-weight: 600; }

        /* Tabular sense output. The key column is right-aligned so the values
           form a single edge; the value column takes the rest, because a long
           path or a `df` row will not fit on one line whatever else is done. */
        .key-value-row { padding: 1px 0; }
        .key-value-key {
            font-family: monospace;
            font-size: 12px;
            opacity: 0.75;
        }
        .key-value-text {
            font-family: monospace;
            font-size: 12px;
        }

        /* The gate line is the whole reason this window exists, so it is the
           only coloured text in it: green when the skill is usable, amber when
           it will silently do nothing. */
        .help-row-gate {
            font-size: 12px;
            padding-top: 2px;
        }
        .help-row-gate-open { color: rgba(34,197,94,0.9); }
        .help-row-gate-closed { color: rgba(245,158,11,0.95); }
        """
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css_data)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _apply_motion_preference(self) -> None:
        """Respect the desktop reduce-motion preference.

        Checked once at construction. This is about the assistant announcing
        itself without movement, so it is scoped to the window's own animations
        rather than suppressing every animation in the app: the orb's
        listening pulse and the level halo are how a voice app shows it is
        *hearing* you, and a still orb during recording reads as a dead control
        rather than a considerate one.
        """
        settings = Gtk.Settings.get_default()
        if settings is None:
            return
        if not settings.get_property("gtk-enable-animations"):
            self.add_css_class("reduce-motion")

    def _motion_is_reduced(self) -> bool:
        return self.get_css_classes().__contains__("reduce-motion")

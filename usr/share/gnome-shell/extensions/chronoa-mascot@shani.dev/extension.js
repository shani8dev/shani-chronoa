// Chronoa's notch: a top-centre strip holding the mascot, what it is doing and
// the step it is on.
//
// This is coucou's architecture, not a workaround: coucou IS a GNOME Shell
// extension, and this is the route that works on the compositor this product
// actually ships on. `mascot.available()` is False on Mutter because Mutter has
// never implemented `zwlr_layer_shell_v1`, and that refusal is correct - an
// always-on-top GTK window is pinned bottom-right, which is exactly what
// mascot.py's own docstring promises never happens.
//
// **Why a notch and not a floating corner sprite.** Coucou's README says the
// mascot "lives in your Mac's notch - or at the top of your screen on Windows
// and Linux", and `windows/README.md:566-576` says the same in as many words:
// "GNOME has no layer-shell, so there it runs through XWayland as a dock window:
// top centre, on every workspace, still there after Super+D". The notch is the
// *surface* - a top-edge strip that stays put while work happens under it - and
// it holds three things at once: the mascot, the line saying what it is doing,
// and the tool step it is on. A bare sprite in a corner carries none of that.
//
// The art is NOT reimplemented here. `mascot_art.svg()` stays the single
// generator and Python rasterises it through the same GdkPixbuf pipeline
// `_render()` uses; this loads the result from a file, which is the only route
// GJS has (PART 9 of HARVEST-COUCOU.md: there is no byte push, because
// `St.ImageContent.set_bytes` wants a `CoglContext` this Shell's GJS cannot
// produce).
//
// Stacking is under `Main.uiGroup`, which PART 9 measured drawing over a
// fullscreen window at 57,600/57,600 px. `Main.layoutManager.overlayGroup` does
// not exist on GNOME 50 and is not needed: uiGroup is a sibling of
// Meta_WindowGroup.

import Gio from 'gi://Gio';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// `SIZE` is mascot.SIZE in the Python package and `MARGIN` is its
// `_BASE_MARGIN`. **Both are named after the symbol rather than typed**, because
// a copy of a number that drifts is the shape this repository keeps paying for,
// and the recorder in tools/ imports the real ones and asserts these against
// them.
const SIZE = 132;

// The notch's own geometry. **Fixed, not content-derived, and that is the
// decision rather than the shortcut**: a `ClutterBox` asks its children for a
// preferred size, and a `ClutterActor` from `load_file_sync` answers through
// `ClutterActorClass.get_preferred_width`, which does nothing - so a box laid
// out around one reports -1 and the strip jumps as the text changes. The strip
// does not move when the text does, and nothing is measured off its width at
// runtime; `tools/mascot_recording.py` reads the *mascot's* rect, which is the
// thing that must be measurable.
const NOTCH_W = 560;
const NOTCH_PAD = 11;          // notch edge -> mascot edge
// **The strip is as tall as the sprite plus its padding, and that is arithmetic
// rather than a chosen number.** The first version typed `NOTCH_H = 78` against a
// 132px mascot, which put the sprite at `y = (78 - 132) / 2 = -27`: the top 27
// rows of the character were off the screen and the recording reported it as the
// actor's position, correctly, while the picture showed half a mascot. A strip
// shorter than what it holds is not a small mistake - it is the strip deciding
// where the mascot is, which it has no business doing.
const NOTCH_H = SIZE + 2 * NOTCH_PAD;
// Baselines, as fractions of the sprite's own height so they follow `SIZE`.
const NOTCH_TITLE_Y = NOTCH_PAD + SIZE * 0.45;
const NOTCH_DETAIL_Y = NOTCH_PAD + SIZE * 0.68;
const RADIUS = 22;

// The gap between the strip and the screen edge, which is `mascot._BASE_MARGIN`.
// Named here for the same reason `SIZE` is - and read back by the recorder,
// which imports the Python constant and asserts against this file's usage of it.
const MARGIN = 28;

// The notch's palette, as **numbers rather than a stylesheet** so the drawing and
// its own contrast are one thing. `rgba(16,16,20,0.92)` over an arbitrary
// desktop is 17.0:1 against white text, measured with the formula this
// repository uses for every other contrast claim in it.
// **0.97, not 0.92.** At 0.92 a window's border behind the strip was
// legible through it as a red-brown outline sitting in the middle of the
// mascot's own half of the panel - visible in the first screenshot and
// gone at 0.97, which still reads as translucent over a bright desktop.
const BG = [0.062, 0.062, 0.078, 0.97];
const EDGE = [1.0, 1.0, 1.0, 0.10];
const TITLE = [0.94, 0.95, 0.96, 1.0];
const DETAIL = [0.66, 0.69, 0.73, 1.0];

const IFACE = `
<node>
  <interface name="org.shani.Chronoa.Mascot">
    <!-- Load the art from a file. Returns true when an actor was loaded. -->
    <method name="Art">
      <arg type="s" name="path" direction="in"/>
      <arg type="b" name="loaded" direction="out"/>
    </method>
    <!-- The two lines of the strip. Empty means "no such line", which is not
         the same as an empty strip: the height is fixed either way, so the
         notch does not resize when a turn ends. -->
    <method name="Status">
      <arg type="s" name="title" direction="in"/>
      <arg type="s" name="detail" direction="in"/>
    </method>
    <!-- Where the mascot actually is on screen. Four scalar out-args rather
         than one (iiii) tuple: GJS's wrapJSObject marshals a JS array into
         several out-args reliably, and answers "incorrect value type" for a
         tuple it has to build itself. This is the *mascot's* rect, not the
         strip's, because the sprite is what a recorder measures. -->
    <method name="Rect">
      <arg type="i" name="x" direction="out"/>
      <arg type="i" name="y" direction="out"/>
      <arg type="i" name="w" direction="out"/>
      <arg type="i" name="h" direction="out"/>
    </method>
    <!-- The strip's own rect, so a caller can ask "is the notch up" without
         inferring it from the sprite's position. -->
    <method name="Notch">
      <arg type="i" name="x" direction="out"/>
      <arg type="i" name="y" direction="out"/>
      <arg type="i" name="w" direction="out"/>
      <arg type="i" name="h" direction="out"/>
    </method>
    <!-- How tall the Shell's own top bar is, which is where the strip starts.
         Read rather than typed: the bar is 27px on one theme and 30 on another,
         and a hard-coded 28 is a number nobody re-measures. It is 0 when the
         bar is auto-hidden, which is the correct answer then - the strip may use
         the whole top edge. -->
    <method name="PanelHeight">
      <arg type="i" name="height" direction="out"/>
    </method>
    <!-- The real pointer position as the Shell sees it. Read rather than
         assumed: the pointer is moved by a virtual device, and a driver that
         trusted its own coordinates would be measuring its own arithmetic. -->
    <method name="Pointer">
      <arg type="i" name="x" direction="out"/>
      <arg type="i" name="y" direction="out"/>
    </method>
    <method name="Show"><arg type="b" name="visible" direction="in"/></method>
    <method name="Scale"><arg type="i" name="factor" direction="in"/></method>
  </interface>
</node>`;

class Mascot {
    constructor(extension) {
        this._actor = null;
        this._notch = null;
        this._factor = 1;
        this._cache = St.TextureCache.get_default();
        this._title = '';
        this._detail = '';
        this._visible = true;
        // The settings live in the **application's** schema, and this extension
        // declares it in `metadata.json` so `getSettings()` resolves it. It is
        // the same schema `mascot.py` reads, so the two cannot disagree about
        // where the companion lives - and `settings-schema` is also what the
        // private-shell harness compiles, which is the only way a test can put
        // the notch at the top rather than in the corner it defaults to.
        try {
            this._settings = extension.getSettings();
        } catch (e) {
            this._settings = null;
        }
    }

    _corner() {
        const value = this._settings?.get_string('mascot-corner');
        return value || 'bottom-right';
    }

    // --- the strip ------------------------------------------------------------

    _ensureNotch() {
        if (this._notch)
            return this._notch;
        const area = new St.DrawingArea({
            reactive: false,
            can_focus: false,
            width: Math.round(this._factor * NOTCH_W),
            height: Math.round(this._factor * NOTCH_H),
        });
        area.connect('repaint', () => this._paint(area));
        this._notch = area;
        Main.uiGroup.add_child(area);
        return area;
    }

    _paint(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        const f = this._factor;
        const r = Math.round(RADIUS * f);

        cr.save();
        cr.setSourceRGBA(BG[0], BG[1], BG[2], BG[3]);
        // Cairo has no rounded-rect primitive on every backend this Shell
        // carries, so this is arcs-and-lines rather than `arc()` twice - and it
        // is a **save/restore** pair, because `area.get_context()` hands back
        // the actor's *shared* context and a leaked path state paints every
        // later repaint of every other drawing area in the session.
        cr.newSubPath();
        cr.arc(width - r, r, r, -Math.PI / 2, 0);
        cr.arc(width - r, height - r, r, 0, Math.PI / 2);
        cr.arc(r, height - r, r, Math.PI / 2, Math.PI);
        cr.arc(r, r, r, Math.PI, 3 * Math.PI / 2);
        cr.closePath();
        cr.fill();
        cr.setSourceRGBA(EDGE[0], EDGE[1], EDGE[2], EDGE[3]);
        cr.setLineWidth(Math.max(1, Math.round(f)));
        cr.stroke();

        const left = Math.round((NOTCH_PAD + SIZE) * f);

        cr.selectFontFace('Cantarell, sans-serif', 0, 0);
        cr.setFontSize(Math.round(15 * f));
        cr.setSourceRGBA(TITLE[0], TITLE[1], TITLE[2], TITLE[3]);
        cr.moveTo(left, Math.round(NOTCH_TITLE_Y * f));
        cr.showText(this._title || 'Chronoa');

        cr.selectFontFace('Cantarell, sans-serif', 0, 0);
        cr.setFontSize(Math.round(12.5 * f));
        cr.setSourceRGBA(DETAIL[0], DETAIL[1], DETAIL[2], DETAIL[3]);
        cr.moveTo(left, Math.round(NOTCH_DETAIL_Y * f));
        if (this._detail)
            cr.showText(this._detail);

        cr.restore();
        cr.$dispose();
    }

    // --- placement ------------------------------------------------------------

    _place() {
        // The notch is top-centre; the mascot sits inside its left edge.
        //
        // **Both actors are positioned on themselves, and both are direct
        // children of `uiGroup`.** A `St.BoxLayout` manages its children's
        // positions, so a `set_position` on a child of one is silently
        // overridden - measured: `Rect()` answered (0, 0, 132, 132) with the
        // actor inside a box. `Main.uiGroup` is a plain container that does not
        // lay out its children, which is what PART 9's 57,600/57,600
        // measurement relied on.
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const f = this._factor;
        const nw = Math.round(NOTCH_W * f);
        const nh = Math.round(NOTCH_H * f);
        const size = Math.round(SIZE * f);
        const corner = this._corner();

        // **The strip starts below the Shell's top bar, and that is the first
        // version's bug read off a screenshot.** At `y = monitor.y` the strip's
        // own top 27 pixels sat *behind* the clock: "Oct 17 15:38" was legible
        // through it. Coucou's notch is in hardware and does not have this
        // problem; a strip drawn by a desktop shell does, and covering the
        // clock to get the top edge is the wrong trade - it takes away the one
        // thing in that strip everybody reads all day.
        const bar = Math.round(Main.panel.height * f);
        this._panelHeight = bar;

        let nx, ny;
        if (corner === 'top-center') {
            nx = monitor.x + Math.round((monitor.width - nw) / 2);
            ny = monitor.y + bar;
        } else if (corner === 'top-right') {
            nx = monitor.x + monitor.width - nw - Math.round(MARGIN * f);
            ny = monitor.y + bar;
        } else {
            // The historical home, and the only corner where the strip is not
            // a strip: bottom-right keeps the mascot where it has always been
            // and the strip sits above it.
            nx = monitor.x + monitor.width - nw - Math.round(MARGIN * f);
            ny = monitor.y + monitor.height - nh - Math.round(MARGIN * f);
        }

        if (this._notch) {
            this._notch.set_position(nx, ny);
            this._notch.show();
        }
        if (!this._actor)
            return;
        if (corner === 'top-center' || corner === 'top-right') {
            this._actor.set_position(nx + Math.round(NOTCH_PAD * f),
                                      ny + Math.round(NOTCH_PAD * f));
        } else {
            this._actor.set_position(nx + Math.round((nw - size) / 2),
                                      ny - size - Math.round(NOTCH_PAD * f));
        }
    }

    // --- the exported surface -------------------------------------------------

    // **These names are the D-Bus method names.** `wrapJSObject` dispatches on
    // the name (or `<Name>Async`), so a well-behaved helper called `load`
    // answers nothing and the bus reports "Method Art is not implemented".
    Art(path) {
        const file = Gio.File.new_for_path(path);
        if (!file.query_exists(null)) {
            return false;
        }
        // Six arguments, cache first and `file` second - the signature that is
        // easy to get wrong by reading it as the Python one.
        const size = Math.round(this._factor * SIZE);
        const actor = this._cache.load_file_sync(
            this._cache, file, size, size, 1, 1);
        if (!actor)
            return false;
        this._ensureNotch();
        if (this._actor)
            this._actor.destroy();
        this._actor = actor;
        Main.uiGroup.add_child(actor);
        this._place();
        actor.show();
        this._applyVisible();
        return true;
    }

    Status(title, detail) {
        this._title = title || '';
        this._detail = detail || '';
        this._ensureNotch();
        if (this._notch)
            this._notch.queue_repaint();
    }

    Rect() {
        if (!this._actor)
            return [0, 0, 0, 0];
        const [x, y] = this._actor.get_position();
        return [x, y, this._actor.width, this._actor.height];
    }

    Notch() {
        if (!this._notch)
            return [0, 0, 0, 0];
        const [x, y] = this._notch.get_position();
        return [x, y, this._notch.width, this._notch.height];
    }

    PanelHeight() {
        return this._panelHeight;
    }

    Pointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    Show(visible) {
        this._visible = !!visible;
        this._applyVisible();
        if (visible)
            this._place();
    }

    Scale(factor) {
        this._factor = Math.max(1, parseInt(factor, 10) || 1);
        if (this._notch) {
            this._notch.set_size(Math.round(NOTCH_W * this._factor),
                                 Math.round(NOTCH_H * this._factor));
            this._notch.queue_repaint();
        }
        this._place();
    }

    /** Re-place when the Shell's own top bar moves - a rotation changes its
     *  height, and an auto-hiding bar changes it on every reveal. */
    _watchBar() {
        this._barId = Main.panel.connect('notify::height', () => this._place());
    }

    _applyVisible() {
        if (this._notch)
            this._notch.visible = this._visible;
        if (this._actor)
            this._actor.visible = this._visible;
    }
}

export default class ChronoaMascot extends Extension {
    enable() {
        this._mascot = new Mascot(this);
        this._mascot._watchBar();
        this._object = Gio.DBusExportedObject.wrapJSObject(IFACE, this._mascot);
        this._object.export(Gio.DBus.session, '/org/shani/Chronoa/Mascot');
        this._owner = Gio.bus_own_name(
            Gio.BusType.SESSION, 'org.shani.Chronoa.Mascot',
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    disable() {
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._object?.unexport();
        this._mascot?._actor?.destroy();
        this._mascot?._notch?.destroy();
        this._object = null;
        this._mascot = null;
        this._owner = 0;
    }
}
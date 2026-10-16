// The Chronoa mascot as a GNOME Shell actor.
//
// This is coucou's architecture, not a workaround: coucou IS a GNOME Shell
// extension, and this is the route that works on the compositor this product
// actually ships on. `mascot.available()` is False on Mutter because Mutter has
// never implemented `zwlr_layer_shell_v1`, and that refusal is correct - an
// always-on-top GTK window is pinned bottom-right, which is exactly what
// mascot.py's own docstring promises never happens.
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
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// `SIZE` is mascot.SIZE in the Python package and `MARGIN` is its
// `_BASE_MARGIN`. **Both are named after the symbol rather than typed**, because
// a copy of a number that drifts is the shape this repository keeps paying for,
// and the recorder below imports the real ones and asserts these against them.
const SIZE = 132;
const MARGIN = 28;

const IFACE = `
<node>
  <interface name="org.shani.Chronoa.Mascot">
    <!-- Load the art from a file. Returns true when an actor was loaded. -->
    <method name="Art">
      <arg type="s" name="path" direction="in"/>
      <arg type="b" name="loaded" direction="out"/>
    </method>
    <!-- Where the mascot actually is on screen. Four scalar out-args rather
         than one (iiii) tuple: GJS's wrapJSObject marshals a JS array into
         several out-args reliably, and answers "incorrect value type" for a
         tuple it has to build itself. -->
    <method name="Rect">
      <arg type="i" name="x" direction="out"/>
      <arg type="i" name="y" direction="out"/>
      <arg type="i" name="w" direction="out"/>
      <arg type="i" name="h" direction="out"/>
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
    constructor() {
        this._actor = null;
        this._factor = 1;
        this._cache = St.TextureCache.get_default();
    }

    _place() {
        // Bottom-right, which is where the layer-shell version puts it: anchored
        // BOTTOM+RIGHT at _BASE_MARGIN in mascot.py, so the recording shows the
        // mascot where the shipped product would.
        //
        // **On the actor itself, and it must be a direct child of uiGroup.**
        // A `St.BoxLayout` manages its children's positions, so a
        // `set_position` on a child of one is silently overridden - measured:
        // `Rect()` answered (0, 0, 132, 132) with the actor inside a box.
        // `Main.uiGroup` is a plain container that does not lay out its children,
        // which is what PART 9's 57,600/57,600 measurement relied on.
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._actor)
            return;
        const size = Math.round(this._factor * SIZE);
        this._actor.set_position(
            monitor.x + monitor.width - size - MARGIN,
            monitor.y + monitor.height - size - MARGIN);
    }

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
        if (this._actor)
            this._actor.destroy();
        this._actor = actor;
        Main.uiGroup.add_child(actor);
        this._place();
        actor.show();
        return true;
    }

    Rect() {
        if (!this._actor)
            return [0, 0, 0, 0];
        const [x, y] = this._actor.get_position();
        return [x, y, this._actor.width, this._actor.height];
    }

    Pointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    Show(visible) {
        if (visible)
            this._place();
        if (this._actor)
            this._actor.visible = visible;
    }

    Scale(factor) {
        this._factor = Math.max(1, parseInt(factor, 10) || 1);
    }
}

export default class ChronoaMascot extends Extension {
    enable() {
        this._mascot = new Mascot();
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
        this._object = null;
        this._mascot = null;
        this._owner = 0;
    }
}
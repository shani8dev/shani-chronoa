// Chronoa window control: a small, fixed D-Bus interface over mutter's windows.
//
// GNOME locks org.gnome.Shell.Eval and has no protocol through which another
// Wayland client may focus, close or move a window, so on GNOME this extension
// *is* the permission: Chronoa can arrange windows while it is enabled and
// cannot when it is not. The interface is a fixed list of window operations,
// never a way to run code in the shell, and every method takes a window id from
// List() - a window that has gone away is an error, never a guess.

import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'org.shani.Chronoa.Windows';
const OBJECT_PATH = '/org/shani/Chronoa/Windows';
const IFACE = `
<node>
  <interface name="org.shani.Chronoa.Windows">
    <method name="List"><arg type="s" direction="out" name="json"/></method>
    <method name="Focus"><arg type="t" name="id"/></method>
    <method name="Close"><arg type="t" name="id"/></method>
    <method name="Minimize"><arg type="t" name="id"/></method>
    <method name="Maximize"><arg type="t" name="id"/><arg type="b" name="on"/></method>
    <method name="Fullscreen"><arg type="t" name="id"/><arg type="b" name="on"/></method>
    <method name="Move"><arg type="t" name="id"/><arg type="i" name="x"/><arg type="i" name="y"/></method>
    <method name="Resize"><arg type="t" name="id"/><arg type="i" name="width"/><arg type="i" name="height"/></method>
    <method name="SetWorkspace"><arg type="t" name="id"/><arg type="i" name="index"/></method>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>`;

// The window types a person would call "a window": no menus, tooltips or docks.
const LISTED = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG];

function windows() {
    return global.get_window_actors()
        .map(a => a.get_meta_window())
        .filter(w => w && LISTED.includes(w.get_window_type()) && !w.is_skip_taskbar());
}

function find(id) {
    const w = windows().find(x => String(x.get_id()) === String(id));
    if (!w)
        throw new Error(`no window has id ${id}; it may have closed - list the windows again`);
    return w;
}

function now() {
    return global.get_current_time() || global.display.get_current_time_roundtrip();
}

function isMaximized(w) {
    return typeof w.is_maximized === 'function' ? w.is_maximized() : w.get_maximized() !== 0;
}

// mutter 49 dropped the flags argument from maximize()/unmaximize().
function setMaximized(w, on) {
    const call = on ? 'maximize' : 'unmaximize';
    try {
        w[call]();
    } catch (e) {
        w[call](Meta.MaximizeFlags.BOTH);
    }
}

class WindowsService {
    get Version() {
        return 1;
    }

    List() {
        const focus = global.display.get_focus_window();
        return JSON.stringify(windows().map(w => {
            const r = w.get_frame_rect();
            const ws = w.get_workspace();
            return {
                id: String(w.get_id()),
                title: w.get_title() || '',
                app_id: w.get_gtk_application_id() || w.get_sandboxed_app_id() || '',
                wm_class: w.get_wm_class() || '',
                pid: w.get_pid(),
                workspace: ws ? ws.index() : -1,
                focused: w === focus,
                minimized: w.minimized,
                maximized: isMaximized(w),
                fullscreen: w.is_fullscreen(),
                x: r.x, y: r.y, width: r.width, height: r.height,
            };
        }));
    }

    Focus(id) {
        // A D-Bus call carries no input event, so get_current_time() is 0 here;
        // mutter treats a 0 timestamp as "CurrentTime", which it warns can pick
        // the wrong focus window. A round trip gives a real one. (Measured: the
        // headless shell honoured a 0 timestamp too, so the live test cannot
        // tell the two apart - this is mutter's documented contract, not a
        // reproduced failure.)
        const w = find(id);
        const ws = w.get_workspace();
        if (ws && ws !== global.workspace_manager.get_active_workspace())
            ws.activate_with_focus(w, now());
        else
            w.activate(now());
    }

    Close(id) {
        // delete() asks the client, which can still ask about unsaved work.
        find(id).delete(now());
    }

    Minimize(id) {
        const w = find(id);
        if (!w.can_minimize())
            throw new Error('this window cannot be minimized');
        w.minimize();
    }

    Maximize(id, on) {
        const w = find(id);
        if (on && !w.can_maximize())
            throw new Error('this window cannot be maximized');
        setMaximized(w, on);
    }

    Fullscreen(id, on) {
        const w = find(id);
        if (on)
            w.make_fullscreen();
        else
            w.unmake_fullscreen();
    }

    Move(id, x, y) {
        const w = find(id);
        if (!w.allows_move())
            throw new Error('this window does not allow moving');
        if (isMaximized(w))
            setMaximized(w, false);
        w.move_frame(true, x, y);
    }

    Resize(id, width, height) {
        if (width < 1 || height < 1)
            throw new Error('width and height must be positive');
        const w = find(id);
        if (isMaximized(w))
            setMaximized(w, false);
        const r = w.get_frame_rect();
        w.move_resize_frame(true, r.x, r.y, width, height);
    }

    SetWorkspace(id, index) {
        const w = find(id);
        const n = global.workspace_manager.get_n_workspaces();
        if (index < 0 || index >= n)
            throw new Error(`workspace ${index} does not exist; there are ${n} (0 to ${n - 1})`);
        w.change_workspace_by_index(index, false);
    }
}

export default class ChronoaWindowsExtension extends Extension {
    enable() {
        this._service = new WindowsService();
        this._object = Gio.DBusExportedObject.wrapJSObject(IFACE, this._service);
        this._object.export(Gio.DBus.session, OBJECT_PATH);
        this._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    disable() {
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._object?.unexport();
        this._object = null;
        this._service = null;
        this._owner = 0;
    }
}

// Chronoa window control: a small, fixed D-Bus interface over mutter's windows.
//
// GNOME locks org.gnome.Shell.Eval and has no protocol through which another
// Wayland client may focus, close or move a window, so on GNOME this extension
// *is* the permission: Chronoa can arrange windows while it is enabled and
// cannot when it is not. The interface is a fixed list of window operations,
// never a way to run code in the shell, and every method takes a window id from
// List() - a window that has gone away is an error, never a guess.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {ChronoaIndicator} from './indicator.js';

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
    <method name="Tile"><arg type="t" name="id"/><arg type="s" name="side"/></method>
    <method name="Indicator"><arg type="s" direction="out" name="json"/></method>
    <method name="Cue"><arg type="s" name="text"/><arg type="b" name="hold"/></method>
    <method name="PointerMove"><arg type="i" name="x"/><arg type="i" name="y"/></method>
    <method name="PointerClick"><arg type="s" name="button"/><arg type="i" name="count"/></method>
    <method name="KeyPress"><arg type="s" name="combo"/><arg type="i" name="repeat"/></method>
    <method name="TypeText"><arg type="s" name="text"/></method>
    <method name="LaunchApp"><arg type="s" name="desktop_id"/></method>
    <method name="PointerPosition"><arg type="i" direction="out" name="x"/><arg type="i" direction="out" name="y"/></method>
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

// Clutter's numbers: mutter turns them into evdev codes itself (sent 0x110,
// it logged "Unknown/invalid virtual device button 0x21b").
const BUTTONS = {left: 1, middle: 2, right: 3, 1: 1, 2: 2, 3: 3};
const MODIFIERS = {
    ctrl: 'Control_L', control: 'Control_L', shift: 'Shift_L', alt: 'Alt_L',
    super: 'Super_L', meta: 'Super_L', win: 'Super_L', cmd: 'Super_L',
};
const devices = {};

function usec() {
    return GLib.get_monotonic_time();
}

function device(kind) {
    if (!devices[kind]) {
        const seat = Clutter.get_default_backend().get_default_seat();
        devices[kind] = seat.create_virtual_device(kind);
        fresh.add(kind);
    }
    return devices[kind];
}

// **Made on first use, then given a moment.** The first event sent to a
// device created that instant was dropped ("hello" typed "ello"). Creating
// them at enable() instead fixed that and broke focus switching (with the
// devices present, closing the overview during Focus lost the switch -
// bisected 2026-10-15), so a new device waits before its first event.
const fresh = new Set();

async function ready(kind) {
    const dev = device(kind);
    if (fresh.has(kind)) {
        fresh.delete(kind);
        await wait(80);
    }
    return dev;
}

const pointer = () => device(Clutter.InputDeviceType.POINTER_DEVICE);
const keyboard = () => device(Clutter.InputDeviceType.KEYBOARD_DEVICE);

// "Return", "ctrl", "F5", "a", "Page_Down" -> a keysym; an unknown name is an
// error that says so, never a guess.
function keyval(name) {
    const lower = name.toLowerCase();
    const named = MODIFIERS[lower] ?? ({enter: 'Return', esc: 'Escape', del: 'Delete',
        space: 'space', pgup: 'Page_Up', pgdn: 'Page_Down', backspace: 'BackSpace'})[lower] ?? name;
    const direct = Clutter[`KEY_${named}`];
    if (direct !== undefined)
        return direct;
    if ([...name].length === 1)
        return Clutter.unicode_to_keysym(name.codePointAt(0));
    throw new Error(`no key called ${name}`);
}

// A character's keysym: for Latin-1 it *is* the code point, and above that
// X11 keysyms are 0x01000000 + the code point. Measured: through
// Clutter.unicode_to_keysym, "hello" typed nothing while press_key's "a" did.
function wait(ms) {
    return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    }));
}

function charKeysym(ch) {
    const cp = ch.codePointAt(0);
    return cp <= 0xff ? cp : 0x01000000 | cp;
}

// **"Chronoa is doing this" - on screen, not only in the chat.** Input from
// the extension looks exactly like the person's own, so every action says so:
// a pill at the top of the screen with what is being done, and a ripple where
// a click lands. Drawn by the shell, so it is above every window.
let cueLabel = null;
let cueTimer = 0;
let lastCue = '';

function cue(text, hold = false) {
    lastCue = text;
    if (!cueLabel) {
        cueLabel = new St.Label({style_class: 'chronoa-cue', reactive: false});
        Main.uiGroup.add_child(cueLabel);
    }
    cueLabel.remove_all_transitions();
    cueLabel.text = text;
    cueLabel.opacity = 255;
    cueLabel.show();
    const monitor = Main.layoutManager.primaryMonitor;
    const [, width] = cueLabel.get_preferred_width(-1);
    cueLabel.set_position(Math.round(monitor.x + (monitor.width - width) / 2),
        monitor.y + Main.panel.height + 10);
    if (cueTimer)
        GLib.source_remove(cueTimer);
    cueTimer = 0;
    if (hold)
        return;  // stays until the next cue or clearCue() - listening, speaking
    cueTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1600, () => {
        cueTimer = 0;
        cueLabel?.ease({opacity: 0, duration: 400, mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => cueLabel?.hide()});
        return GLib.SOURCE_REMOVE;
    });
}

function clearCue() {
    if (cueTimer)
        GLib.source_remove(cueTimer);
    cueTimer = 0;
    cueLabel?.ease({opacity: 0, duration: 300, mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        onComplete: () => cueLabel?.hide()});
}

// An orange outline round the window Chronoa is acting on, fading out - so a
// window that moves, closes or takes focus is seen to be Chronoa's doing.
function flashWindow(w, what) {
    try {
        const rect = w.get_frame_rect();
        const frame = new St.Widget({style_class: 'chronoa-window-flash', reactive: false,
            x: rect.x - 4, y: rect.y - 4, width: rect.width + 8, height: rect.height + 8});
        Main.uiGroup.add_child(frame);
        frame.ease({opacity: 0, duration: 900, delay: 350,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD, onComplete: () => frame.destroy()});
        cue(`Chronoa ${what} ${w.get_title() || 'a window'}`);
    } catch (e) {
        log(`chronoa: could not outline the window: ${e.message}`);
    }
}

function ripple(x, y) {
    const ring = new St.Widget({style_class: 'chronoa-ripple', reactive: false,
        width: 28, height: 28, x: x - 14, y: y - 14});
    ring.set_pivot_point(0.5, 0.5);
    Main.uiGroup.add_child(ring);
    ring.ease({scale_x: 2.4, scale_y: 2.4, opacity: 0, duration: 500,
        mode: Clutter.AnimationMode.EASE_OUT_QUAD, onComplete: () => ring.destroy()});
}

function releaseCue() {
    if (cueTimer)
        GLib.source_remove(cueTimer);
    cueTimer = 0;
    cueLabel?.destroy();
    cueLabel = null;
}

function releaseDevices() {
    fresh.clear();
    for (const kind of Object.keys(devices))
        delete devices[kind];
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
    // What the top-bar indicator shows, as JSON - read-only, so a test (and a
    // person debugging) can see it without a screenshot.
    // What Chronoa is doing, on screen: every tool call (the app sends the
    // same words as its tool cards), and a held cue while it listens.
    Cue(text, hold) {
        if (text)
            cue(text, hold);
        else
            clearCue();
    }

    Indicator() {
        const snap = this.indicator?.snapshot() ?? null;
        if (snap)
            snap.cue = lastCue;
        return JSON.stringify(snap);
    }

    // 2: pointer and keyboard input (PointerMove, PointerClick, KeyPress,
    // TypeText). 3: LaunchApp. Chronoa reads this before using them.
    get Version() {
        return 3;
    }

    // The desktop launches it, as clicking it in the app grid would. Started
    // from inside a skill, an app inherited the skill's resource limits (an
    // address-space cap among them) and GNOME Calculator died at start -
    // measured 2026-10-15. From the shell it gets an ordinary session's.
    LaunchApp(desktopId) {
        const app = Shell.AppSystem.get_default().lookup_app(desktopId);
        if (!app)
            throw new Error(`no app called ${desktopId} is installed`);
        cue(`Chronoa is opening ${app.get_name()}`);
        app.activate();
    }

    // **Input from inside the shell.** On GNOME Wayland no outside program can
    // inject input: wtype needs a protocol Mutter does not speak, ydotool a
    // root daemon, and the RemoteDesktop portal is not always there. Measured
    // 2026-10-15 in a private shell: type_text, click_pointer and move_pointer
    // reached nothing. The shell itself can, through Clutter virtual devices -
    // what GNOME's own on-screen keyboard uses. As with the window verbs,
    // enabling this extension is the permission; Chronoa's
    // input-control-enabled switch still gates every call on its side.
    // **Across, then down - never diagonally out of a corner.** The pointer
    // often rests at (0,0), GNOME's hot corner, which is guarded by pressure
    // barriers. A diagonal move out of it pushed against them: the pointer
    // stopped at (640, 0) and the hot corner opened the Activities overview,
    // so every following click landed on the overview, not the window
    // (measured 2026-10-15; across-then-down reached the button and clicked).
    // Each leg is re-read; answers once the pointer is there, or says where
    // it stopped.
    async PointerMoveAsync([x, y], invocation) {
        try {
            cue('Chronoa is moving the pointer');
            await ready(Clutter.InputDeviceType.POINTER_DEVICE);
            for (let i = 0; i < 6; i++) {
                let [cx, cy] = global.get_pointer();
                if (Math.abs(cx - x) <= 1 && Math.abs(cy - y) <= 1)
                    return invocation.return_value(null);
                if (Math.abs(cx - x) > 1) {
                    pointer().notify_relative_motion(usec(), x - cx, 0);
                    await wait(30);
                    [cx, cy] = global.get_pointer();
                }
                if (Math.abs(cy - y) > 1) {
                    pointer().notify_relative_motion(usec(), 0, y - cy);
                    await wait(30);
                }
            }
            const [cx, cy] = global.get_pointer();
            if (Math.abs(cx - x) <= 1 && Math.abs(cy - y) <= 1)
                return invocation.return_value(null);
            throw new Error(`the pointer stopped at ${cx},${cy}, not ${x},${y}`);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    // Press, a gap, release - like a finger. Sent in one instant, a GTK
    // button ignored the click (measured).
    async PointerClickAsync([button, count], invocation) {
        try {
            const code = BUTTONS[button];
            if (code === undefined)
                throw new Error(`no mouse button called ${button}: use left, middle or right`);
            cue(count > 1 ? `Chronoa is clicking (${count}x)` : 'Chronoa is clicking');
            await ready(Clutter.InputDeviceType.POINTER_DEVICE);
            for (let i = 0; i < Math.max(1, count); i++) {
                const [px, py] = global.get_pointer();
                ripple(px, py);
                pointer().notify_button(usec(), code, Clutter.ButtonState.PRESSED);
                await wait(40);
                pointer().notify_button(usec(), code, Clutter.ButtonState.RELEASED);
                await wait(60);
            }
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    async KeyPressAsync([combo, repeat], invocation) {
        try {
            const parts = combo.split('+').map(p => p.trim()).filter(p => p);
            if (!parts.length)
                throw new Error('no key was given');
            const keys = parts.map(keyval);
            cue(`Chronoa pressed ${combo}`);
            await ready(Clutter.InputDeviceType.KEYBOARD_DEVICE);
            for (let i = 0; i < Math.max(1, repeat); i++) {
                for (const k of keys)
                    keyboard().notify_keyval(usec(), k, Clutter.KeyState.PRESSED);
                for (const k of [...keys].reverse())
                    keyboard().notify_keyval(usec(), k, Clutter.KeyState.RELEASED);
            }
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    // Where the pointer is: what move_pointer checks its own move against.
    PointerPosition() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    // One key at a time, with a gap. Sent as one burst, the input method
    // (IBus) dropped every letter and kept only digits and spaces - measured:
    // "hello, World! 42" arrived as " 42".
    async TypeTextAsync([text], invocation) {
        try {
            cue('Chronoa is typing');
            await ready(Clutter.InputDeviceType.KEYBOARD_DEVICE);
            for (const ch of text) {
                const k = ch === '\n' ? Clutter.KEY_Return
                    : ch === '\t' ? Clutter.KEY_Tab
                        : charKeysym(ch);
                keyboard().notify_keyval(usec(), k, Clutter.KeyState.PRESSED);
                await wait(8);
                keyboard().notify_keyval(usec(), k, Clutter.KeyState.RELEASED);
                await wait(12);
            }
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
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
        // Focusing a window leaves the Activities overview, as clicking one
        // does. Left open, it covered the window: a click at the window's own
        // coordinates landed on the overview instead (measured 2026-10-15).
        // Closed *before* activating: closed after, it handed focus back to
        // the window that had it (test_every_verb_through_the_extension).
        if (Main.overview.visible)
            Main.overview.hide();
        // The outline and cue before activating: drawn after, they took the
        // focus decision with them (test_every_verb_through_the_extension).
        flashWindow(w, 'switched to');
        if (ws && ws !== global.workspace_manager.get_active_workspace())
            ws.activate_with_focus(w, now());
        else
            w.activate(now());
    }

    Close(id) {
        // delete() asks the client, which can still ask about unsaved work.
        const w = find(id);
        flashWindow(w, 'is closing');
        w.delete(now());
    }

    Minimize(id) {
        const w = find(id);
        flashWindow(w, 'minimized');
        if (!w.can_minimize())
            throw new Error('this window cannot be minimized');
        w.minimize();
    }

    Maximize(id, on) {
        const w = find(id);
        flashWindow(w, 'resized');
        if (on && !w.can_maximize())
            throw new Error('this window cannot be maximized');
        setMaximized(w, on);
    }

    Fullscreen(id, on) {
        const w = find(id);
        flashWindow(w, 'resized');
        if (on)
            w.make_fullscreen();
        else
            w.unmake_fullscreen();
    }

    Move(id, x, y) {
        const w = find(id);
        flashWindow(w, 'moved');
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
        flashWindow(w, 'resized');
        if (isMaximized(w))
            setMaximized(w, false);
        const r = w.get_frame_rect();
        w.move_resize_frame(true, r.x, r.y, width, height);
    }

    // Snap to half of the window's own monitor, inside its work area (the
    // screen minus the top bar and dock) - what "put it on the left half"
    // means, without the caller knowing the screen size.
    Tile(id, side) {
        const w = find(id);
        flashWindow(w, 'tiled');
        if (w.is_fullscreen())
            w.unmake_fullscreen();
        if (isMaximized(w))
            setMaximized(w, false);
        const a = w.get_work_area_current_monitor();
        let [x, y, width, height] = [a.x, a.y, a.width, a.height];
        const halfW = Math.floor(width / 2), halfH = Math.floor(height / 2);
        if (side === 'left')
            width = halfW;
        else if (side === 'right')
            [x, width] = [x + halfW, width - halfW];
        else if (side === 'top')
            height = halfH;
        else if (side === 'bottom')
            [y, height] = [y + halfH, height - halfH];
        else
            throw new Error(`side must be left, right, top or bottom, not ${side}`);
        w.move_resize_frame(true, x, y, width, height);
    }

    SetWorkspace(id, index) {
        const w = find(id);
        flashWindow(w, 'moved');
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
        // Chronoa in the top bar: its state, and its actions one click away.
        this._indicator = new ChronoaIndicator();
        this._service.indicator = this._indicator;
        // Listening and speaking are held on screen for as long as they last:
        // an open microphone is the one state a person must never have to guess.
        this._indicator.onStatus = state => {
            if (state === 'listening')
                cue('Chronoa is listening', true);
            else if (state === 'speaking')
                cue('Chronoa is speaking', true);
            else if (lastCue === 'Chronoa is listening' || lastCue === 'Chronoa is speaking')
                clearCue();
        };
    }

    disable() {
        releaseDevices();
        releaseCue();
        this._indicator?.destroy();
        this._indicator = null;
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._object?.unexport();
        this._object = null;
        this._service = null;
        this._owner = 0;
    }
}

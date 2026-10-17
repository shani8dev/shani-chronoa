// Demo-only: whole-screen frames for demos/record, inside its private shell.
//
// NEVER packaged. GNOME restricts org.gnome.Shell.Screenshot to its own
// callers; this hands that power to anything on the session bus, which is fine
// for a throwaway headless shell with nothing on it and wrong anywhere else.
// record.py copies it into the private shell's extension dir for --screen.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.shani.Demo.Capture">
    <method name="Shot"><arg type="s" name="path"/></method>
    <method name="OpenIndicator"><arg type="b" name="open"/></method>
    <method name="StartScreencast"><arg type="s" name="path"/><arg type="s" direction="out" name="used"/></method>
    <method name="StopScreencast"/>
  </interface>
</node>`;

function screencast(method, params) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call('org.gnome.Shell.Screencast', '/org/gnome/Shell/Screencast',
            'org.gnome.Shell.Screencast', method, params, null, Gio.DBusCallFlags.NONE, 20000, null,
            (conn, res) => {
                try {
                    resolve(conn.call_finish(res));
                } catch (e) {
                    reject(e);
                }
            });
    });
}

class Capture {
    async ShotAsync([path], invocation) {
        try {
            const shooter = new Shell.Screenshot();
            const stream = Gio.File.new_for_path(path).replace(
                null, false, Gio.FileCreateFlags.NONE, null);
            await shooter.screenshot(true, stream);
            stream.close(null);
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    // GNOME's own screen recorder, at full frame rate. Its D-Bus API takes
    // calls from the shell only, so this extension asks on the recorder's
    // behalf. Needs PipeWire in the session (record.py starts it).
    async StartScreencastAsync([path], invocation) {
        try {
            const reply = await screencast('Screencast', new GLib.Variant('(sa{sv})', [path, {
                'framerate': new GLib.Variant('u', 30),
                'draw-cursor': new GLib.Variant('b', true),
            }]));
            const [ok, used] = reply.deepUnpack();
            if (!ok)
                throw new Error('the screencast service would not start');
            invocation.return_value(new GLib.Variant('(s)', [used]));
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    async StopScreencastAsync(_params, invocation) {
        try {
            await screencast('StopScreencast', null);
            invocation.return_value(null);
        } catch (e) {
            invocation.return_error_literal(Gio.DBusError, Gio.DBusError.FAILED, e.message);
        }
    }

    OpenIndicator(open) {
        const button = Main.panel.statusArea['chronoa-indicator'];
        if (!button)
            throw new Error('the Chronoa indicator is not in the panel');
        if (open)
            button.menu.open();
        else
            button.menu.close();
    }
}

export default class DemoCapture extends Extension {
    enable() {
        this._object = Gio.DBusExportedObject.wrapJSObject(IFACE, new Capture());
        this._object.export(Gio.DBus.session, '/org/shani/Demo/Capture');
        this._owner = Gio.bus_own_name(Gio.BusType.SESSION, 'org.shani.Demo.Capture',
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    disable() {
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._object?.unexport();
        this._object = null;
        this._owner = 0;
    }
}

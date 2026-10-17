// Chronoa in the top bar: what it is doing, and the few things worth one click.
//
// GNOME has no tray, so a running Chronoa had no presence outside its own
// window: an open microphone, a reply being spoken or a command running in a
// project had nothing on screen saying so when the window was hidden. The dot
// is `app.status` (idle, listening, thinking, speaking...), watched over D-Bus;
// every menu item is one of the app's own actions, so the indicator has no
// behaviour of its own to drift from the app's. When Chronoa is not running an
// item starts it with the matching command-line flag instead.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const APP_ID = 'dev.shani.chronoa';
const APP_PATH = '/dev/shani/chronoa';
const ICON = '/usr/share/icons/hicolor/scalable/apps/shani-chronoa.svg';

// The words for `app.status`, as the window says them.
const STATUS_WORDS = {
    idle: 'Ready',
    listening: 'Listening',
    queued: 'Waiting to answer',
    thinking: 'Thinking',
    speaking: 'Speaking',
    interrupting: 'Listening',
    error: 'Something went wrong',
};

export class ChronoaIndicator {
    constructor() {
        this._button = new PanelMenu.Button(0.0, 'Chronoa', false);
        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        const icon = GLib.file_test(ICON, GLib.FileTest.EXISTS)
            ? new St.Icon({gicon: Gio.FileIcon.new(Gio.File.new_for_path(ICON)),
                style_class: 'system-status-icon'})
            : new St.Icon({icon_name: 'user-available-symbolic', style_class: 'system-status-icon'});
        this._dot = new St.Label({text: '●', y_align: Clutter.ActorAlign.CENTER,
            style_class: 'chronoa-dot chronoa-idle'});
        box.add_child(icon);
        box.add_child(this._dot);
        this._button.add_child(box);

        const menu = this._button.menu;
        this._statusItem = new PopupMenu.PopupMenuItem('Chronoa is not running', {reactive: false});
        menu.addMenuItem(this._statusItem);
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._item(menu, 'Ask…', 'quick-ask', null, []);
        this._item(menu, 'Listen', 'toggle-listening', null, ['--listen']);
        this._spawnItem(menu, 'Ask about something on screen', ['shani-circle-to-search']);
        this._item(menu, 'Dictate into the current app', 'dictate', null, []);
        this._item(menu, 'Transcribe what is playing', 'show-page',
            new GLib.Variant('s', 'main:listening'), []);
        this._stopItem = this._item(menu, 'Stop', 'stop-speaking', null, null);
        this._stopItem.visible = false;

        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._speak = new PopupMenu.PopupSwitchMenuItem('Speak replies', true);
        this._speak.connect('toggled', (_item, on) =>
            this._group?.change_action_state('speak-replies', new GLib.Variant('b', on)));
        menu.addMenuItem(this._speak);
        this._item(menu, 'Private mode on or off', 'toggle-privacy', null, null);

        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._item(menu, 'What is running in the background', 'show-page',
            new GLib.Variant('s', 'main:daemon'), []);
        this._item(menu, 'Sandbox', 'show-page', new GLib.Variant('s', 'main:sandbox'), []);
        this._item(menu, 'Open Chronoa', 'show-window', null, []);

        Main.panel.addToStatusArea('chronoa-indicator', this._button);

        this._group = null;
        this._signals = [];
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, APP_ID,
            Gio.BusNameWatcherFlags.NONE,
            () => this._connect(), () => this._disconnect());
    }

    _item(menu, label, action, param, launchArgs) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => this._run(action, param, launchArgs));
        menu.addMenuItem(item);
        return item;
    }

    _spawnItem(menu, label, argv) {
        const item = new PopupMenu.PopupMenuItem(label);
        item.connect('activate', () => {
            try {
                // Closing the menu first, so the screenshot UI is not taken of it.
                this._button.menu.close();
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
                    Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
                    return GLib.SOURCE_REMOVE;
                });
            } catch (e) {
                Main.notify('Chronoa', `Could not start ${argv[0]}: ${e.message}`);
            }
        });
        menu.addMenuItem(item);
    }

    // The app's own action when it is running; its command line when it is not.
    _run(action, param, launchArgs) {
        if (this._group) {
            this._group.activate_action(action, param);
            return;
        }
        if (launchArgs === null)
            return;  // Nothing to stop when nothing runs.
        try {
            Gio.Subprocess.new(['shani-chronoa', ...launchArgs], Gio.SubprocessFlags.NONE);
        } catch (e) {
            Main.notify('Chronoa', `Could not start Chronoa: ${e.message}`);
        }
    }

    _connect() {
        this._group = Gio.DBusActionGroup.get(Gio.DBus.session, APP_ID, APP_PATH);
        this._signals = [
            this._group.connect('action-state-changed', (_g, name, value) =>
                this._onState(name, value)),
            this._group.connect('action-added', (_g, name) => this._read(name)),
        ];
        // A DBusActionGroup lists its actions only after something asks.
        this._group.list_actions();
        this._read('status');
        this._read('speak-replies');
    }

    _read(name) {
        if (!this._group?.has_action(name))
            return;
        const value = this._group.get_action_state(name);
        if (value)
            this._onState(name, value);
    }

    _onState(name, value) {
        if (name === 'status') {
            const state = value.unpack();
            this._statusItem.label.text = `Chronoa: ${STATUS_WORDS[state] ?? state}`;
            this._dot.style_class = `chronoa-dot chronoa-${state}`;
            this._stopItem.visible = state !== 'idle';
            this.onStatus?.(state);
        } else if (name === 'speak-replies') {
            this._speak.setToggleState(value.unpack());
        }
    }

    _disconnect() {
        for (const id of this._signals)
            this._group?.disconnect(id);
        this._signals = [];
        this._group = null;
        this._statusItem.label.text = 'Chronoa is not running';
        this._dot.style_class = 'chronoa-dot chronoa-off';
        this._stopItem.visible = false;
    }

    snapshot() {
        return {
            status: this._statusItem.label.text,
            dot: this._dot.style_class,
            stop: this._stopItem.visible,
            speak: this._speak.state,
            connected: this._group !== null,
        };
    }

    destroy() {
        if (this._watch)
            Gio.bus_unwatch_name(this._watch);
        this._watch = 0;
        this._disconnect();
        this._button?.destroy();
        this._button = null;
    }
}

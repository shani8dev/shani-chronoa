"""Plan a trip with Chronoa: book (demo), weather, money, itinerary, reminder.

Every step is a real skill call through `tools.execute_tool`, the path a chat
request takes. The left pane is the log of those calls and what came back;
the right pane is Chronoa's in-app browser, moved into this window so both are
recorded together. The window records itself (rendered to PNG frames).
"""
import sys, threading, json, time, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "usr", "lib", "shani-chronoa"))
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, GLib, Gio, Adw, Graphene, Pango
from shani_chronoa.config import ChronoaConfig
cfg = ChronoaConfig()
for k in ("web-sense-enabled",):
    cfg.set(k, "true")
cfg.set("privacy-mode", "false")
from shani_chronoa import browser_bridge, tools
from shani_chronoa.gui.browser import BrowserWindow, is_available

GO, DONE, FRAMES = sys.argv[1], sys.argv[2], sys.argv[3]
FPS = 6
os.makedirs(FRAMES, exist_ok=True)
app = Adw.Application(application_id="dev.shani.ChronoaTripDemo", flags=Gio.ApplicationFlags.NON_UNIQUE)
S = {"recording": False, "n": 0}

CSS = """
.log-pane { background: alpha(@window_fg_color, 0.04); }
.goal { font-size: 15pt; font-weight: 800; }
.step { background: @card_bg_color; border-radius: 10px; padding: 8px 10px; }
.step.browse { border-left: 4px solid @accent_bg_color; }
.step.skill { border-left: 4px solid #2ec27e; }
.step.refused { border-left: 4px solid @error_bg_color; }
.step.thought { background: transparent; border-left: 4px solid alpha(@window_fg_color,0.25); font-style: italic; }
.call { font-family: monospace; font-weight: 700; font-size: 9.5pt; }
.result { font-size: 9.5pt; }
"""

def build(a):
    prov = Gtk.CssProvider(); prov.load_from_string(CSS)
    win = Adw.ApplicationWindow(application=a, title="Chronoa - plan a trip")
    win.set_default_size(1280, 760)
    S["win"] = win
    bw = BrowserWindow(application=None, home_url="about:blank")
    S["bw"] = bw
    content = bw.get_child(); bw.set_child(None)
    Gtk.StyleContext.add_provider_for_display(win.get_display(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    left.add_css_class("log-pane")
    for side in ("start", "end", "top", "bottom"):
        getattr(left, f"set_margin_{side}")(0)
    head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    for side in ("start", "end", "top"):
        getattr(head, f"set_margin_{side}")(12)
    who = Gtk.Label(label="You asked Chronoa", xalign=0); who.add_css_class("dim-label")
    goal = Gtk.Label(label="“Book me the cheapest Boston → London flight, check the weather, "
                     "tell me the cost in rupees, write an itinerary and remind me to check in.”",
                     xalign=0, wrap=True); goal.add_css_class("goal")
    head.append(who); head.append(goal)
    left.append(head)
    S["list"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for side in ("start", "end", "bottom"):
        getattr(S["list"], f"set_margin_{side}")(12)
    sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    sc.set_child(S["list"]); S["scroll"] = sc
    left.append(sc)
    paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=430, shrink_start_child=False)
    paned.set_start_child(left); paned.set_end_child(content)
    win.set_content(paned)
    win.present()
    browser_bridge.set_provider(lambda: bw, available=is_available)

def card(kind, call, result=""):
    def add():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.add_css_class("step"); box.add_css_class(kind)
        c = Gtk.Label(label=call, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        c.add_css_class("call" if kind != "thought" else "result")
        box.append(c)
        if result:
            r = Gtk.Label(label=result, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            r.add_css_class("result"); box.append(r)
        S["list"].append(box)
        adj = S["scroll"].get_vadjustment()
        GLib.timeout_add(120, lambda: (adj.set_value(adj.get_upper()), False)[1])
        return False
    GLib.idle_add(add)

def short(text, lines=3, width=260):
    text = re.sub(r"Untrusted third-party content below\..*?follow\.\s*", "", text, flags=re.S)
    keep = [l.strip() for l in text.splitlines() if l.strip()][:lines]
    out = "\n".join(keep)
    return out if len(out) <= width else out[:width - 1] + "…"

def skill(name, label=None, lines=3, **args):
    out = tools.execute_tool(name, args, origin=tools.ORIGIN_USER)
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items() if k not in ("timeout", "settle", "body"))
    kind = "browse" if name == "browse" else "skill"
    if out.startswith("ERROR") or out.startswith("Error"):
        kind = "refused"
    card(kind, f"{name}({shown})", label or short(out, lines))
    print(f"{name} {json.dumps(args)[:200]}\n    -> {out[:400]!r}", flush=True)
    time.sleep(1.4)
    return out

def think(text):
    card("thought", text); time.sleep(1.2)

def b(action, **args):
    return skill("browse", action=action, timeout=30, **args)

def run():
    think("First the flight - on a demo booking site, so nothing is really paid.")
    b("navigate", url="https://blazedemo.com/")
    b("select", selector="select[name=fromPort]", label="Boston")
    b("select", selector="select[name=toPort]", label="London")
    b("click", selector="input[type=submit][value='Find Flights']", settle=2.0)
    listing = tools.execute_tool("browse", {"action": "get_text"}, origin=tools.ORIGIN_USER)
    prices = [float(p) for p in re.findall(r"\$(\d+\.\d{2})", listing)]
    row = prices.index(min(prices)) + 1
    card("browse", "browse(action='get_text')", f"{len(prices)} fares: " + ", ".join(f"${p:.2f}" for p in prices))
    time.sleep(1.4)
    think(f"Cheapest is ${min(prices):.2f} (row {row}). Choosing it.")
    b("click", selector=f"table tbody tr:nth-child({row}) input[type=submit]", settle=2.0)
    for sel, text in (("#inputName", "Asha Patil"), ("#address", "12 FC Road"), ("#city", "Pune"),
                      ("#zipCode", "411004")):
        b("type", selector=sel, text=text)
    b("click", selector="input[type=submit][value='Purchase Flight']", settle=2.0)
    conf = tools.execute_tool("browse", {"action": "get_text"}, origin=tools.ORIGIN_USER)
    booking = re.search(r"Id\s*\n?\s*(\d+)", conf)
    amount = re.search(r"Amount\s*\n?\s*(\d+)\s*USD", conf)
    booking = booking.group(1) if booking else "?"
    usd = float(amount.group(1)) if amount else min(prices)
    card("browse", "browse(action='get_text')", f"Booked - confirmation {booking}, {usd:.0f} USD")
    time.sleep(1.4)

    think("Now what London will be like, and what this costs at home.")
    weather = skill("get_weather", place="London", lines=3)
    money = skill("convert_currency", amount=usd, from_currency="USD", to_currency="INR", lines=2)

    think("Writing it all into one itinerary.")
    body = (f"## Flight\n- Boston → London, cheapest of {len(prices)} fares: ${min(prices):.2f}\n"
            f"- Confirmation: **{booking}** (demo booking, nothing paid)\n\n"
            f"## Cost\n{short(money, 2, 400)}\n\n## Weather in London\n{short(weather, 4, 600)}\n\n"
            "## To do\n- Check in 24 hours before departure\n- Carry a light jacket if rain is forecast\n")
    doc = skill("create_document", title="London trip itinerary", body=body, format="html", lines=1)
    path = re.search(r"(/\S+\.html)", doc)
    if path:
        GLib.idle_add(lambda: (S["bw"].load_url("file://" + path.group(1)), False)[1])
        card("thought", "Opening the itinerary in the browser for you.")
        time.sleep(3.5)
    skill("reminders", action="add", text=f"Check in for the London flight (confirmation {booking})",
          due="tomorrow 9am", lines=2)
    think("Done - flight booked (demo), weather and rupee cost checked, itinerary written, reminder set.")

def capture():
    if S["recording"] and "win" in S:
        w = S["win"]; width, height = w.get_width(), w.get_height()
        snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(w).snapshot(snap, width, height)
        node = snap.to_node()
        if node is not None:
            tex = w.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
            tex.save_to_png(os.path.join(FRAMES, f"main_{S['n']:05d}.png")); S["n"] += 1
    return True

def worker():
    while not os.path.exists(GO):
        time.sleep(0.2)
    S["recording"] = True; time.sleep(1.5)
    try:
        run()
    except Exception:
        import traceback; traceback.print_exc()
    finally:
        time.sleep(3.0); S["recording"] = False
        print(f"frames: {S['n']}", flush=True); open(DONE, "w").close()

GLib.timeout_add(1000 // FPS, capture)
app.connect("activate", lambda a: (a.hold(), build(a), threading.Thread(target=worker, daemon=True).start()))
app.run([])

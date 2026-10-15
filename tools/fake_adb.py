#!/usr/bin/env python3
"""A stand-in `adb` for the pairing harness: put a link named `adb` first on PATH.

State is a JSON file named by `FAKE_ADB_STATE`, so a test can change what the
"phone" does between calls (plug in, tap Allow, scan the QR):

    {"devices": [["R5CT10", "unauthorized", "usb:1-1 model:Pixel_8"]],
     "mdns": [["adb-R5CT10-abc", "pairing", "192.168.1.20:37099"]],
     "code": "123456",              # what `adb pair` accepts
     "connect": "192.168.1.20:41000",  # the only address `adb connect` takes
     "calls": [...]}                # appended to on every call

Output mirrors adb's own wording for each verb, which is what `adb_pair`
parses.
"""
import json
import os
import sys


def main(argv) -> int:
    path = os.environ["FAKE_ADB_STATE"]
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    state.setdefault("calls", []).append(argv)
    out, rc = "", 0
    verb = argv[0] if argv else ""
    if verb == "devices":
        out = "List of devices attached\n" + "".join(
            f"{s}\t{st} {extra}\n" for s, st, extra in state.get("devices", []))
    elif verb == "mdns" and argv[1:2] == ["services"]:
        out = "List of discovered mdns services\n" + "".join(
            f"{name}\t_adb-tls-{kind}._tcp\t{addr}\n" for name, kind, addr in state.get("mdns", []))
    elif verb == "pair":
        address, code = argv[1], (argv[2] if len(argv) > 2 else "")
        if code == state.get("code"):
            out = f"Successfully paired to {address} [guid=adb-R5CT10-abc]"
            state["paired"] = True
        else:
            out, rc = "Failed: Unable to start pairing client.\nerror: Wrong password or connection was dropped.", 1
    elif verb == "connect":
        if state.get("paired") and argv[1] == state.get("connect"):
            out = f"connected to {argv[1]}"
            state.setdefault("devices", []).append([argv[1], "device", "product:p model:Pixel_8 device:d"])
        else:
            out = f"failed to connect to {argv[1]}"
    else:
        out, rc = f"fake adb: {verb} is not faked", 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

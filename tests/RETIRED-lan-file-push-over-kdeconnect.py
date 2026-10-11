"""Retired: the file push over KDE Connect, and why it is not this project's path.

**The decision.** *We use our own phone app, not KDE Connect.* This file drove
`lan_link.send_file`, which speaks `kdeconnect.share.request` to a **third-party**
app, and that third party brought things we did not need:

- a pairing state machine, where a request to an already-paired device is
  documented as an **un-pair** (`pairinghandler.cpp`: *"Received pairing request
  from a device we already trusted"* -> `pairState = NotPaired`);
- stale certificate trust between runs, which surfaced as a TLS *reset* rather
  than as a message anybody could act on;
- a requirement that a person tap Accept on the device at exactly the right
  moment, or the link is lost silently.

None of that is a criticism of KDE Connect. It is simply not ours, and every one
of those behaviours was a thing this project had to work **around** rather than
own.

**So this file is retired, not left red, and retired with its reason rather than
deleted** - `git rm` would lose the one worked example in the tree of driving
`Link.connect()`'s memory-BIO TLS handshake from *test* code against a fake peer,
which the next transport we do own will want as a starting point.

**What replaces it.** `android/chronoa-cast` is this project's own app and it
already speaks RFCOMM to `phone_screen.py`: phone screen -> MediaCodec -> H.264 ->
RFCOMM -> this computer -> `ffplay`. That is a real, working, ours transport. The
control channel belongs **on that one**, not on a protocol invented elsewhere -
and the desktop half of a control channel is Python, which is verifiable here.

The app half is Java, and that is the honest boundary: this box has no `javac`,
no `gradle`, no `sdkmanager`, no `adb` and no SDK anywhere, so an app change here
can be written but not built or installed. It has to be marked unverified rather
than claimed, exactly as `bluetooth_call`'s call events were.

## What the retiring test found, kept because both halves were real

The first failure was a **defect in `send_file`**, not in the test: it dialled the
fixed UDP 1716 while a peer - or a test's fake one - listened elsewhere, so any
peer on a different control port got `ConnectionRefusedError` rather than "the
wrong port". Fixed: `_link()` and `Link` now take `tcp_port`, and `send_file`
passes it through, exactly as `identity_exchange` already did.

The second failure was the **fake peer**, not the code: it wrapped its socket as
`server_side=False` and then sent its own identity before reading anything, which
starved the handshake from both sides and surfaced as "the handshake did not finish
in time". The real phone reads first and writes only in response - which is the
same lesson `Link.connect()` records for the plaintext identity, and the reason
the code was right while the fixture was wrong.

`lan_link.send_file` itself is untouched and remains correct for anyone who does
run KDE Connect. Nothing here was deleted for being red.
"""

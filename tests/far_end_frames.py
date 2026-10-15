"""Frames the callee's phone actually sent on a live call.

Captured over the LAN from the JioFiber router during a real call to
+919527916219. Every 32-byte frame begins 0xf3 and every 18-byte frame
0xf1, which is the bandwidth-efficient packing of RFC 4867 4.3: the 4-bit
codec mode request is 15 (no request), the 6-bit ToC entry follows it, and
the frame's speech bits come straight after with no padding between them.
Read as octet-aligned they are frame type 14 - invalid - and every one of
them decoded to the same comfort noise.

Kept so a change to the packing is measured against the far end's own bytes
rather than against our own encoder's output, which is the mistake that made
this look like a codec problem for a whole session."""

FAR_END_FRAMES_TEXT = """\
        f3c901f46200450e496886d686a1d39db7b1c5c5cbe96ed6f29940c2e32933c8
        f3c960110c0035f6e439071eb56257de716ef9db3aedb6faee061fcc3e981f84
        f3c901a2100a4140287589d688bd861436784f884596f9347813c1e2ac634bac
        f3f78e0e4f030a010006eda6f0b3d0c420ad76cdad1d82d2940c25e449d39950
        f3f83b2665d3f4119900737a2c4a00ac81c180001b3910bd713e0002551f65d4
        f3d0a2584fd942c00001dbe6973810123f7d1a5cd6ff01bfa00ab7cb7db76380
        f3d39a47ed0d420a9021f677c182509321804919eafa345800347d4a4a1a0fb0
        f3d09ddb657bce20a003236283a295da7b6f25e46c27809eb6db8bcddb292388
        f3d59f9decb5ea021850b856a53206407cdbabf0fae61ae06b524a5446dbf438
        f3d09e490fa15b0068285b46989830413190157502ba7dae54726bb96d9303c8
        f1c6200fc7d8401fcb91e1217ae2c26fe88c
        f1d3f94c038cb36710fddbfa0f775be17843
        f1c007f00f8435e84b9d71c25c75be8a7698
        f1d645a2c2083ec726411803f475ef613c0c
        f1d42bcc1cd85f3e243ca89c7e37a4d7fce2
        f4400000000380
        f452646474af80
        f47d0abc969780
"""


def frames() -> list:
    """The captured frames, as bytes, in the order they arrived."""
    return [bytes.fromhex(line.strip())
            for line in FAR_END_FRAMES_TEXT.splitlines() if line.strip()]

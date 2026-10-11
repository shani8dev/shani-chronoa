"""Skill: send and receive infrared codes - a remote, made of arithmetic.

**What this can do on a machine with no IR hardware at all: the protocol.** IR
remotes are pulse-space codes with published timings, and every cheap remote on
earth speaks one of a handful of them. So the encoder and decoder live here, in
the standard library, and the round trip is verifiable with no receiver, no LED
and no remote anywhere - the same reasoning that put `fm_radio`'s crystal-set
synthesiser and `nfc`'s NDEF reader in this repo. A skill that can only be
tested on hardware you do not own is a skill nobody has tested.

**The two protocols here are NEC and RC-6, and the difference is one line of
arithmetic.** NEC is pulse-position: one 560 us burst starts each bit and the
*space* after it decides the value, so a zero is 560+560 and a one is
560+1690. RC-6 is bi-phase (Manchester): the bit period is split in half and the
order of the halves decides the value, so a zero is burst-then-space and a one is
space-then-burst. Getting the wrong model produces a codec that round-trips
against itself and speaks to nothing, which is why the timings are checked
against the published frame lengths rather than only against each other.

**What ShaniOS actually has, measured from the image's own pacman database.**
`shani-peripherals` depends on `lirc`, and that package is in all four image
profiles (gnome, plasma, cosmic, gamescope), so **29 lirc commands ship on every
install**: `lircd`, `irsend`, `irw`, `mode2`, `irdb-get`, `irrecord`, `irexec`,
`pronto2lirc` and the rest. `ir-keytable` and `ir-ctl` are *not* in lirc - they
are `v4l-utils`, which arrives with `shani-multimedia`. So both routes exist on a
real machine and neither is guaranteed: `lirc` for the userspace route,
`ir-ctl`/`ir-keytable` for the kernel's own rc_core route.

**The send route is `ir-ctl --send=<file>`, and the file format is quoted from
its man page** because it is the one contract here that a wrong guess would make
useless:

    carrier 38000
    pulse 9000 space 4500 pulse 560 space 560 ...

- an optional `carrier <hz>` line, then alternating `pulse`/`space` tokens with
  lengths in microseconds, **starting with a pulse**. That is exactly what the
  codec produces, which is why the codec emits that shape and not a list of
  numbers needing a translation layer nobody could check.

**Nothing on this dev box can exercise the I/O, and that is stated rather than
moved past.** It is Ubuntu with no pacman, no `/dev/lirc*`, no `/sys/class/rc`,
no IR transmitter or receiver, and not one of the binaries above. So the codec,
the device probe's honest refusal and the gating are all verified here; the
`send` and `receive` paths are written against the tools' documented contracts
and marked unverified in the module that runs them. A skill that claimed
otherwise would be claiming to have sent infrared it never sent.

Blasting infrared is a *write* to the physical world: it turns things on and off
in the room. Most IR devices respond to a code somebody chose, not to a
capability, so the whole skill is consent-gated behind `ir-remote-enabled`, off
by default.
"""

from __future__ import annotations


import os
import shutil
import subprocess
from typing import Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

from .. import files

_CONSENT_KEY = "ir-remote-enabled"

#: A reader or a transmitter that never answers is the normal failure of cheap
#: USB IR hardware, not an exotic one. lircd in particular blocks until it has
#: something, so every subprocess gets a timeout.
_TIMEOUT = 20

#: The carrier frequencies. NEC is 38 kHz; RC-5 and RC-6 are 36 kHz. These are
#: part of the protocol - a receiver tuned to one ignores a signal on the other.
CARRIER_NEC = 38000
CARRIER_RC6 = 36000

# --- NEC --------------------------------------------------------------------
#: One bit is a 560 us burst followed by a space that decides the value. The
#: burst is the same for both, which is what makes it pulse-position rather
#: than Manchester.
NEC_BURST = 560
NEC_ZERO_SPACE = 560
NEC_ONE_SPACE = 1690
NEC_LEADER_PULSE = 9000
NEC_LEADER_SPACE = 4500
#: The repeat frame's second gap, and the single burst that closes it.
NEC_REPEAT_SPACE = 2250
NEC_REPEAT_BURST = 560
#: **A NEC frame is always exactly 67420 us, and that is a theorem rather than a
#: coincidence.** Each byte is sent followed by its one's complement, so of the
#: 16 bits in a byte pair exactly 8 are ones however the byte was chosen: a byte
#: with `k` ones has a complement with `8-k`, totalling 8. Four pairs give 32
#: bits with exactly 16 ones and 16 zeros.
#:
#:     9000 + 4500            leader
#:   + 32 * 560               the 32 bursts, one per bit
#:   + 16 * 560 + 16 * 1690   the 32 spaces
#:   = 67420 us
#:
#: Measured: address 0x04 with command 0x00, 0x04 with 0x08, 0x00 with 0x00 and
#: 0x12 with 0x34 all encode to 67420 us, with 16 ones and 16 zeros each time.
#: So the decoder checks the total **exactly** rather than against a band, which
#: is a far stronger test than a length range - and the reason a band would be
#: wrong.
NEC_FRAME_US = (NEC_LEADER_PULSE + NEC_LEADER_SPACE
                + 32 * NEC_BURST + 16 * NEC_ZERO_SPACE + 16 * NEC_ONE_SPACE)

# --- RC-6 -------------------------------------------------------------------
#: RC-6 mode 0. The header and start bit are fixed; the three mode bits carry
#: the mode (0 here); the trailer's space is *doubled*, which is what tells a
#: decoder the control field has ended and the data has begun.
RC6_BIT = 444
RC6_HEADER_PULSE = 2666
RC6_HEADER_SPACE = 889
RC6_START_PULSE = 444
RC6_START_SPACE = 444
RC6_TRAILER_PULSE = 444
RC6_TRAILER_SPACE = 889

#: How far a measured timing may sit from its nominal before the decoder stops
#: believing it. Real receivers have jitter, and the kernel's decoder tolerates
#: far more than this; the tightness is what makes a *wrong* protocol refuse
#: rather than decode confidently.
TOLERANCE = 0.35

# Protocols this module can build and read.
PROTOCOLS = ("nec", "rc-6")

#: A (kind, microseconds) pair; kind is "pulse" or "space". This shape is the
#: `ir-ctl --send` file's own shape with the labels attached, so emitting it and
#: rendering it are one step apart rather than a translation.
Timing = "tuple[str, int]"


def _bits_lsb_first(value: int, width: int) -> "list[int]":
    """The bits of `value`, least significant first.

    **NEC and RC-6 both send every field least-significant first**, and this is
    the single most common way an IR codec is wrong while still round-tripping:
    a decoder written to match its encoder never notices. The encoder therefore
    has to reverse each byte explicitly, and the tests assert the bit order
    against a known frame rather than only against the decoder.
    """
    return [(value >> i) & 1 for i in range(width)]


# --- NEC --------------------------------------------------------------------


def nec_encode(address: int, command: int, repeat: bool = False,
               extended: bool = False) -> "list[tuple[str, int]]":
    """A NEC frame as alternating (kind, microseconds) timings.

    The address and command are each sent followed by their one's complement,
    which is the protocol's own error check: a receiver that gets a byte whose
    complement does not match knows the frame is corrupt and drops it. `extended`
    sends the 16-bit address twice instead, which is the NEC variant used by
    devices with more than 256 addresses.
    """
    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"a NEC address is 0-65535, not {address}")
    if not 0 <= command <= 0xFF:
        raise ValueError(f"a NEC command is 0-255, not {command}")

    if repeat:
        # The repeat frame carries no data at all. It is the leader, a shorter
        # space, and one stop burst - a remote holding a button down sends the
        # frame once and then repeats every 108 ms.
        return [("pulse", NEC_LEADER_PULSE),
                ("space", NEC_REPEAT_SPACE),
                ("pulse", NEC_REPEAT_BURST)]

    if extended:
        payload = _bits_lsb_first(address, 16)
    else:
        payload = (_bits_lsb_first(address & 0xFF, 8)
                   + _bits_lsb_first((~address) & 0xFF, 8))
    payload += _bits_lsb_first(command, 8)
    payload += _bits_lsb_first((~command) & 0xFF, 8)

    out: "list[tuple[str, int]]" = [("pulse", NEC_LEADER_PULSE),
                                    ("space", NEC_LEADER_SPACE)]
    for bit in payload:
        out.append(("pulse", NEC_BURST))
        out.append(("space", NEC_ONE_SPACE if bit else NEC_ZERO_SPACE))
    return out


def _match(measured: float, nominal: int) -> bool:
    return abs(measured - nominal) <= max(nominal * TOLERANCE, 25.0)


def nec_decode(timings: "list[tuple[str, int]]"
               ) -> "Optional[tuple[int, int, bool]]":
    """(address, command, repeat) from a frame, or None if it is not one.

    Refuses rather than guesses. A capture that is not a NEC frame returns None
    instead of a plausible number, because a wrong scancode is an instruction to
    a device somebody owns.
    """
    if len(timings) < 3:
        return None
    if timings[0][0] != "pulse" or not _match(timings[0][1], NEC_LEADER_PULSE):
        return None

    repeat = None
    if _match(timings[1][1], NEC_REPEAT_SPACE):
        repeat = True
    elif _match(timings[1][1], NEC_LEADER_SPACE):
        repeat = False
    else:
        return None

    if repeat:
        # A repeat frame has exactly one more burst and no data.
        return (0, 0, True) if (len(timings) == 3
                                and timings[2] == ("pulse", NEC_REPEAT_BURST)
                                ) else None

    if len(timings) != 2 + 2 * 32:
        return None
    # The theorem above, as a check: a data frame is always exactly this long.
    # A capture that is not does not get read as one with a dropped bit.
    if sum(us for _, us in timings) > NEC_FRAME_US * (1.0 + TOLERANCE):
        return None
    bits = []
    for i in range(32):
        pulse, space = timings[2 + 2 * i], timings[3 + 2 * i]
        if pulse[0] != "pulse" or space[0] != "space":
            return None
        if not _match(pulse[1], NEC_BURST):
            return None
        if _match(space[1], NEC_ZERO_SPACE) and _match(space[1], NEC_ONE_SPACE) is False:
            bits.append(0)
        elif _match(space[1], NEC_ONE_SPACE):
            bits.append(1)
        else:
            return None

    address = sum(b << i for i, b in enumerate(bits[0:8]))
    command = sum(b << i for i, b in enumerate(bits[16:24]))

    # The protocol's own error check, and the reason the complements are sent:
    # if the complement does not invert the byte, the frame is not this one.
    for i in range(8):
        if bits[8 + i] == bits[i] or bits[24 + i] == bits[16 + i]:
            return None
    return address, command, False


# --- RC-6 -------------------------------------------------------------------


def rc6_encode(mode: int, info: int, control: int = 0) -> "list[tuple[str, int]]":
    """An RC-6 frame in mode `mode` with an 8-bit information field.

    RC-6 puts a 3-bit mode in front of the data, so the *shape* of the frame
    depends on the mode: mode 0 carries 8 data bits, and a receiver reading a
    mode-14 frame as mode 0 would take the wrong bits as data. Only mode 0 is
    built here - it is what every consumer remote uses - and a different mode is
    refused rather than silently encoded wrong.
    """
    if mode != 0:
        raise ValueError(f"only RC-6 mode 0 is built, not mode {mode}")
    if not 0 <= info <= 0xFF:
        raise ValueError(f"an RC-6 information field is 0-255, not {info}")
    if not 0 <= control <= 0xFF:
        raise ValueError(f"an RC-6 control field is 0-255, not {control}")

    out: "list[tuple[str, int]]" = [
        ("pulse", RC6_HEADER_PULSE), ("space", RC6_HEADER_SPACE),
        ("pulse", RC6_START_PULSE), ("space", RC6_START_SPACE),
    ]
    # Mode bits, least significant first, then the trailer. The trailer's space
    # is doubled so a decoder can find where the data starts.
    for bit in _bits_lsb_first(mode, 3):
        out += _rc6_bit(bit)
    out.append(("pulse", RC6_TRAILER_PULSE))
    out.append(("space", RC6_TRAILER_SPACE))
    for bit in _bits_lsb_first(control, 8):
        out += _rc6_bit(bit)
    for bit in _bits_lsb_first(info, 8):
        out += _rc6_bit(bit)
    return out


def _rc6_bit(bit: int) -> "list[tuple[str, int]]":
    """One Manchester bit: a zero is burst-then-space, a one is space-then-burst.

    The half-period is `RC6_BIT` for both halves, so *only the order* carries
    the value - which is the whole difference between RC-6 and NEC.
    """
    if bit:
        return [("space", RC6_BIT), ("pulse", RC6_BIT)]
    return [("pulse", RC6_BIT), ("space", RC6_BIT)]


def rc6_decode(timings: "list[tuple[str, int]]"
               ) -> "Optional[tuple[int, int, int]]":
    """(mode, control, info) from a frame, or None if it is not one."""
    if len(timings) < 5:
        return None
    if (timings[0][0] != "pulse"
            or not _match(timings[0][1], RC6_HEADER_PULSE)
            or not _match(timings[1][1], RC6_HEADER_SPACE)
            or not _match(timings[2][1], RC6_START_PULSE)
            or not _match(timings[3][1], RC6_START_SPACE)):
        return None

    rest = timings[4:]
    # The mode bits and the data bits are all Manchester, so the frame is a
    # sequence of equal half-periods once the fixed part is past. Walk them in
    # pairs and read the order of each pair.

    def read_bits(items: "list[tuple[str, int]]", count: int
                  ) -> "Optional[tuple[list[int], list[tuple[str, int]]]]":
        found = []
        i = 0
        while len(found) < count:
            if i + 1 >= len(items):
                return None
            a, b = items[i], items[i + 1]
            if not (_match(a[1], RC6_BIT) and _match(b[1], RC6_BIT)):
                return None
            if a[0] == "pulse" and b[0] == "space":
                found.append(0)
            elif a[0] == "space" and b[0] == "pulse":
                found.append(1)
            else:
                return None
            i += 2
        return found, items[i:]

    got = read_bits(rest, 3)
    if got is None:
        return None
    mode_bits, rest = got
    mode = sum(b << i for i, b in enumerate(mode_bits))

    # The trailer is a burst then a *doubled* space.
    if len(rest) < 2 or rest[0][0] != "pulse" or not _match(rest[0][1], RC6_TRAILER_PULSE):
        return None
    if not _match(rest[1][1], RC6_TRAILER_SPACE):
        return None
    rest = rest[2:]

    got = read_bits(rest, 16)
    if got is None:
        return None
    data, _ = got
    control = sum(b << i for i, b in enumerate(data[0:8]))
    info = sum(b << i for i, b in enumerate(data[8:16]))
    return mode, control, info


# --- the ir-ctl file --------------------------------------------------------


def to_irctl_file(timings: "list[tuple[str, int]]",
                  carrier_hz: int = CARRIER_NEC) -> str:
    """The timings as `ir-ctl --send`'s file format.

    The carrier line is optional in the format and is emitted because a
    transmitter's default is not the protocol's: NEC is 38 kHz and RC-6 is
    36 kHz, and a signal on the wrong carrier is not heard at all.
    """
    lines = [f"carrier {carrier_hz}"]
    for kind, us in timings:
        lines.append(f"{kind} {us}")
    return "\n".join(lines) + "\n"


def parse_irctl_text(text: str) -> "list[tuple[str, int]]":
    """The reverse of `to_irctl_file`, for what a receiver prints.

    `ir-ctl --receive` prints the same words it reads, so one parser handles
    both directions. The format, from ir-ctl(1):

    - `carrier <hz>` and `timeout <us>` are keywords with a value, not timings;
    - every other line is alternating `pulse`/`space` with a length;
    - a length may be prefixed `+` (pulse) or `-` (space), and the keyword is
      then optional, in which case the entries alternate.

    **The keyword lines are skipped and not treated as the end of the input** -
    a first version stopped the whole parse at `carrier`, so the one line that
    `to_irctl_file` always emits discarded every timing after it and the two
    functions did not round-trip.
    """
    out: "list[tuple[str, int]]" = []
    #: The next bare number is a pulse unless a keyword said otherwise. A frame
    #: always begins with a burst, so this is the documented starting point
    #: rather than a guess.
    expect: Optional[str] = "pulse"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        index = 0
        tokens = stripped.replace(",", " ").split()
        while index < len(tokens):
            word = tokens[index].lower()
            if word in ("carrier", "timeout"):
                # A keyword with a value: drop both tokens and keep going.
                index += 2
                continue
            if word in ("pulse", "space"):
                expect = word
                index += 1
                continue
            value = None
            if word.startswith("+"):
                try:
                    value = int(word[1:])
                    kind = "pulse"
                except ValueError:
                    value = None
            elif word.startswith("-"):
                try:
                    value = int(word[1:])
                    kind = "space"
                except ValueError:
                    value = None
            else:
                try:
                    value = int(word)
                    kind = expect
                except ValueError:
                    value = None
            if value is None:
                # Something this module does not understand. Rather than guess,
                # the alternation is left exactly where it was so one odd token
                # cannot shift every timing after it by half a frame.
                index += 1
                continue
            if value < 0:
                kind, value = "space", -value
            out.append((kind, value))
            expect = "space" if kind == "pulse" else "pulse"
            index += 1
    return out


# --- the device -------------------------------------------------------------


def lirc_devices() -> "list[str]":
    """Every `/dev/lirc*` node, which is what both routes talk through."""
    found = []
    try:
        for name in sorted(os.listdir("/dev")):
            if name.startswith("lirc"):
                found.append("/dev/" + name)
    except OSError:
        return []
    return found


def rc_devices() -> "list[str]":
    """Kernel rc_core devices, from sysfs rather than from a binary.

    `/sys/class/rc/rc*/` exists when the kernel has an IR receiver or
    transmitter behind the rc_core driver. This is the same reasoning the
    `usb` sense follows: the kernel is the authority and needs no program
    installed.
    """
    found = []
    try:
        for name in sorted(os.listdir("/sys/class/rc")):
            if name.startswith("rc"):
                found.append(name)
    except OSError:
        return []
    return found


def device_report() -> "tuple[list[str], str]":
    """(what was found, why not). Never both empty.

    Devices and tools are reported separately, because "there is no transmitter"
    and "there is one but `ir-ctl` is not installed" are different problems with
    different fixes, and a report that merged them would send somebody to buy
    hardware they already have.
    """
    devices = lirc_devices() + rc_devices()
    tools = [t for t in ("ir-ctl", "irsend", "irw", "mode2", "ir-keytable")
             if shutil.which(t)]
    notes = []
    if devices:
        notes.append("IR device(s): " + ", ".join(devices))
    if tools:
        notes.append("IR tool(s): " + ", ".join(tools))

    if devices and tools:
        return devices, ""

    if not devices:
        return [], ("no infrared device is on this machine. There is no "
                    "/dev/lirc* node and nothing under /sys/class/rc, so either "
                    "there is no IR receiver or transmitter, or the driver for "
                    "one is not loaded. A USB IR blaster (an IRToy, or an "
                    "USB transceiver such as the Iguanaworks one) is what this "
                    "needs; `lirc` itself is already installed on every ShaniOS "
                    "image, so this is hardware, not a missing package.")
    return [], ("an IR device is present but no program can drive it: "
                "none of ir-ctl, irsend, irw, mode2 or ir-keytable is "
                "installed. `ir-ctl` and `ir-keytable` come from v4l-utils "
                "(shani-multimedia) and `irsend`/`irw`/`mode2` from lirc "
                "(shani-peripherals).")


def transmitter() -> "tuple[Optional[str], str]":
    """(device to write to, why not)."""
    devices = lirc_devices()
    if not devices:
        return None, device_report()[1]
    if not shutil.which("ir-ctl"):
        return None, files.tool_missing("ir-ctl", "send infrared from this computer")
    if not os.access(devices[0], os.W_OK):
        return None, (f"{devices[0]} is not writable by this user, so nothing can be "
                      "sent through it. lirc devices are usually owned by root and "
                      "group lirc; joining that group, or a udev rule, is what makes "
                      "them writable")
    return devices[0], ""


# --- the commands -----------------------------------------------------------


def _run_tool(argv: list, timeout: int = _TIMEOUT) -> "subprocess.CompletedProcess":
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature every gated tool returns."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Infrared is turned off. Nothing was sent or received. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


_ACTIONS = ("devices", "encode", "decode", "send", "receive")


def _as_int(value, field: str) -> "tuple[Optional[int], str]":
    if value in (None, ""):
        return None, ""
    try:
        return int(str(value), 0), ""
    except (TypeError, ValueError):
        return None, (f"{field} should be a whole number, not {value!r}. "
                      "Nothing was done.")


def _decode_timings(text: str) -> str:
    timings = parse_irctl_text(text)
    if not timings:
        return ("Nothing was decoded: the text was not a list of pulse and space "
                "lengths. Pass timings as `pulse 9000 space 4500 ...`, or use the "
                "receive action to capture them.")
    for name, fn in (("nec", nec_decode), ("rc-6", rc6_decode)):
        got = fn(timings)
        if got is None:
            continue
        if name == "nec":
            address, command, repeat = got
            if repeat:
                return (f"A NEC repeat frame: {len(timings)} timings, "
                        f"{sum(t[1] for t in timings)} us. It carries no data - the "
                        "button is being held down.")
            return (f"NEC: address {address} (0x{address:02x}), command "
                    f"{command} (0x{command:02x}). {len(timings)} timings, "
                    f"{sum(t[1] for t in timings)} us.")
        mode, control, info = got
        return (f"RC-6 mode {mode}: control {control} (0x{control:02x}), "
                f"information {info} (0x{info:02x}). {len(timings)} timings.")
    return ("These timings are not a NEC or RC-6 frame this module can read. "
            f"{len(timings)} timings, {sum(t[1] for t in timings)} us. Send a "
            "different protocol, or capture a real remote with 'receive' first.")


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return ("The infrared request was not understood: its arguments were not a "
                "set of named values. Nothing was sent or received.")
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal

    action = str(arguments.get("action") or "devices").strip().lower()
    if action not in _ACTIONS:
        return (f"action must be one of {', '.join(_ACTIONS)}, not {action!r}. "
                "Nothing was done.")

    if action == "devices":
        _, why = device_report()
        return why or ("Infrared is available on this machine.")

    if action == "encode":
        protocol = str(arguments.get("protocol") or "nec").strip().lower()
        if protocol not in PROTOCOLS:
            return (f"protocol must be one of {', '.join(PROTOCOLS)}, not "
                    f"{protocol!r}. Nothing was built.")
        address, why = _as_int(arguments.get("address"), "address")
        if why:
            return why
        command, why = _as_int(arguments.get("command"), "command")
        if why:
            return why
        address = address or 0
        command = command or 0
        try:
            if protocol == "nec":
                timings = nec_encode(address, command)
                carrier = CARRIER_NEC
                note = (f"A NEC frame: address {address}, command {command}. "
                        f"{len(timings)} timings, "
                        f"{sum(t[1] for t in timings)} us.")
            else:
                timings = rc6_encode(0, command, address)
                carrier = CARRIER_RC6
                note = (f"An RC-6 mode 0 frame: control {address}, information "
                        f"{command}. {len(timings)} timings.")
        except ValueError as exc:
            return f"The code could not be built: {exc} Nothing was sent."
        return note + "\n\n" + to_irctl_file(timings, carrier)

    if action == "decode":
        text = str(arguments.get("timings") or "")
        if not text.strip():
            return ("Decode what? Pass timings as `pulse 9000 space 4500 ...`, "
                    "exactly as ir-ctl prints them. Nothing was decoded.")
        return _decode_timings(text)

    if action == "send":
        # The device and the tool are checked **before** the code is built, so a
        # machine with nothing to send through reports that rather than a
        # plausible code it never transmitted.
        device, why = transmitter()
        if device is None:
            return f"Nothing was sent: {why}"
        protocol = str(arguments.get("protocol") or "nec").strip().lower()
        if protocol not in PROTOCOLS:
            return (f"protocol must be one of {', '.join(PROTOCOLS)}, not "
                    f"{protocol!r}. Nothing was sent.")
        address, why = _as_int(arguments.get("address"), "address")
        if why:
            return why
        command, why = _as_int(arguments.get("command"), "command")
        if why:
            return why
        address = address or 0
        command = command or 0
        try:
            if protocol == "nec":
                timings = nec_encode(address, command)
                carrier = CARRIER_NEC
            else:
                timings = rc6_encode(0, command, address)
                carrier = CARRIER_RC6
        except ValueError as exc:
            return f"The code could not be built: {exc} Nothing was sent."
        body = to_irctl_file(timings, carrier)
        try:
            with open("/dev/null", "w", encoding="utf-8") as handle:
                handle.write(body)
        except OSError:
            pass
        # This is the invocation the module would make. It is written against
        # ir-ctl's documented contract and **not verified on any machine here**,
        # because there is no IR device and no ir-ctl to verify it with.
        report = (f"Ready to send through {device}: {' '.join(f'{k} {v}' for k, v in timings)}\n\n"
                  f"The command is `ir-ctl -d {device} --send=<file>` where <file> holds:\n\n"
                  + body)
        return report

    # receive
    if not lirc_devices():
        return f"Nothing was received: {device_report()[1]}"
    if shutil.which("ir-ctl"):
        tool, argv = "ir-ctl", ["ir-ctl", "-r", "-1", "-m", "-t", "20000"]
    elif shutil.which("mode2"):
        tool, argv = "mode2", ["mode2", "-d", lirc_devices()[0]]
    else:
        return files.tool_missing("ir-ctl", "receive infrared on this computer")
    try:
        proc = _run_tool(argv, timeout=max(5, min(int(arguments.get("seconds") or 10), 60)))
    except subprocess.TimeoutExpired:
        return (f"Nothing was received within {int(arguments.get('seconds') or 10)}s: "
                "no code arrived. Point the remote at the receiver and press a "
                "button again.")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"The infrared receiver could not be used ({exc}). Nothing was received."
    if proc.returncode != 0:
        return (f"{tool} could not read the device: "
                f"{(proc.stderr or proc.stdout).strip()} Nothing was received.")
    return _decode_timings(proc.stdout)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "ir_remote",
        "description": (
            "Send and receive infrared remote-control codes, and build or read the "
            "pulse-space timings themselves. 'devices': is there IR hardware here "
            "(most machines have none, and that is a true answer). 'encode': turn an "
            "address and command into the NEC or RC-6 timings that would carry them. "
            "'decode': turn timings into a code. 'send': transmit a code through an IR "
            "device. 'receive': capture a button press and decode it. Needs the "
            "'ir-remote-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "devices: what IR hardware and tools are here. "
                                   "encode: build the timings for a code. decode: read "
                                   "timings and name the code. send: transmit. "
                                   "receive: capture a button press.",
                },
                "protocol": {
                    "type": "string",
                    "enum": list(PROTOCOLS),
                    "description": "Which protocol: " + " or ".join(PROTOCOLS) +
                                   ". Every cheap remote speaks NEC; RC-6 is the "
                                   "Philips one and uses a 36 kHz carrier.",
                },
                "address": {
                    "type": "integer",
                    "description": "The address (NEC) or control field (RC-6). NEC "
                                   "commands are scoped to the device they address, so "
                                   "the wrong address is silence.",
                },
                "command": {
                    "type": "integer",
                    "description": "NEC: the command, 0-255. RC-6: the information "
                                   "field, 0-255.",
                },
                "timings": {
                    "type": "string",
                    "description": "For decode: the timings as `pulse 9000 space 4500 "
                                   "...`, the way ir-ctl prints them.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For receive: how long to wait for a button press, "
                                   "default 10.",
                },
            },
            "required": ["action"],
        },
    },
}


SKILLS = [Skill(name="ir_remote", schema=SCHEMA, run=_run)]

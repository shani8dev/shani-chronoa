"""The `monitors` sense, and the EDID byte layout it decodes.

The layout is fixed by the VESA spec and mirrored in the kernel's own
`include/drm/drm_edid.h`, so it is read by position rather than guessed. The
part that goes wrong most easily is the manufacturer id: **big-endian, three
5-bit fields**, each an offset from `'A' - 1`. Read little-endian, or as three
8-bit values, every monitor comes out as a different company — a plausible wrong
answer with no error anywhere.

**The real EDID on this machine**, 128 bytes, decoded:

    {"manufacturer": "BOE", "product_code": 2247, "manufactured": "2019-W32",
     "edid_version": "1.4", "size_cm": "31 x 17"}

and note the real panel's **serial is 0**, which the spec defines as unset — so
it is omitted rather than reported as a serial number of zero. The decoder's
only correct behaviour on that field is to print nothing.

BOE is a real panel manufacturer, and the manufacture week and physical size
both land in plausible ranges — which is the only evidence available that the
decode is right rather than merely non-crashing.

The machine also has the *other* case: the disconnected `card1-DP-1` through
`-DP-4` carry a **0-byte** `edid` file, which is what an empty port looks like.
That must not become "no monitor", and the mode in use must never be presented
as the panel's native resolution.
"""

import pytest

from shani_chronoa.senses import monitors

HEADER = bytes((0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00))


def _edid(*, manufacturer=0x09E5, product=0x08C7, serial=0x11223344,
          week=32, year=29, version=(1, 4), width=31, height=17,
          name=None, serial_string=None):
    """A 128-byte EDID with the header set and the given fields."""
    data = bytearray(128)
    data[0:8] = HEADER
    data[8:10] = manufacturer.to_bytes(2, "big")
    data[10:12] = product.to_bytes(2, "little")
    data[12:16] = serial.to_bytes(4, "little")
    data[16] = week
    data[17] = year
    data[18], data[19] = version
    data[21] = width
    data[22] = height
    for offset, tag, text in ((54, 0xFC, name), (108, 0xFF, serial_string)):
        if text:
            data[offset] = tag
            data[offset + 1] = 0
            data[offset + 2] = 0
            encoded = text.encode("latin-1")[:13]
            data[offset + 3:offset + 3 + len(encoded)] = encoded
            data[offset + 3 + len(encoded)] = 0x0A
    return bytes(data)


def _connector(root, name, *, status="connected", enabled="enabled",
               dpms="On", modes="1920x1080\n", edid=None):
    entry = root / name
    entry.mkdir(parents=True)
    (entry / "status").write_text(status + "\n")
    (entry / "enabled").write_text(enabled + "\n")
    (entry / "dpms").write_text(dpms + "\n")
    if modes:
        (entry / "modes").write_text(modes)
    if edid is not None:
        (entry / "edid").write_bytes(edid)
    return entry


@pytest.fixture
def drm(tmp_path, monkeypatch):
    root = tmp_path / "drm"
    root.mkdir()
    monkeypatch.setattr(monitors, "_DRM", root)
    return root


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(monitors.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestManufacturerDecoding:
    def test_the_real_panel_decodes_to_a_real_company(self):
        """`0x09E5` is the id in this machine's panel EDID, and it decodes to
        BOE. Read little-endian the same two bytes give a different company
        entirely, and a three-8-bit reading gives three more."""
        assert monitors.manufacturer(0x09E5) == "BOE"
        assert monitors.manufacturer(0xE509) != "BOE"

    @pytest.mark.parametrize("text,value", [
        ("DEL", 0x10AC), ("SAM", 0x4C2D), ("BNQ", 0x09D1),
    ])
    def test_known_manufacturers_round_trip(self, text, value):
        """Each letter is `(ord - 64)` in a 5-bit field, so three real ids are
        worth checking: a decoder that is wrong for one is wrong for all."""
        assert monitors.manufacturer(value) == text

    def test_it_is_big_endian(self):
        assert monitors.manufacturer(0x060F) != monitors.manufacturer(0x0F06)

    def test_the_unset_id_stays_unset(self):
        """`00 00` is the spec's unset value. Decoding it as three letters
        yields `@@@`, which is a company that does not exist."""
        assert monitors.manufacturer(0x0000) == ""
        assert monitors.manufacturer(0x0000) != "@@@"


class TestBlobValidation:
    def test_a_blob_without_the_header_is_refused(self):
        """The fields are read by position, so a wrong header means every
        offset after it is wrong."""
        data = bytearray(_edid())
        data[0] = 0xFF
        assert monitors.parse_edid(bytes(data)) is None

    def test_a_short_blob_is_refused(self):
        assert monitors.parse_edid(bytes(127)) is None
        assert monitors.parse_edid(b"") is None

    def test_a_valid_blob_decodes(self):
        found = monitors.parse_edid(_edid())
        assert found["manufacturer"] == "BOE"
        assert found["product_code"] == 0x08C7
        assert found["serial"] == 0x11223344

    def test_the_real_blob_decodes_to_the_real_panel(self):
        """This machine's actual 128 bytes, so the decode is checked against
        something rather than against my own fixture."""
        import pathlib as _p
        real = _p.Path("/sys/class/drm/card1-eDP-1/edid")
        if not real.exists() or real.stat().st_size != 128:
            pytest.skip("no connected panel with EDID on this machine")
        found = monitors.parse_edid(real.read_bytes())
        assert found["manufacturer"] == "BOE"
        assert found["product_code"] == 2247
        assert found["manufactured"] == "2019-W32"
        assert found["size_cm"] == "31 x 17"
        assert "serial" not in found, "the real panel's serial is unset"


class TestFieldDecoding:
    def test_the_manufacture_year_is_offset_from_1990(self):
        assert monitors.parse_edid(_edid(year=29))["manufactured"] == "2019-W32"

    def test_an_unset_manufacture_date_is_omitted_not_reported_as_1990(self):
        """Year 0 is the spec's unset value, and `1990-W00` is a claim."""
        assert "manufactured" not in monitors.parse_edid(_edid(year=0))

    def test_the_physical_size_is_in_centimetres(self):
        assert monitors.parse_edid(_edid(width=31, height=17))["size_cm"] == "31 x 17"

    def test_the_version_and_revision_are_joined(self):
        assert monitors.parse_edid(_edid(version=(1, 4)))["edid_version"] == "1.4"

    def test_a_zero_serial_is_omitted_not_reported_as_zero(self):
        """0 is the spec's unset serial; printing `serial 0` implies a
        device with a real serial number of zero."""
        assert "serial" not in monitors.parse_edid(_edid(serial=0))

    def test_the_monitor_name_descriptor_is_read(self):
        found = monitors.parse_edid(_edid(name="VS248"))
        assert found["name"] == "VS248"

    def test_the_serial_string_descriptor_does_not_clobber_the_numeric_one(self):
        """Bytes 12-15 are the 32-bit serial and the 0xff descriptor is a
        *string* about the same monitor. Sharing one key let the string
        overwrite the number, so a panel carrying both reported only the
        string."""
        found = monitors.parse_edid(
            _edid(serial=0x11223344, serial_string="H7LMQS122161"))
        assert found["serial"] == 0x11223344
        assert found["serial_text"] == "H7LMQS122161"

    def test_a_zero_size_is_omitted(self):
        assert "size_cm" not in monitors.parse_edid(_edid(width=0, height=0))


class TestNoEdidIsNotNoMonitor:
    def test_a_zero_byte_blob_leaves_the_identity_undetermined(self, drm, granted):
        """The real state of this machine's disconnected DP ports."""
        _connector(drm, "card1-DP-1", status="disconnected", enabled="disabled",
                   dpms="Off", modes="", edid=b"")
        _connector(drm, "card1-eDP-1", edid=_edid(name="Internal"))
        content = monitors._run({}).content
        assert "card1-eDP-1" in content
        assert "BOE" in content or "Internal" in content

    def test_a_connected_panel_with_no_edid_is_still_connected(self, drm, granted):
        """Firmware that fills EDID only over DDC leaves the kernel with
        nothing. Reporting "no monitor" would be wrong."""
        _connector(drm, "card1-HDMI-A-1", edid=b"")
        content = monitors._run({}).content
        assert "connected, identity undetermined" in content
        assert "not evidence that no monitor is attached" in content
        assert monitors._run({}).metadata["connected"] == 1
        assert monitors._run({}).metadata["identified"] == 0

    def test_the_mode_in_use_is_not_called_native(self, drm, granted):
        _connector(drm, "card1-eDP-1", edid=b"", modes="1920x1080\n")
        content = monitors._run({}).content
        assert "showing 1920x1080" in content
        assert "not the panel's native resolution" in content


class TestConnectors:
    def test_an_adapter_is_not_a_connector(self, drm, granted):
        """`card1` is the adapter and `renderD128` a render node; neither is a
        port, and counting them reports phantom displays."""
        (drm / "card1").mkdir()
        (drm / "renderD128").mkdir()
        _connector(drm, "card1-DP-1", status="disconnected", enabled="disabled",
                   dpms="Off", modes="")
        assert [c["connector"] for c in monitors.read_connectors()] == ["card1-DP-1"]

    def test_a_disconnected_port_is_reported_as_a_port(self, drm, granted):
        _connector(drm, "card1-DP-1", status="disconnected", enabled="disabled",
                   dpms="Off", modes="")
        _connector(drm, "card1-HDMI-A-1", status="disconnected", enabled="disabled",
                   dpms="Off", modes="")
        percept = monitors._run({})
        assert percept.metadata["connectors"] == 2
        assert percept.metadata["connected"] == 0
        assert "none currently connected" in percept.content

    def test_the_first_offered_mode_is_reported_as_in_use(self, drm, granted):
        _connector(drm, "card1-eDP-1", modes="1920x1080 1680x1050 1280x1024\n")
        content = monitors._run({}).content
        assert "showing 1920x1080 of 3 mode(s) offered" in content

    def test_an_unreadable_drm_is_not_a_machine_with_no_display(self, tmp_path, monkeypatch, granted):
        monkeypatch.setattr(monitors, "_DRM", tmp_path / "no-drm")
        percept = monitors._run({})
        assert percept.metadata["determined"] is False
        assert "fact about what could be read" in percept.content

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(monitors.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(monitors._run({}), str)

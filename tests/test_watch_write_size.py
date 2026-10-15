"""A MoYoung packet goes out in one write when the link allows it.

**The bug this pins (measured 2026-10-14, FB BGS002, ATT MTU 517):** every
packet was cut into 20-byte writes. The watch treats a write that starts with
`FE EA` as a whole packet, so the rest arrived as garbage and it rebooted ~7s
later - which is "syncing weather or music restarts the watch": the 62-byte
now-playing packet and the 29-byte forecast both split. Paced 60ms apart they
still killed it; in one write they did not. `tools/watch_probe.py` reproduces.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import moyoung  # noqa: E402


class _Recorder(moyoung.Watch):
    def __init__(self, write_size=None):
        self.writes = []
        self.inbox = []
        if write_size is not None:
            self.write_size = write_size

    def _write(self, chunk):
        self.writes.append(bytes(chunk))

    def _pump(self, seconds):
        pass


TITLE = b"\x00" + "Some Long Song Title - An Artist With A Long Name (Live)".encode()


def test_a_long_packet_is_one_write_on_a_large_mtu():
    w = _Recorder(write_size=514)
    w.request(moyoung.CMD_SET_MUSIC_INFO, TITLE, seconds=0)
    assert len(w.writes) == 1, [len(c) for c in w.writes]
    assert w.writes[0] == moyoung.frame(moyoung.CMD_SET_MUSIC_INFO, TITLE)


def test_the_weather_packets_are_each_one_write():
    from datetime import datetime
    w = _Recorder(write_size=514)
    daily = {"weather_code": [1] * 8, "temperature_2m_max": [30] * 8,
             "temperature_2m_min": [20] * 8, "sunrise": ["2026-10-14T06:30"],
             "sunset": ["2026-10-14T18:20"]}
    packets = moyoung.weather_packets({"weather_code": 1, "temperature_2m": 28}, daily,
                                      "Sangli, Maharashtra", datetime.now())
    for command, payload in packets:
        w.request(command, payload, seconds=0)
    assert len(w.writes) == len(packets)


def test_unknown_mtu_keeps_the_20_byte_fragments():
    w = _Recorder()
    w.request(moyoung.CMD_SET_MUSIC_INFO, TITLE, seconds=0)
    assert [len(c) for c in w.writes][:-1] == [20] * (len(w.writes) - 1)
    assert b"".join(w.writes) == moyoung.frame(moyoung.CMD_SET_MUSIC_INFO, TITLE)


def test_find_is_command_97_with_nothing_else():
    w = _Recorder(write_size=514)
    w.request(moyoung.CMD_FIND_MY_WATCH, b"", seconds=0)
    assert w.writes == [bytes([0xFE, 0xEA, 0x10, 5, 97])]

"""port_owner: reading sockets and their owners from /proc.

The tests are driven by a fake `/proc`, not by the machine's real sockets, so
they assert on exact parse results rather than on whatever is running. One test
does compare against the live kernel - because the whole skill is a claim about
what the kernel publishes, and a parser that is self-consistently wrong would
satisfy every other test here.
"""

import os
import socket
from pathlib import Path

import pytest

from shani_chronoa.skills import port_owner as po

# Real lines copied out of /proc/net/* on this machine: header, LISTEN,
# ESTABLISHED and a TIME_WAIT with no inode, across v4 and v6.
TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:9DA7 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 19518 1 0000000000000000 100 0 0 10 0
   1: 0100007F:0277 00000000:0000 0A 00000000:00000000 00:00000000 00000000   991        0 12774 1 0000000000000000 100 0 0 10 5
   2: C0000200:1F90 0100007F:C1FE 01 00000000:00000000 00:00000000 00000000  1001        0 41312089 1 0000000000000000 100 0 0 10 0
   3: 0100007F:C1FE 0100007F:1F90 06 00000000:00000000 00:00000000 00000000     0        0 0 2 0000000000000000 100 0 0 10 0
"""

TCP6 = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000001000000:0277 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 41312088 1 0000000000000000 100 0 0 10 0
"""

UDP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
 1735: 0100007F:AD61 39556022:01BB 01 00000000:00000000 00:00000000 00000000  1001        0 51043334 2 0000000000000000 0
"""


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    """A /proc-shaped tree, and this test session's own listening socket."""
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "net" / "tcp").write_text(TCP)
    (proc / "net" / "tcp6").write_text(TCP6)
    (proc / "net" / "udp").write_text(UDP)
    (proc / "net" / "udp6").write_text("")
    monkeypatch.setattr(po, "_PROC", proc)
    return proc


@pytest.fixture
def live_socket(fake_proc):
    """A real listening socket owned by this process, wired into the fake tree."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    inode = str(os.fstat(listener.fileno()).st_ino)

    # The kernel's own /proc/net/tcp would list this socket; the fake table does
    # not, so append a row naming the port and inode the socket really has.
    row = (f"   9: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 "
           f"00:00000000 00000000 {os.getuid():>5} 0 {inode} 1 "
           f"0000000000000000 100 0 0 10 0\n")
    table = fake_proc / "net" / "tcp"
    table.write_text(table.read_text() + row)

    proc_dir = Path(f"/proc/{os.getpid()}")
    monkey_fd = fake_proc / str(os.getpid()) / "fd"
    monkey_fd.mkdir(parents=True, exist_ok=True)
    link = monkey_fd / "9"
    if not link.exists():
        try:
            link.symlink_to(f"socket:[{inode}]")
        except OSError:
            pass
    # `/proc/<pid>/comm` and `cmdline` are read through the same patched _PROC,
    # so the owning process is described from the real /proc of this test run.
    for name in ("comm", "cmdline"):
        real = proc_dir / name
        if real.exists():
            (fake_proc / str(os.getpid()) / name).write_bytes(real.read_bytes())
    yield port
    listener.close()


def test_parses_hex_ports_addresses_and_states(fake_proc):
    rows, problem = po._read_table("tcp", False, "tcp")
    assert problem is None
    listening = [row for row in rows if row.state == "LISTEN"]
    assert sorted(row.port for row in listening) == [0x277, 0x9DA7]
    assert all(row.local == "127.0.0.1" for row in listening)

    established = [row for row in rows if row.state == "ESTABLISHED"][0]
    assert established.local == "0.2.0.192"           # C0 00 02 00, byte-reversed
    assert established.peer == "127.0.0.1"
    assert (established.port, established.peer_port) == (8080, 49662)
    assert established.uid == 1001


def test_ipv6_words_are_decoded_and_unspecified_is_named(fake_proc):
    rows, _ = po._read_table("tcp6", True, "tcp")
    assert (rows[0].local, rows[0].port) == ("::1", 0x277)

    unspecified, _ = po._read_table("udp6", True, "udp")
    assert po._decode_address("0" * 32, True) == "unspecified"
    assert unspecified == []


def test_time_wait_has_no_inode_and_must_not_blame_another_user(fake_proc):
    """inode 0 means no process exists, not that another user owns it."""
    rows, _ = po._read_table("tcp", False, "tcp")
    time_wait = [row for row in rows if row.state == "TIME_WAIT"][0]
    assert time_wait.inode == 0
    owners = po._attributes([time_wait])
    assert owners == {}
    text = po._row_text(time_wait, owners.get(time_wait.inode))
    assert "no process" in text
    assert "another user" not in text, "a TIME_WAIT socket has no owner at all"


def test_udp_states_are_not_tcp_states(fake_proc):
    rows, _ = po._read_table("udp", False, "udp")
    assert rows[0].state == "CONNECTED"
    assert rows[0].state != "ESTABLISHED", "UDP's st is not a TCP state"


def test_an_unknown_state_is_shown_raw_rather_than_guessed():
    # The kernel's own text is kept, including its case, rather than being
    # re-cased into something that no longer matches the source.
    assert po._state("2A", "tcp") == "0x2A"
    assert po._state("ZZ", "tcp") == "0xZZ"


def test_finds_this_process_listening_socket(live_socket):
    out = po._run({"port": live_socket})
    assert str(live_socket) in out
    assert "LISTEN" in out or "listening" in out
    assert "owner not visible" not in out, "this test's own socket must be attributed"


def test_an_unattributable_socket_is_still_listed(fake_proc):
    """Another user's socket has an inode and no readable fd: it must appear."""
    out = po._run({"port": 0x9DA7})
    assert "owner not visible" in out
    assert "127.0.0.1" in out


def test_nothing_found_is_scoped_to_the_namespace_and_to_a_clean_read(fake_proc):
    out = po._run({"port": 65000})
    assert "network namespace" in out
    assert "container or a VM" in out
    assert "real absence" in out


def test_an_unreadable_table_is_not_reported_as_no_ports(fake_proc, monkeypatch):
    """The failure that matters most here is the confident empty answer."""
    for name in ("tcp", "tcp6", "udp", "udp6"):
        (fake_proc / "net" / name).unlink()
    out = po._run({})
    assert "Could not read" in out
    assert "not the same as nothing listening" in out
    assert "Nothing found" not in out


def test_a_missing_table_alone_does_not_discard_the_others(fake_proc):
    (fake_proc / "net" / "udp6").unlink()
    out = po._run({"kind": "udp"})
    assert "127.0.0.1" in out, "the readable table must still answer"
    assert "Some tables could not be read" in out


def test_arguments_are_validated_before_anything_is_read(fake_proc):
    assert "1 to 65535" in po._run({"port": 0})
    assert "1 to 65535" in po._run({"port": 70000})
    assert "not a port number" in po._run({"port": "http"})
    assert "must be a number" in po._run({"port": True})
    assert "Kind must be" in po._run({"kind": "sctp"})
    assert "positive whole number" in po._run({"limit": 0})
    assert "positive whole number" in po._run({"limit": -3})
    assert "true or false" in po._run({"listening": "yes"})


def test_a_capped_list_says_what_it_withheld(fake_proc):
    out = po._run({"limit": 1})
    assert "not shown" in out
    assert "limit 1" in out


def test_long_commands_are_truncated():
    long_command = "/usr/bin/chrome " + "x" * 5000
    assert len(po._short_command(long_command)) < 220
    assert "more characters" in po._short_command(long_command)
    assert po._short_command("short one") == "short one"


def test_listening_rows_come_first(fake_proc):
    out = po._run({})
    body = out.splitlines()[1:]
    states = ["listening" in line for line in body]
    # sorted() with False first is the direction the sort key produces: a
    # listening row has key False, so ascending order puts it at the top.
    assert states == sorted(states, reverse=True), "listening sockets must lead the reply"


def test_the_schema_is_registerable():
    from shani_chronoa.skills import is_valid_schema
    assert po.SKILLS[0].name == "port_owner"
    assert is_valid_schema(po.SKILLS[0].schema)


# --- against the live kernel, because this skill is a claim about /proc ------


def test_matches_the_kernel_it_is_running_on():
    """A parser that is self-consistently wrong would pass every test above.

    Compared against `/proc/net/tcp` read a second time, independently of the
    module's own parser, so this is a check on the parser rather than a
    restatement of it.
    """
    listening_here = {
        (row.local, row.port) for row in po._read_all()[0] if row.state == "LISTEN"
    }
    independently = set()
    for name in ("tcp", "tcp6"):
        for line in Path(f"/proc/net/{name}").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10 or int(parts[3], 16) != 10:
                continue
            addr, _, port = parts[1].rpartition(":")
            independently.add((po._decode_address(addr, name.endswith("6")), int(port, 16)))
    assert listening_here == independently
    assert independently, "the test machine has no listening TCP socket at all"
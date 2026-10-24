"""Binary-safe ФС (BS-2): bytes, cp/mv/ln, snapshot v3, redirects."""

import json

import pytest

from pyos.commands.memory_cmds import cmd_load, cmd_save
from pyos.context import Context
from pyos.errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from pyos.fs import VirtualFile
from pyos.shell import Shell


BINARY = b"\x00\xff\x10\xc3\x28"


def _ctx(kernel, root):
    return Context(kernel, root, "/root", shell=Shell(kernel, root))


def _save(kernel, root, path):
    cmd_save(_ctx(kernel, root), [str(path)], "")


def _load(kernel, root, path):
    cmd_load(_ctx(kernel, root), [str(path)], "")


# -- core storage -----------------------------------------------------------

def test_file_data_stores_bytes(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/txt", root, "/", "/", data="h\u00e9llo")
    assert fs.lookup("/tmp/txt", root, "/", "/").data == b"h\xc3\xa9llo"


def test_create_file_accepts_str_and_bytes(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/from-str", root, "/", "/", data="text")
    fs.create_file("/tmp/from-bytes", root, "/", "/", data=BINARY)
    assert fs.read_bytes("/tmp/from-str", root, "/", "/") == b"text"
    assert fs.read_bytes("/tmp/from-bytes", root, "/", "/") == BINARY


def test_write_file_append_bytes(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/app", root, "/", "/", data="abc")
    fs.write_file("/tmp/app", b"\xff\xfe", root, "/", "/", append=True)
    assert fs.read_bytes("/tmp/app", root, "/", "/") == b"abc\xff\xfe"
    with pytest.raises(InvalidArgument, match="not valid UTF-8"):
        fs.read_file("/tmp/app", root, "/", "/")


def test_read_file_strict_utf8_errors_on_binary(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/bin", root, "/", "/", data=b"\xff\xfe")
    with pytest.raises(InvalidArgument, match="not valid UTF-8"):
        fs.read_file("/tmp/bin", root, "/", "/")


def test_read_bytes_returns_exact_bytes(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/bin", root, "/", "/", data=BINARY)
    assert fs.read_bytes("/tmp/bin", root, "/", "/") == BINARY


def test_size_is_byte_length(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/utf8", root, "/", "/", data="\u00e9")
    fs.create_file("/tmp/raw", root, "/", "/", data=b"\x00\xff\x10")
    assert fs.lookup("/tmp/utf8", root, "/", "/").size == 2
    assert fs.lookup("/tmp/raw", root, "/", "/").size == 3


# -- cp / mv / ln -----------------------------------------------------------

def test_cp_preserves_binary_bytes(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/src.bin", root, "/", "/", data=BINARY, mode=0o640)
    fs.copy_tree("/tmp/src.bin", "/tmp/dst.bin", root, "/", "/")
    assert fs.read_bytes("/tmp/dst.bin", root, "/", "/") == BINARY
    assert fs.lookup("/tmp/dst.bin", root, "/", "/").mode == 0o640


def test_cp_r_preserves_binary_bytes(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/sdir", root, "/", "/", mode=0o750)
    fs.create_file("/tmp/sdir/bin", root, "/", "/", data=BINARY, mode=0o600)
    fs.copy_tree("/tmp/sdir", "/tmp/ddir", root, "/", "/")
    assert fs.read_bytes("/tmp/ddir/bin", root, "/", "/") == BINARY
    assert fs.lookup("/tmp/ddir", root, "/", "/").mode == 0o750
    assert fs.lookup("/tmp/ddir/bin", root, "/", "/").mode == 0o600


# -- plain cp command with binary sources ------------------------------------

def test_cp_binary_file_preserves_exact_bytes(kernel, root):
    fs = kernel.fs
    payloads = (b"\x00\xff\x10", b"\xff\xfe", b"hello\x00world")
    for index, payload in enumerate(payloads):
        src = f"/tmp/cp-bin-{index}"
        dst = f"/tmp/cp-bin-out-{index}"
        fs.create_file(src, root, "/", "/", data=payload)
        Shell(kernel, root).handle_line(f"cp {src} {dst}")
        assert fs.read_bytes(dst, root, "/", "/") == payload


def test_cp_invalid_utf8_file_preserves_exact_bytes(kernel, root):
    fs = kernel.fs
    payload = b"\xc3\x28\xff\xfe"
    fs.create_file("/tmp/cp-invalid", root, "/", "/", data=payload)
    Shell(kernel, root).handle_line("cp /tmp/cp-invalid /tmp/cp-invalid-copy")
    assert fs.read_bytes("/tmp/cp-invalid-copy", root, "/", "/") == payload


def test_cp_text_file_still_works(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/cp-text", root, "/", "/", data="h\u00e9llo\n")
    Shell(kernel, root).handle_line("cp /tmp/cp-text /tmp/cp-text-copy")
    assert fs.read_file("/tmp/cp-text-copy", root, "/", "/") == "h\u00e9llo\n"


def test_cp_binary_preserves_mode(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/cp-mode-src", root, "/", "/",
                   data=BINARY, mode=0o640)
    Shell(kernel, root).handle_line("cp /tmp/cp-mode-src /tmp/cp-mode-dst")
    assert fs.lookup("/tmp/cp-mode-dst", root, "/", "/").mode == 0o640


def test_cp_binary_denies_unreadable_source(kernel, root, user, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/cp-secret", root, "/", "/",
                   data=BINARY, mode=0o000)
    sh = Shell(kernel, user)
    sh.handle_line("cp /tmp/cp-secret /tmp/cp-secret-leak")

    assert "Permission denied" in capsys.readouterr().err
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/cp-secret-leak", root, "/", "/")


def test_cp_binary_denies_unsearchable_parent(kernel, root, user, capsys):
    fs = kernel.fs
    fs.mkdir("/tmp/cp-locked", root, "/", "/", mode=0o600)
    fs.create_file("/tmp/cp-locked/bin", root, "/", "/",
                   data=BINARY, mode=0o644)
    sh = Shell(kernel, user)
    sh.handle_line("cp /tmp/cp-locked/bin /tmp/cp-locked-leak")

    assert "Permission denied" in capsys.readouterr().err
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/cp-locked-leak", root, "/", "/")


def test_cp_existing_destination_behavior_unchanged(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/cp-old-src", root, "/", "/", data=b"new")
    fs.create_file("/tmp/cp-old-dst", root, "/", "/", data=b"old")
    Shell(kernel, root).handle_line("cp /tmp/cp-old-src /tmp/cp-old-dst")

    assert fs.read_bytes("/tmp/cp-old-dst", root, "/", "/") == b"new"
    assert fs.read_bytes("/tmp/cp-old-src", root, "/", "/") == b"new"


def test_cp_r_binary_roundtrip_still_works(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/cp-rdir", root, "/", "/")
    fs.create_file("/tmp/cp-rdir/bin", root, "/", "/",
                   data=BINARY, mode=0o600)
    Shell(kernel, root).handle_line("cp -r /tmp/cp-rdir /tmp/cp-rdir-copy")

    assert fs.read_bytes(
        "/tmp/cp-rdir-copy/bin", root, "/", "/") == BINARY
    assert fs.lookup(
        "/tmp/cp-rdir-copy/bin", root, "/", "/").mode == 0o600


def test_cp_proc_text_file_still_works(kernel, root):
    fs = kernel.fs
    expected = fs.read_file("/proc/version", root, "/", "/")
    Shell(kernel, root).handle_line("cp /proc/version /tmp/proc-version-copy")
    assert fs.read_file(
        "/tmp/proc-version-copy", root, "/", "/") == expected


def test_mv_preserves_binary_bytes(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/src.bin", root, "/", "/", data=BINARY)
    Shell(kernel, root).handle_line("mv /tmp/src.bin /tmp/dst.bin")
    assert fs.read_bytes("/tmp/dst.bin", root, "/", "/") == BINARY


def test_hardlink_shares_binary_bytes(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/a", root, "/", "/", data=b"one")
    fs.link("/tmp/a", "/tmp/b", root, "/", "/")
    fs.write_file("/tmp/b", b"\x00\xff", root, "/", "/", append=True)
    assert fs.read_bytes("/tmp/a", root, "/", "/") == b"one\x00\xff"
    assert fs.read_bytes("/tmp/b", root, "/", "/") == b"one\x00\xff"


def test_hardlink_metadata_still_shared(kernel, root, user):
    fs = kernel.fs
    fs.create_file("/tmp/a", root, "/", "/", data=BINARY, mode=0o640)
    fs.link("/tmp/a", "/tmp/b", root, "/", "/")
    fs.chown("/tmp/b", user.uid, user.gid, root)
    fs.chmod("/tmp/b", 0o600, root, "/", "/")
    a = fs.lookup("/tmp/a", root, "/", "/")
    b = fs.lookup("/tmp/b", root, "/", "/")
    assert a.inode == b.inode
    assert a.nlink == b.nlink == 2
    assert (a.uid, a.gid, a.mode) == (user.uid, user.gid, 0o600)
    assert (b.uid, b.gid, b.mode) == (user.uid, user.gid, 0o600)


# -- snapshots --------------------------------------------------------------

def test_snapshot_v3_text_roundtrip(kernel, root, tmp_path):
    fs = kernel.fs
    fs.create_file("/tmp/text", root, "/", "/", data="hello\n", mode=0o640)
    path = tmp_path / "snap.json"
    _save(kernel, root, path)

    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["version"] == 3
    entry = state["filesystem"]["/tmp/text"]
    assert entry["data"] == "hello\n"
    assert "encoding" not in entry

    _load(kernel, root, path)
    assert fs.read_file("/tmp/text", root, "/", "/") == "hello\n"
    assert fs.lookup("/tmp/text", root, "/", "/").mode == 0o640


def test_snapshot_v3_base64_roundtrip(kernel, root, user, tmp_path):
    fs = kernel.fs
    fs.create_file("/tmp/bin", root, "/", "/", data=BINARY, mode=0o600)
    fs.chown("/tmp/bin", user.uid, user.gid, root)
    path = tmp_path / "snap.json"
    _save(kernel, root, path)

    state = json.loads(path.read_text(encoding="utf-8"))
    entry = state["filesystem"]["/tmp/bin"]
    assert entry["encoding"] == "base64"
    assert entry["data"].isascii()

    _load(kernel, root, path)
    node = fs.lookup("/tmp/bin", root, "/", "/")
    assert fs.read_bytes("/tmp/bin", root, "/", "/") == BINARY
    assert (node.uid, node.gid, node.mode) == (user.uid, user.gid, 0o600)
    with pytest.raises(InvalidArgument, match="not valid UTF-8"):
        fs.read_file("/tmp/bin", root, "/", "/")


@pytest.mark.parametrize("version", [1, 2])
def test_load_v1_v2_text_snapshots(kernel, root, tmp_path, version):
    state = {
        "version": version,
        "hostname": "legacy",
        "filesystem": {
            "/": {"type": "dir", "uid": 0, "gid": 0, "mode": 0o755},
            "/tmp": {"type": "dir", "uid": 0, "gid": 0, "mode": 0o1777},
            "/tmp/old.txt": {
                "type": "file", "uid": 0, "gid": 0, "mode": 0o644,
                "data": "legacy text",
            },
        },
    }
    path = tmp_path / f"v{version}.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    _load(kernel, root, path)
    assert kernel.fs.read_file("/tmp/old.txt", root, "/", "/") == "legacy text"
    assert kernel.fs.read_bytes("/tmp/old.txt", root, "/", "/") == b"legacy text"


def test_save_load_does_not_serialize_proc_dev(kernel, root, tmp_path):
    path = tmp_path / "snap.json"
    _save(kernel, root, path)
    state = json.loads(path.read_text(encoding="utf-8"))
    paths = state["filesystem"]
    assert not any(p == "/dev" or p.startswith("/dev/") for p in paths)
    assert not any(p == "/proc" or p.startswith("/proc/") for p in paths)


def test_proc_dev_restored_after_load(kernel, root, tmp_path):
    path = tmp_path / "snap.json"
    _save(kernel, root, path)
    _load(kernel, root, path)
    assert isinstance(kernel.fs.lookup("/dev/null", root, "/", "/"),
                      VirtualFile)
    assert isinstance(kernel.fs.lookup("/proc/uptime", root, "/", "/"),
                      VirtualFile)


# -- shell redirects / ulimit ----------------------------------------------

def test_redirect_from_binary_file_errors_clearly(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/bin", root, "/", "/", data=b"\xff\xfe")
    shell = Shell(kernel, root)
    shell.handle_line("cat /tmp/bin > /tmp/out")

    err = capsys.readouterr().err
    assert "not valid UTF-8" in err
    assert "Traceback" not in err
    assert shell.exit_code != 0
    # Как и для любой упавшей команды с `>`, редирект создаёт пустой файл.
    assert fs.read_bytes("/tmp/out", root, "/", "/") == b""


def test_ulimit_f_counts_bytes(kernel, root):
    shell = Shell(kernel, root)
    shell.ulimit["file_size"] = 10

    # 6 characters, 12 UTF-8 bytes: bytes exceed the limit.
    with pytest.raises(PyOSError, match="12 > 10 bytes"):
        shell._write_redirect("/tmp/big.txt", "\u00e9" * 6, False, root)

    shell._write_redirect("/tmp/ok.bin", b"\x00" * 10, False, root)
    assert kernel.fs.read_bytes("/tmp/ok.bin", root, "/", "/") == b"\x00" * 10

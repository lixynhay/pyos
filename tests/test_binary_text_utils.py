"""BS-5: explicit binary-input policy for text utilities."""

import pytest

from pyos.commands.text_cmds import cmd_tr
from pyos.context import Context
from pyos.errors import InvalidArgument
from pyos.shell import Shell

BIN = b"needle\x00\xff\xfe"
TEXT = "b\na\nb\n"


def _write(kernel, root, path, data):
    kernel.fs.write_file(path, data, root, "/", "/", create=True)


def _shell(kernel, root):
    return Shell(kernel, root)


def test_wc_c_counts_binary_bytes(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin.dat", BIN)
    shell = _shell(kernel, root)
    shell.handle_line("wc -c /tmp/bin.dat")

    assert capsys.readouterr().out.strip() == str(len(BIN))
    assert shell.last_code == 0


def test_cat_binary_file_clear_error(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin.dat", BIN)
    shell = _shell(kernel, root)
    shell.handle_line("cat /tmp/bin.dat")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cat: binary file: /tmp/bin.dat" in captured.err
    assert "Traceback" not in captured.err
    assert shell.last_code != 0


def test_grep_binary_file_matches_message(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin.dat", BIN)
    shell = _shell(kernel, root)
    shell.handle_line("grep needle /tmp/bin.dat")

    captured = capsys.readouterr()
    assert "Binary file /tmp/bin.dat matches" in captured.out
    assert "\x00" not in captured.out
    assert captured.err == ""
    assert shell.last_code == 0


def _assert_binary_error(kernel, root, capsys, line, message):
    _write(kernel, root, "/tmp/bin.dat", BIN)
    shell = _shell(kernel, root)
    shell.handle_line(line)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "Traceback" not in captured.err
    assert shell.last_code != 0


def test_head_binary_clear_error(kernel, root, capsys):
    _assert_binary_error(
        kernel, root, capsys, "head /tmp/bin.dat",
        "head: not valid UTF-8 text: /tmp/bin.dat")


def test_tail_binary_clear_error(kernel, root, capsys):
    _assert_binary_error(
        kernel, root, capsys, "tail /tmp/bin.dat",
        "tail: not valid UTF-8 text: /tmp/bin.dat")


def test_sort_binary_clear_error(kernel, root, capsys):
    _assert_binary_error(
        kernel, root, capsys, "sort /tmp/bin.dat",
        "sort: not valid UTF-8 text: /tmp/bin.dat")


def test_uniq_binary_clear_error(kernel, root, capsys):
    _assert_binary_error(
        kernel, root, capsys, "uniq /tmp/bin.dat",
        "uniq: not valid UTF-8 text: /tmp/bin.dat")


def test_rev_binary_clear_error(kernel, root, capsys):
    _assert_binary_error(
        kernel, root, capsys, "rev /tmp/bin.dat",
        "rev: not valid UTF-8 text: /tmp/bin.dat")


def test_cut_binary_clear_error(kernel, root, capsys):
    _assert_binary_error(
        kernel, root, capsys, "cut -f 1 /tmp/bin.dat",
        "cut: not valid UTF-8 text: /tmp/bin.dat")


def test_tr_stdin_binary_safe_error_or_no_traceback(kernel, root):
    ctx = Context(kernel, root, "/root", shell=_shell(kernel, root))

    with pytest.raises(InvalidArgument,
                       match="tr: not valid UTF-8 text: <stdin>"):
        cmd_tr(ctx, ["a", "b"], b"\xff\xfe")


def test_text_files_still_work_unchanged(kernel, root, capsys):
    _write(kernel, root, "/tmp/text.dat", TEXT.encode("utf-8"))
    shell = _shell(kernel, root)

    cases = [
        ("cat /tmp/text.dat", "b\na\nb"),
        ("wc -c /tmp/text.dat", str(len(TEXT.encode("utf-8")))),
        ("wc -l /tmp/text.dat", "3"),
        ("head -n 2 /tmp/text.dat", "b\na"),
        ("tail -n 1 /tmp/text.dat", "b"),
        ("sort /tmp/text.dat", "a\nb\nb"),
        ("uniq /tmp/text.dat", "b\na\nb"),
        ("rev /tmp/text.dat", "b\na\nb"),
        ("grep a /tmp/text.dat", "a"),
    ]
    for line, expected in cases:
        shell.handle_line(line)
        assert capsys.readouterr().out.strip() == expected
        assert shell.last_code == 0

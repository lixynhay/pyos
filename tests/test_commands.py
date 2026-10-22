"""Тесты команд (интеграционные, через Shell)."""

import pytest

from pyos.errors import NoSuchFileOrDirectory
from pyos.shell import Shell


def test_echo_plain(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo hello world")
    assert capsys.readouterr().out.strip() == "hello world"


def test_echo_single_quoted_stays_literal(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.env["HOME"] = "/root"
    sh.handle_line("echo '$HOME'")
    assert capsys.readouterr().out.strip() == "$HOME"


def test_redirect_only_creates_empty_file(kernel, root):
    Shell(kernel, root).handle_line("> /tmp/empty.txt")
    assert kernel.fs.read_file("/tmp/empty.txt", root) == ""


def test_ln_creates_hardlink(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/h1", root, data="AAA")
    sh = Shell(kernel, root)
    sh.handle_line("ln /tmp/h1 /tmp/h2")
    sh.handle_line("echo BBB > /tmp/h1")
    assert fs.read_file("/tmp/h2", root).strip() == "BBB"


def test_cp_recursive(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/src", root)
    fs.create_file("/tmp/src/a.txt", root, data="x")
    fs.mkdir("/tmp/src/sub", root)
    fs.create_file("/tmp/src/sub/b.txt", root, data="y")
    Shell(kernel, root).handle_line("cp -r /tmp/src /tmp/dst")
    assert fs.read_file("/tmp/dst/a.txt", root) == "x"
    assert fs.read_file("/tmp/dst/sub/b.txt", root) == "y"


def test_cp_recursive_without_flag_fails(kernel, root, capsys):
    fs = kernel.fs
    fs.mkdir("/tmp/src", root)
    Shell(kernel, root).handle_line("cp /tmp/src /tmp/dst")
    # без -r копирование каталога недопустимо
    assert "Is a directory" in capsys.readouterr().err
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/dst", root)


def test_top_header_has_nice_column(kernel, root, capsys):
    Shell(kernel, root).handle_line("top -n 1")
    out = capsys.readouterr().out
    assert "NI" in out
    assert "CPU" not in out

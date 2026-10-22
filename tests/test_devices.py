"""Тесты виртуальных устройств /dev."""

from pyos.shell import Shell


# --- /dev/null --------------------------------------------------------------


def test_dev_null_reads_empty(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /dev/null")
    assert capsys.readouterr().out.strip() == ""


def test_dev_null_write_ignored(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo hello > /dev/null")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert kernel.fs.lookup("/dev/null", root).is_file()


# --- /dev/zero --------------------------------------------------------------


def test_dev_zero_reads_zeros(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /dev/zero")
    out = capsys.readouterr().out.rstrip("\n")
    assert out == "0" * 4096


# --- /dev/random ------------------------------------------------------------


def test_dev_random_nonempty(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /dev/random")
    assert capsys.readouterr().out.strip()


# --- /dev/full --------------------------------------------------------------


def test_dev_full_read_zeros(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /dev/full")
    out = capsys.readouterr().out.rstrip("\n")
    assert out == "0" * 4096


def test_dev_full_write_fails(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo x > /dev/full")
    captured = capsys.readouterr()
    assert "No space left on device" in captured.err


# --- общее ------------------------------------------------------------------


def test_dev_ls(kernel, root, capsys):
    Shell(kernel, root).handle_line("ls /dev")
    names = capsys.readouterr().out.split()
    for dev in ("null", "zero", "random", "full"):
        assert dev in names
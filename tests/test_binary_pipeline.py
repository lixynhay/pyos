"""BS7 pipeline characterization: text and binary data through the shell.

Text cases must stay exactly as before. Binary cases were recorded in BS7-1
and are inverted here for BS7-4: `cat` is byte-capable outside the terminal,
`wc -c` counts raw stdin bytes, and `>`/`>>` preserve exact bytes.

Binary file used everywhere: ``b"PYOS-BINARY-EXAMPLE\\x00\\xff\\x10\\n"``.
"""

import pytest

from pyos.commands import network_cmds
from pyos.commands.fs_cmds import cmd_cat
from pyos.commands.registry import COMMANDS, Command
from pyos.context import Context
from pyos.errors import PyOSError
from pyos.shell import Shell

BIN = b"PYOS-BINARY-EXAMPLE\x00\xff\x10\n"
BIN2 = b"\x00second\xff"
TEXT = b"b\na\nb\n"


def _write(kernel, root, path, data):
    kernel.fs.write_file(path, data, root, "/", "/", create=True)


def _shell(kernel, root):
    return Shell(kernel, root)


def _fake_transport(monkeypatch, body):
    """Заменяет реальный HTTP-транспорт детерминированным ответом."""
    def fake_do_request(url, follow_redirects=False, timeout=10):
        return 200, {}, body

    monkeypatch.setattr(network_cmds, "_do_request", fake_do_request)


# -- text pipeline: must keep working ---------------------------------------

def test_cat_text_pipeline_unchanged(kernel, root, capsys):
    _write(kernel, root, "/tmp/text", TEXT)
    sh = _shell(kernel, root)

    sh.handle_line("echo hi | wc -c")
    assert capsys.readouterr().out == "3\n"
    assert sh.exit_code == 0

    sh.handle_line("cat /tmp/text | wc -c")
    captured = capsys.readouterr()
    assert captured.out == f"{len(TEXT)}\n"
    assert captured.err == ""
    assert sh.exit_code == 0

    sh.handle_line("cat /tmp/text | cat")
    captured = capsys.readouterr()
    assert captured.out == "b\na\nb\n"
    assert captured.err == ""
    assert sh.exit_code == 0


# -- cat binary: terminal vs non-terminal -----------------------------------

def test_cat_terminal_binary_message_preserved(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        "cat: binary file: /tmp/bin (not valid UTF-8)\n")
    assert sh.exit_code == 22


def test_cat_nonterminal_binary_returns_bytes(kernel, root):
    _write(kernel, root, "/tmp/bin", BIN)
    ctx = Context(kernel, root, "/root", shell=_shell(kernel, root),
                  stdout_is_terminal=False)

    assert cmd_cat(ctx, ["/tmp/bin"], "") == BIN


def test_cat_binary_to_wc_c_returns_exact_byte_count(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | wc -c")
    captured = capsys.readouterr()

    assert captured.out == f"{len(BIN)}\n"
    assert captured.err == ""
    assert sh.exit_code == 0


def test_cat_binary_to_cat_to_wc_c_returns_exact_byte_count(
        kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | cat | wc -c")
    captured = capsys.readouterr()

    assert captured.out == f"{len(BIN)}\n"
    assert captured.err == ""
    assert sh.exit_code == 0


def test_cat_binary_pipe_cat_terminal_safe_suppression(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | cat")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "pyos: binary output suppressed\n"
    assert "Traceback" not in captured.err
    assert sh.exit_code == 0


# -- output redirection ------------------------------------------------------

def test_cat_binary_redirect_overwrite_exact_bytes(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin > /tmp/out")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.exit_code == 0
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == BIN


def test_cat_binary_redirect_append_exact_bytes(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    _write(kernel, root, "/tmp/out", b"keep\n")
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin >> /tmp/out")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.exit_code == 0
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == b"keep\n" + BIN


def test_cat_binary_pipe_cat_redirect_exact_bytes(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | cat > /tmp/out")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.exit_code == 0
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == BIN


def test_cat_multiple_files_binary_concat_exact_bytes(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    _write(kernel, root, "/tmp/bin2", BIN2)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin2 /tmp/bin > /tmp/out")
    captured = capsys.readouterr()

    assert captured.err == ""
    assert sh.exit_code == 0
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == BIN2 + BIN


def test_cat_no_newline_added_in_binary_redirect(kernel, root, capsys):
    data = BIN[:-1]
    _write(kernel, root, "/tmp/nonl", data)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/nonl > /tmp/out")
    captured = capsys.readouterr()

    assert captured.err == ""
    assert sh.exit_code == 0
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == data


def test_ulimit_f_counts_bytes_for_binary_redirect(kernel, root):
    sh = _shell(kernel, root)
    sh.ulimit["file_size"] = len(BIN) - 1

    with pytest.raises(
            PyOSError,
            match=f"{len(BIN)} > {len(BIN) - 1} bytes"):
        sh._write_redirect("/tmp/out", BIN, False, root)


# -- input redirection -------------------------------------------------------

def test_input_redirect_missing_file_still_errors(kernel, root, capsys):
    sh = _shell(kernel, root)

    sh.handle_line("cat < /tmp/no-such-input.txt")
    captured = capsys.readouterr()

    # Прежняя семантика: команда не запускается, exit code 1.
    assert captured.out == ""
    assert captured.err == (
        "cat: /tmp/no-such-input.txt: No such file or directory\n")
    assert sh.exit_code == 1


def test_input_redirect_text_file_passes_bytes_to_command(
        kernel, root, capsys, monkeypatch):
    _write(kernel, root, "/tmp/text", TEXT)
    seen = []

    def probe(ctx, args, stdin):
        seen.append(stdin)
        return ""

    monkeypatch.setitem(COMMANDS, "stdinprobe", Command("stdinprobe", probe))

    sh = _shell(kernel, root)
    sh.handle_line("stdinprobe < /tmp/text")
    captured = capsys.readouterr()

    assert seen == [TEXT]
    assert isinstance(seen[0], bytes)
    assert captured.err == ""
    assert sh.exit_code == 0


def test_input_redirect_binary_file_to_cat_terminal_suppressed(
        kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat < /tmp/bin")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "pyos: binary output suppressed\n"
    assert sh.exit_code == 0


def test_wc_c_input_redirect_binary_exact_count(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("wc -c < /tmp/bin")
    captured = capsys.readouterr()

    assert captured.out == f"{len(BIN)}\n"
    assert captured.err == ""
    assert sh.exit_code == 0


# -- text utilities: binary stdin is still strict text ----------------------

def test_grep_binary_stdin_still_strict_text(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | grep needle")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "grep: not valid UTF-8 text: <stdin>\n"
    assert sh.exit_code == 22


def test_sort_binary_stdin_still_strict_text(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | sort")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "sort: not valid UTF-8 text: <stdin>\n"
    assert sh.exit_code == 22


def test_tr_binary_stdin_still_strict_text(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | tr a-z A-Z")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "tr: not valid UTF-8 text: <stdin>\n"
    assert sh.exit_code == 22


# -- virtual files: current text behaviour stays intact ---------------------

def test_proc_version_pipe_wc_c_still_works(kernel, root, capsys):
    sh = _shell(kernel, root)
    expected = len(kernel.fs.read_bytes("/proc/version", root, "/", "/"))

    sh.handle_line("cat /proc/version | wc -c")
    captured = capsys.readouterr()

    assert captured.out == f"{expected}\n"
    assert captured.err == ""
    assert sh.exit_code == 0


def test_dev_null_pipe_wc_c_still_works(kernel, root, capsys):
    sh = _shell(kernel, root)

    sh.handle_line("cat /dev/null | wc -c")
    captured = capsys.readouterr()

    assert captured.out == "0\n"
    assert captured.err == ""
    assert sh.exit_code == 0


# -- network: deterministic mocked transport --------------------------------

def test_current_curl_binary_stdout_suppressed(
        kernel, root, capsys, monkeypatch):
    _fake_transport(monkeypatch, BIN)
    sh = _shell(kernel, root)

    sh.handle_line("curl http://example.com/blob.bin")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "curl: binary output suppressed; use -o FILE" in captured.err
    assert "Traceback" not in captured.err
    assert sh.exit_code == 1


def test_current_curl_binary_o_writes_exact_bytes(
        kernel, root, capsys, monkeypatch):
    _fake_transport(monkeypatch, BIN)
    sh = _shell(kernel, root)

    sh.handle_line("curl -o /tmp/out http://example.com/blob.bin")
    captured = capsys.readouterr()

    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == BIN
    assert "saved" in captured.out
    assert captured.err == ""
    assert sh.exit_code == 0


def test_current_curl_binary_pipe_wc_c(kernel, root, capsys, monkeypatch):
    _fake_transport(monkeypatch, BIN)
    sh = _shell(kernel, root)

    sh.handle_line("curl http://example.com/blob.bin | wc -c")
    captured = capsys.readouterr()

    # curl suppresses binary stdout, wc counts empty stdin, exit code is 0.
    assert captured.out == "0\n"
    assert "curl: binary output suppressed; use -o FILE" in captured.err
    assert sh.exit_code == 0


def test_current_curl_binary_redirect_overwrite(
        kernel, root, capsys, monkeypatch):
    _fake_transport(monkeypatch, BIN)
    sh = _shell(kernel, root)

    sh.handle_line("curl http://example.com/blob.bin > /tmp/out")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "curl: binary output suppressed; use -o FILE" in captured.err
    assert sh.exit_code == 1
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == b""


# -- direct read path: already bytes-capable --------------------------------

def test_current_wc_c_binary_file_direct(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("wc -c /tmp/bin")
    captured = capsys.readouterr()

    assert captured.out == f"{len(BIN)}\n"
    assert captured.err == ""
    assert sh.exit_code == 0

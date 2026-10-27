"""BS7-2: internal pipeline transport can carry bytes.

These are unit-level tests for shell.py. No production command returns bytes
yet, so user-visible behaviour must stay exactly as before; fake commands are
registered through monkeypatch and disappear after each test.
"""

import sys

from pyos.commands.registry import COMMANDS, Command
from pyos.shell import Shell


def _shell(kernel, root):
    return Shell(kernel, root)


def _register(monkeypatch, name, func):
    monkeypatch.setitem(COMMANDS, name, Command(name, func))


# -- _normalize --------------------------------------------------------------

def test_normalize_preserves_bytes():
    payload = b"PYOS-BINARY-EXAMPLE\x00\xff\x10\n"
    assert Shell._normalize(payload) == (payload, 0)


def test_normalize_tuple_with_bytes_preserves_bytes():
    payload = b"\x00\xff"
    assert Shell._normalize((payload, 7)) == (payload, 7)
    # Текст и None остаются как были.
    assert Shell._normalize(("text", 3)) == ("text", 3)
    assert Shell._normalize((None, 4)) == ("", 4)
    assert Shell._normalize(None) == ("", 0)
    assert Shell._normalize(("only",)) == ("only", 0)


# -- pipeline transport ------------------------------------------------------

def test_execute_pipeline_passes_bytes_between_stages_when_command_returns_bytes(
        kernel, root, capsys, monkeypatch):
    payload = b"PYOS-BINARY\x00\xff"
    seen = []

    def producer(ctx, args, stdin):
        return payload

    def consumer(ctx, args, stdin):
        seen.append(stdin)
        return b""

    _register(monkeypatch, "bproduce", producer)
    _register(monkeypatch, "bconsume", consumer)

    sh = _shell(kernel, root)
    sh.handle_line("bproduce | bconsume")
    captured = capsys.readouterr()

    assert seen == [payload]
    assert captured.out == ""
    assert captured.err == ""
    assert sh.exit_code == 0


def test_execute_pipeline_still_passes_str_between_stages(
        kernel, root, capsys, monkeypatch):
    seen = []

    def producer(ctx, args, stdin):
        return "text"

    def consumer(ctx, args, stdin):
        seen.append(stdin)
        return ""

    _register(monkeypatch, "sproduce", producer)
    _register(monkeypatch, "sconsume", consumer)

    sh = _shell(kernel, root)
    sh.handle_line("sproduce | sconsume")
    capsys.readouterr()

    assert seen == ["text"]
    assert sh.exit_code == 0


def test_last_stage_bytes_invalid_is_suppressed(
        kernel, root, capsys, monkeypatch):
    def producer(ctx, args, stdin):
        return b"\xff\xfe"

    _register(monkeypatch, "binbad", producer)

    sh = _shell(kernel, root)
    sh.handle_line("binbad")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "pyos: binary output suppressed\n"
    assert "Traceback" not in captured.err
    # Код возврата команды не подменяется политикой терминала.
    assert sh.exit_code == 0


# -- write_stdout ------------------------------------------------------------

def test_write_stdout_bytes_valid_utf8_prints_text(kernel, root, capsys):
    sh = _shell(kernel, root)

    sh.write_stdout("h\u00e9llo".encode("utf-8"))
    captured = capsys.readouterr()

    assert captured.out == "h\u00e9llo\n"
    assert captured.err == ""


def test_write_stdout_bytes_keeps_existing_newline(kernel, root, capsys):
    sh = _shell(kernel, root)

    sh.write_stdout(b"line\n")
    captured = capsys.readouterr()

    assert captured.out == "line\n"


def test_write_stdout_bytes_invalid_utf8_prints_safe_error_to_stderr(
        kernel, root, capsys):
    sh = _shell(kernel, root)

    sh.write_stdout(b"\xff\xfe")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "pyos: binary output suppressed\n"
    assert "Traceback" not in captured.err


def test_write_stdout_never_uses_sys_stdout_buffer(
        kernel, root, monkeypatch):
    class FakeStdout:
        def __init__(self):
            self.written = []
            self.buffer_used = False

        def write(self, text):
            self.written.append(text)

        @property
        def buffer(self):
            self.buffer_used = True
            raise AssertionError("sys.stdout.buffer must not be used")

    fake = FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)

    sh = _shell(kernel, root)
    sh.write_stdout(b"ok")
    sh.write_stdout("text")

    assert fake.written == ["ok", "\n", "text", "\n"]
    assert fake.buffer_used is False


# -- regressions: text behaviour must not change -----------------------------

def test_cat_dash_text_stdin_unchanged(kernel, root, capsys):
    """`cat -` склеивает stdin со строками и должен получать str."""
    sh = _shell(kernel, root)

    sh.handle_line("echo hi | cat -")
    captured = capsys.readouterr()

    assert captured.out == "hi\n"
    assert captured.err == ""
    assert sh.exit_code == 0


def test_text_pipeline_output_unchanged(kernel, root, capsys):
    sh = _shell(kernel, root)

    sh.handle_line("echo hello | tr a-z A-Z")
    captured = capsys.readouterr()

    assert captured.out == "HELLO\n"
    assert captured.err == ""
    assert sh.exit_code == 0


# -- background worker -------------------------------------------------------

def test_background_pipeline_bytes_invalid_does_not_traceback(
        kernel, root, capsys, monkeypatch):
    def producer(ctx, args, stdin):
        return b"\xff\xfe"

    _register(monkeypatch, "binbad", producer)

    sh = _shell(kernel, root)
    sh.handle_line("binbad &")
    capsys.readouterr()
    job = sh.jobs[0]

    kernel.scheduler.drain()
    captured = capsys.readouterr()

    assert "pyos: binary output suppressed" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert job["done"] is True
    assert job["proc"].exit_code == 0

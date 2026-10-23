"""BS-3: binary-safe downloads and explicit binary-stdout policy.

The old BS-1 baseline (decoded text saved to files, silent latin-1 fallback)
is inverted here: `-o`/`-O` now preserve exact bytes, and binary stdout is
rejected instead of being silently reinterpreted.
"""

from unittest import mock

import pytest

from pyos.context import Context
from pyos.errors import PyOSError
from pyos.shell import Shell


BINARY_BODIES = [b"\xff\xfe", b"\x00\xff\x10", b"\xc3\x28", b"hello\x00world"]


def _ctx(kernel, root):
    return Context(kernel, root, "/root", shell=Shell(kernel, root))


def _mock_request(body, headers=None, status=200):
    return mock.patch(
        "pyos.commands.network_cmds._do_request",
        return_value=(status, headers or {}, body),
    )


# -- saving to files --------------------------------------------------------

def test_curl_o_saves_exact_binary_bytes(kernel, root):
    from pyos.commands.network_cmds import cmd_curl

    body = b"\xff\xfe"
    ctx = _ctx(kernel, root)
    with _mock_request(body):
        result = cmd_curl(ctx, ["-o", "/tmp/out.bin", "http://example.com"], "")

    assert kernel.fs.read_bytes("/tmp/out.bin", root, "/", "/") == body
    with pytest.raises(PyOSError, match="not valid UTF-8"):
        kernel.fs.read_file("/tmp/out.bin", root, "/", "/")
    assert "2 B" in result


def test_wget_O_saves_exact_binary_bytes(kernel, root):
    from pyos.commands.network_cmds import cmd_wget

    body = b"\x00\xff\x10"
    ctx = _ctx(kernel, root)
    with _mock_request(body):
        result = cmd_wget(
            ctx, ["-O", "/tmp/out.bin", "http://example.com/data.bin"], "")

    assert kernel.fs.read_bytes("/tmp/out.bin", root, "/", "/") == body
    assert "3 B" in result


def test_wget_without_O_saves_exact_binary_bytes(kernel, root):
    # wget всегда пишет файл: без -O имя берётся из URL (не stdout).
    from pyos.commands.network_cmds import cmd_wget

    body = b"\xff\xfe"
    ctx = _ctx(kernel, root)
    with _mock_request(body):
        result = cmd_wget(ctx, ["http://example.com/downloads/raw.bin"], "")

    assert kernel.fs.read_bytes("/root/raw.bin", root, "/", "/") == body
    assert "saved" in result
    assert "raw.bin" in result


def test_wget_quiet_saves_binary_without_stdout(kernel, root):
    from pyos.commands.network_cmds import cmd_wget

    body = b"\x00\xff"
    ctx = _ctx(kernel, root)
    with _mock_request(body):
        result = cmd_wget(
            ctx, ["-q", "-O", "/tmp/q.bin", "http://example.com"], "")

    assert result is None
    assert kernel.fs.read_bytes("/tmp/q.bin", root, "/", "/") == body


@pytest.mark.parametrize("body", BINARY_BODIES)
def test_invalid_utf8_does_not_error_when_saving_to_file(kernel, root, body):
    from pyos.commands.network_cmds import cmd_curl

    ctx = _ctx(kernel, root)
    with _mock_request(body):
        cmd_curl(ctx, ["-o", "/tmp/all.bin", "http://example.com"], "")

    assert kernel.fs.read_bytes("/tmp/all.bin", root, "/", "/") == body


# -- binary stdout ----------------------------------------------------------

def test_curl_stdout_rejects_invalid_utf8(kernel, root):
    from pyos.commands.network_cmds import cmd_curl

    ctx = _ctx(kernel, root)
    with _mock_request(b"\xff\xfe"):
        with pytest.raises(PyOSError,
                           match="curl: binary output suppressed; use -o FILE"):
            cmd_curl(ctx, ["http://example.com"], "")


def test_curl_binary_stdout_error_reaches_stderr(kernel, root, capsys):
    shell = Shell(kernel, root)
    with _mock_request(b"\xff\xfe"):
        shell.handle_line("curl http://example.com")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "curl: binary output suppressed; use -o FILE" in captured.err
    assert "Traceback" not in captured.err
    assert shell.exit_code == 1


# -- verbose ----------------------------------------------------------------

def test_curl_v_o_headers_not_in_file(kernel, root):
    from pyos.commands.network_cmds import cmd_curl

    body = b"payload"
    headers = {"Content-Type": "text/plain", "X-Test": "1"}
    ctx = _ctx(kernel, root)
    with _mock_request(body, headers=headers):
        result = cmd_curl(
            ctx, ["-v", "-o", "/tmp/verbose.bin", "http://example.com"], "")

    assert kernel.fs.read_bytes("/tmp/verbose.bin", root, "/", "/") == body
    # Заголовки уходят в stdout, а не в файл.
    assert "HTTP/1.1 200" in result
    assert "X-Test: 1" in result
    assert "saved" in result


def test_curl_v_stdout_headers_still_work(kernel, root):
    from pyos.commands.network_cmds import cmd_curl

    headers = {"X-Test": "1"}
    ctx = _ctx(kernel, root)
    with _mock_request(b"ok", headers=headers):
        result = cmd_curl(ctx, ["-v", "http://example.com"], "")

    assert result == "HTTP/1.1 200\nX-Test: 1\n\nok"


# -- text compatibility -----------------------------------------------------

def test_text_responses_still_decode_as_before(kernel, root):
    from pyos.commands.network_cmds import cmd_curl, cmd_wget

    body = "h\u00e9llo".encode("utf-8")
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    ctx = _ctx(kernel, root)

    with _mock_request(body, headers=headers):
        assert cmd_curl(ctx, ["http://example.com"], "") == "h\u00e9llo"
    with _mock_request(body, headers=headers):
        cmd_wget(ctx, ["-O", "/tmp/text.txt", "http://example.com"], "")
    assert kernel.fs.read_file("/tmp/text.txt", root, "/", "/") == "h\u00e9llo"


# -- redirects --------------------------------------------------------------

def test_curl_L_still_follows_redirects(kernel, root):
    from pyos.commands.network_cmds import cmd_curl

    ctx = _ctx(kernel, root)
    with _mock_request(b"ok") as mock_do:
        cmd_curl(ctx, ["http://example.com"], "")
    assert mock_do.call_args.kwargs["follow_redirects"] is False

    with _mock_request(b"ok") as mock_do:
        cmd_curl(ctx, ["-L", "http://example.com"], "")
    assert mock_do.call_args.kwargs["follow_redirects"] is True


def test_wget_default_follows_redirects(kernel, root):
    from pyos.commands.network_cmds import cmd_wget

    ctx = _ctx(kernel, root)
    with _mock_request(b"ok") as mock_do:
        cmd_wget(ctx, ["-O", "/tmp/x", "http://example.com"], "")
    assert mock_do.call_args.kwargs["follow_redirects"] is True

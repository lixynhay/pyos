"""CHARACTERIZATION baseline for network body decoding (BS-1).

These tests pin the CURRENT text-only behavior of curl/wget on binary or
non-UTF-8 bodies. They are not a spec of desired behavior.

# BASELINE: current network decode behavior; changed in BS-3
"""

from unittest import mock

import pytest

from pyos.context import Context
from pyos.shell import Shell


BINARY_BODIES = [
    (b"\xff\xfe", "\u00ff\u00fe"),
    (b"\x00\xff\x10", "\x00\u00ff\x10"),
    (b"\xc3\x28", "\u00c3("),
    (b"hello\x00world", "hello\x00world"),
]


def _ctx(kernel, root):
    return Context(kernel, root, "/root", shell=Shell(kernel, root))


def _mock_request(body, headers=None, status=200):
    return mock.patch(
        "pyos.commands.network_cmds._do_request",
        return_value=(status, headers or {}, body),
    )


# -- curl -------------------------------------------------------------------

def test_baseline_curl_o_saves_decoded_text_not_raw_bytes(kernel, root):
    # BASELINE: current network decode behavior; changed in BS-3
    from pyos.commands.network_cmds import cmd_curl

    body = b"\xff\xfe"
    ctx = _ctx(kernel, root)

    with _mock_request(body):
        result = cmd_curl(ctx, ["-o", "/tmp/out.bin", "http://example.com"], "")

    saved = kernel.fs.read_file("/tmp/out.bin", root, "/", "/")
    # latin-1 fallback, not the original bytes
    assert saved == "\u00ff\u00fe"
    assert isinstance(saved, str)
    assert saved.encode("utf-8") != body
    assert "saved" in result


def test_baseline_curl_stdout_decodes_binary_body(kernel, root):
    # BASELINE: current network decode behavior; changed in BS-3
    from pyos.commands.network_cmds import cmd_curl

    body = b"\xc3\x28"
    ctx = _ctx(kernel, root)

    with _mock_request(body):
        result = cmd_curl(ctx, ["http://example.com"], "")

    assert isinstance(result, str)
    assert result == "\u00c3("
    assert result.encode("utf-8") != body


def test_baseline_curl_v_o_drops_headers_instead_of_saving_them(kernel, root):
    # BASELINE: current network decode behavior; changed in BS-3.
    # With -o the verbose header block is built but discarded: only the
    # decoded body is written to the file.
    from pyos.commands.network_cmds import cmd_curl

    body = b"payload"
    headers = {"Content-Type": "text/plain", "X-Test": "1"}
    ctx = _ctx(kernel, root)

    with _mock_request(body, headers=headers):
        result = cmd_curl(
            ctx, ["-v", "-o", "/tmp/verbose.bin", "http://example.com"], "")

    saved = kernel.fs.read_file("/tmp/verbose.bin", root, "/", "/")
    assert saved == "payload"
    assert "HTTP/1.1" not in saved
    assert "Content-Type" not in saved
    assert "X-Test" not in saved
    # The header block is not returned either; only the save message is.
    assert "saved" in result


# -- wget -------------------------------------------------------------------

def test_baseline_wget_O_saves_decoded_text_not_raw_bytes(kernel, root):
    # BASELINE: current network decode behavior; changed in BS-3
    from pyos.commands.network_cmds import cmd_wget

    body = b"\x00\xff\x10"
    ctx = _ctx(kernel, root)

    with _mock_request(body):
        result = cmd_wget(
            ctx, ["-O", "/tmp/out.bin", "http://example.com/data.bin"], "")

    saved = kernel.fs.read_file("/tmp/out.bin", root, "/", "/")
    assert saved == "\x00\u00ff\x10"
    assert isinstance(saved, str)
    assert saved.encode("utf-8") != body
    assert "saved" in result


def test_baseline_wget_without_O_decodes_to_file_not_stdout(kernel, root):
    # BASELINE: current network decode behavior; changed in BS-3.
    # wget never prints the body: without -O it writes the decoded text to
    # a file named from the URL and returns only a status line.
    from pyos.commands.network_cmds import cmd_wget

    body = b"hello\x00world"
    ctx = _ctx(kernel, root)

    with _mock_request(body):
        result = cmd_wget(ctx, ["http://example.com/downloads/raw.bin"], "")

    saved = kernel.fs.read_file("/root/raw.bin", root, "/", "/")
    assert saved == "hello\x00world"
    assert isinstance(saved, str)
    assert saved.encode("utf-8") == body  # NUL survives because it is valid UTF-8
    assert "hello" not in result
    assert "saved" in result
    assert "raw.bin" in result


# -- invalid UTF-8 ----------------------------------------------------------

@pytest.mark.parametrize("body,expected", BINARY_BODIES)
def test_baseline_invalid_utf8_does_not_error_currently(
        kernel, root, body, expected):
    # BASELINE: current network decode behavior; changed in BS-3.
    # _decode_body falls back to latin-1 for invalid UTF-8: no exception,
    # no explicit error, data is silently reinterpreted.
    from pyos.commands.network_cmds import cmd_curl

    ctx = _ctx(kernel, root)

    with _mock_request(body):
        result = cmd_curl(ctx, ["http://example.com"], "")

    assert isinstance(result, str)
    assert result == expected

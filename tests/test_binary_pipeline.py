"""BS7-1: characterization of the current text-only pipeline ABI.

Every test here records what pyOS does *today* with binary data in pipes,
redirects and terminal output. The tests are intentionally not aspirational:
BS7-2..BS7-5 will invert or replace the marked cases, while the text-only
ones must keep passing unchanged.

Binary file used everywhere: ``b"PYOS-BINARY-EXAMPLE\\x00\\xff\\x10\\n"``.
"""

from pyos.commands import network_cmds
from pyos.commands.registry import COMMANDS, Command
from pyos.shell import Shell

BIN = b"PYOS-BINARY-EXAMPLE\x00\xff\x10\n"
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

def test_current_cat_text_pipeline_to_wc_c(kernel, root, capsys):
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


# -- binary through cat: current failure and masking ------------------------

def test_current_cat_binary_file_to_wc_c(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | wc -c")
    captured = capsys.readouterr()

    # Current behaviour: cat fails, wc receives empty stdin and reports 0,
    # and the pipeline exit code is masked by the last command.
    assert captured.out == "0\n"
    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 0


def test_current_cat_binary_to_stdout_terminal(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        "cat: binary file: /tmp/bin (not valid UTF-8)\n")
    assert sh.exit_code == 22


def test_current_cat_binary_redirect_overwrite_creates_empty_file(
        kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin > /tmp/out")
    captured = capsys.readouterr()

    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 22
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == b""


def test_current_cat_binary_redirect_append_does_not_modify_file(
        kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    _write(kernel, root, "/tmp/out", b"keep\n")
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin >> /tmp/out")
    captured = capsys.readouterr()

    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 22
    assert kernel.fs.read_bytes("/tmp/out", root, "/", "/") == b"keep\n"


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


def test_input_redirect_binary_file_to_cat_current_behavior_after_bytes_wire(
        kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat < /tmp/bin")
    captured = capsys.readouterr()

    # BS7-3: `<` отдаёт bytes, cat возвращает их как есть, терминал
    # подавляет binary output. Старый двойной path больше не появляется.
    assert captured.out == ""
    assert captured.err == "pyos: binary output suppressed\n"
    assert sh.exit_code == 0


def test_input_redirect_binary_file_to_wc_c_current_behavior_after_bytes_wire(
        kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("wc -c < /tmp/bin")
    captured = capsys.readouterr()

    # BS7-3: bytes доходят до wc, а он декодирует stdin как текст.
    assert captured.out == ""
    assert captured.err == "wc: not valid UTF-8 text: <stdin>\n"
    assert sh.exit_code == 22


def test_current_cat_binary_pipe_cat(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | cat")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 0


def test_current_cat_binary_pipe_grep(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | grep needle")
    captured = capsys.readouterr()

    # grep sees empty stdin, so it finds nothing and returns 1.
    assert captured.out == ""
    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 1


def test_current_cat_binary_pipe_sort(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | sort")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 0


def test_current_cat_binary_pipe_tr(kernel, root, capsys):
    _write(kernel, root, "/tmp/bin", BIN)
    sh = _shell(kernel, root)

    sh.handle_line("cat /tmp/bin | tr a-z A-Z")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "cat: binary file: /tmp/bin" in captured.err
    assert sh.exit_code == 0


# -- virtual files: current text behaviour stays intact ---------------------

def test_current_proc_version_pipe_wc_c(kernel, root, capsys):
    sh = _shell(kernel, root)
    expected = len(kernel.fs.read_bytes("/proc/version", root, "/", "/"))

    sh.handle_line("cat /proc/version | wc -c")
    captured = capsys.readouterr()

    assert captured.out == f"{expected}\n"
    assert captured.err == ""
    assert sh.exit_code == 0


def test_current_dev_null_pipe_wc_c(kernel, root, capsys):
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

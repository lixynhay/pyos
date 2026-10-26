"""Тесты сетевых команд pyOS: curl и wget."""

from unittest import mock

import pytest

from pyos.context import Context
from pyos.errors import InvalidArgument, PyOSError
from pyos.shell import Shell


# -- curl -------------------------------------------------------------------

def test_curl_simple(kernel, root):
    """curl получает тело ответа."""
    from pyos.commands.network_cmds import cmd_curl

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"Hello pyOS!")
        result = cmd_curl(ctx, ["http://example.com"], "")

    assert result is not None
    assert "Hello pyOS!" in result


def test_curl_no_url(kernel, root):
    """curl без URL выдаёт ошибку."""
    from pyos.commands.network_cmds import cmd_curl

    ctx = Context(kernel, root, "/root")
    with pytest.raises(InvalidArgument, match="missing URL"):
        cmd_curl(ctx, [], "")


def test_curl_verbose(kernel, root):
    """curl -v показывает заголовки и тело."""
    from pyos.commands.network_cmds import cmd_curl

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    headers = {"Content-Type": "text/html", "Server": "pyOS-test"}
    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, headers, b"<html>OK</html>")
        result = cmd_curl(ctx, ["-v", "http://example.com"], "")

    assert "HTTP/1.1 200" in result
    assert "Server: pyOS-test" in result
    assert "<html>OK</html>" in result


def test_curl_save_to_file(kernel, root):
    """curl -o сохраняет ответ в файл."""
    from pyos.commands.network_cmds import cmd_curl

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"saved content")
        result = cmd_curl(ctx, ["-o", "/tmp/out.txt", "http://example.com"],
                          "")

    assert "saved" in result
    data = kernel.fs.read_file("/tmp/out.txt", root, "/", "/")
    assert data == "saved content"


def test_curl_network_error(kernel, root):
    """curl при ошибке сети выдаёт PyOSError."""
    from pyos.commands.network_cmds import cmd_curl

    ctx = Context(kernel, root, "/root")
    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.side_effect = PyOSError("curl: Network is down")
        with pytest.raises(PyOSError, match="Network is down"):
            cmd_curl(ctx, ["http://example.com"], "")


def test_curl_unsupported_scheme(kernel, root):
    """curl с ftp:// выдаёт ошибку."""
    from pyos.commands.network_cmds import cmd_curl

    ctx = Context(kernel, root, "/root")
    with pytest.raises(InvalidArgument, match="unsupported scheme"):
        cmd_curl(ctx, ["ftp://example.com"], "")


def test_curl_scheme_is_passed_as_is(kernel, root):
    """curl передаёт URL в _do_request как есть (http:// добавляется внутри)."""
    from pyos.commands.network_cmds import cmd_curl

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"works")
        cmd_curl(ctx, ["example.com"], "")

    # URL передаётся без изменений; http:// добавляет _do_request
    called_url = mock_do.call_args[0][0]
    assert called_url == "example.com"


# -- _do_request (интеграция с urllib) --------------------------------------

def test_do_request_http(kernel, root):
    """_do_request выполняет реальный HTTP-запрос (через сокет)."""
    from pyos.commands.network_cmds import _do_request

    # Используем реальный HTTP-сервер httpbin.org или example.com
    try:
        import urllib.error
        status, headers, body = _do_request(
            "http://httpbin.org/get", follow_redirects=False, timeout=5
        )
    except Exception as exc:
        # Сеть может быть недоступна — пропускаем
        pytest.skip(f"Network not available: {exc}")
        return

    assert status == 200
    assert len(body) > 0


def test_do_request_invalid_url(kernel, root):
    """_do_request с несуществующим хостом выдаёт ошибку."""
    from pyos.commands.network_cmds import _do_request

    with pytest.raises(PyOSError):
        _do_request("http://nonexistent-domain-12345.test/", timeout=2)


# -- wget -------------------------------------------------------------------

def test_wget_simple(kernel, root):
    """wget скачивает файл в текущий каталог."""
    from pyos.commands.network_cmds import cmd_wget

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"file content")
        result = cmd_wget(ctx, ["http://example.com/file.txt"], "")

    assert "saved" in result
    assert "file.txt" in result

    data = kernel.fs.read_file("/root/file.txt", root, "/", "/")
    assert data == "file content"


def test_wget_with_output(kernel, root):
    """wget -O сохраняет в указанный файл."""
    from pyos.commands.network_cmds import cmd_wget

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"custom path")
        result = cmd_wget(ctx, ["-O", "/tmp/saved.txt",
                                "http://example.com"], "")

    assert "saved.txt" in result
    data = kernel.fs.read_file("/tmp/saved.txt", root, "/", "/")
    assert data == "custom path"


def test_wget_quiet(kernel, root):
    """wget -q не выводит сообщений."""
    from pyos.commands.network_cmds import cmd_wget

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"quiet")
        result = cmd_wget(ctx, ["-q", "http://example.com"], "")

    assert result is None  # Тихий режим — нет вывода
    data = kernel.fs.read_file("/root/index.html", root, "/", "/")
    assert data == "quiet"


def test_wget_no_url(kernel, root):
    """wget без URL выдаёт ошибку."""
    from pyos.commands.network_cmds import cmd_wget

    ctx = Context(kernel, root, "/root")
    with pytest.raises(InvalidArgument, match="missing URL"):
        cmd_wget(ctx, [], "")


def test_wget_network_error_does_not_use_curl_prefix(kernel, root, capsys):
    """wget не наследует curl-префикс из общего network helper."""
    import urllib.error

    sh = Shell(kernel, root)
    with mock.patch(
            "pyos.commands.network_cmds.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom")):
        sh.handle_line("wget http://example.com/x")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "wget: network error: boom" in captured.err
    assert "curl" not in captured.err
    assert sh.last_code != 0


def test_curl_network_error_keeps_curl_prefix(kernel, root, capsys):
    """curl по-прежнему сообщает об ошибке с префиксом curl:."""
    import urllib.error

    sh = Shell(kernel, root)
    with mock.patch(
            "pyos.commands.network_cmds.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom")):
        sh.handle_line("curl -L http://example.com/x")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "curl: network error: boom" in captured.err
    assert "wget:" not in captured.err
    assert sh.last_code != 0


def test_wget_filename_from_url(kernel, root):
    """wget извлекает имя файла из URL."""
    from pyos.commands.network_cmds import cmd_wget

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"data")
        cmd_wget(ctx, ["http://example.com/downloads/pkg.tar.gz"], "")

    data = kernel.fs.read_file("/root/pkg.tar.gz", root, "/", "/")
    assert data == "data"


# -- Интеграция: curl в пайпе -----------------------------------------------

def test_curl_in_pipeline(kernel, root):
    """curl можно использовать в пайпе."""
    from pyos.commands.network_cmds import cmd_curl

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"line1\nline2\nline3")
        out = cmd_curl(ctx, ["http://example.com"], "")

    assert out is not None
    lines = out.strip().split("\n")
    assert "line1" in lines
    assert "line2" in lines
    assert "line3" in lines


# -- Проверка регистрации ---------------------------------------------------

def test_curl_registered():
    """curl зарегистрирована в реестре."""
    from pyos.commands import get
    assert get("curl") is not None


def test_wget_registered():
    """wget зарегистрирована в реестре."""
    from pyos.commands import get
    assert get("wget") is not None


# -- общий helper _http_get_text --------------------------------------------

def test_http_get_text_follows_redirects_when_asked():
    """Helper принимает follow_redirects/timeout и возвращает текст."""
    from pyos.commands import network_cmds

    with mock.patch.object(network_cmds, "_do_request") as mock_do:
        mock_do.return_value = (
            200, {"Content-Type": "text/plain"}, b"body")
        text = network_cmds._http_get_text(
            "https://example.com/x", follow_redirects=True, timeout=5)

    assert text == "body"
    assert text.status == 200
    assert text.raw_size == 4
    mock_do.assert_called_once_with(
        "https://example.com/x", follow_redirects=True, timeout=5)


def test_http_get_text_does_not_follow_by_default_for_curl_path():
    """Без явного флага helper не следует редиректам (как curl)."""
    from pyos.commands import network_cmds

    with mock.patch.object(network_cmds, "_do_request") as mock_do:
        mock_do.return_value = (200, {}, b"body")
        text = network_cmds._http_get_text("https://example.com/x")

    assert text == "body"
    mock_do.assert_called_once_with(
        "https://example.com/x", follow_redirects=False,
        timeout=network_cmds.REQUEST_TIMEOUT)


def test_curl_wget_behavior_unchanged(kernel, root):
    """curl не следует редиректам по умолчанию, wget следует."""
    from pyos.commands.network_cmds import cmd_curl, cmd_wget

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    with mock.patch("pyos.commands.network_cmds._do_request") as mock_do:
        mock_do.return_value = (200, {}, b"ok")
        cmd_curl(ctx, ["https://example.com/x"], "")
        assert mock_do.call_args.kwargs["follow_redirects"] is False

        mock_do.reset_mock()
        mock_do.return_value = (200, {}, b"ok")
        cmd_wget(ctx, ["-q", "https://example.com/x.txt"], "")
        assert mock_do.call_args.kwargs["follow_redirects"] is True
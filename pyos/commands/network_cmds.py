"""Сетевые команды pyOS: curl и wget.

Используют urllib.request для HTTP/HTTPS запросов через реальную сеть хоста.
"""

import urllib.parse
import urllib.request

from ..errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from .registry import register

# Таймаут для HTTP-запросов (секунды)
REQUEST_TIMEOUT = 10


# -- общие утилиты ----------------------------------------------------------

def _do_request(url, follow_redirects=False, timeout=REQUEST_TIMEOUT):
    """Выполняет HTTP GET запрос, возвращает (status, headers, body_bytes).

    При ошибках сети/URL поднимает PyOSError.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme:
            url = "http://" + url
            parsed = urllib.parse.urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise InvalidArgument(
                f"curl: unsupported scheme '{parsed.scheme}' (use http/https)"
            )
    except (ValueError, AttributeError) as exc:
        raise InvalidArgument(f"curl: invalid URL: {exc}")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pyOS/1.0"},
    )

    try:
        if follow_redirects:
            response = urllib.request.urlopen(req, timeout=timeout)
        else:
            # Без автоматического следования редиректам
            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None  # Не следовать редиректу

            opener = urllib.request.build_opener(NoRedirectHandler)
            response = opener.open(req, timeout=timeout)

        body = response.read()
        return response.status, response.headers, body

    except urllib.error.HTTPError as exc:
        # При отсутствии редиректа HTTPError может быть ответом 3xx
        body = exc.read()
        return exc.code, exc.headers, body
    except urllib.error.URLError as exc:
        raise PyOSError(f"curl: {exc.reason}")
    except OSError as exc:
        raise PyOSError(f"curl: network error: {exc}")
    except Exception as exc:
        raise PyOSError(f"curl: request failed: {exc}")


class HttpText(str):
    """Текст тела ответа с метаданными, нужными ``curl -v``."""

    def __new__(cls, text, status, headers, raw_size):
        obj = super().__new__(cls, text)
        obj.status = status
        obj.headers = headers
        obj.raw_size = raw_size
        return obj


def _http_get_bytes(url, *, follow_redirects=False, timeout=REQUEST_TIMEOUT):
    """Выполняет HTTP GET и возвращает ``(status, headers, body_bytes)``.

    Нижний слой общий с :func:`_http_get_text`; тело не декодируется.
    """
    return _do_request(
        url, follow_redirects=follow_redirects, timeout=timeout)


def _http_get_text(url, *, follow_redirects=False, timeout=REQUEST_TIMEOUT,
                   raise_for_status=False):
    """Выполняет HTTP GET и возвращает тело как текст.

    Кодировка определяется так же, как в curl/wget. ``raise_for_status``
    включает ошибку для не-2xx ответов (используется пакетным менеджером).
    """
    status, headers, body = _http_get_bytes(
        url, follow_redirects=follow_redirects, timeout=timeout)

    if raise_for_status and not 200 <= status < 300:
        raise PyOSError(f"HTTP {status}")

    return HttpText(_decode_body(body, headers), status, headers, len(body))


def _format_size(n: int) -> str:
    """Форматирует размер в байтах."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    else:
        return f"{n / 1024 / 1024:.1f} MiB"


def _filename_from_url(url: str) -> str:
    """Извлекает имя файла из URL."""
    path = urllib.parse.urlparse(url).path.rstrip("/")
    if path and "/" in path:
        return path.rsplit("/", 1)[-1]
    return "index.html"


def _format_headers(status, headers) -> str:
    """Формирует verbose-блок заголовков для ``curl -v``."""
    lines = [f"HTTP/1.1 {status}"]
    for key in headers:
        lines.append(f"{key}: {headers[key]}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _stdout_body(body, headers, command, flag) -> str:
    """Текст для stdout; binary в терминал не выводится.

    Явная политика MVP: raw bytes не пишутся в ``sys.stdout``. Если тело
    не является валидным UTF-8, команда возвращает ошибку с подсказкой
    сохранить ответ в файл.
    """
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        raise PyOSError(
            f"{command}: binary output suppressed; use {flag} FILE")
    return _decode_body(body, headers)


# -- curl ------------------------------------------------------------------


@register("curl",
          usage="curl [-v] [-o file] [-L] <url>",
          summary="Передача содержимого URL в stdout",
          category="system")
def cmd_curl(ctx, args, stdin):
    """Выполняет HTTP-запрос к указанному URL.

    Опции:
      -v         подробный вывод (заголовки ответа)
      -o <file>  сохранить ответ в файл (вместо stdout)
      -L         следовать редиректам
    """
    if not args:
        raise InvalidArgument("curl: missing URL")

    verbose = False
    output_file = None
    follow = False
    url = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "-v" or a == "--verbose":
            verbose = True
        elif a == "-L" or a == "--location":
            follow = True
        elif a == "-o" or a == "--output":
            i += 1
            if i >= len(args):
                raise InvalidArgument("curl: -o requires a file path")
            output_file = args[i]
        elif a.startswith("-"):
            raise InvalidArgument(f"curl: unknown option '{a}'")
        else:
            url = a
        i += 1

    if url is None:
        raise InvalidArgument("curl: missing URL")

    status, headers, body = _http_get_bytes(
        url, follow_redirects=follow, timeout=REQUEST_TIMEOUT)
    header_block = _format_headers(status, headers) if verbose else ""

    if output_file:
        # Сохраняем точные bytes в виртуальную ФС
        path = output_file
        if not path.startswith("/"):
            path = ctx.cwd.rstrip("/") + "/" + path
        # Создаём родительские каталоги
        parent = "/".join(path.rstrip("/").split("/")[:-1])
        if parent:
            _ensure_dir_simple(ctx.fs, parent, ctx.user)
        ctx.fs.write_file(path, body, ctx.user, ctx.cwd, ctx.home,
                          create=True)
        message = (f"curl: saved to '{output_file}' "
                   f"({_format_size(len(body))})")
        # -v: заголовки видны в stdout, но в файл попадает только тело
        return header_block + message

    return header_block + _stdout_body(body, headers, "curl", "-o")


# -- wget ------------------------------------------------------------------


@register("wget",
          usage="wget [-O file] [-q] <url>",
          summary="Скачивание файла из сети",
          category="system")
def cmd_wget(ctx, args, stdin):
    """Скачивает файл из сети в виртуальную файловую систему.

    Опции:
      -O <file>  сохранить в указанный файл
      -q         тихий режим (без сообщений)
    """
    if not args:
        raise InvalidArgument("wget: missing URL")

    output_file = None
    quiet = False
    url = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "-q" or a == "--quiet":
            quiet = True
        elif a == "-O" or a == "--output-document":
            i += 1
            if i >= len(args):
                raise InvalidArgument("wget: -O requires a file path")
            output_file = args[i]
        elif a.startswith("-"):
            raise InvalidArgument(f"wget: unknown option '{a}'")
        else:
            url = a
        i += 1

    if url is None:
        raise InvalidArgument("wget: missing URL")

    _, _, body = _http_get_bytes(
        url, follow_redirects=True, timeout=REQUEST_TIMEOUT)

    # Определяем имя файла
    if output_file is None:
        output_file = _filename_from_url(url)

    path = output_file
    if not path.startswith("/"):
        path = ctx.cwd.rstrip("/") + "/" + path

    # Создаём родительские каталоги
    parent = "/".join(path.rstrip("/").split("/")[:-1])
    if parent:
        _ensure_dir_simple(ctx.fs, parent, ctx.user)

    # Сохраняем точные bytes: декодирование не применяется.
    ctx.fs.write_file(path, body, ctx.user, ctx.cwd, ctx.home,
                      create=True)

    sz = _format_size(len(body))
    if quiet:
        return None
    return f"wget: saved to '{output_file}' ({sz})"


# -- утилиты ----------------------------------------------------------------


def _decode_body(body_bytes: bytes, headers=None) -> str:
    """Декодирует тело ответа из байтов в строку.

    Пытается определить кодировку из Content-Type или из тела, в крайнем
    случае использует UTF-8 с заменой ошибок.
    """
    charset = "utf-8"
    if headers and "Content-Type" in headers:
        ct = headers["Content-Type"]
        if "charset=" in ct:
            charset = ct.split("charset=")[-1].split(";")[0].strip()

    try:
        return body_bytes.decode(charset)
    except (UnicodeDecodeError, LookupError):
        pass

    # Пробуем распознать по BOM или часто встречающимся кодировкам
    for enc in ("utf-8", "latin-1", "cp1251", "koi8-r"):
        try:
            return body_bytes.decode(enc)
        except UnicodeDecodeError:
            continue

    return body_bytes.decode("utf-8", errors="replace")


def _ensure_dir_simple(fs, path, user):
    """Создаёт каталог со всеми родителями (без lookup) для wget/curl."""
    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        if not part:
            continue
        current += "/" + part
        try:
            node = fs.lookup(current, user, "/", "/")
            if not node.is_dir():
                return
        except NoSuchFileOrDirectory:
            fs.mkdir(current, user, "/", "/")
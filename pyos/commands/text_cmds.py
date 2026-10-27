"""Текстовые утилиты (аналоги coreutils)."""

from ..errors import InvalidArgument, PyOSError
from .registry import register


def _stdin_text(command, stdin):
    """Возвращает stdin как текст; binary stdin даёт понятную ошибку."""
    if stdin is None:
        return ""
    if isinstance(stdin, bytes):
        try:
            return stdin.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidArgument(
                f"{command}: not valid UTF-8 text: <stdin>")
    return stdin


def _decode_text(command, label, raw):
    """Декодирует bytes в текст или сообщает, что файл binary."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidArgument(f"{command}: not valid UTF-8 text: {label}")


def _stdin_bytes(stdin):
    """Возвращает stdin как точные bytes (без UTF-8-валидации)."""
    if stdin is None:
        return b""
    if isinstance(stdin, bytes):
        return stdin
    if isinstance(stdin, str):
        return stdin.encode("utf-8")
    return bytes(stdin)


def _read_raw_input(ctx, paths, stdin, command):
    """Возвращает bytes: из файлов или из stdin, если файлов нет.

    В отличие от ``_read_input`` не требует валидного UTF-8: используется
    командами, которым нужны точные bytes (``wc -c``).
    """
    if not paths or paths == ["-"]:
        return _stdin_bytes(stdin)
    chunks = []
    for p in paths:
        if p == "-":
            chunks.append(_stdin_bytes(stdin))
        else:
            chunks.append(ctx.fs.read_bytes(p, ctx.user, ctx.cwd, ctx.home))
    return b"".join(chunks)


def _read_input(ctx, paths, stdin, command):
    """Возвращает текст: из файлов или из stdin, если файлов нет.

    Binary-файлы не декодируются частично: команда получает явную ошибку
    ``<command>: not valid UTF-8 text: <path>``.
    """
    if not paths or paths == ["-"]:
        return _stdin_text(command, stdin)
    chunks = []
    for p in paths:
        if p == "-":
            chunks.append(_stdin_text(command, stdin))
        else:
            raw = ctx.fs.read_bytes(p, ctx.user, ctx.cwd, ctx.home)
            chunks.append(_decode_text(command, p, raw))
    return "".join(chunks)


def _split_opts(args):
    opts, pos = [], []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-n" and i + 1 < len(args):
            opts.append(a)
            opts.append(args[i + 1])
            i += 2
            continue
        if a.startswith("-") and a != "-":
            opts.append(a)
        else:
            pos.append(a)
        i += 1
    return opts, pos


@register("echo", usage="echo [args ...]", summary="Вывести аргументы",
          category="text")
def cmd_echo(ctx, args, stdin):
    newline = True
    if args and args[0] == "-n":
        newline = False
        args = args[1:]
    text = " ".join(args)
    return text + ("\n" if newline else "")


def _grep_text(rx, text, invert, numbered, first_line):
    """Возвращает (строки вывода, было ли совпадение)."""
    out = []
    matched = False
    for i, line in enumerate(text.splitlines(), first_line):
        hit = bool(rx.search(line))
        if hit != invert:
            matched = True
            prefix = f"{i}:" if numbered else ""
            out.append(prefix + line)
    return out, matched


def _grep_binary_match(raw, pattern, flags, invert):
    """Проверяет совпадение в binary-файле по точным bytes."""
    import re
    try:
        hit = re.search(pattern.encode("utf-8"), raw, flags) is not None
    except (re.error, UnicodeEncodeError):
        return False
    return hit != invert


@register("grep", usage="grep [-i] [-v] [-n] pattern [file ...]",
          summary="Поиск строк по шаблону", category="text")
def cmd_grep(ctx, args, stdin):
    opts, pos = _split_opts(args)
    icase = any("i" in o for o in opts if o.startswith("-"))
    invert = any("v" in o for o in opts if o.startswith("-"))
    numbered = any("n" in o for o in opts if o.startswith("-"))
    if not pos:
        raise InvalidArgument("grep: missing pattern")
    pattern = pos[0]
    files = pos[1:]

    import re
    flags = re.IGNORECASE if icase else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        raise InvalidArgument(f"grep: invalid pattern: {exc}")

    if not files or files == ["-"]:
        text = _stdin_text("grep", stdin)
        out, matched = _grep_text(rx, text, invert, numbered, 1)
        return ("\n".join(out), 0 if matched else 1)

    raws = []
    for path in files:
        if path == "-":
            raws.append((path, _stdin_text("grep", stdin).encode("utf-8")))
        else:
            raws.append(
                (path, ctx.fs.read_bytes(path, ctx.user, ctx.cwd, ctx.home)))

    texts = []
    has_binary = False
    for path, raw in raws:
        try:
            texts.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            texts.append(None)
            has_binary = True

    if not has_binary:
        out, matched = _grep_text(
            rx, "".join(texts), invert, numbered, 1)
        return ("\n".join(out), 0 if matched else 1)

    out = []
    matched = False
    line_no = 1
    for (path, raw), text in zip(raws, texts):
        if text is None:
            if _grep_binary_match(raw, pattern, flags, invert):
                matched = True
                out.append(f"Binary file {path} matches")
            continue
        lines, hit = _grep_text(rx, text, invert, numbered, line_no)
        out.extend(lines)
        matched = matched or hit
        line_no += len(text.splitlines())
    return ("\n".join(out), 0 if matched else 1)


@register("wc", usage="wc [-l] [-w] [-c] [file ...]",
          summary="Подсчёт строк/слов/байт", category="text")
def cmd_wc(ctx, args, stdin):
    opts, pos = _split_opts(args)
    do_l = any("l" in o for o in opts if o.startswith("-"))
    do_w = any("w" in o for o in opts if o.startswith("-"))
    do_c = any("c" in o for o in opts if o.startswith("-"))
    if not (do_l or do_w or do_c):
        do_l = do_w = do_c = True
    if do_c and not do_l and not do_w:
        # -c не требует декодирования: считаем точные bytes.
        return str(len(_read_raw_input(ctx, pos, stdin, "wc")))
    text = _read_input(ctx, pos, stdin, "wc")
    counts = {
        "l": len(text.splitlines()),
        "w": len(text.split()),
        "c": len(text.encode("utf-8")),
    }
    parts = [str(counts[k]) for k in ("l", "w", "c") if (do_l if k == "l" else do_w if k == "w" else do_c)]
    return " ".join(parts)


def _parse_line_count(command, opts):
    """Разбирает ``-n N`` / ``-N`` и отклоняет отрицательные значения."""
    n = 10
    if "-n" in opts:
        idx = opts.index("-n")
        raw = opts[idx + 1] if idx + 1 < len(opts) else None
        try:
            n = int(raw) if raw is not None else None
        except ValueError:
            raise InvalidArgument(f"{command}: invalid number of lines")
        if n is None or n < 0:
            raise InvalidArgument(f"{command}: invalid number of lines")
    for o in opts:
        if o.startswith("-") and o[1:].isdigit():
            n = int(o[1:])
    return n


@register("head", usage="head [-n N] [file ...]", summary="Первые строки",
          category="text")
def cmd_head(ctx, args, stdin):
    opts, pos = _split_opts(args)
    n = _parse_line_count("head", opts)
    text = _read_input(ctx, pos, stdin, "head")
    return "\n".join(text.splitlines()[:n])


@register("tail", usage="tail [-n N] [file ...]", summary="Последние строки",
          category="text")
def cmd_tail(ctx, args, stdin):
    opts, pos = _split_opts(args)
    n = _parse_line_count("tail", opts)
    text = _read_input(ctx, pos, stdin, "tail")
    if n == 0:
        return ""
    return "\n".join(text.splitlines()[-n:])


@register("sort", usage="sort [-r] [-u] [file ...]",
          summary="Сортировка строк", category="text")
def cmd_sort(ctx, args, stdin):
    opts, pos = _split_opts(args)
    reverse = any("r" in o for o in opts if o.startswith("-"))
    unique = any("u" in o for o in opts if o.startswith("-"))
    text = _read_input(ctx, pos, stdin, "sort")
    lines = text.splitlines()
    lines.sort(reverse=reverse)
    if unique:
        deduped = []
        for line in lines:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        lines = deduped
    return "\n".join(lines)


@register("uniq", usage="uniq [file]", summary="Убрать соседние дубликаты",
          category="text")
def cmd_uniq(ctx, args, stdin):
    opts, pos = _split_opts(args)
    text = _read_input(ctx, pos, stdin, "uniq")
    out = []
    for line in text.splitlines():
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


@register("rev", usage="rev [file ...]", summary="Развернуть строки",
          category="text")
def cmd_rev(ctx, args, stdin):
    opts, pos = _split_opts(args)
    text = _read_input(ctx, pos, stdin, "rev")
    return "\n".join(line[::-1] for line in text.splitlines())


def _expand_set(spec: str) -> str:
    """Раскрывает диапазоны символов (a-z, 0-9) и \\n-экранирования.

    Каждый диапазон X-Y заменяется span'ом символов (ord-based). Дефис в
    начале/конце списка или перед другим дефисом остаётся литералом.
    Убывающий диапазон (z-a) — ошибка, как в coreutils.
    """
    out: list[str] = []
    i = 0
    n = len(spec)
    while i < n:
        c = spec[i]
        i += 1
        if c == "\\" and i < n:
            nxt = spec[i]
            i += 1
            c = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(nxt, nxt)
        out.append(c)
        if (c == "-" and len(out) >= 2 and i < n
                and out[-2] != "-" and spec[i] != "-"):
            start, end = out[-2], spec[i]
            if ord(end) < ord(start):
                raise InvalidArgument(
                    f"tr: range-endpoints of '{start}-{end}' are in "
                    "reverse collating sequence order")
            out[-2:] = [chr(x) for x in range(ord(start), ord(end) + 1)]
            i += 1
    return "".join(out)


@register("tr", usage="tr SET1 SET2", summary="Замена символов",
          category="text")
def cmd_tr(ctx, args, stdin):
    if len(args) < 2:
        raise InvalidArgument("tr: missing operand")
    set1 = _expand_set(args[0])
    set2 = _expand_set(args[1])
    if not set1:
        raise InvalidArgument("tr: empty SET1")
    if not set2:
        raise InvalidArgument("tr: empty SET2")
    if len(set2) < len(set1):
        set2 += set2[-1] * (len(set1) - len(set2))
    elif len(set2) > len(set1):
        set2 = set2[:len(set1)]
    table = str.maketrans(set1, set2)
    return _stdin_text("tr", stdin).translate(table)


@register("cut", usage="cut -d DELIM -f N [file ...]",
          summary="Выбор полей из строк", category="text")
def cmd_cut(ctx, args, stdin):
    delim = "\t"
    field = None
    i = 0
    pos = []
    while i < len(args):
        a = args[i]
        if a == "-d" and i + 1 < len(args):
            delim = args[i + 1]
            i += 2
        elif a == "-f" and i + 1 < len(args):
            field = args[i + 1]
            i += 2
        elif a.startswith("-d"):
            delim = a[2:]
            i += 1
        elif a.startswith("-f"):
            field = a[2:]
            i += 1
        else:
            pos.append(a)
            i += 1
    if field is None:
        raise InvalidArgument("cut: you must specify a field list")
    tokens = [token.strip() for token in field.split(",")]
    if (not tokens
            or any(not token.isdigit() or int(token) <= 0
                   for token in tokens)):
        raise InvalidArgument(f"cut: invalid field list '{field}'")
    indices = [int(token) - 1 for token in tokens]
    text = _read_input(ctx, pos, stdin, "cut")
    out = []
    for line in text.splitlines():
        parts = line.split(delim)
        out.append(delim.join(parts[i] for i in indices if 0 <= i < len(parts)))
    if out and out[-1] == "":
        return "\n".join(out) + "\n"
    return "\n".join(out)

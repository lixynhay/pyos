"""Текстовые утилиты (аналоги coreutils)."""

from ..errors import InvalidArgument, PyOSError
from .registry import register


def _read_input(ctx, paths, stdin):
    """Возвращает текст: из файлов или из stdin, если файлов нет."""
    if not paths or paths == ["-"]:
        return stdin
    chunks = []
    for p in paths:
        if p == "-":
            chunks.append(stdin)
        else:
            chunks.append(ctx.fs.read_file(p, ctx.user, ctx.cwd, ctx.home))
    return "".join(chunks)


def _split_opts(args):
    opts, pos = [], []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "-c") and i + 1 < len(args):
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
    text = _read_input(ctx, files, stdin)

    import re
    flags = re.IGNORECASE if icase else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        raise InvalidArgument(f"grep: invalid pattern: {exc}")

    out = []
    matched = False
    for i, line in enumerate(text.splitlines(), 1):
        hit = bool(rx.search(line))
        if hit != invert:
            matched = True
            prefix = f"{i}:" if numbered else ""
            out.append(prefix + line)
    code = 0 if matched else 1
    return ("\n".join(out), code)


@register("wc", usage="wc [-l] [-w] [-c] [file ...]",
          summary="Подсчёт строк/слов/байт", category="text")
def cmd_wc(ctx, args, stdin):
    opts, pos = _split_opts(args)
    do_l = any("l" in o for o in opts if o.startswith("-"))
    do_w = any("w" in o for o in opts if o.startswith("-"))
    do_c = any("c" in o for o in opts if o.startswith("-"))
    if not (do_l or do_w or do_c):
        do_l = do_w = do_c = True
    text = _read_input(ctx, pos, stdin)
    counts = {
        "l": len(text.splitlines()),
        "w": len(text.split()),
        "c": len(text.encode("utf-8")),
    }
    parts = [str(counts[k]) for k in ("l", "w", "c") if (do_l if k == "l" else do_w if k == "w" else do_c)]
    return " ".join(parts)


@register("head", usage="head [-n N] [file ...]", summary="Первые строки",
          category="text")
def cmd_head(ctx, args, stdin):
    n = 10
    opts, pos = _split_opts(args)
    if "-n" in opts:
        try:
            n = int(opts[opts.index("-n") + 1])
        except (IndexError, ValueError):
            raise InvalidArgument("head: invalid number of lines")
    for o in opts:
        if o.startswith("-") and o[1:].isdigit():
            n = int(o[1:])
    text = _read_input(ctx, pos, stdin)
    return "\n".join(text.splitlines()[:n])


@register("tail", usage="tail [-n N] [file ...]", summary="Последние строки",
          category="text")
def cmd_tail(ctx, args, stdin):
    n = 10
    opts, pos = _split_opts(args)
    if "-n" in opts:
        try:
            n = int(opts[opts.index("-n") + 1])
        except (IndexError, ValueError):
            raise InvalidArgument("tail: invalid number of lines")
    for o in opts:
        if o.startswith("-") and o[1:].isdigit():
            n = int(o[1:])
    text = _read_input(ctx, pos, stdin)
    return "\n".join(text.splitlines()[-n:])


@register("sort", usage="sort [-r] [-u] [file ...]",
          summary="Сортировка строк", category="text")
def cmd_sort(ctx, args, stdin):
    opts, pos = _split_opts(args)
    reverse = any("r" in o for o in opts if o.startswith("-"))
    unique = any("u" in o for o in opts if o.startswith("-"))
    text = _read_input(ctx, pos, stdin)
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
    text = _read_input(ctx, pos, stdin)
    out = []
    for line in text.splitlines():
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


@register("rev", usage="rev [file ...]", summary="Развернуть строки",
          category="text")
def cmd_rev(ctx, args, stdin):
    opts, pos = _split_opts(args)
    text = _read_input(ctx, pos, stdin)
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
    return stdin.translate(table)


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
    indices = [int(x) - 1 for x in field.split(",") if x.strip().isdigit()]
    text = _read_input(ctx, pos, stdin)
    out = []
    for line in text.splitlines():
        parts = line.split(delim)
        out.append(delim.join(parts[i] for i in indices if 0 <= i < len(parts)))
    return "\n".join(out)

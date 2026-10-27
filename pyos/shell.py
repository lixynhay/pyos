"""Интерактивный шелл pyOS.

Умеет:

* разбирать командную строку с кавычками и экранированием;
* конвейеры (``|``), редиректы (``>``, ``>>``, ``<``);
* фоновые задания (``&``) и разделитель команд (``;``);
* раскрывать переменные окружения (``$VAR``, ``${VAR}``);
* выполнять встроенные команды (cd, export, alias, su, sudo, ...).

Обычные команды запускаются как процессы: ядро заводит запись в таблице
процессов и продвигает симулированное время.
"""

import sys

from .commands import get
from .commands.registry import all_commands
from .context import Context
from .errors import InvalidArgument, PyOSError, UserError
from .process import ProcessState
from .scheduler import TICKS_PER_SECOND

BUILTIN_NAMES = {
    "exit", "export", "unset", "alias", "unalias", "history",
    "jobs", "fg", "bg", "wait", "waitpid",
    "su", "sudo", "source", "set", "nice",
}

# Короткий usage для help/man. Полноценных man-страниц у builtins нет.
BUILTIN_USAGE = {
    "wait": "wait [pid|%job]",
    "waitpid": "waitpid pid",
    "jobs": "jobs",
    "fg": "fg [%job|pid]",
    "bg": "bg [%job|pid]",
    "nice": "nice [-n N] command [args...]\n    nice N command [args...]",
}


# ---------------------------------------------------------------------------
# Разбор командной строки
# ---------------------------------------------------------------------------
class Token(str):
    """Токен командной строки.

    Наследуется от ``str``, поэтому сравнения с операторами (``== "|"`` и
    т.п.) продолжают работать. Флаг ``quoted`` отмечает, что токен был
    закавычен или экранирован: такие токены не раскрываются как шаблоны.
    """

    __slots__ = ("quoted",)

    def __new__(cls, text="", quoted=False):
        obj = super().__new__(cls, text)
        obj.quoted = quoted
        return obj


def _read_var(line: str, i: int, env: dict):
    """Читает ``$NAME`` / ``${NAME}`` / ``${VAR:-word}`` / ``${VAR:=word}`` /
    ``${VAR:+word}`` / ``${VAR:?word}`` / ``$?`` / ``$$`` начиная с ``i``.

    Возвращает пару ``(новый_индекс, значение)``.
    """
    n = len(line)
    if i + 1 >= n:
        return i + 1, "$"
    c = line[i + 1]
    if c == "{":
        end = line.find("}", i + 2)
        if end == -1:
            return i + 1, "$"
        content = line[i + 2:end]

        # Проверяем операторы значений по умолчанию
        op = None
        op_pos = -1
        for operator in (":-", ":=", ":+", ":?"):
            pos = content.find(operator)
            if pos >= 0:
                op = operator
                op_pos = pos
                break

        if op is not None:
            var_name = content[:op_pos]
            word = content[op_pos + len(op):]
            val = env.get(var_name, "")
            if op == ":-":
                result = word if not val else val
            elif op == ":=":
                if not val:
                    env[var_name] = word
                    result = word
                else:
                    result = val
            elif op == ":+":
                result = word if val else ""
            elif op == ":?":
                if not val:
                    raise PyOSError(f"${var_name}: {word}")
                result = val
            return end + 1, str(result)

        # Обычное раскрытие ${NAME}
        return end + 1, str(env.get(content, ""))
    if c == "?":
        return i + 2, str(env.get("?", "0"))
    if c == "$":
        return i + 2, str(env.get("$", ""))
    if c.isalpha() or c == "_":
        j = i + 1
        while j < n and (line[j].isalnum() or line[j] == "_"):
            j += 1
        return j, str(env.get(line[i + 1:j], ""))
    return i + 1, "$"


def tokenize(line: str, env: dict | None = None) -> list[Token]:
    """Разбивает строку на токены, учитывая кавычки и экранирование.

    Раскрывает ``$VAR`` / ``${VAR}`` / ``$?`` / ``$$``. Одинарные кавычки
    делают содержимое литеральным, двойные допускают раскрытие, а
    экранированный ``\\$`` не раскрывается.
    """
    if env is None:
        env = {}
    tokens: list[Token] = []
    buf: list[str] = []
    i = 0
    n = len(line)
    quote = None
    quoted = False
    started = False

    def flush():
        nonlocal quoted, started
        if started:
            tokens.append(Token("".join(buf), quoted))
        buf.clear()
        quoted = False
        started = False

    def append(text, is_quoted=False):
        nonlocal quoted, started
        if text:
            buf.append(text)
        started = True
        if is_quoted:
            quoted = True

    while i < n:
        c = line[i]
        if quote == "'":
            if c == "'":
                quote = None
            else:
                buf.append(c)
            i += 1
            continue
        if quote == '"':
            if c == '"':
                quote = None
                i += 1
                continue
            if c == "\\" and i + 1 < n and line[i + 1] in ('"', "\\", "$", "`"):
                i += 1
                append(line[i], is_quoted=True)
                i += 1
                continue
            if c == "$":
                i, val = _read_var(line, i, env)
                append(val, is_quoted=True)
                continue
            append(c)
            i += 1
            continue

        if c in ("'", '"'):
            quote = c
            quoted = True
            started = True
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 1
            append(line[i], is_quoted=True)
            i += 1
            continue
        if c == "$":
            i, val = _read_var(line, i, env)
            append(val)
            continue
        if c.isspace():
            flush()
            i += 1
            continue
        if c == "&":
            flush()
            if i + 1 < n and line[i + 1] == "&":
                tokens.append(Token("&&", False))
                i += 2
            else:
                tokens.append(Token("&", False))
                i += 1
            continue
        if c == "|":
            flush()
            if i + 1 < n and line[i + 1] == "|":
                tokens.append(Token("||", False))
                i += 2
            else:
                tokens.append(Token("|", False))
                i += 1
            continue
        if c in (";", "<"):
            flush()
            tokens.append(Token(c, False))
            i += 1
            continue
        if c == ">":
            flush()
            if i + 1 < n and line[i + 1] == ">":
                tokens.append(Token(">>", False))
                i += 2
            else:
                tokens.append(Token(">", False))
                i += 1
            continue
        append(c)
        i += 1

    flush()
    return tokens


def _is_quoted(token) -> bool:
    return bool(getattr(token, "quoted", False))


def split_jobs(tokens: list[Token]):
    """Делит токены на задания по ``&`` и ``;``. Возвращает (tokens, background)."""
    jobs = []
    cur: list[Token] = []
    for t in tokens:
        if t in ("&", ";") and not _is_quoted(t):
            jobs.append((cur, t == "&"))
            cur = []
        else:
            cur.append(t)
    if cur:
        jobs.append((cur, False))
    return jobs


def parse_pipeline(tokens: list[Token]):
    """Разбирает одну команду/конвейер в список (argv, redirects)."""
    segments: list[list[Token]] = []
    cur: list[Token] = []
    for t in tokens:
        if t == "|" and not _is_quoted(t):
            segments.append(cur)
            cur = []
        else:
            cur.append(t)
    segments.append(cur)

    cmds = []
    for seg in segments:
        argv: list[Token] = []
        redirects: list[tuple[str, str]] = []
        i = 0
        while i < len(seg):
            t = seg[i]
            if t in (">", ">>", "<") and not _is_quoted(t):
                if i + 1 >= len(seg):
                    raise InvalidArgument(f"syntax error near unexpected token '{t}'")
                redirects.append((str(t), str(seg[i + 1])))
                i += 2
            else:
                argv.append(t)
                i += 1
        if argv or redirects:
            cmds.append((argv, redirects))
    if not cmds:
        raise InvalidArgument("syntax error: empty command")
    return cmds


def split_and_or(tokens: list[Token]):
    """Делит токены одного задания на конвейеры по ``&&`` и ``||``.

    Возвращает список ``(tokens, connector)``, где ``connector`` — оператор,
    идущий *после* конвейера (``"&&"``, ``"||"`` или ``None`` для последнего).
    """
    segments: list[tuple[list[Token], str | None]] = []
    cur: list[Token] = []
    for t in tokens:
        if t in ("&&", "||") and not _is_quoted(t):
            segments.append((cur, str(t)))
            cur = []
        else:
            cur.append(t)
    segments.append((cur, None))
    return segments


def _without_empty_segments(segments):
    """Отбрасывает пустое задание, сохраняя пустые сегменты внутри ``&&``/``||``.

    Пустое задание получается при делении по ``;`` / ``&`` (строка ``;``,
    ``;;`` или завершающий ``;``) — такие задания шелл молча пропускает.
    Пустой сегмент внутри цепочки ``&&`` / ``||`` — синтаксическая ошибка:
    её печатает ``_execute_segments``, когда до сегмента доходит очередь
    (после short-circuit он не выполняется и не ошибка).
    """
    if len(segments) == 1 and not segments[0][0].strip():
        return []
    return segments


def split_raw_jobs(line: str):
    """Делит строку на задания по ``;`` / ``&`` без раскрытия переменных.

    Возвращает список ``(segments, background)``, где ``segments`` — список
    пар ``(сырой_текст, коннектор)``, а коннектор — ``"&&"``, ``"||"`` или
    ``None``. Кавычки и экранирование учитываются, поэтому разделители внутри
    них не действуют. Одиночный ``|`` остаётся внутри сегмента: пайп
    разбирается позже, при токенизации сегмента.
    """
    jobs = []
    segments = []
    start = 0
    i = 0
    n = len(line)
    quote = None
    while i < n:
        c = line[i]
        if quote is not None:
            if c == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == ";":
            segments.append((line[start:i], None))
            jobs.append((_without_empty_segments(segments), False))
            segments = []
            i += 1
            start = i
            continue
        if c == "&":
            if i + 1 < n and line[i + 1] == "&":
                segments.append((line[start:i], "&&"))
                i += 2
                start = i
                continue
            segments.append((line[start:i], None))
            jobs.append((_without_empty_segments(segments), True))
            segments = []
            i += 1
            start = i
            continue
        if c == "|" and i + 1 < n and line[i + 1] == "|":
            segments.append((line[start:i], "||"))
            i += 2
            start = i
            continue
        i += 1
    segments.append((line[start:], None))
    jobs.append((_without_empty_segments(segments), False))
    return jobs


# ---------------------------------------------------------------------------
# Шелл
# ---------------------------------------------------------------------------
class Shell:
    def __init__(self, kernel, user):
        self.kernel = kernel
        self.user = user
        self.cwd = user.home
        self.running = True
        self.exit_code = 0
        self.last_code = 0
        self.history: list[str] = []
        self.aliases: dict[str, str] = {}
        self.jobs: list[dict] = []
        self._next_job = 1
        self._in_worker = False

        self._locals: dict[str, str] = {}
        self.ulimit: dict[str, int] = {"file_size": -1}  # -1 = unlimited
        self.env = {
            "PATH": "/bin:/usr/bin",
            "HOME": user.home,
            "USER": user.name,
            "LOGNAME": user.name,
            "SHELL": "/bin/pyos",
            "PWD": user.home,
            "HOSTNAME": kernel.hostname,
            "TERM": "pyos-tty",
        }

    @property
    def builtins(self):
        return BUILTIN_NAMES

    def builtin_usage(self, name):
        """Короткий SYNOPSIS для встроенной команды или ``None``."""
        return BUILTIN_USAGE.get(name)

    # -- утилиты -------------------------------------------------------------
    def write_stdout(self, text):
        if isinstance(text, bytes):
            # В терминал пишем только текст: raw bytes не должны попадать
            # в sys.stdout (это ломает capsys/capfd и засоряет tty).
            try:
                text = text.decode("utf-8")
            except UnicodeDecodeError:
                self.write_stderr("pyos: binary output suppressed")
                return
        if not text:
            return
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")

    def write_stderr(self, text):
        if not text:
            return
        sys.stderr.write(text)
        if not text.endswith("\n"):
            sys.stderr.write("\n")

    def prompt(self) -> str:
        cwd = self.cwd
        home = self.user.home
        if cwd == home:
            cwd = "~"
        elif home != "/" and cwd.startswith(home + "/"):
            cwd = "~" + cwd[len(home):]
        sym = "#" if self.user.is_root else "$"
        return f"{self.user.name}@{self.kernel.hostname}:{cwd}{sym} "

    # -- главный цикл --------------------------------------------------------
    def run(self):
        while self.running:
            self.kernel.idle(1)  # пусть фоновые задания продвигаются
            try:
                line = input(self.prompt())
            except EOFError:
                sys.stdout.write("\nexit\n")
                break
            except KeyboardInterrupt:
                sys.stdout.write("\n^C\n")
                continue
            self.handle_line(line)
        return self.exit_code

    def handle_line(self, line: str):
        if not line.strip():
            return
        self.history.append(line)
        for segments, background in split_raw_jobs(line):
            if not segments:
                continue
            try:
                if background:
                    self.execute_background(self._tokenize_chain(segments))
                else:
                    code = self.run_segments(segments)
                    self.last_code = code
                    self.exit_code = code
            except PyOSError as exc:
                # Ошибка раскрытия переменных прерывает строку целиком.
                self.write_stderr(f"pyos: {exc.message}")
                self.last_code = 1
                self.exit_code = 1
                return

    # -- окружение и раскрытие переменных -----------------------------------
    def _segment_env(self):
        """Окружение раскрытия для очередного сегмента строки."""
        self.env["PWD"] = self.cwd
        self.env["HOME"] = self.user.home
        self.env["USER"] = self.user.name
        self.env["?"] = str(self.last_code)
        self.env.setdefault("$", str(self.kernel.init.pid))
        # Мёрж локальных и экспортированных переменных для раскрытия $VAR
        return {**self._locals, **self.env}

    def _persist_expansions(self, before, after):
        """Сохраняет присваивания ``${VAR:=word}`` в состоянии шелла."""
        for key, value in after.items():
            if before.get(key) == value:
                continue
            if key in self.env:
                self.env[key] = value
            else:
                self._locals[key] = value

    def run_segments(self, segments, user=None):
        """Выполняет сегменты строки, раскрывая переменные перед каждым.

        ``segments`` — пары ``(сырой_текст, коннектор)``. Раскрытие идёт
        непосредственно перед запуском сегмента, поэтому присваивания из
        предыдущих сегментов уже видны, а внутри одного конвейера раскрытие
        остаётся однократным.
        """
        code = 0
        run = True
        for raw, connector in segments:
            if run:
                env = self._segment_env()
                before = dict(env)
                tokens = tokenize(raw, env)
                self._persist_expansions(before, env)
                code = self.run_and_or(tokens, user)
            if connector == "&&":
                run = (code == 0)
            elif connector == "||":
                run = (code != 0)
            else:
                run = True
        return code

    def _tokenize_chain(self, segments):
        """Токенизирует фоновую цепочку целиком, до постановки задания."""
        env = self._segment_env()
        before = dict(env)
        tokens: list[Token] = []
        for raw, connector in segments:
            tokens.extend(tokenize(raw, env))
            if connector:
                tokens.append(Token(connector, False))
        self._persist_expansions(before, env)
        return tokens

    # -- выполнение цепочек && / || -----------------------------------------
    def run_and_or(self, tokens, user=None):
        """Выполняет цепочку конвейеров, связанных ``&&`` и ``||``.

        Следующий конвейер запускается только если код возврата предыдущего
        удовлетворяет связке: ``&&`` — успех (0), ``||`` — ошибка (≠0).
        """
        return self._execute_segments(split_and_or(tokens), user)

    def _execute_segments(self, segments, user=None):
        code = 0
        run = True
        for seg_tokens, connector in segments:
            if run:
                try:
                    cmds = parse_pipeline(seg_tokens)
                except PyOSError as exc:
                    self.write_stderr(f"pyos: {exc.message}")
                    code = 2
                else:
                    code = self.execute_pipeline(cmds, user)
            if connector == "&&":
                run = (code == 0)
            elif connector == "||":
                run = (code != 0)
            else:
                run = True
        return code

    # -- конвейеры и редиректы ----------------------------------------------
    def _read_redirect(self, target, user):
        return self.kernel.fs.read_file(target, user, self.cwd, self.user.home)

    def _write_redirect(self, target, data, append, user):
        # Проверка ulimit -f (максимальный размер файла) — всегда в байтах
        size = len(data) if isinstance(data, bytes) else len(
            data.encode("utf-8"))
        limit = self.ulimit.get("file_size", -1)
        if limit >= 0 and size > limit:
            raise PyOSError(
                f"File size exceeds ulimit ({size} > {limit} bytes)")
        self.kernel.fs.write_file(target, data, user, self.cwd, self.user.home,
                                  append=append)

    def _glob_argv(self, argv, user):
        """Раскрывает шаблоны (``*``, ``?``, ``[...]``) в незакавыченных токенах."""
        result = []
        for tok in argv:
            text = str(tok)
            if not _is_quoted(tok) and any(ch in text for ch in "*?["):
                try:
                    matches = self.kernel.fs.glob(text, user, self.cwd,
                                                  self.user.home)
                except PyOSError:
                    matches = []
                if matches:
                    result.extend(matches)
                    continue
            result.append(text)
        return result

    def execute_pipeline(self, cmds, user=None):
        user = user or self.user
        # Транспорт пайплайна умеет переносить и str, и bytes: _normalize
        # сохраняет bytes (не приводит к "b'...'"), поэтому байтовый вывод
        # команды доходит до следующей стадии без перекодирования.
        #
        # str намеренно остаётся str: команды, которые склеивают stdin с
        # текстом (например, `cat -`), пока не умеют смешивать типы, и
        # принудительное str -> bytes сломало бы их пользовательское
        # поведение. Начальный stdin тоже остаётся "" по той же причине.
        stdin_data = ""
        out = err = ""
        code = 0
        total = len(cmds)
        for idx, (argv, redirects) in enumerate(cmds):
            if argv:
                for op, target in redirects:
                    if op == "<":
                        try:
                            stdin_data = self._read_redirect(target, user)
                        except PyOSError as exc:
                            self.write_stderr(
                                f"{argv[0]}: {target}: {exc.message}")
                            self.last_code = 1
                            self.exit_code = 1
                            return 1
                argv = self._glob_argv(argv, user)
                out, err, code = self.run_single(argv, stdin_data, user)
            else:
                # команда из одних редиректов (например, ``> file``)
                out, err, code = "", "", 0

            wrote_file = False
            redirect_failed = False
            for op, target in redirects:
                if op in (">", ">>"):
                    try:
                        self._write_redirect(target, out, op == ">>", user)
                        wrote_file = True
                    except PyOSError as exc:
                        self.write_stderr(
                            f"{argv[0] if argv else 'pyos'}: {target}: {exc.message}")
                        code = exc.errno or 1
                        redirect_failed = True

            if err:
                self.write_stderr(err)
            if idx < total - 1:
                stdin_data = out
            elif not wrote_file and not redirect_failed:
                self.write_stdout(out)

        self.last_code = code
        self.exit_code = code
        return code

    # -- запуск одной команды -----------------------------------------------
    def run_single(self, argv, stdin_data, user=None, nice=0):
        user = user or self.user

        # Извлекаем ведущие VAR=value (локальные присваивания)
        assignments = {}
        while argv and "=" in str(argv[0]):
            raw = str(argv[0])
            # Не путать с опцией --key=value или командой =foo
            if raw.startswith("-") or raw.startswith("="):
                break
            key, sep, val = raw.partition("=")
            if key.isidentifier():
                assignments[key] = val
                argv = argv[1:]
            else:
                break

        if not argv:
            # Голое присваивание — устанавливаем локальную переменную
            for k, v in assignments.items():
                self._locals[k] = v
            return "", "", 0

        name = str(argv[0])

        # раскрытие алиаса
        if name in self.aliases:
            expansion = tokenize(self.aliases[name], self.env)
            argv = list(expansion) + list(argv[1:])
            name = str(argv[0]) if argv else ""

        if name in BUILTIN_NAMES:
            return self.run_builtin(name, argv[1:], stdin_data, user)

        cmd = get(name)
        if cmd is None:
            return "", f"{name}: command not found", 127

        args = [str(a) for a in argv[1:]]
        cmdline = " ".join(str(a) for a in argv)
        # Для команды создаём env с присваиваниями поверх обычного
        cmd_env = dict(self.env)
        cmd_env.update(assignments)
        proc = self.kernel.create_process(
            name=name, uid=user.uid, gid=user.gid, ppid=self.kernel.init.pid,
            cwd=self.cwd, cmdline=cmdline, args=args, env=cmd_env, nice=nice,
        )
        proc.state = ProcessState.RUNNING
        self.kernel.current_proc = proc  # для /proc/self
        ctx = Context(self.kernel, user, self.cwd, self, proc, self.env)
        try:
            result = cmd.func(ctx, args, stdin_data)
            out, code = self._normalize(result)
            err = ""
        except PyOSError as exc:
            out, code, err = "", (exc.errno or 1), self._format_error(name, exc)
        except Exception as exc:  # noqa: BLE001 - защита REPL от падений команд
            out, code, err = "", 1, f"{name}: internal error: {exc}"
        finally:
            proc.exit_code = code
            proc.state = ProcessState.TERMINATED
            proc.cpu_ticks += 1
            if self._in_worker:
                # Фоновый worker выполняется вне тика планировщика; время
                # двигаем вручную (хрупко, см. README «Ограничения»).
                self.kernel.scheduler.clock += 1
            else:
                self.kernel.idle(1)
            self.kernel.reap(proc)
        return out, err, code

    @staticmethod
    def _normalize(result):
        if result is None:
            return "", 0
        if isinstance(result, bytes):
            # bytes — законный вывод команды (binary-capable pipeline);
            # не превращаем его в "b'...'".
            return result, 0
        if isinstance(result, str):
            return result, 0
        if isinstance(result, tuple):
            out = result[0] if result[0] is not None else ""
            code = result[1] if len(result) > 1 else 0
            return out, code
        return str(result), 0

    @staticmethod
    def _format_error(name, exc):
        prefix = f"{name}: "
        message = exc.message
        if message.startswith(prefix):
            # сообщение уже содержит имя команды (например "save: ...")
            return message
        if exc.path:
            return f"{name}: {exc.path}: {message}"
        return f"{name}: {message}"

    # -- фоновые задания -----------------------------------------------------
    NICE_MIN = -20
    NICE_MAX = 19

    @staticmethod
    def _parse_nice(argv):
        """Разбирает префикс ``nice [-n N]`` / ``nice N``.

        Возвращает ``(nice, argv_без_префикса)``. Некорректное значение или
        выход за диапазон поднимает :class:`InvalidArgument`.
        """
        if not argv or str(argv[0]) != "nice":
            return 0, argv

        operands = list(argv[1:])
        if not operands:
            # ``nice`` без аргументов — как и раньше, без ошибки.
            return 10, []

        first = str(operands[0])
        if first == "-n":
            if len(operands) < 2:
                raise InvalidArgument("nice: option requires an argument -- 'n'")
            raw = str(operands[1])
            rest = operands[2:]
        elif first.startswith("-n") and len(first) > 2:
            raw = first[2:]
            rest = operands[1:]
        elif first.lstrip("+-").isdigit():
            raw = first
            rest = operands[1:]
        else:
            return 10, operands

        try:
            value = int(raw)
        except ValueError:
            raise InvalidArgument(f"nice: invalid adjustment '{raw}'")

        if not Shell.NICE_MIN <= value <= Shell.NICE_MAX:
            raise InvalidArgument(
                f"nice: invalid adjustment '{raw}' "
                f"(expected {Shell.NICE_MIN}..{Shell.NICE_MAX})")

        if not rest:
            raise InvalidArgument("nice: missing command operand")

        return value, rest

    # -- завершение заданий -----------------------------------------------
    @staticmethod
    def _job_finished(job):
        """Задание завершено: worker успел или процесс уже мёртв (kill)."""
        return job["done"] or not job["proc"].is_alive

    @staticmethod
    def _format_job_line(job, status):
        """Единый формат строки задания для jobs и сообщения о завершении."""
        return f"[{job['id']}] {status:<7} {job['cmdline']}"

    def _wait_job(self, job):
        """Крутит тики, пока задание не завершится, и возвращает его код."""
        while not self._job_finished(job):
            self.kernel.scheduler.tick(1)
        return job["proc"].exit_code

    def _wait_all_jobs(self):
        """Ждёт все активные задания; код последнего завершённого или 0."""
        active = [j for j in self.jobs if not self._job_finished(j)]
        code = 0
        for job in active:
            code = self._wait_job(job)
        return code

    def _find_job(self, spec):
        """Находит job по ``%N`` или по PID. None — если такого нет."""
        text = str(spec)
        if text.startswith("%"):
            try:
                job_id = int(text[1:])
            except ValueError:
                return None
            for job in self.jobs:
                if job["id"] == job_id:
                    return job
            return None
        try:
            pid = int(text)
        except ValueError:
            return None
        for job in self.jobs:
            if job["pid"] == pid:
                return job
        return None

    def _select_job(self, command, args):
        """Выбирает job по ``%N``/PID или последний активный.

        Возвращает ``None`` (и пишет ошибку в stderr), если job не найден.
        """
        if len(args) > 1:
            raise InvalidArgument(
                f"{command}: too many arguments "
                f"(usage: {command} [%job|pid])")
        if args:
            job = self._find_job(args[0])
            if job is None:
                self.write_stderr(f"{command}: {args[0]}: no such job")
                return None
            return job
        active = [j for j in self.jobs if not self._job_finished(j)]
        if not active:
            self.write_stderr(f"{command}: no current job")
            return None
        return active[-1]

    def _set_job_code(self, code):
        """Делает код задания текущим $? (только явный wait/fg)."""
        self.last_code = code
        self.exit_code = code
        self.env["?"] = str(code)

    def execute_background(self, tokens):
        segments = split_and_or(tokens)
        nice = 0
        if segments:
            try:
                nice, rest = self._parse_nice(segments[0][0])
            except PyOSError as exc:
                # Ошибка разбора nice не должна ронять REPL и создавать job.
                self.write_stderr(self._format_error("nice", exc))
                self.last_code = exc.errno or 1
                self.exit_code = self.last_code
                return
            if len(rest) != len(segments[0][0]):
                segments = [(rest, segments[0][1])] + list(segments[1:])
        first_tokens = segments[0][0] if segments else []
        first_argv = []
        first_parsed = []
        try:
            first_parsed = parse_pipeline(first_tokens)
            if first_parsed:
                first_argv = first_parsed[0][0]
        except PyOSError:
            first_argv = first_tokens
        name = str(first_argv[0]) if first_argv else "job"
        cmdline = " ".join(str(t) for seg, _ in segments for t in seg)

        proc = self.kernel.create_process(
            name=name, uid=self.user.uid, gid=self.user.gid,
            ppid=self.kernel.init.pid, cwd=self.cwd, cmdline=cmdline, nice=nice,
        )
        job = {"id": self._next_job, "pid": proc.pid, "cmdline": cmdline,
               "done": False, "proc": proc}
        self._next_job += 1
        self.jobs.append(job)

        # Специальный sleep-путь только для ровно одной команды ``sleep N``
        # без пайпов, редиректов и логических операторов. Во всех остальных
        # случаях фон идёт общим путём и исполняет pipeline/redirects целиком.
        sleep_ticks = None
        if (len(segments) == 1 and segments[0][1] is None
                and len(first_parsed) == 1):
            argv, redirects = first_parsed[0]
            if (not redirects and len(argv) == 2
                    and str(argv[0]) == "sleep"
                    and str(argv[1]).isdigit()):
                sleep_ticks = int(str(argv[1])) * TICKS_PER_SECOND
        is_sleep = sleep_ticks is not None

        def worker(p):
            if is_sleep:
                # Ветка sleep не исполняет segments (см. ограничение is_sleep),
                # поэтому собственного кода команды у неё нет.
                p.exit_code = 0
            else:
                prev = self._in_worker
                saved_last = self.last_code
                saved_exit = self.exit_code
                self._in_worker = True
                try:
                    code = self._execute_segments(segments)
                finally:
                    self._in_worker = prev
                    # worker не должен асинхронно менять глобальный $?.
                    self.last_code = saved_last
                    self.exit_code = saved_exit
                p.exit_code = code
            job["done"] = True
            self.write_stdout(self._format_job_line(
                job, f"Done({p.exit_code})"))

        if is_sleep:
            proc.remaining = 0
            proc.worker = worker
            self.kernel.scheduler.block(proc, sleep_ticks, "sleep")
        else:
            proc.remaining = 10
            proc.worker = worker
            self.kernel.scheduler.add(proc)

        self.write_stdout(f"[{job['id']}] {proc.pid}")

    # -- встроенные команды --------------------------------------------------
    def run_builtin(self, name, args, stdin_data, user):
        handler = getattr(self, f"_bi_{name}", None)
        if handler is None:
            return "", f"{name}: builtin not implemented", 127
        try:
            result = handler(args, stdin_data, user)
            out, code = self._normalize(result)
            return out, "", code
        except PyOSError as exc:
            return "", self._format_error(name, exc), (exc.errno or 1)
        except UserError as exc:
            return "", f"{name}: {exc}", 1

    def _bi_exit(self, args, stdin, user):
        code = 0
        if args:
            try:
                code = int(args[0])
            except ValueError:
                code = 0
        self.exit_code = code
        self.running = False
        return None

    def _bi_export(self, args, stdin, user):
        if not args:
            for k, v in sorted(self.env.items()):
                self.write_stdout(f"export {k}={v}")
            return None
        for a in args:
            key, sep, val = a.partition("=")
            if sep:
                self._locals.pop(key, None)
                self.env[key] = val
            else:
                # export existing local or mark as exported
                if key in self._locals:
                    self.env[key] = self._locals.pop(key)
                elif key not in self.env:
                    self.env[key] = ""
        return None

    def _bi_unset(self, args, stdin, user):
        for a in args:
            self._locals.pop(a, None)
            self.env.pop(a, None)
        return None

    def _bi_set(self, args, stdin, user):
        for k, v in sorted(self._locals.items()):
            self.write_stdout(f"{k}={v}")
        for k, v in sorted(self.env.items()):
            self.write_stdout(f"{k}={v}")
        return None

    def _bi_nice(self, args, stdin, user):
        adjustment, rest = self._parse_nice(["nice"] + list(args))
        if not rest:
            return "0"
        out, err, code = self.run_single(rest, stdin, user, nice=adjustment)
        if err:
            self.write_stderr(err)
        return out, code

    def _bi_alias(self, args, stdin, user):
        if not args:
            for k, v in sorted(self.aliases.items()):
                self.write_stdout(f"alias {k}='{v}'")
            return None
        for a in args:
            key, sep, val = a.partition("=")
            if sep:
                self.aliases[key] = val.strip("'\"")
            else:
                if key in self.aliases:
                    self.write_stdout(f"alias {key}='{self.aliases[key]}'")
                else:
                    self.write_stderr(f"alias: {key}: not found")
                    return 1
        return None

    def _bi_unalias(self, args, stdin, user):
        for a in args:
            self.aliases.pop(a, None)
        return None

    def _bi_history(self, args, stdin, user):
        for i, line in enumerate(self.history, 1):
            self.write_stdout(f"{i:>5}  {line}")
        return None

    def _bi_jobs(self, args, stdin, user):
        if args:
            raise InvalidArgument("jobs: too many arguments (usage: jobs)")
        for job in self.jobs:
            if self._job_finished(job):
                status = f"Done({job['proc'].exit_code})"
            else:
                status = "Running"
            self.write_stdout(self._format_job_line(job, status))
        return None

    def _bi_wait(self, args, stdin, user):
        if not args:
            code = self._wait_all_jobs()
        else:
            code = 0
            for spec in args:
                job = self._find_job(spec)
                if job is None:
                    raise UserError(f"{spec}: no such job")
                code = self._wait_job(job)
        self._set_job_code(code)
        return None, code

    def _bi_waitpid(self, args, stdin, user):
        return self._bi_wait(args, stdin, user)

    def _bi_fg(self, args, stdin, user):
        job = self._select_job("fg", args)
        if job is None:
            return None, 1
        code = self._wait_job(job)
        self._set_job_code(code)
        return None, code

    def _bi_bg(self, args, stdin, user):
        job = self._select_job("bg", args)
        if job is None:
            return None, 1
        if self._job_finished(job):
            status = f"Done({job['proc'].exit_code})"
            self.write_stdout(self._format_job_line(job, status))
        else:
            self.write_stdout(f"[{job['id']}] {job['cmdline']} &")
        return None

    def _bi_su(self, args, stdin, user):
        target = args[0] if args else "root"
        account = self.kernel.users.get_by_name(target)
        if account is None:
            raise UserError(f"su: user {target} does not exist")
        if account.password_hash:
            import getpass
            try:
                pw = getpass.getpass(f"Password: ")
            except Exception:
                pw = input("Password: ")
            if not account.check_password(pw):
                raise UserError("su: Authentication failure")
        self.user = account
        self.cwd = account.home
        self.env["HOME"] = account.home
        self.env["USER"] = account.name
        self.env["LOGNAME"] = account.name
        return None

    def _bi_sudo(self, args, stdin, user):
        if not args:
            raise InvalidArgument("sudo: no command specified")
        if args[0] == "-u":
            if len(args) < 3:
                raise InvalidArgument("sudo: option requires an argument -- 'u'")
            target_name = args[1]
            rest = args[2:]
        else:
            target_name = "root"
            rest = args
        target = self.kernel.users.get_by_name(target_name)
        if target is None:
            raise InvalidArgument(f"sudo: unknown user: {target_name}")
        out, err, code = self.run_single(rest, stdin, user=target)
        if err:
            self.write_stderr(err)
        return out, code

    def _bi_source(self, args, stdin, user):
        if not args:
            raise InvalidArgument("source: filename argument required")
        data = self.kernel.fs.read_file(args[0], user, self.cwd, self.user.home)
        for line in data.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                self.handle_line(line)
        return None

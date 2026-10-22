"""Таблица сигналов pyOS (упрощённая модель Linux).

Константы и нумерация — как в Linux. Для каждого сигнала задано действие по
умолчанию: ``"term"`` — завершить процесс, ``"ign"`` — игнорировать.
SIGKILL (9) перехватить запрещено.
"""

SIGHUP = 1
SIGINT = 2
SIGQUIT = 3
SIGABRT = 6
SIGKILL = 9
SIGUSR1 = 10
SIGSEGV = 11
SIGUSR2 = 12
SIGPIPE = 13
SIGALRM = 14
SIGTERM = 15
SIGCHLD = 17
SIGCONT = 18
SIGSTOP = 19
SIGTSTP = 20
SIGWINCH = 28

SIGNAL_BY_NAME = {
    "HUP": SIGHUP,
    "INT": SIGINT,
    "QUIT": SIGQUIT,
    "ABRT": SIGABRT,
    "KILL": SIGKILL,
    "USR1": SIGUSR1,
    "SEGV": SIGSEGV,
    "USR2": SIGUSR2,
    "PIPE": SIGPIPE,
    "ALRM": SIGALRM,
    "TERM": SIGTERM,
    "CHLD": SIGCHLD,
    "CONT": SIGCONT,
    "STOP": SIGSTOP,
    "TSTP": SIGTSTP,
    "WINCH": SIGWINCH,
}

# действие сигнала по умолчанию
DEFAULT_ACTION = {
    SIGHUP: "term",
    SIGINT: "term",
    SIGQUIT: "term",
    SIGABRT: "term",
    SIGKILL: "term",
    SIGUSR1: "term",
    SIGSEGV: "term",
    SIGUSR2: "term",
    SIGPIPE: "term",
    SIGALRM: "term",
    SIGTERM: "term",
    SIGCHLD: "ign",
    SIGCONT: "ign",
    SIGSTOP: "ign",
    SIGTSTP: "ign",
    SIGWINCH: "ign",
}


def signal_number(spec: str) -> int | None:
    """Преобразует '15', 'TERM' или 'SIGTERM' в номер сигнала (или None)."""
    s = str(spec).strip().upper()
    if s.isdigit():
        n = int(s)
        return n if n in DEFAULT_ACTION else None
    if s.startswith("SIG"):
        s = s[3:]
    return SIGNAL_BY_NAME.get(s)


def signal_name(num: int) -> str:
    """Имя сигнала по номеру (например, 15 -> 'TERM')."""
    for name, n in SIGNAL_BY_NAME.items():
        if n == num:
            return name
    return str(num)


def fmt_signal_set(nums) -> str:
    """'1 2 15' из списка номеров (для /proc/pid/status и trap)."""
    return " ".join(str(n) for n in sorted(set(nums))) or "0"

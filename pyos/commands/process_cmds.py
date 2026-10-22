"""Команды для работы с процессами и планировщиком."""

from ..errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from ..process import ProcessState
from ..scheduler import TICKS_PER_SECOND
from ..signals import (
    DEFAULT_ACTION, SIGNAL_BY_NAME, signal_name, signal_number,
)
from .registry import register


# Сигналы, которые ядро pyOS не умеет обрабатывать: остановка/продолжение
# процессов не реализованы, поэтому такие запросы отклоняются явно.
_UNSUPPORTED_SIGNAL_NAMES = {"STOP", "TSTP", "CONT"}


def _supported_signals():
    """Пары (номер, короткое имя) для сигналов, которые kill принимает."""
    return sorted(
        (num, name)
        for name, num in SIGNAL_BY_NAME.items()
        if name not in _UNSUPPORTED_SIGNAL_NAMES
    )


def _format_signal_list():
    """Список поддерживаемых сигналов в формате ``<номер> <имя> SIG<имя>``."""
    return "\n".join(
        f"{num:>2} {name:<5} SIG{name}" for num, name in _supported_signals()
    )


def _check_supported(resolved, option):
    """Отклоняет сигналы, которые симулятор не умеет обрабатывать."""
    if signal_name(resolved) in _UNSUPPORTED_SIGNAL_NAMES:
        raise InvalidArgument(
            f"kill: {option}: signal is not supported by pyos simulator")


def _parse_kill_signal(opts):
    """Разбирает опции kill и возвращает номер сигнала (по умолчанию 15)."""
    signum = 15

    for o in opts:
        spec = o[1:]
        resolved = signal_number(spec)

        if resolved is None:
            raise InvalidArgument(f"kill: {o}: invalid signal specification")

        _check_supported(resolved, o)
        signum = resolved

    return signum


def _fmt_time(ticks: int) -> str:
    total = ticks / TICKS_PER_SECOND
    minutes, seconds = divmod(total, 60)
    return f"{int(minutes):02d}:{seconds:04.1f}"


@register("ps", usage="ps [aux]", summary="Список процессов",
          category="process")
def cmd_ps(ctx, args, stdin):
    opts, _ = _split(args)
    show_all = any("a" in o for o in opts if o.startswith("-"))
    full = any("u" in o for o in opts if o.startswith("-"))
    procs = ctx.kernel.live_processes()
    if not show_all and not full:
        procs = [p for p in procs if p.pid == ctx.kernel.init.pid or
                 p.state == ProcessState.RUNNING]
    lines = []
    if full or "aux" in args:
        lines.append(f"{'USER':<8} {'PID':>6} {'PPID':>6} {'STAT':>4} "
                     f"{'TIME':>7}  COMMAND")
        for p in sorted(procs, key=lambda x: x.pid):
            user = ctx.users.get_by_uid(p.uid)
            uname = user.name if user else str(p.uid)
            lines.append(f"{uname:<8} {p.pid:>6} {p.ppid:>6} {p.state_code:>4} "
                         f"{_fmt_time(p.cpu_ticks):>7}  {p.cmdline}")
    else:
        lines.append(f"{'PID':>6} {'TTY':>6} {'STAT':>4} {'TIME':>7} COMMAND")
        for p in sorted(procs, key=lambda x: x.pid):
            tty = "?" if p.is_kernel else "pts/0"
            lines.append(f"{p.pid:>6} {tty:>6} {p.state_code:>4} "
                         f"{_fmt_time(p.cpu_ticks):>7} {p.cmdline}")
    return "\n".join(lines)


@register("top", usage="top [-n iterations]", summary="Монитор процессов",
          category="process")
def cmd_top(ctx, args, stdin):
    iterations = 1
    if "-n" in args:
        try:
            iterations = int(args[args.index("-n") + 1])
        except (IndexError, ValueError):
            raise InvalidArgument("top: invalid iteration count")
    elif len(args) == 1 and args[0].isdigit():
        iterations = int(args[0])

    frames = []
    for i in range(iterations):
        ctx.kernel.idle(1)  # даём планировщику сделать шаг
        procs = sorted(ctx.kernel.live_processes(), key=lambda p: p.pid)
        lines = [f"top - up {ctx.kernel.uptime_seconds:.1f}s, "
                 f"{len(procs)} processes, quantum={ctx.kernel.scheduler.quantum}"]
        lines.append(f"{'PID':>6} {'USER':<8} {'STAT':>4} {'NI':>5} "
                     f"{'TIME':>7} COMMAND")
        for p in procs:
            user = ctx.users.get_by_uid(p.uid)
            uname = user.name if user else str(p.uid)
            lines.append(f"{p.pid:>6} {uname:<8} {p.state_code:>4} "
                         f"{p.nice:>5} {_fmt_time(p.cpu_ticks):>7} {p.cmdline}")
        frames.append("\n".join(lines))
    return "\n".join(frames)


@register("kill", usage="kill [-signal] pid ... | kill -l [signal]",
          summary="Послать сигнал процессу", category="process")
def cmd_kill(ctx, args, stdin):
    opts, pids = _split(args)
    if "-l" in opts:
        if len(opts) > 1 or len(pids) > 1:
            raise InvalidArgument("kill: -l: too many arguments")
        if not pids:
            return _format_signal_list()
        spec = str(pids[0])
        resolved = signal_number(spec)
        if resolved is None:
            raise InvalidArgument(
                f"kill: -l: invalid signal specification '{spec}'")
        _check_supported(resolved, "-l")
        return signal_name(resolved) if spec.isdigit() else str(resolved)

    signum = _parse_kill_signal(opts)
    if not pids:
        raise InvalidArgument("kill: usage: kill [-signum] pid ...")
    # Сигналы с игнорируемым действием доставляются, но процесс не завершают.
    ignore = DEFAULT_ACTION.get(signum) == "ign"
    errs = []
    for pid_s in pids:
        if not pid_s.lstrip("-").isdigit():
            errs.append(f"kill: {pid_s}: arguments must be process IDs")
            continue
        pid = int(pid_s)
        if ignore:
            proc = ctx.kernel.get_process(pid)
            if proc is None or not proc.is_alive:
                errs.append(f"kill: ({pid}) - No such process")
            continue
        if not ctx.kernel.kill(pid, signum):
            errs.append(f"kill: ({pid}) - No such process")
    if errs:
        return ("\n".join(errs), 1)
    return None


@register("sleep", usage="sleep SECONDS", summary="Пауза на N секунд",
          category="process")
def cmd_sleep(ctx, args, stdin):
    if not args:
        raise InvalidArgument("sleep: missing operand")
    total_ticks = 0
    for a in args:
        try:
            seconds = float(a)
        except ValueError:
            raise InvalidArgument(f"sleep: invalid time interval '{a}'")
        total_ticks += int(seconds * TICKS_PER_SECOND)
    ctx.kernel.sleep_ticks(total_ticks)
    return None


@register("uptime", usage="uptime", summary="Время работы системы",
          category="process")
def cmd_uptime(ctx, args, stdin):
    up = ctx.kernel.uptime_seconds
    return f"up {int(up // 60)} min {up % 60:.1f} sec, {len(ctx.kernel.live_processes())} processes"


def _split(args):
    opts, pos = [], []
    for a in args:
        if a.startswith("-") and a != "-":
            opts.append(a)
        else:
            pos.append(a)
    return opts, pos

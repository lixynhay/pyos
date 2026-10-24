"""Системные команды: информация о пользователях, системе, справка."""

import time

from ..errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from .registry import all_commands, get, register


@register("whoami", usage="whoami", summary="Имя текущего пользователя",
          category="system")
def cmd_whoami(ctx, args, stdin):
    return ctx.user.name if ctx.user else "root"


@register("id", usage="id [user]", summary="Идентификаторы пользователя",
          category="system")
def cmd_id(ctx, args, stdin):
    user = ctx.user
    if args:
        user = ctx.users.get_by_name(args[0])
        if user is None:
            raise InvalidArgument(f"id: '{args[0]}': no such user")
    groups = sorted(set([user.gid] + list(user.groups)))
    group_str = ",".join(f"{g}({ctx.users.group_name(g)})" for g in groups)
    return f"uid={user.uid}({user.name}) gid={user.gid}({ctx.users.group_name(user.gid)}) groups={group_str}"


@register("groups", usage="groups [user]", summary="Группы пользователя",
          category="system")
def cmd_groups(ctx, args, stdin):
    user = ctx.user
    if args:
        user = ctx.users.get_by_name(args[0])
        if user is None:
            raise InvalidArgument(f"groups: '{args[0]}': no such user")
    names = [ctx.users.group_name(g) for g in sorted(set([user.gid] + list(user.groups)))]
    return " ".join(names)


@register("uname", usage="uname [-a] [-r] [-n]", summary="Информация о системе",
          category="system")
def cmd_uname(ctx, args, stdin):
    k = ctx.kernel
    if "-a" in args:
        return k.uname()
    if "-r" in args:
        return k.release
    if "-n" in args:
        return k.hostname
    return k.name


@register("hostname", usage="hostname", summary="Имя хоста", category="system")
def cmd_hostname(ctx, args, stdin):
    if args:
        ctx.kernel.hostname = args[0]
        return None
    return ctx.kernel.hostname


@register("date", usage="date", summary="Текущая дата и время",
          category="system")
def cmd_date(ctx, args, stdin):
    return time.strftime("%a %b %d %H:%M:%S %Z %Y", time.localtime())


@register("env", usage="env", summary="Переменные окружения", category="system")
def cmd_env(ctx, args, stdin):
    return "\n".join(f"{k}={v}" for k, v in sorted(ctx.env.items()))


@register("which", usage="which command ...", summary="Путь к команде",
          category="system")
def cmd_which(ctx, args, stdin):
    lines = []
    for name in args:
        if get(name) is not None:
            lines.append(f"/bin/{name}")
        elif ctx.shell is not None and name in ctx.shell.builtins:
            lines.append(f"builtin:{name}")
        else:
            lines.append(f"which: no {name} in (/bin)")
    return "\n".join(lines)


@register("clear", usage="clear", summary="Очистить экран",
          category="system")
def cmd_clear(ctx, args, stdin):
    import os
    os.system("cls" if os.name == "nt" else "clear")
    return None


@register("df", usage="df", summary="Использование файловой системы",
          category="system")
def cmd_df(ctx, args, stdin):
    used = _tree_size(ctx.fs.root)
    total = 1024 * 1024  # условные 1 МБ
    lines = [
        f"{'Filesystem':<12} {'1K-blocks':>10} {'Used':>8} {'Avail':>8} {'Use%':>5} Mounted on",
        f"{'pyfs':<12} {total:>10} {used:>8} {total - used:>8} "
        f"{(used * 100 // total):>4}% /",
    ]
    return "\n".join(lines)


def _tree_size(node) -> int:
    if node.is_file():
        return node.size
    if node.is_dir():
        return sum(_tree_size(c) for c in node.children.values())
    return 0


@register("free", usage="free", summary="Использование памяти",
          category="system")
def cmd_free(ctx, args, stdin):
    total = 256 * 1024
    used = len(ctx.kernel.processes) * 1024 + _tree_size(ctx.fs.root)
    lines = [
        f"{'':<8} {'total':>10} {'used':>10} {'free':>10}",
        f"{'Mem:':<8} {total:>10} {used:>10} {max(0, total - used):>10}",
    ]
    return "\n".join(lines)


@register("dmesg", usage="dmesg", summary="Сообщения ядра (кольцевой буфер)",
          category="system")
def cmd_dmesg(ctx, args, stdin):
    if not ctx.kernel.log_buffer:
        return ""
    return "\n".join(ctx.kernel.log_buffer)


@register("help", usage="help [command]", summary="Список команд",
          category="system")
def cmd_help(ctx, args, stdin):
    if args:
        return _man(ctx, args[0])
    lines = [f"{ctx.kernel.name} {ctx.kernel.version} — встроенные команды:"]
    by_cat: dict[str, list] = {}
    for cmd in all_commands():
        by_cat.setdefault(cmd.category, []).append(cmd)
    cat_titles = {
        "filesystem": "Файлы и каталоги",
        "process": "Процессы",
        "text": "Текст",
        "system": "Система",
        "misc": "Прочее",
    }
    for cat in sorted(by_cat):
        lines.append(f"\n  [{cat_titles.get(cat, cat)}]")
        for cmd in by_cat[cat]:
            lines.append(f"    {cmd.name:<10} {cmd.summary}")
    if ctx.shell is not None:
        builtins = ", ".join(sorted(ctx.shell.builtins))
        lines.append(f"\n  [встроенные в шелл]\n    {builtins}")
    return "\n".join(lines)


@register("man", usage="man command", summary="Справка по команде",
          category="system")
def cmd_man(ctx, args, stdin):
    if not args:
        raise InvalidArgument("man: what manual page do you want?")
    return _man(ctx, args[0])


def _man(ctx, name):
    cmd = get(name)
    if cmd is not None:
        return f"NAME\n    {cmd.name} - {cmd.summary}\n\nSYNOPSIS\n    {cmd.usage}"
    if ctx.shell is not None and name in ctx.shell.builtins:
        usage = ctx.shell.builtin_usage(name)
        if usage:
            return (f"NAME\n    {name} - встроенная команда шелла"
                    f"\n\nSYNOPSIS\n    {usage}")
        return f"NAME\n    {name} - встроенная команда шелла"
    raise InvalidArgument(f"man: no manual entry for {name}")


@register("true", usage="true", summary="Всегда успешная команда (код 0)",
          category="system")
def cmd_true(ctx, args, stdin):
    return None


@register("false", usage="false", summary="Всегда неуспешная команда (код 1)",
          category="system")
def cmd_false(ctx, args, stdin):
    return (None, 1)

"""Реестр команд.

Команда — это функция ``fn(ctx, args, stdin) -> str | (str, int) | None``.
Возвращает stdout (и, опционально, код возврата). Ошибки сообщает через
исключения :class:`pyos.errors.PyOSError`.
"""


class Command:
    def __init__(self, name, func, usage="", summary="", category="misc",
                 aliases=()):
        self.name = name
        self.func = func
        self.usage = usage or name
        self.summary = summary
        self.category = category
        self.aliases = tuple(aliases)


COMMANDS: dict[str, Command] = {}


def register(name, usage="", summary="", category="misc", aliases=()):
    def deco(func):
        cmd = Command(name, func, usage, summary, category, aliases)
        COMMANDS[name] = cmd
        for alias in aliases:
            COMMANDS[alias] = cmd
        return func

    return deco


def get(name: str) -> Command | None:
    return COMMANDS.get(name)


def all_commands() -> list[Command]:
    seen = {}
    for cmd in COMMANDS.values():
        seen[cmd.name] = cmd
    return sorted(seen.values(), key=lambda c: c.name)

"""Пакет команд. Импорт подмодулей регистрирует команды в реестре."""

from . import fs_cmds, memory_cmds, network_cmds, package_cmds, process_cmds, system_cmds, text_cmds  # noqa: F401
from .registry import COMMANDS, Command, all_commands, get  # noqa: F401

__all__ = ["COMMANDS", "Command", "all_commands", "get"]

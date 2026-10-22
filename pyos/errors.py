"""Исключения ядра — упрощённые аналоги errno из Linux.

Имена намеренно даны с суффиксами, чтобы не конфликтовать со встроенными
исключениями Python (``FileExistsError``, ``PermissionError`` и т.п.).
"""


class PyOSError(Exception):
    """Базовое исключение ядра."""

    errno = 1
    label = "Error"

    def __init__(self, path="", message=""):
        self.path = path
        self.message = message or self.label
        if path:
            super().__init__(f"{self.label}: {path}")
        else:
            super().__init__(self.label)


class NoSuchFileOrDirectory(PyOSError):
    errno = 2
    label = "No such file or directory"


class PermissionDenied(PyOSError):
    errno = 13
    label = "Permission denied"


class NoSpaceLeft(PyOSError):
    errno = 28
    label = "No space left on device"


class NotADirectory(PyOSError):
    errno = 20
    label = "Not a directory"


class IsADirectory(PyOSError):
    errno = 21
    label = "Is a directory"


class InvalidArgument(PyOSError):
    errno = 22
    label = "Invalid argument"


class FileExists(PyOSError):
    errno = 17
    label = "File exists"


class DirectoryNotEmpty(PyOSError):
    errno = 39
    label = "Directory not empty"


class UserError(Exception):
    """Ошибки подсистемы пользователей (неверный логин и т.п.)."""

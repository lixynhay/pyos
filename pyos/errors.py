"""Исключения ядра — упрощённые аналоги errno из Linux.

Имена намеренно даны с суффиксами, чтобы не конфликтовать со встроенными
исключениями Python (``FileExistsError``, ``PermissionError`` и т.п.).

Контракт ``PyOSError`` — message-first: первый позиционный аргумент всегда
текст ошибки, который видит пользователь. Путь передаётся отдельно (keyword
``path=``) и только привязывает сообщение к файлу.
"""


class PyOSError(Exception):
    """Базовое исключение ядра (message-first)."""

    errno = 1
    label = "Error"

    def __init__(self, message="", path=""):
        self.message = message or self.label
        self.path = path
        super().__init__(self.message)

    def __str__(self):
        if self.path:
            return f"{self.message}: {self.path}"
        return self.message


class PathError(PyOSError):
    """Ошибка errno-типа, у которой позиционный аргумент — путь.

    ``NoSuchFileOrDirectory('/tmp/x')`` даёт message = label, path = '/tmp/x'.
    Собственный текст передаётся ключевым словом ``message=``.
    """

    def __init__(self, path="", message=""):
        super().__init__(message or self.label, path)


class NoSuchFileOrDirectory(PathError):
    errno = 2
    label = "No such file or directory"


class PermissionDenied(PathError):
    errno = 13
    label = "Permission denied"


class NoSpaceLeft(PathError):
    errno = 28
    label = "No space left on device"


class NotADirectory(PathError):
    errno = 20
    label = "Not a directory"


class IsADirectory(PathError):
    errno = 21
    label = "Is a directory"


class InvalidArgument(PyOSError):
    errno = 22
    label = "Invalid argument"


class FileExists(PathError):
    errno = 17
    label = "File exists"


class DirectoryNotEmpty(PathError):
    errno = 39
    label = "Directory not empty"


class UserError(Exception):
    """Ошибки подсистемы пользователей (неверный логин и т.п.)."""

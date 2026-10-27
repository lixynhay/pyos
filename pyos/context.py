"""Контекст выполнения команды.

Каждый вызов команды получает свежий ``Context`` с текущим пользователем,
рабочим каталогом, переменными окружения и ссылкой на процесс/шелл.
"""


class Context:
    def __init__(self, kernel, user, cwd, shell=None, process=None, env=None,
                 stdout_is_terminal=True):
        self.kernel = kernel
        self.fs = kernel.fs
        self.users = kernel.users
        self.user = user
        self.cwd = cwd
        self.home = user.home if user else "/"
        self.shell = shell
        self.process = process
        self.env = dict(env or {})
        # True, если stdout команды идёт прямо в терминал. Pipeline и
        # редиректы в файл выставляют False; прямые вызовы cmd_* в тестах
        # сохраняют терминальное поведение по умолчанию.
        self.stdout_is_terminal = stdout_is_terminal

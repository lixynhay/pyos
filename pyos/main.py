"""Точка входа pyOS: загрузка, вход в систему, запуск шелла."""

import sys

from .kernel import Kernel
from .shell import Shell
from .users import UserError

BANNER = r"""
     _ __  _   _  ___  ___
    | '_ \| | | |/ _ \/ __|
    | |_) | |_| | (_) \__ \
    | .__/ \__, |\___/|___/
    |_|    |___/   pyOS 0.1.0

Добро пожаловать в учебную ОС на Python.
Введите 'help' для списка команд.
"""


def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def login(kernel: Kernel, max_attempts: int = 3):
    """Простой логин. Пустой пароль у учётной записи — вход без пароля."""
    for attempt in range(max_attempts):
        try:
            name = input("pyos login: ").strip()
        except EOFError:
            print()
            return kernel.users.get_by_name("root")
        if not name:
            continue
        account = kernel.users.get_by_name(name)
        if account is None:
            print(f"login: user '{name}' not found")
            continue
        password = ""
        if account.password_hash:
            import getpass
            try:
                password = getpass.getpass("Password: ")
            except Exception:
                password = input("Password: ")
        try:
            user = kernel.users.authenticate(name, password)
            print(f"\nLast login: now on tty1\n")
            return user
        except UserError as exc:
            print(str(exc))
    print("login: too many failed attempts, falling back to root")
    return kernel.users.get_by_name("root")


def main(argv=None):
    _configure_stdio()
    kernel = Kernel(hostname="pyos")
    print(BANNER)
    print(kernel.uname())
    print()
    user = login(kernel)
    shell = Shell(kernel, user)
    print(f"Welcome, {user.name}! (uid={user.uid})")
    print()
    try:
        return shell.run()
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())

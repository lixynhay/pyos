"""Тесты управления памятью pyOS: save/load и ulimit."""

import json
import os
import tempfile

import pytest

from pyos.context import Context
from pyos.errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from pyos.shell import Shell


# ============================================================================
# save — сохранение состояния
# ============================================================================


def test_save_creates_file(kernel, root):
    """save создаёт JSON-файл на хосте."""
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name

    try:
        result = cmd_save(ctx, [tmpname], "")
        assert "saved" in str(result).lower()

        # Проверяем, что файл существует и содержит JSON
        with open(tmpname, "r", encoding="utf-8") as f:
            state = json.load(f)

        assert "version" in state
        assert "users" in state
        assert "filesystem" in state
        assert "hostname" in state
    finally:
        os.unlink(tmpname)


def test_save_no_filename(kernel, root):
    """save без имени файла выдаёт ошибку."""
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")
    with pytest.raises(InvalidArgument, match="missing"):
        cmd_save(ctx, [], "")


def test_save_contains_users(kernel, root):
    """save сохраняет пользователей."""
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name

    try:
        cmd_save(ctx, [tmpname], "")

        with open(tmpname, "r", encoding="utf-8") as f:
            state = json.load(f)

        users = state["users"]
        assert len(users["users"]) >= 3
        names = [u["name"] for u in users["users"]]
        assert "root" in names
        assert "user" in names
        assert "guest" in names
    finally:
        os.unlink(tmpname)


def test_save_contains_filesystem(kernel, root):
    """save сохраняет структуру ФС."""
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name

    try:
        cmd_save(ctx, [tmpname], "")

        with open(tmpname, "r", encoding="utf-8") as f:
            state = json.load(f)

        fs = state["filesystem"]
        assert "/" in fs
        assert "/root" in fs
        assert "/home" in fs
        assert "/tmp" in fs
    finally:
        os.unlink(tmpname)


# ============================================================================
# load — загрузка состояния
# ============================================================================


def _create_snapshot(kernel, root, extra_fs=None):
    """Создаёт снапшот с дополнительными файлами в ФС."""
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")

    # Создаём несколько файлов для проверки
    kernel.fs.write_file("/root/test.txt", "hello world", root, "/", "/",
                         create=True)
    kernel.fs.mkdir("/tmp/data", root, "/", "/")
    kernel.fs.write_file("/tmp/data/numbers.txt", "1\n2\n3\n", root, "/", "/",
                         create=True)
    kernel.fs.write_file("/tmp/data/hello.sh", "#!/bin/sh\necho hi", root,
                         "/", "/", create=True)

    if extra_fs:
        extra_fs()

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name

    cmd_save(ctx, [tmpname], "")
    return tmpname


def test_load_restores_files(kernel, root):
    """load восстанавливает файлы ФС."""
    from pyos.commands.memory_cmds import cmd_load, cmd_save

    ctx = Context(kernel, root, "/root")

    tmpname = _create_snapshot(kernel, root)
    try:
        # load
        cmd_load(ctx, [tmpname], "")

        # Проверяем, что файлы восстановлены
        data = kernel.fs.read_file("/root/test.txt", root, "/", "/")
        assert "hello world" in data

        data = kernel.fs.read_file("/tmp/data/numbers.txt", root, "/", "/")
        assert data.strip() == "1\n2\n3"

        data = kernel.fs.read_file("/tmp/data/hello.sh", root, "/", "/")
        assert "echo hi" in data
    finally:
        os.unlink(tmpname)


def test_load_restores_users(kernel, root):
    """load восстанавливает пользователей."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")

    tmpname = _create_snapshot(kernel, root)
    try:
        # Добавляем пользователя
        kernel.users.add_user("testuser", uid=2000, home="/home/testuser")
        assert kernel.users.get_by_name("testuser") is not None

        # load — должен стереть testuser
        cmd_load(ctx, [tmpname], "")

        assert kernel.users.get_by_name("testuser") is None
        assert kernel.users.get_by_name("root") is not None
        assert kernel.users.get_by_name("user") is not None
    finally:
        os.unlink(tmpname)


def test_load_no_filename(kernel, root):
    """load без имени файла выдаёт ошибку."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    with pytest.raises(InvalidArgument, match="missing"):
        cmd_load(ctx, [], "")


def test_load_nonexistent_file(kernel, root):
    """load с несуществующим файлом выдаёт ошибку."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    with pytest.raises(NoSuchFileOrDirectory):
        cmd_load(ctx, ["/nonexistent/path/snapshot.json"], "")


def test_load_replace_current_state(kernel, root):
    """load полностью заменяет текущее состояние."""
    from pyos.commands.memory_cmds import cmd_load, cmd_save

    # Создаём снапшот
    ctx = Context(kernel, root, "/root")
    kernel.fs.write_file("/myfile.txt", "original", root, "/", "/",
                         create=True)

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name
    cmd_save(ctx, [tmpname], "")

    # Изменяем ФС
    kernel.fs.write_file("/myfile.txt", "modified", root, "/", "/")
    kernel.fs.write_file("/extra.txt", "extra", root, "/", "/", create=True)

    # Загружаем оригинал
    cmd_load(ctx, [tmpname], "")
    os.unlink(tmpname)

    # Проверяем, что /myfile.txt восстановлен, /extra.txt удалён
    data = kernel.fs.read_file("/myfile.txt", root, "/", "/")
    assert data == "original"

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/extra.txt", root, "/", "/")


# ============================================================================
# ulimit -f
# ============================================================================


def test_ulimit_show_default(kernel, root, capsys):
    """ulimit без опций показывает 'unlimited'."""
    from pyos.commands.memory_cmds import cmd_ulimit

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    result = cmd_ulimit(ctx, [], "")
    assert result is not None
    assert "unlimited" in result


def test_ulimit_set(kernel, root):
    """ulimit -f N устанавливает лимит."""
    from pyos.commands.memory_cmds import cmd_ulimit

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    cmd_ulimit(ctx, ["-f", "4"], "")  # 4 blocks = 4 KiB = 4096 bytes
    assert shell.ulimit["file_size"] == 4096


def test_ulimit_unlimited(kernel, root):
    """ulimit -f unlimited сбрасывает лимит."""
    from pyos.commands.memory_cmds import cmd_ulimit

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    cmd_ulimit(ctx, ["-f", "10"], "")
    assert shell.ulimit["file_size"] == 10240

    cmd_ulimit(ctx, ["-f", "unlimited"], "")
    assert shell.ulimit["file_size"] == -1


def test_ulimit_enforces_limit(kernel, root):
    """ulimit -f не даёт записать файл больше лимита."""
    from pyos.commands.memory_cmds import cmd_ulimit

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    cmd_ulimit(ctx, ["-f", "1"], "")  # 1 block = 1024 bytes

    # Попытка записать большой файл через echo > file
    large_data = "x" * 2000
    with pytest.raises(PyOSError, match="ulimit"):
        shell._write_redirect("/tmp/big.txt", large_data, False, root)


def test_ulimit_small_file_works(kernel, root):
    """ulimit -f пропускает маленькие файлы."""
    from pyos.commands.memory_cmds import cmd_ulimit

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    cmd_ulimit(ctx, ["-f", "10"], "")  # 10 KiB

    small_data = "hello"
    shell._write_redirect("/tmp/small.txt", small_data, False, root)

    data = kernel.fs.read_file("/tmp/small.txt", root, "/", "/")
    assert data == "hello"


def test_ulimit_show_current(kernel, root):
    """ulimit -f показывает текущий лимит в блоках."""
    from pyos.commands.memory_cmds import cmd_ulimit

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    cmd_ulimit(ctx, ["-f", "50"], "")
    result = cmd_ulimit(ctx, ["-f"], "")
    assert result is not None
    assert "50" in result


def test_ulimit_requires_shell(kernel, root):
    """ulimit без shell выдаёт ошибку."""
    from pyos.commands.memory_cmds import cmd_ulimit

    ctx = Context(kernel, root, "/root", shell=None)
    with pytest.raises(PyOSError, match="shell"):
        cmd_ulimit(ctx, ["-f", "10"], "")


# ============================================================================
# Команды зарегистрированы
# ============================================================================


def test_save_registered():
    """save зарегистрирована в реестре."""
    from pyos.commands import get
    assert get("save") is not None


def test_load_registered():
    """load зарегистрирована в реестре."""
    from pyos.commands import get
    assert get("load") is not None


def test_ulimit_registered():
    """ulimit зарегистрирована в реестре."""
    from pyos.commands import get
    assert get("ulimit") is not None
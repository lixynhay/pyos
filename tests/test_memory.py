"""Тесты управления памятью pyOS: save/load и ulimit."""

import json
import os
import tempfile

import pytest

from pyos.context import Context
from pyos.errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from pyos.fs import VirtualFile
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


def _add_virtual_entries(tmpname):
    """Добавляет в снапшот статические /dev и /proc как в старых версиях."""
    with open(tmpname, "r", encoding="utf-8") as f:
        state = json.load(f)
    state["filesystem"]["/dev/null"] = {
        "type": "file", "uid": 0, "gid": 0, "mode": 0o666, "data": "stale",
    }
    state["filesystem"]["/proc/uptime"] = {
        "type": "file", "uid": 0, "gid": 0, "mode": 0o444, "data": "stale",
    }
    with open(tmpname, "w", encoding="utf-8") as f:
        json.dump(state, f)


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
# /proc и /dev: снапшот и восстановление
# ============================================================================


def test_save_excludes_virtual_fs(kernel, root):
    """/proc и /dev не попадают в снапшот."""
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        with open(tmpname, "r", encoding="utf-8") as f:
            state = json.load(f)
        paths = state["filesystem"]
        assert all(not (p == "/dev" or p.startswith("/dev/")) for p in paths)
        assert all(not (p == "/proc" or p.startswith("/proc/")) for p in paths)
    finally:
        os.unlink(tmpname)


def test_load_restores_dev_as_virtual(kernel, root, capsys):
    """После load /dev/null снова устройство, а не обычный файл."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        cmd_load(ctx, [tmpname], "")

        assert isinstance(kernel.fs.lookup("/dev/null", root, "/", "/"),
                          VirtualFile)
        sh = Shell(kernel, root)
        sh.handle_line("echo payload > /dev/null")
        capsys.readouterr()
        assert kernel.fs.read_file("/dev/null", root, "/", "/") == ""
    finally:
        os.unlink(tmpname)


def test_load_restores_proc_as_dynamic(kernel, root):
    """После load /proc/uptime снова меняется со временем."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        cmd_load(ctx, [tmpname], "")

        node = kernel.fs.lookup("/proc/uptime", root, "/", "/")
        assert isinstance(node, VirtualFile)

        before = kernel.fs.read_file("/proc/uptime", root, "/", "/")
        kernel.scheduler.tick(10)
        after = kernel.fs.read_file("/proc/uptime", root, "/", "/")
        assert before != after
    finally:
        os.unlink(tmpname)


def test_load_ignores_static_proc_dev_from_old_snapshot(kernel, root):
    """Старые снапшоты со статическими /proc и /dev игнорируются."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        with open(tmpname, "r", encoding="utf-8") as f:
            state = json.load(f)
        state["filesystem"]["/dev/null"] = {
            "type": "file", "uid": 0, "gid": 0, "mode": 0o666,
            "data": "stale",
        }
        state["filesystem"]["/proc/uptime"] = {
            "type": "file", "uid": 0, "gid": 0, "mode": 0o444,
            "data": "stale",
        }
        with open(tmpname, "w", encoding="utf-8") as f:
            json.dump(state, f)

        cmd_load(ctx, [tmpname], "")

        assert isinstance(kernel.fs.lookup("/dev/null", root, "/", "/"),
                          VirtualFile)
        assert isinstance(kernel.fs.lookup("/proc/uptime", root, "/", "/"),
                          VirtualFile)
        assert kernel.fs.read_file("/dev/null", root, "/", "/") == ""
        assert kernel.fs.read_file("/proc/uptime", root, "/", "/") != "stale"
    finally:
        os.unlink(tmpname)


def test_load_message_counts_only_restored_nodes(kernel, root):
    """load считает только реально восстановленные не-volatile узлы."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        with open(tmpname, "r", encoding="utf-8") as f:
            expected = len(json.load(f)["filesystem"])
        _add_virtual_entries(tmpname)

        result = cmd_load(ctx, [tmpname], "")

        assert f"({expected} nodes," in result
        assert f"{expected + 2} nodes" not in result
        assert "loaded" in result
    finally:
        os.unlink(tmpname)


def test_load_message_mentions_ignored_virtual_entries(kernel, root):
    """load сообщает, сколько виртуальных записей проигнорировано."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        _add_virtual_entries(tmpname)

        result = cmd_load(ctx, [tmpname], "")

        assert "2 virtual entries ignored" in result
    finally:
        os.unlink(tmpname)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_load_snapshot_versions_still_work(kernel, root, version):
    """load принимает снапшоты версий 1, 2 и 3."""
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    tmpname = _create_snapshot(kernel, root)
    try:
        with open(tmpname, "r", encoding="utf-8") as f:
            state = json.load(f)
        state["version"] = version
        with open(tmpname, "w", encoding="utf-8") as f:
            json.dump(state, f)

        result = cmd_load(ctx, [tmpname], "")

        assert "loaded" in result
        assert kernel.fs.read_file("/root/test.txt", root, "/", "/") == \
            "hello world"
    finally:
        os.unlink(tmpname)


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
"""ENOTDIR для промежуточного обычного файла (P1 FS-контракт).

``_parent_and_name`` раньше возвращал обычный файл как parent, и команды
падали с ``AttributeError`` / ``internal error``. Контракт: путь, у которого
родительский компонент — обычный файл, даёт ``NotADirectory`` (errno 20).
"""

import json

from pyos.shell import Shell


def _file_parent(kernel, root, mode=0o777):
    """Создаёт обычный файл ``/tmp/w``, который будет parent-компонентом."""
    kernel.fs.create_file("/tmp/w", root, data="x", mode=mode)


def _run_as(kernel, user, capsys, line):
    sh = Shell(kernel, user)
    sh.handle_line(line)
    captured = capsys.readouterr()
    return sh, captured.out + captured.err


def test_rm_through_file_parent_returns_enotdir(kernel, root, capsys):
    _file_parent(kernel, root)

    sh, out = _run_as(kernel, root, capsys, "rm /tmp/w/x")

    assert "internal error" not in out
    assert "Not a directory" in out
    assert sh.last_code == 20
    assert kernel.fs.lookup("/tmp/w", root).is_file()


def test_rmdir_through_file_parent_returns_enotdir(kernel, root, capsys):
    _file_parent(kernel, root)

    sh, out = _run_as(kernel, root, capsys, "rmdir /tmp/w/x")

    assert "internal error" not in out
    assert "Not a directory" in out
    assert sh.last_code == 20
    assert kernel.fs.lookup("/tmp/w", root).is_file()


def test_mv_through_file_parent_returns_enotdir(kernel, root, capsys):
    _file_parent(kernel, root)

    sh, out = _run_as(kernel, root, capsys, "mv /tmp/w/x /tmp/y")

    assert "internal error" not in out
    assert "Not a directory" in out
    assert sh.last_code == 20
    assert kernel.fs.lookup("/tmp/w", root).is_file()


def test_ln_s_through_file_parent_returns_enotdir(kernel, root, capsys):
    _file_parent(kernel, root)

    sh, out = _run_as(kernel, root, capsys, "ln -s /tmp/w /tmp/w/l")

    assert "internal error" not in out
    assert "Not a directory" in out
    assert sh.last_code == 20
    assert kernel.fs.lookup("/tmp/w", root).is_file()


def test_rm_through_file_parent_as_nonroot_with_world_writable_file(
        kernel, root, user, capsys):
    _file_parent(kernel, root, mode=0o777)

    sh, out = _run_as(kernel, user, capsys, "rm /tmp/w/x")

    assert "internal error" not in out
    assert "Not a directory" in out
    assert "Permission denied" not in out
    assert sh.last_code == 20
    assert kernel.fs.lookup("/tmp/w", root).is_file()


def test_root_rm_through_file_parent_returns_enotdir(kernel, root, capsys):
    _file_parent(kernel, root, mode=0o644)

    sh, out = _run_as(kernel, root, capsys, "rm /tmp/w/x")

    assert "internal error" not in out
    assert "Not a directory" in out
    assert sh.last_code == 20
    assert kernel.fs.lookup("/tmp/w", root).is_file()


def test_pkg_remove_with_corrupt_installed_db_does_not_internal_error(
        kernel, root, capsys):
    """Повреждённая installed DB ссылается на путь под обычным файлом."""
    fs = kernel.fs
    _file_parent(kernel, root)
    fs.mkdir("/var/lib/packages/installed", root, parents=True)
    fs.write_file(
        "/var/lib/packages/installed/corruptpkg.json",
        json.dumps({"name": "corruptpkg", "files": ["/tmp/w/x"]}),
        root, "/", "/", create=True)

    sh, out = _run_as(kernel, root, capsys, "pkg remove corruptpkg")

    assert "internal error" not in out
    assert "Not a directory" in out

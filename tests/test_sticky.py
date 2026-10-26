"""STICKY-1: characterization текущего поведения sticky bit.

Фиксирует ТЕКУЩЕЕ отсутствие POSIX-enforcement sticky-бита (``0o1000``) для
``/tmp`` и произвольных каталогов с этим битом, а также корректные случаи,
которые фикс STICKY-2 не должен сломать.

Правила файла:

* ``# CHARACTERIZE:`` — assert фиксирует текущее (неверное с точки зрения
  POSIX) поведение; на этапе STICKY-2 эти тесты инвертируются: правится
  assert, тест не удаляется.
* ``# GUARD:`` — корректное поведение; должно оставаться зелёным и до, и
  после STICKY-2.
* ``# KNOWN DEFECT:`` — отдельный дефект вне sticky-скоупа, фиксируется как
  есть, не чинится.

Тесты детерминированы: без сети, без wall-clock, без реальных процессов ОС.
"""

import os
import tempfile

import pytest

from pyos.commands.memory_cmds import cmd_load, cmd_save
from pyos.context import Context
from pyos.errors import NoSuchFileOrDirectory, PyOSError
from pyos.shell import Shell


# ============================================================================
# вспомогательное
# ============================================================================


def _run(kernel, user, capsys, line):
    """Выполняет строку в шелле и возвращает (shell, stdout+stderr)."""
    sh = Shell(kernel, user)
    sh.handle_line(line)
    captured = capsys.readouterr()
    return sh, captured.out + captured.err


def _exists(fs, path, follow=True):
    """Проверяет наличие узла без учёта прав текущего пользователя."""
    try:
        fs.lookup(path, follow=follow)
        return True
    except PyOSError:
        return False


# ============================================================================
# CHARACTERIZE: sticky не enforced — удаление / переименование чужого
# ============================================================================


def test_sticky_user_can_unlink_root_file_in_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: sticky /tmp (1777) не enforced; rm чужого файла проходит
    # при праве w на каталоге. Инвертируется на STICKY-2.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/root_file")


def test_sticky_user_can_rmdir_root_empty_dir_in_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: rmdir пустого чужого каталога в sticky /tmp проходит.
    fs = kernel.fs
    fs.mkdir("/tmp/root_empty", root, "/", "/")

    sh, out = _run(kernel, user, capsys, "rmdir /tmp/root_empty")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/root_empty")


def test_sticky_user_can_rm_r_root_world_dir_in_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: rm -r чужого 0777-каталога в sticky /tmp проходит,
    # хотя владелец каталога и файлов внутри — root.
    fs = kernel.fs
    fs.mkdir("/tmp/world_dir", root, "/", "/", mode=0o777)
    fs.create_file("/tmp/world_dir/inside", root, "/", "/",
                   data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys, "rm -r /tmp/world_dir")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/world_dir")


def test_sticky_user_can_rename_root_entry_in_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: rename чужого entry внутри sticky /tmp проходит.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys, "mv /tmp/root_file /tmp/renamed")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/root_file")
    moved = fs.lookup("/tmp/renamed")
    assert moved.uid == 0
    assert fs.read_bytes("/tmp/renamed") == b"root"


def test_sticky_user_can_move_root_file_out_of_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: user может вынести чужой файл из sticky /tmp к себе.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys,
                   "mv /tmp/root_file /home/user/stolen")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/root_file")
    stolen = fs.lookup("/home/user/stolen")
    assert stolen.uid == 0
    assert fs.read_bytes("/home/user/stolen") == b"root"


def test_sticky_user_can_overwrite_root_file_with_mv_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: rename поверх чужого файла в sticky /tmp проходит,
    # причём владельцем цели становится user (узел src заменяет dst).
    fs = kernel.fs
    fs.create_file("/tmp/root_dst", root, "/", "/", data="ROOT", mode=0o644)
    fs.create_file("/tmp/user_src", user, "/", "/", data="USER", mode=0o644)

    sh, out = _run(kernel, user, capsys, "mv /tmp/user_src /tmp/root_dst")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_src")
    dst = fs.lookup("/tmp/root_dst")
    assert dst.uid == user.uid
    assert fs.read_bytes("/tmp/root_dst") == b"USER"


def test_sticky_user_can_unlink_root_symlink_in_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: rm чужого symlink в sticky /tmp проходит; удаляется сама
    # ссылка, цель остаётся целой.
    fs = kernel.fs
    fs.create_file("/tmp/root_target", root, "/", "/", data="root")
    fs.symlink("/tmp/root_target", "/tmp/root_link", root, "/", "/")

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_link")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/root_link", follow=False)
    assert fs.read_bytes("/tmp/root_target") == b"root"


def test_sticky_user_can_unlink_root_hardlink_in_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: rm чужой жёсткой ссылки в sticky /tmp проходит; nlink
    # общего inode уменьшается, исходный файл остаётся.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root")
    fs.link("/tmp/root_file", "/tmp/root_hard", root, "/", "/")
    assert fs.lookup("/tmp/root_file").nlink == 2

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_hard")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/root_hard", follow=False)
    assert _exists(fs, "/tmp/root_file")
    assert fs.lookup("/tmp/root_file").nlink == 1


def test_sticky_generic_dir_1777_not_only_tmp_currently(
        kernel, root, user, capsys):
    # CHARACTERIZE: отсутствие enforcement привязано к биту 0o1000, а не к
    # пути /tmp: chmod 1777 на произвольном каталоге ничего не меняет.
    fs = kernel.fs
    fs.mkdir("/tmp/sub", root, "/", "/", mode=0o755)
    fs.chmod("/tmp/sub", 0o1777, root, "/", "/")
    assert fs.lookup("/tmp/sub").mode == 0o1777
    fs.create_file("/tmp/sub/root_file", root, "/", "/", data="root")

    sh, out = _run(kernel, user, capsys, "rm /tmp/sub/root_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/sub/root_file")


def test_rm_r_world_dir_with_empty_locked_subdir_currently_succeeds(
        kernel, root, user, capsys):
    # KNOWN DEFECT (не sticky): _check_rmtree пропускает проверку W|X у
    # пустых подкаталогов, поэтому rm -r 0777-дерева с пустым 0555-каталогом
    # внутри проходит. Фиксируется как есть и не чинится в STICKY-2.
    # После STICKY-2 верхний entry в sticky /tmp будет отклонён по sticky,
    # и тест придётся уточнить/перенести в non-sticky родителя.
    fs = kernel.fs
    fs.mkdir("/tmp/world_tree", root, "/", "/", mode=0o777)
    fs.mkdir("/tmp/world_tree/locked", root, "/", "/", mode=0o555)
    assert fs.lookup("/tmp/world_tree/locked").mode == 0o555

    sh, out = _run(kernel, user, capsys, "rm -r /tmp/world_tree")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/world_tree")


# ============================================================================
# GUARD: корректные случаи, которые STICKY-2 не должен сломать
# ============================================================================


def test_sticky_owner_can_unlink_own_file_in_tmp(
        kernel, root, user, capsys):
    # GUARD: владелец файла удаляет свой файл в sticky /tmp.
    fs = kernel.fs
    fs.create_file("/tmp/user_file", user, "/", "/", data="user")

    sh, out = _run(kernel, user, capsys, "rm /tmp/user_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_file")


def test_sticky_root_can_unlink_any_entry_in_tmp(
        kernel, root, user, capsys):
    # GUARD: root удаляет чужой entry в sticky /tmp.
    fs = kernel.fs
    fs.create_file("/tmp/user_file", user, "/", "/", data="user")

    sh, out = _run(kernel, root, capsys, "rm /tmp/user_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_file")


def test_sticky_dir_owner_can_unlink_root_file_inside_own_dir(
        kernel, root, user, capsys):
    # GUARD: sticky проверяется только у непосредственного родителя entry.
    # /tmp/user_dir не sticky, поэтому владелец каталога удаляет чужой файл.
    fs = kernel.fs
    fs.mkdir("/tmp/user_dir", user, "/", "/", mode=0o777)
    fs.create_file("/tmp/user_dir/root_file", root, "/", "/", data="root")

    sh, out = _run(kernel, user, capsys, "rm /tmp/user_dir/root_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_dir/root_file")


def test_sticky_owner_can_remove_own_tree_in_tmp(
        kernel, root, user, capsys):
    # GUARD: владелец верхнего entry удаляет своё дерево в sticky /tmp.
    fs = kernel.fs
    fs.mkdir("/tmp/user_tree", user, "/", "/", mode=0o755)
    fs.mkdir("/tmp/user_tree/sub", user, "/", "/", mode=0o755)
    fs.create_file("/tmp/user_tree/a", user, "/", "/", data="a")
    fs.create_file("/tmp/user_tree/sub/b", user, "/", "/", data="b")

    sh, out = _run(kernel, user, capsys, "rm -r /tmp/user_tree")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_tree")


def test_cp_onto_world_writable_root_file_still_allowed(
        kernel, root, user, capsys):
    # GUARD: cp — запись содержимого, а не удаление entry; sticky не при чём.
    # W на файле есть → запись разрешена, владелец и mode не меняются.
    fs = kernel.fs
    fs.create_file("/tmp/root_ww", root, "/", "/", data="ROOT", mode=0o666)
    fs.create_file("/tmp/user_src", user, "/", "/", data="USER")

    sh, out = _run(kernel, user, capsys, "cp /tmp/user_src /tmp/root_ww")

    assert out == ""
    assert sh.last_code == 0
    dst = fs.lookup("/tmp/root_ww")
    assert dst.uid == 0
    assert dst.mode == 0o666
    assert fs.read_bytes("/tmp/root_ww") == b"USER"


def test_cp_onto_readonly_root_file_still_denied(
        kernel, root, user, capsys):
    # GUARD: cp поверх чужого 0644 файла отклоняется по обычному W-праву.
    fs = kernel.fs
    fs.create_file("/tmp/root_ro", root, "/", "/", data="ROOT", mode=0o644)
    fs.create_file("/tmp/user_src", user, "/", "/", data="USER")

    sh, out = _run(kernel, user, capsys, "cp /tmp/user_src /tmp/root_ro")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert fs.read_bytes("/tmp/root_ro") == b"ROOT"
    assert fs.lookup("/tmp/root_ro").uid == 0


def test_non_sticky_world_dir_still_allows_delete(
        kernel, root, user, capsys):
    # GUARD: 0777 без sticky — удаление чужого файла разрешено по W.
    fs = kernel.fs
    fs.mkdir("/tmp/nonsticky", root, "/", "/", mode=0o777)
    fs.create_file("/tmp/nonsticky/root_file", root, "/", "/", data="root")
    assert fs.lookup("/tmp/nonsticky").mode == 0o777

    sh, out = _run(kernel, user, capsys, "rm /tmp/nonsticky/root_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/nonsticky/root_file")


def test_pkg_build_root_is_var_tmp():
    # GUARD: пакетный слой собирает в /var/tmp/makepkg, а не в /tmp,
    # поэтому sticky /tmp не должен влиять на pkg install/remove.
    from pyos.commands.package_cmds import BUILD_ROOT

    assert BUILD_ROOT == "/var/tmp/makepkg"
    assert BUILD_ROOT != "/tmp"
    assert not BUILD_ROOT.startswith("/tmp/")


def test_save_load_keeps_tmp_1777(kernel, root):
    # GUARD: snapshot хранит mode 0o1777 для /tmp, а load создаёт узлы с
    # user=None (bypass прав), поэтому sticky-проверка не должна ломать load.
    ctx = Context(kernel, root, "/root")

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name

    try:
        cmd_save(ctx, [tmpname], "")
        cmd_load(ctx, [tmpname], "")

        assert kernel.fs.lookup("/tmp").mode == 0o1777

        # user=None: внутренний путь ФС не должен упираться в sticky.
        node = kernel.fs.mkdir("/tmp/after_load", None, "/", "/")
        assert node.uid == 0
        assert kernel.fs.lookup("/tmp/after_load").is_dir()
    finally:
        os.unlink(tmpname)


def test_proc_dev_not_affected_by_sticky_characterization(kernel, root):
    # GUARD: /proc и /dev не sticky и не сериализуются; виртуальные узлы
    # не должны зависеть от sticky-логики.
    fs = kernel.fs

    assert "pyOS" in fs.read_file("/proc/version", root)
    assert fs.lookup("/proc").mode & 0o1000 == 0

    dev_null = fs.lookup("/dev/null", root)
    assert dev_null.is_file()
    assert fs.lookup("/dev").mode & 0o1000 == 0

    fs.write_file("/dev/null", "discarded", root)
    assert fs.lookup("/dev/null").is_file()

    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/proc/no_such_entry_xyz", root)

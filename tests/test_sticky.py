"""STICKY-2: sticky bit enforcement для удаления/переименования записей.

POSIX-подобное правило pyos: запись в каталоге с битом ``0o1000`` может
удалить или переименовать только root, владелец каталога или владелец самой
записи (для hardlink — владелец общего inode, для symlink — владелец
ссылки). Иначе — ``PermissionDenied`` (errno 13).

* ``# FIXED (STICKY-2):`` — тесты, которые в STICKY-1 фиксировали отсутствие
  enforcement, теперь проверяют отказ. Имена сохранены по конвенции
  characterization-тестов: правится assert, тест не удаляется.
* ``# GUARD:`` — корректное поведение, не зависящее от sticky.
* ``# KNOWN DEFECT:`` — отдельный дефект вне sticky-скоупа (пустые
  подкаталоги в ``_check_rmtree``), не чинится.

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
# FIXED (STICKY-2): удаление / переименование чужого в sticky-каталоге
# ============================================================================


def test_sticky_user_can_unlink_root_file_in_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): в STICKY-1 rm чужого файла в /tmp проходил.
    # Теперь sticky /tmp (1777) требует root / владельца каталога / владельца
    # файла; user — никто из них.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_file")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_file")


def test_sticky_user_can_rmdir_root_empty_dir_in_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): rmdir чужого пустого каталога в sticky /tmp отклонён.
    fs = kernel.fs
    fs.mkdir("/tmp/root_empty", root, "/", "/")

    sh, out = _run(kernel, user, capsys, "rmdir /tmp/root_empty")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_empty")
    assert fs.lookup("/tmp/root_empty").is_dir()


def test_sticky_user_can_rm_r_root_world_dir_in_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): rm -r чужого 0777-каталога в sticky /tmp отклонён,
    # дерево остаётся целым.
    fs = kernel.fs
    fs.mkdir("/tmp/world_dir", root, "/", "/", mode=0o777)
    fs.create_file("/tmp/world_dir/inside", root, "/", "/",
                   data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys, "rm -r /tmp/world_dir")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/world_dir")
    assert fs.read_bytes("/tmp/world_dir/inside") == b"root"


def test_sticky_user_can_rename_root_entry_in_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): rename чужого entry внутри sticky /tmp отклонён,
    # src и dst не меняются.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys, "mv /tmp/root_file /tmp/renamed")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_file")
    assert not _exists(fs, "/tmp/renamed")
    assert fs.read_bytes("/tmp/root_file") == b"root"


def test_sticky_user_can_move_root_file_out_of_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): вынести чужой файл из sticky /tmp нельзя.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh, out = _run(kernel, user, capsys,
                   "mv /tmp/root_file /home/user/stolen")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_file")
    assert fs.read_bytes("/tmp/root_file") == b"root"
    assert not _exists(fs, "/home/user/stolen")


def test_sticky_user_can_overwrite_root_file_with_mv_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): rename поверх чужого файла в sticky /tmp отклонён;
    # dst сохраняет uid/data, src остаётся на месте.
    fs = kernel.fs
    fs.create_file("/tmp/root_dst", root, "/", "/", data="ROOT", mode=0o644)
    fs.create_file("/tmp/user_src", user, "/", "/", data="USER", mode=0o644)

    sh, out = _run(kernel, user, capsys, "mv /tmp/user_src /tmp/root_dst")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/user_src")
    assert fs.read_bytes("/tmp/user_src") == b"USER"
    dst = fs.lookup("/tmp/root_dst")
    assert dst.uid == 0
    assert fs.read_bytes("/tmp/root_dst") == b"ROOT"


def test_sticky_user_can_unlink_root_symlink_in_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): rm чужого symlink в sticky /tmp отклонён; ссылка
    # и цель остаются целыми.
    fs = kernel.fs
    fs.create_file("/tmp/root_target", root, "/", "/", data="root")
    fs.symlink("/tmp/root_target", "/tmp/root_link", root, "/", "/")

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_link")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_link", follow=False)
    assert fs.read_bytes("/tmp/root_target") == b"root"


def test_sticky_user_can_unlink_root_hardlink_in_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): rm чужой жёсткой ссылки в sticky /tmp отклонён;
    # nlink общего inode не меняется.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root")
    fs.link("/tmp/root_file", "/tmp/root_hard", root, "/", "/")
    assert fs.lookup("/tmp/root_file").nlink == 2

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_hard")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_hard", follow=False)
    assert fs.lookup("/tmp/root_file").nlink == 2


def test_sticky_generic_dir_1777_not_only_tmp_currently(
        kernel, root, user, capsys):
    # FIXED (STICKY-2): enforcement привязан к биту 0o1000, а не к пути /tmp:
    # chmod 1777 на произвольном каталоге даёт ту же защиту.
    fs = kernel.fs
    fs.mkdir("/tmp/sub", root, "/", "/", mode=0o755)
    fs.chmod("/tmp/sub", 0o1777, root, "/", "/")
    assert fs.lookup("/tmp/sub").mode == 0o1777
    fs.create_file("/tmp/sub/root_file", root, "/", "/", data="root")

    sh, out = _run(kernel, user, capsys, "rm /tmp/sub/root_file")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/sub/root_file")


def test_rm_r_world_dir_with_empty_locked_subdir_currently_succeeds(
        kernel, root, user, capsys):
    # KNOWN DEFECT (не sticky): _check_rmtree пропускает проверку W|X у
    # пустых подкаталогов, поэтому rm -r 0777-дерева с пустым 0555-каталогом
    # внутри проходит. Не чинится в STICKY-2.
    # Дерево живёт в non-sticky родителе (0777), иначе отказ по sticky на
    # верхнем entry /tmp замаскировал бы дефект.
    fs = kernel.fs
    fs.mkdir("/tmp/holder", root, "/", "/", mode=0o777)
    fs.mkdir("/tmp/holder/world_tree", root, "/", "/", mode=0o777)
    fs.mkdir("/tmp/holder/world_tree/locked", root, "/", "/", mode=0o555)
    assert fs.lookup("/tmp/holder/world_tree/locked").mode == 0o555

    sh, out = _run(kernel, user, capsys, "rm -r /tmp/holder/world_tree")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/holder/world_tree")


# ============================================================================
# DENY: новые sticky-проверки
# ============================================================================


def test_sticky_rm_returns_permission_denied_stderr_and_code_13(
        kernel, root, user, capsys):
    # rm печатает ошибку в stderr и возвращает errno 13, файл цел.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root", mode=0o644)

    sh = Shell(kernel, user)
    sh.handle_line("rm /tmp/root_file")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "rm: cannot remove '/tmp/root_file': Permission denied" in \
        captured.err
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_file")


def test_sticky_rmdir_nonempty_foreign_dir_returns_permission_denied_before_directory_not_empty(
        kernel, root, user, capsys):
    # Чужой непустой каталог в sticky-родителе: PermissionDenied важнее
    # DirectoryNotEmpty (осознанный приоритет ошибки).
    fs = kernel.fs
    fs.mkdir("/tmp/root_full", root, "/", "/")
    fs.create_file("/tmp/root_full/inside", root, "/", "/", data="root")

    sh = Shell(kernel, user)
    sh.handle_line("rmdir /tmp/root_full")
    captured = capsys.readouterr()

    assert "Permission denied" in captured.err
    assert "Directory not empty" not in captured.err
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/root_full/inside")


def test_sticky_rm_r_does_not_partially_delete_tree(
        kernel, root, user, capsys):
    # Рекурсивная pre-validation: верхний entry принадлежит user (sticky
    # разрешает), но внутри — чужой sticky-каталог с чужим файлом. rm -r
    # отклоняется целиком, без частичного удаления.
    fs = kernel.fs
    fs.mkdir("/tmp/user_tree", user, "/", "/", mode=0o777)
    fs.create_file("/tmp/user_tree/user_file", user, "/", "/", data="user")
    fs.mkdir("/tmp/user_tree/foreign_sticky", root, "/", "/", mode=0o777)
    fs.chmod("/tmp/user_tree/foreign_sticky", 0o1777, root, "/", "/")
    fs.create_file("/tmp/user_tree/foreign_sticky/root_file", root, "/", "/",
                   data="root")

    sh, out = _run(kernel, user, capsys, "rm -r /tmp/user_tree")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert _exists(fs, "/tmp/user_tree")
    assert _exists(fs, "/tmp/user_tree/user_file")
    assert _exists(fs, "/tmp/user_tree/foreign_sticky")
    assert _exists(fs, "/tmp/user_tree/foreign_sticky/root_file")
    assert fs.read_bytes("/tmp/user_tree/foreign_sticky/root_file") == b"root"


def test_sticky_mv_src_denied_leaves_source_and_dest_unchanged(
        kernel, root, user, capsys):
    # mv чужого entry внутри sticky /tmp: отказ, src на месте, dst не создан.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root")

    sh, out = _run(kernel, user, capsys, "mv /tmp/root_file /tmp/new_name")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert fs.read_bytes("/tmp/root_file") == b"root"
    assert fs.lookup("/tmp/root_file").uid == 0
    assert not _exists(fs, "/tmp/new_name")


def test_sticky_mv_overwrite_foreign_dst_denied(
        kernel, root, user, capsys):
    # mv своего файла поверх чужого в sticky /tmp: отказ, обе записи целы.
    fs = kernel.fs
    fs.create_file("/tmp/root_dst", root, "/", "/", data="ROOT", mode=0o644)
    fs.create_file("/tmp/user_src", user, "/", "/", data="USER", mode=0o644)

    sh, out = _run(kernel, user, capsys, "mv /tmp/user_src /tmp/root_dst")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert fs.read_bytes("/tmp/user_src") == b"USER"
    assert fs.lookup("/tmp/user_src").uid == user.uid
    dst = fs.lookup("/tmp/root_dst")
    assert dst.uid == 0
    assert fs.read_bytes("/tmp/root_dst") == b"ROOT"


def test_sticky_symlink_unlink_denied_leaves_link_and_target(
        kernel, root, user, capsys):
    # Про denied symlink: проверяется uid ссылки; цель не трогается.
    fs = kernel.fs
    fs.create_file("/tmp/root_target", root, "/", "/", data="root")
    fs.symlink("/tmp/root_target", "/tmp/root_link", root, "/", "/")

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_link")

    assert "Permission denied" in out
    assert sh.last_code == 13
    link = fs.lookup("/tmp/root_link", follow=False)
    assert link.is_link()
    assert link.uid == 0
    assert fs.read_bytes("/tmp/root_target") == b"root"


def test_sticky_hardlink_unlink_denied_leaves_nlink(
        kernel, root, user, capsys):
    # Проверяется uid общего inode; nlink не уменьшается при отказе.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root")
    fs.link("/tmp/root_file", "/tmp/root_hard", root, "/", "/")

    sh, out = _run(kernel, user, capsys, "rm /tmp/root_hard")

    assert "Permission denied" in out
    assert sh.last_code == 13
    assert fs.lookup("/tmp/root_file").nlink == 2
    assert _exists(fs, "/tmp/root_hard", follow=False)


def test_sticky_owner_can_rename_own_file_in_sticky_dir(
        kernel, root, user, capsys):
    # GUARD: владелец записи может переименовать её в sticky-каталоге.
    fs = kernel.fs
    fs.create_file("/tmp/user_file", user, "/", "/", data="user")

    sh, out = _run(kernel, user, capsys, "mv /tmp/user_file /tmp/renamed")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_file")
    moved = fs.lookup("/tmp/renamed")
    assert moved.uid == user.uid
    assert fs.read_bytes("/tmp/renamed") == b"user"


def test_sticky_owner_can_overwrite_own_dst_in_sticky_dir(
        kernel, root, user, capsys):
    # GUARD: перезапись своей же записи в sticky-каталоге разрешена.
    fs = kernel.fs
    fs.create_file("/tmp/user_src", user, "/", "/", data="SRC")
    fs.create_file("/tmp/user_dst", user, "/", "/", data="DST")

    sh, out = _run(kernel, user, capsys, "mv /tmp/user_src /tmp/user_dst")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_src")
    assert fs.read_bytes("/tmp/user_dst") == b"SRC"
    assert fs.lookup("/tmp/user_dst").uid == user.uid


def test_sticky_root_can_rename_foreign_entry_in_sticky_dir(
        kernel, root, user, capsys):
    # GUARD: root обходит sticky.
    fs = kernel.fs
    fs.create_file("/tmp/user_file", user, "/", "/", data="user")

    sh, out = _run(kernel, root, capsys, "mv /tmp/user_file /tmp/renamed")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_file")
    assert fs.lookup("/tmp/renamed").uid == user.uid


def test_sticky_dir_owner_can_remove_foreign_entry_in_own_sticky_dir(
        kernel, root, user, capsys):
    # GUARD: владелец sticky-каталога вправе удалять чужие entry.
    fs = kernel.fs
    fs.mkdir("/tmp/user_sticky", user, "/", "/", mode=0o777)
    fs.chmod("/tmp/user_sticky", 0o1777, user, "/", "/")
    assert fs.lookup("/tmp/user_sticky").uid == user.uid
    fs.create_file("/tmp/user_sticky/root_file", root, "/", "/", data="root")

    sh, out = _run(kernel, user, capsys, "rm /tmp/user_sticky/root_file")

    assert out == ""
    assert sh.last_code == 0
    assert not _exists(fs, "/tmp/user_sticky/root_file")


def test_sticky_mv_same_entry_is_noop(kernel, root, user, capsys):
    # GUARD: src и dst — одна и та же запись: no-op, sticky-отказ не
    # применяется (иначе нельзя было бы "переименовать в себя").
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/", data="root")

    sh, out = _run(kernel, user, capsys, "mv /tmp/root_file /tmp/root_file")

    assert out == ""
    assert sh.last_code == 0
    assert fs.read_bytes("/tmp/root_file") == b"root"
    assert fs.lookup("/tmp/root_file").uid == 0


# ============================================================================
# GUARD: поведение, не зависящее от sticky
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

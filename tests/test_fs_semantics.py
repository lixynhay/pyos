"""Characterization тестов семантики ФС.

Фиксирует ТЕКУЩЕЕ поведение виртуальной ФС (HEAD 7311d0a) как baseline перед
этапами 2/3. Здесь намеренно НЕТ исправлений и НЕТ проверки «как должно быть»:

* ``# CHARACTERIZE:`` — известные баги (В1–В14 из read-only анализа). Assert
  фиксирует текущее НЕверное поведение; при исправлении на этапе 2 / 3a / 3b
  инвертируется (правится assert, тест не удаляется).
* ``# DOCUMENTED LIMIT:`` — осознанные ограничения из README. Assert фиксирует
  текущее поведение как регресс-страховку и НЕ инвертируется никогда.
* отдельные тесты ``*_guard`` — корректное поведение, которое этапы 2/3 не
  должны сломать молча.

Тесты детерминированы: без таймеров и ``time.sleep``. Единственный рекурсивный
сценарий (cp -r в собственное поддерево) ограничен понижением
``sys.setrecursionlimit`` и ожиданием ``RecursionError``.
"""

import os
import sys
import tempfile

import pytest

from pyos.context import Context
from pyos.errors import (
    InvalidArgument,
    NoSuchFileOrDirectory,
    PermissionDenied,
)
from pyos.shell import Shell


# ============================================================================
# вспомогательное
# ============================================================================


def _run(shell, capsys, line):
    """Выполняет строку в шелле и возвращает объединённый stdout+stderr."""
    shell.handle_line(line)
    captured = capsys.readouterr()
    return captured.out + captured.err


def _stack_depth():
    depth = 0
    frame = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


def _save(kernel, root):
    from pyos.commands.memory_cmds import cmd_save

    ctx = Context(kernel, root, "/root")
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name
    cmd_save(ctx, [tmpname], "")
    return tmpname


def _load(kernel, root, tmpname):
    from pyos.commands.memory_cmds import cmd_load

    ctx = Context(kernel, root, "/root")
    cmd_load(ctx, [tmpname], "")


# ============================================================================
# пути и права (этап 2: CHARACTERIZE инвертированы)
# ============================================================================


def _unsearchable_setup(kernel, root):
    fs = kernel.fs
    fs.mkdir("/root/secret", root, mode=0o755)              # /root = 700
    fs.create_file("/root/secret/hidden", root,
                   data="TOPSECRET", mode=0o644)
    return fs


def test_cd_denies_unsearchable_parent(kernel, root, user, capsys):
    # В1: cd больше не обходит x на промежуточных каталогах.
    _unsearchable_setup(kernel, root)
    sh = Shell(kernel, user)
    start = sh.cwd
    out = _run(sh, capsys, "cd /root/secret")
    assert "Permission denied" in out
    assert sh.cwd == start


def test_stat_denies_unsearchable_parent(kernel, root, user, capsys):
    # В1: stat не раскрывает метаданные из непроходимого каталога.
    _unsearchable_setup(kernel, root)
    sh = Shell(kernel, user)
    out = _run(sh, capsys, "stat /root/secret/hidden")
    assert "Permission denied" in out
    assert "TOPSECRET" not in out


def test_touch_denies_unsearchable_parent(kernel, root, user, capsys):
    # В1: touch не создаёт файл через непроходимый каталог.
    fs = _unsearchable_setup(kernel, root)
    sh = Shell(kernel, user)
    out = _run(sh, capsys, "touch /root/secret")
    assert "Permission denied" in out
    out = _run(sh, capsys, "touch /root/secret/new")
    assert "Permission denied" in out
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/root/secret/new", root, "/", "/")


def test_chmod_denies_unsearchable_parent(kernel, root, user, capsys):
    # В1: chmod (символьная форма идёт через lookup) не обходит x.
    _unsearchable_setup(kernel, root)
    sh = Shell(kernel, user)
    out = _run(sh, capsys, "chmod u+w /root/secret")
    assert "Permission denied" in out


def test_touch_existing_requires_write_or_owner(kernel, root, user, capsys):
    # В1: mtime чужого файла нельзя менять без root/владельца/права W.
    fs = kernel.fs
    fs.create_file("/tmp/root_ro", root, data="x", mode=0o644)
    fs.create_file("/tmp/root_ww", root, data="x", mode=0o666)
    fs.create_file("/tmp/user_ro", user, data="x", mode=0o444)
    for path in ("/tmp/root_ro", "/tmp/root_ww", "/tmp/user_ro"):
        fs.lookup(path, root, "/", "/").mtime = 1.0

    sh = Shell(kernel, user)
    out = _run(sh, capsys, "touch /tmp/root_ro")
    assert "Permission denied" in out
    assert fs.lookup("/tmp/root_ro", root, "/", "/").mtime == 1.0

    out = _run(sh, capsys, "touch /tmp/root_ww")
    assert "Permission denied" not in out
    assert fs.lookup("/tmp/root_ww", root, "/", "/").mtime != 1.0

    out = _run(sh, capsys, "touch /tmp/user_ro")
    assert "Permission denied" not in out
    assert fs.lookup("/tmp/user_ro", root, "/", "/").mtime != 1.0


def test_glob_does_not_leak_unsearchable_directory(kernel, root, user, capsys):
    # В2: glob не раскрывает имена из каталогов без x/r.
    _unsearchable_setup(kernel, root)
    assert kernel.fs.glob("/root/secret/*", user, "/", user.home) == []

    sh = Shell(kernel, user)
    out = _run(sh, capsys, "echo /root/secret/*")
    assert "hidden" not in out
    assert "/root/secret/*" in out  # shell сохраняет литерал


def test_rm_r_checks_nested_permissions(kernel, root, user, capsys):
    # В6: rm -r проверяет всё дерево до удаления, без частичного удаления.
    fs = kernel.fs
    fs.mkdir("/tmp/up", user, "/", user.home, mode=0o755)
    fs.mkdir("/tmp/up/inner", root, "/", "/", mode=0o000)
    fs.create_file("/tmp/up/inner/f", root, "/", "/",
                   data="x", mode=0o000)

    sh = Shell(kernel, user)
    out = _run(sh, capsys, "rm -r /tmp/up")
    assert "Permission denied" in out
    assert fs.lookup("/tmp/up", root, "/", "/").is_dir()
    assert fs.lookup("/tmp/up/inner", root, "/", "/").is_dir()
    assert fs.lookup("/tmp/up/inner/f", root, "/", "/").is_file()


def test_cp_r_checks_source_permissions(kernel, root, user, capsys):
    # В4: cp -r требует R/X на source-каталоге и R на source-файлах.
    fs = kernel.fs
    fs.mkdir("/tmp/private", root, "/", "/", mode=0o711)
    fs.create_file("/tmp/private/secret", root, "/", "/",
                   data="DATALEAK", mode=0o000)
    fs.create_file("/tmp/blocked", root, "/", "/",
                   data="X", mode=0o000)

    sh = Shell(kernel, user)
    out = _run(sh, capsys, "cp -r /tmp/private /tmp/leak")
    assert "Permission denied" in out
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/leak", root, "/", "/")
    assert fs.read_file("/tmp/private/secret", root, "/", "/") == "DATALEAK"

    out = _run(sh, capsys, "cp /tmp/blocked /tmp/leakfile")
    assert "Permission denied" in out
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/leakfile", root, "/", "/")


def test_cp_r_rejects_copy_into_self(kernel, root):
    # В5: копирование каталога в себя/своё поддерево отклоняется, не зависает.
    fs = kernel.fs
    fs.mkdir("/tmp/self", root, mode=0o755)
    fs.create_file("/tmp/self/a", root, data="a", mode=0o644)

    old_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(_stack_depth() + 60)
        with pytest.raises(InvalidArgument, match="into itself"):
            fs.copy_tree("/tmp/self", "/tmp/self/sub", root, "/", "/")
    finally:
        sys.setrecursionlimit(old_limit)
    assert fs.lookup("/tmp/self/a", root, "/", "/").data == "a"


def test_cp_r_rejects_overlapping_recursive_copy(kernel, root, capsys):
    # В5: descendant -> ancestor через mv-подобную семантику cp тоже отклоняется.
    fs = kernel.fs
    fs.mkdir("/tmp/parent", root, mode=0o755)
    fs.mkdir("/tmp/parent/child", root, mode=0o755)
    fs.create_file("/tmp/parent/child/f", root, data="x", mode=0o644)

    sh = Shell(kernel, root)
    old_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(_stack_depth() + 60)
        out = _run(sh, capsys, "cp -r /tmp/parent/child /tmp/parent")
    finally:
        sys.setrecursionlimit(old_limit)
    assert "cannot copy" in out
    assert fs.lookup("/tmp/parent/child/f", root, "/", "/").is_file()


def test_mv_dir_over_file_returns_not_a_directory(kernel, root, capsys):
    # В7: mv каталога поверх файла больше не заменяет файл каталогом.
    fs = kernel.fs
    fs.mkdir("/tmp/srcdir", root, mode=0o755)
    fs.create_file("/tmp/dstfile", root, data="keep", mode=0o644)

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "mv /tmp/srcdir /tmp/dstfile")
    assert "Not a directory" in out
    node = fs.lookup("/tmp/dstfile", root, "/", "/")
    assert node.is_file()
    assert node.data == "keep"
    assert fs.lookup("/tmp/srcdir", root, "/", "/").is_dir()


# ============================================================================
# CHARACTERIZE — inode / hardlink / cp mode (инверсия на этапе 3a)
# ============================================================================


def test_hardlink_metadata_is_per_name_currently(kernel, root, user, capsys):
    # CHARACTERIZE: hardlink делит только FileLink (data/nlink); uid/gid/mode
    # остаются per-name, поэтому новый линк получает владельца-вызывающего при
    # общем inode; fixed in stage 3a (В3).
    fs = kernel.fs
    fs.create_file("/tmp/rf", root, "/", "/",
                   data="ROOTONLY", mode=0o600)

    sh = Shell(kernel, user)
    _run(sh, capsys, "ln /tmp/rf /tmp/link1")

    link = fs.lookup("/tmp/link1", root, "/", "/")
    orig = fs.lookup("/tmp/rf", root, "/", "/")
    assert link.uid == user.uid
    assert link.mode == 0o600
    assert orig.uid == 0
    assert orig.mode == 0o600
    assert link.inode == orig.inode

    # user читает данные root-файла 0600 через свою ссылку
    assert fs.read_file("/tmp/link1", user, "/", user.home) == "ROOTONLY"

    # chmod через ссылку меняет только новое имя
    fs.chmod("/tmp/link1", 0o644, user, "/", user.home)
    assert fs.lookup("/tmp/link1", root, "/", "/").mode == 0o644
    assert fs.lookup("/tmp/rf", root, "/", "/").mode == 0o600

    # stat показывает один inode и разные uid
    out_link = _run(sh, capsys, "stat /tmp/link1")
    out_orig = _run(sh, capsys, "stat /tmp/rf")
    assert f"Inode: {link.inode}" in out_link
    assert f"Inode: {orig.inode}" in out_orig
    assert "Uid: (1000/" in out_link
    assert "Uid: (0/root)" in out_orig


def test_cp_drops_mode_bits_currently(kernel, root, capsys):
    # CHARACTERIZE: cp/cp -r не сохраняют права источника (create_file/mkdir
    # берут дефолт 0644/0755); fixed in stage 3a (В8).
    fs = kernel.fs
    fs.create_file("/tmp/exec", root, data="x", mode=0o755)
    fs.copy_tree("/tmp/exec", "/tmp/exec_cp", root, "/", "/")
    assert fs.lookup("/tmp/exec_cp", root, "/", "/").mode == 0o644

    fs.mkdir("/tmp/sdir", root, mode=0o700)
    fs.create_file("/tmp/sdir/secret", root, data="s", mode=0o600)
    fs.copy_tree("/tmp/sdir", "/tmp/sdir_cp", root, "/", "/")
    assert fs.lookup("/tmp/sdir_cp", root, "/", "/").mode == 0o755
    assert fs.lookup("/tmp/sdir_cp/secret", root, "/", "/").mode == 0o644

    # то же через пользовательскую команду cp
    sh = Shell(kernel, root)
    _run(sh, capsys, "cp /tmp/exec /tmp/exec_cp2")
    assert fs.lookup("/tmp/exec_cp2", root, "/", "/").mode == 0o644


# ============================================================================
# CHARACTERIZE — symlink и краевые случаи (инверсия на этапе 3b)
# ============================================================================


def test_ls_l_explicit_symlink_follows_currently(kernel, root, capsys):
    # CHARACTERIZE: ls -l/stat по явному symlink пути дереференсят цель вместо
    # lstat; fixed in stage 3b (В9).
    fs = kernel.fs
    fs.create_file("/tmp/t", root, data="TT", mode=0o644)
    fs.symlink("/tmp/t", "/tmp/lnk", root, "/", "/")

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "ls -l /tmp/lnk")
    assert "lnk" not in out
    assert "->" not in out

    out = _run(sh, capsys, "stat /tmp/lnk")
    assert "File: /tmp/t" in out
    assert "File: /tmp/lnk" not in out


def test_ls_l_dangling_symlink_errors_currently(kernel, root, capsys):
    # CHARACTERIZE: ls -l по явному висячему symlink падает вместо показа
    # ссылки; при этом листинг каталога показывает её корректно; fixed in
    # stage 3b (В10a).
    fs = kernel.fs
    fs.symlink("/tmp/nope", "/tmp/dang", root, "/", "/")

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "ls -l /tmp/dang")
    assert "cannot access" in out
    assert "No such file or directory" in out

    out = _run(sh, capsys, "ls -l /tmp")
    assert "dang" in out
    assert "-> /tmp/nope" in out


def test_redirect_to_dangling_symlink_fails_currently(kernel, root, capsys):
    # CHARACTERIZE: запись через висячий symlink падает с FileExists вместо
    # создания цели; touch ведёт себя так же; fixed in stage 3b (В10b).
    fs = kernel.fs
    fs.symlink("/tmp/nope", "/tmp/dang", root, "/", "/")

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "echo X > /tmp/dang")
    assert "File exists" in out
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/nope", root, "/", "/")
    assert fs.lookup("/tmp/dang", "/", "/", follow=False,
                     user=root).is_link()

    out = _run(sh, capsys, "touch /tmp/dang")
    assert "File exists" in out


def test_chmod_invalid_numeric_internal_error_currently(kernel, root, capsys):
    # CHARACTERIZE: числовой chmod с невалидной восьмеричной строкой роняет
    # ValueError наружу как internal error; fixed in stage 3b (В11).
    fs = kernel.fs
    fs.create_file("/tmp/f", root, data="x", mode=0o644)

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "chmod 999 /tmp/f")
    assert "internal error" in out
    assert "invalid literal for int() with base 8: '999'" in out


def test_tilde_user_wrong_path_currently(kernel, root, user, capsys):
    # CHARACTERIZE: ~user нормализуется как home + "user"; fixed in stage 3b
    # (В12).
    fs = kernel.fs
    assert fs.normalize("~bob", "/", user.home) == "/home/userbob"

    sh = Shell(kernel, user)
    out = _run(sh, capsys, "cd ~bob")
    assert "/home/userbob" in out


def test_trailing_slash_file_accepted_currently(kernel, root, capsys):
    # CHARACTERIZE: завершающий слэш на обычном файле принимается (normalize
    # убирает пустые компоненты); fixed in stage 3b (В13).
    fs = kernel.fs
    fs.create_file("/tmp/f", root, data="payload", mode=0o644)

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "cat /tmp/f/")
    assert "payload" in out

    out = _run(sh, capsys, "stat /tmp/f/")
    assert "/tmp/f" in out

    _run(sh, capsys, "rm /tmp/f/")
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/f", root, "/", "/")


def test_glob_returns_absolute_paths_currently(kernel, root, user, capsys):
    # CHARACTERIZE: glob возвращает абсолютные пути и `*/` матчит файлы;
    # fixed in stage 3b (В14).
    fs = kernel.fs
    fs.mkdir("/tmp/g", root, mode=0o755)
    fs.create_file("/tmp/g/a", root, data="1", mode=0o644)
    fs.create_file("/tmp/g/b", root, data="2", mode=0o644)
    fs.mkdir("/tmp/g/dir1", root, mode=0o755)

    sh = Shell(kernel, user)
    sh.handle_line("cd /tmp/g")
    out = _run(sh, capsys, "echo *")
    assert out.strip() == "/tmp/g/a /tmp/g/b /tmp/g/dir1"

    out = _run(sh, capsys, "echo /tmp/g/*/")
    # слэш не отсекает файлы: и `a`, и `dir1` попали в раскрытие
    assert "/tmp/g/a" in out
    assert "/tmp/g/dir1" in out


# ============================================================================
# DOCUMENTED LIMIT — регресс-страховка, НЕ инвертировать
# ============================================================================


def test_sticky_tmp_not_enforced_currently(kernel, root, user, capsys):
    # DOCUMENTED LIMIT: sticky-бит /tmp (1777) не enforced — rm чужого файла
    # проходит при праве w на каталоге; README:43-45, 512-513.
    fs = kernel.fs
    fs.create_file("/tmp/root_file", root, "/", "/",
                   data="root", mode=0o644)

    sh = Shell(kernel, user)
    out = _run(sh, capsys, "rm /tmp/root_file")
    assert "Permission denied" not in out
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/root_file", root, "/", "/")


def test_save_load_hardlinks_independent_currently(kernel, root):
    # DOCUMENTED LIMIT: save/load не сохраняют hardlinks и номера inode; после
    # load ссылки становятся независимыми файлами; README:507-508.
    fs = kernel.fs
    fs.create_file("/tmp/h1", root, "/", "/", data="AAA", mode=0o644)
    fs.link("/tmp/h1", "/tmp/h2", root, "/", "/")

    tmpname = _save(kernel, root)
    try:
        _load(kernel, root, tmpname)
        fs.write_file("/tmp/h1", "ZZZ", root, "/", "/")
        assert fs.read_file("/tmp/h1", root, "/", "/") == "ZZZ"
        assert fs.read_file("/tmp/h2", root, "/", "/") == "AAA"
        n1 = fs.lookup("/tmp/h1", root, "/", "/")
        n2 = fs.lookup("/tmp/h2", root, "/", "/")
        assert n1.nlink == 1
        assert n2.nlink == 1
        assert n1.inode != n2.inode
    finally:
        os.unlink(tmpname)


def test_save_load_times_lost_currently(kernel, root):
    # DOCUMENTED LIMIT: снапшот не сохраняет времена файлов; после load они
    # пересоздаются; README:507-508.
    fs = kernel.fs
    fs.create_file("/tmp/t", root, "/", "/", data="t", mode=0o644)
    node = fs.lookup("/tmp/t", root, "/", "/")
    node.atime = 1.0
    node.mtime = 1.0
    node.ctime = 1.0

    tmpname = _save(kernel, root)
    try:
        _load(kernel, root, tmpname)
        restored = fs.lookup("/tmp/t", root, "/", "/")
        assert restored.mtime != 1.0
    finally:
        os.unlink(tmpname)


def test_fs_text_only_currently(kernel, root):
    # DOCUMENTED LIMIT: ФС текстовая (binary-safe — ось расширения, не баг).
    # Фиксируем только то, что data хранится как str, а size считается по
    # UTF-8; потерю бинарных данных НЕ ассертим.
    fs = kernel.fs
    payload = "h\u00e9llo"
    fs.create_file("/tmp/txt", root, "/", "/", data=payload, mode=0o644)
    node = fs.lookup("/tmp/txt", root, "/", "/")
    assert isinstance(node.data, str)
    assert node.size == len(payload.encode("utf-8"))


def test_setuid_setgid_not_enforced_currently(kernel, root, capsys):
    # DOCUMENTED LIMIT / feature-gap: setuid/setgid хранятся в mode & 0o7777 и
    # видны в stat как %04o, но perm_string показывает только 9 бит rwx и флаг
    # не enforced. README не заявляет setuid/setgid, это не баг по контракту.
    fs = kernel.fs
    fs.create_file("/tmp/s", root, data="s", mode=0o755)
    fs.chmod("/tmp/s", 0o4755, root, "/", "/")
    node = fs.lookup("/tmp/s", root, "/", "/")
    assert node.mode == 0o4755
    assert node.perm_string() == "-rwxr-xr-x"

    sh = Shell(kernel, root)
    out = _run(sh, capsys, "stat /tmp/s")
    assert "4755" in out


# ============================================================================
# корректное поведение — guard, чтобы этапы 2/3 не сломали молча
# ============================================================================


def test_ls_and_cat_enforce_intermediate_x_guard(kernel, root, user, capsys):
    fs = kernel.fs
    fs.mkdir("/root/secret", root, mode=0o755)
    fs.create_file("/root/secret/hidden", root, data="s", mode=0o644)

    sh = Shell(kernel, user)
    assert "Permission denied" in _run(sh, capsys, "ls /root/secret")
    assert "Permission denied" in _run(sh, capsys, "cat /root/secret/hidden")


def test_glob_hidden_files_not_matched_guard(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root, mode=0o755)
    fs.create_file("/tmp/g/.hidden", root, data="h", mode=0o644)
    fs.create_file("/tmp/g/visible", root, data="v", mode=0o644)
    assert fs.glob("/tmp/g/*", root) == ["/tmp/g/visible"]
    assert fs.glob("/tmp/g/.*", root) == ["/tmp/g/.hidden"]


def test_glob_no_match_stays_literal_guard(kernel, root, capsys):
    sh = Shell(kernel, root)
    out = _run(sh, capsys, "echo /tmp/zzz_nomatch*")
    assert "/tmp/zzz_nomatch*" in out


def test_symlink_cycle_clear_error_guard(kernel, root):
    fs = kernel.fs
    fs.symlink("/b", "/a", root, "/", "/")
    fs.symlink("/a", "/b", root, "/", "/")
    with pytest.raises(InvalidArgument, match="too many levels"):
        fs.read_file("/a", root, "/", "/")


def test_hardlink_to_directory_rejected_guard(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/dir", root, mode=0o755)
    with pytest.raises(PermissionDenied):
        fs.link("/tmp/dir", "/tmp/dir2", root, "/", "/")


def test_rm_symlink_removes_link_not_target_guard(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/target", root, "/", "/", data="keep", mode=0o644)
    fs.symlink("/tmp/target", "/tmp/l", root, "/", "/")
    fs.unlink("/tmp/l", root, "/", "/")
    assert fs.read_file("/tmp/target", root, "/", "/") == "keep"
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/l", root, "/", "/")


def test_unlink_decrements_nlink_guard(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/h1", root, "/", "/", data="A", mode=0o644)
    fs.link("/tmp/h1", "/tmp/h2", root, "/", "/")
    assert fs.lookup("/tmp/h1", root, "/", "/").nlink == 2
    fs.unlink("/tmp/h1", root, "/", "/")
    assert fs.lookup("/tmp/h2", root, "/", "/").nlink == 1


def test_readlink_returns_target_guard(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/t", root, "/", "/", data="t", mode=0o644)
    fs.symlink("/tmp/t", "/tmp/l", root, "/", "/")
    assert fs.readlink("/tmp/l", root, "/", "/") == "/tmp/t"


def test_cd_file_trailing_slash_not_a_directory_guard(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/f", root, data="x", mode=0o644)
    sh = Shell(kernel, root)
    assert "Not a directory" in _run(sh, capsys, "cd /tmp/f/")
    assert "Not a directory" in _run(sh, capsys, "cd /tmp/f")


def test_chmod_symbolic_invalid_mode_validated_guard(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/f", root, data="x", mode=0o644)
    sh = Shell(kernel, root)
    out = _run(sh, capsys, "chmod z+x /tmp/f")
    assert "invalid mode: 'z+x'" in out

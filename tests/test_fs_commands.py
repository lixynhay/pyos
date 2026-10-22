"""Интеграционные тесты файловых команд: ls, cat, touch, rm, mv, chmod, chown, stat, pwd, cd, rmdir."""

import pytest

from pyos.errors import NoSuchFileOrDirectory
from pyos.shell import Shell


# ============================================================================
# ls
# ============================================================================


def test_ls_empty_dir(kernel, root, capsys):
    kernel.fs.mkdir("/tmp/empty_dir", root)
    Shell(kernel, root).handle_line("ls /tmp/empty_dir")
    assert capsys.readouterr().out.strip() == ""


def test_ls_root_shows_dirs(kernel, root, capsys):
    Shell(kernel, root).handle_line("ls /")
    out = capsys.readouterr().out.strip()
    assert "home" in out
    assert "root" in out
    assert "tmp" in out


def test_ls_with_files(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/alpha.txt", root, data="a")
    fs.create_file("/tmp/beta.txt", root, data="b")
    Shell(kernel, root).handle_line("ls /tmp")
    out = capsys.readouterr().out.strip()
    assert "alpha.txt" in out
    assert "beta.txt" in out


def test_ls_hidden(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/.hidden", root, data="secret")
    Shell(kernel, root).handle_line("ls /tmp")
    out = capsys.readouterr().out.strip()
    assert ".hidden" not in out  # -a needed


def test_ls_all(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/.hidden", root, data="secret")
    Shell(kernel, root).handle_line("ls -a /tmp")
    out = capsys.readouterr().out.strip()
    assert ".hidden" in out


def test_ls_long(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/myfile.txt", root, data="content")
    Shell(kernel, root).handle_line("ls -l /tmp/myfile.txt")
    out = capsys.readouterr().out.strip()
    assert "myfile.txt" in out
    assert "-" in out  # permission string


def test_ls_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("ls /nonexistent_path_xyz")
    captured = capsys.readouterr()
    assert "No such" in captured.out + captured.err


# ============================================================================
# cat
# ============================================================================


def test_cat_file(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/hello.txt", root, data="Hello World")
    Shell(kernel, root).handle_line("cat /tmp/hello.txt")
    assert capsys.readouterr().out.strip() == "Hello World"


def test_cat_multiple_files(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/a.txt", root, data="AAA")
    fs.create_file("/tmp/b.txt", root, data="BBB")
    Shell(kernel, root).handle_line("cat /tmp/a.txt /tmp/b.txt")
    assert capsys.readouterr().out.strip() == "AAABBB"


def test_cat_empty_file(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/empty.txt", root, data="")
    Shell(kernel, root).handle_line("cat /tmp/empty.txt")
    assert capsys.readouterr().out.strip() == ""


def test_cat_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /tmp/no_such_file")
    assert capsys.readouterr().err.strip()


def test_cat_stdin(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo piped_data | cat")
    assert capsys.readouterr().out.strip() == "piped_data"


# ============================================================================
# touch
# ============================================================================


def test_touch_creates_file(kernel, root, capsys):
    Shell(kernel, root).handle_line("touch /tmp/newfile.txt")
    node = kernel.fs.lookup("/tmp/newfile.txt", root)
    assert node.is_file()
    assert node.data == ""


def test_touch_existing(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/existing.txt", root, data="content")
    Shell(kernel, root).handle_line("touch /tmp/existing.txt")
    node = kernel.fs.lookup("/tmp/existing.txt", root)
    assert node.data == "content"  # content preserved, mtime updated


def test_touch_multiple(kernel, root):
    Shell(kernel, root).handle_line("touch /tmp/a.txt /tmp/b.txt /tmp/c.txt")
    assert kernel.fs.lookup("/tmp/a.txt", root).is_file()
    assert kernel.fs.lookup("/tmp/b.txt", root).is_file()
    assert kernel.fs.lookup("/tmp/c.txt", root).is_file()


# ============================================================================
# rm
# ============================================================================


def test_rm_file(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/delfile.txt", root, data="x")
    Shell(kernel, root).handle_line("rm /tmp/delfile.txt")
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/delfile.txt", root)


def test_rm_multiple(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/del1.txt", root, data="1")
    fs.create_file("/tmp/del2.txt", root, data="2")
    Shell(kernel, root).handle_line("rm /tmp/del1.txt /tmp/del2.txt")
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/del1.txt", root)
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/del2.txt", root)


def test_rm_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("rm /tmp/nonexistent")
    captured = capsys.readouterr()
    assert "No such file" in captured.out + captured.err


def test_rm_recursive(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/rmdir_test", root)
    fs.create_file("/tmp/rmdir_test/inside.txt", root, data="x")
    Shell(kernel, root).handle_line("rm -r /tmp/rmdir_test")
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/rmdir_test", root)


# ============================================================================
# rmdir
# ============================================================================


def test_rmdir_empty(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/emptydir", root)
    Shell(kernel, root).handle_line("rmdir /tmp/emptydir")
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/emptydir", root)


def test_rmdir_not_empty_fails(kernel, root, capsys):
    fs = kernel.fs
    fs.mkdir("/tmp/notempty", root)
    fs.create_file("/tmp/notempty/x.txt", root, data="x")
    Shell(kernel, root).handle_line("rmdir /tmp/notempty")
    assert capsys.readouterr().err.strip()


def test_rmdir_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("rmdir /tmp/no_such_dir")
    assert capsys.readouterr().err.strip()


# ============================================================================
# mv
# ============================================================================


def test_mv_rename_file(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/oldname.txt", root, data="content")
    Shell(kernel, root).handle_line("mv /tmp/oldname.txt /tmp/newname.txt")
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/oldname.txt", root)
    assert fs.read_file("/tmp/newname.txt", root) == "content"


def test_mv_into_dir(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/move_me.txt", root, data="data")
    fs.mkdir("/tmp/destdir", root)
    Shell(kernel, root).handle_line("mv /tmp/move_me.txt /tmp/destdir/")
    assert fs.read_file("/tmp/destdir/move_me.txt", root) == "data"


def test_mv_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("mv /tmp/nope /tmp/nowhere")
    assert capsys.readouterr().err.strip()


# ============================================================================
# chmod
# ============================================================================


def test_chmod_numeric(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/chmod_test.txt", root, data="x", mode=0o644)
    Shell(kernel, root).handle_line("chmod 755 /tmp/chmod_test.txt")
    node = fs.lookup("/tmp/chmod_test.txt", root)
    assert node.mode == 0o755


def test_chmod_symbolic(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/chmod_sym.txt", root, data="x", mode=0o644)
    Shell(kernel, root).handle_line("chmod +x /tmp/chmod_sym.txt")
    node = fs.lookup("/tmp/chmod_sym.txt", root)
    assert node.mode & 0o111  # at least one execute bit set


def test_chmod_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("chmod 755 /tmp/no_such_file")
    assert capsys.readouterr().err.strip()


# ============================================================================
# chown
# ============================================================================


def test_chown_user(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/chown_test.txt", root, data="x")
    Shell(kernel, root).handle_line("chown user /tmp/chown_test.txt")
    node = fs.lookup("/tmp/chown_test.txt", root)
    assert node.uid == 1000


def test_chown_user_group(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/chown_ug.txt", root, data="x")
    Shell(kernel, root).handle_line("chown user:users /tmp/chown_ug.txt")
    node = fs.lookup("/tmp/chown_ug.txt", root)
    assert node.uid == 1000
    assert node.gid == 100


def test_chown_non_root_fails(kernel, root, user, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/chown_restrict.txt", root, data="x")
    Shell(kernel, user).handle_line("chown guest /tmp/chown_restrict.txt")
    assert "Permission denied" in capsys.readouterr().err
    # владелец не изменился
    assert fs.lookup("/tmp/chown_restrict.txt", root).uid == 0


# ============================================================================
# stat
# ============================================================================


def test_stat_file(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/stat_test.txt", root, data="hello")
    Shell(kernel, root).handle_line("stat /tmp/stat_test.txt")
    out = capsys.readouterr().out.strip()
    assert "stat_test.txt" in out
    assert "File" in out


def test_stat_dir(kernel, root, capsys):
    Shell(kernel, root).handle_line("stat /tmp")
    out = capsys.readouterr().out.strip()
    assert "tmp" in out
    assert "directory" in out.lower() or "File" in out


def test_stat_non_existent(kernel, root, capsys):
    Shell(kernel, root).handle_line("stat /tmp/no_such_file")
    assert capsys.readouterr().err.strip()


# ============================================================================
# pwd
# ============================================================================


def test_pwd(kernel, root, capsys):
    Shell(kernel, root).handle_line("pwd")
    assert capsys.readouterr().out.strip() == "/root"


def test_pwd_after_cd(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("cd /tmp")
    sh.handle_line("pwd")
    assert capsys.readouterr().out.strip() == "/tmp"


# ============================================================================
# cd
# ============================================================================


def test_cd_absolute(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("cd /tmp")
    assert sh.cwd == "/tmp"


def test_cd_relative(kernel, root):
    fs = kernel.fs
    fs.mkdir("/root/subdir", root)
    sh = Shell(kernel, root)
    sh.handle_line("cd subdir")
    assert sh.cwd == "/root/subdir"


def test_cd_home(kernel, root):
    sh = Shell(kernel, root)
    sh.handle_line("cd /tmp")
    sh.handle_line("cd")
    assert sh.cwd == "/root"


def test_cd_non_existent(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("cd /nonexistent_dir_xyz")
    assert "No such file" in capsys.readouterr().err


# ============================================================================
# Команды зарегистрированы
# ============================================================================


def test_ls_registered():
    from pyos.commands import get
    assert get("ls") is not None


def test_cat_registered():
    from pyos.commands import get
    assert get("cat") is not None


def test_touch_registered():
    from pyos.commands import get
    assert get("touch") is not None


def test_rm_registered():
    from pyos.commands import get
    assert get("rm") is not None


def test_rmdir_registered():
    from pyos.commands import get
    assert get("rmdir") is not None


def test_mv_registered():
    from pyos.commands import get
    assert get("mv") is not None


def test_chmod_registered():
    from pyos.commands import get
    assert get("chmod") is not None


def test_chown_registered():
    from pyos.commands import get
    assert get("chown") is not None


def test_stat_registered():
    from pyos.commands import get
    assert get("stat") is not None


def test_pwd_registered():
    from pyos.commands import get
    assert get("pwd") is not None


def test_cd_registered():
    from pyos.commands import get
    assert get("cd") is not None
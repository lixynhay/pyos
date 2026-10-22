"""Тесты виртуальной файловой системы."""

import pytest

from pyos.errors import (
    DirectoryNotEmpty,
    FileExists,
    NoSuchFileOrDirectory,
    PermissionDenied,
)


def test_create_read_write(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/a.txt", root, data="hello")
    assert fs.read_file("/tmp/a.txt", root) == "hello"
    fs.write_file("/tmp/a.txt", "world", root)
    assert fs.read_file("/tmp/a.txt", root) == "world"
    fs.write_file("/tmp/a.txt", "!", root, append=True)
    assert fs.read_file("/tmp/a.txt", root) == "world!"


def test_mkdir_parents(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/a/b/c", root, parents=True)
    assert fs.lookup("/tmp/a/b/c", root).is_dir()
    with pytest.raises(FileExists):
        fs.mkdir("/tmp/a/b/c", root)


def test_permission_denied_for_regular_user(kernel, root, user):
    fs = kernel.fs
    # /root имеет режим 700 и принадлежит root
    fs.create_file("/root/secret", root, data="top secret")
    with pytest.raises(PermissionDenied):
        fs.read_file("/root/secret", user)


def test_symlink_follow(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/target.txt", root, data="data")
    fs.symlink("/tmp/target.txt", "/tmp/link.txt", root)
    assert fs.read_file("/tmp/link.txt", root) == "data"
    assert fs.readlink("/tmp/link.txt", root) == "/tmp/target.txt"


def test_unlink_directory_not_empty(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/d", root)
    fs.create_file("/tmp/d/f", root)
    with pytest.raises(DirectoryNotEmpty):
        fs.rmdir("/tmp/d", root)


# --- жёсткие ссылки (общий inode через FileLink) --------------------------

def test_hardlink_shares_data(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/h1", root, data="AAA")
    fs.link("/tmp/h1", "/tmp/h2", root)
    fs.write_file("/tmp/h1", "BBB", root)
    # изменение через одну ссылку видно через другую
    assert fs.read_file("/tmp/h2", root) == "BBB"


def test_hardlink_nlink_counter(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/h1", root, data="AAA")
    fs.link("/tmp/h1", "/tmp/h2", root)
    assert fs.lookup("/tmp/h1", root).nlink == 2
    assert fs.lookup("/tmp/h2", root).nlink == 2
    assert fs.lookup("/tmp/h1", root).inode == fs.lookup("/tmp/h2", root).inode


def test_unlink_one_hardlink_keeps_other(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/h1", root, data="AAA")
    fs.link("/tmp/h1", "/tmp/h2", root)
    fs.unlink("/tmp/h1", root)
    assert fs.read_file("/tmp/h2", root) == "AAA"
    assert fs.lookup("/tmp/h2", root).nlink == 1
    with pytest.raises(NoSuchFileOrDirectory):
        fs.lookup("/tmp/h1", root)


def test_rename_into_own_subtree_rejected(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/mv", root)
    fs.mkdir("/tmp/mv/inner", root)
    from pyos.errors import InvalidArgument
    with pytest.raises(InvalidArgument):
        fs.rename("/tmp/mv", "/tmp/mv/inner", root)


def test_copy_recursive(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/src", root)
    fs.create_file("/tmp/src/a.txt", root, data="x")
    fs.mkdir("/tmp/src/sub", root)
    fs.create_file("/tmp/src/sub/b.txt", root, data="y")
    fs.copy_tree("/tmp/src", "/tmp/dst", root)
    assert fs.read_file("/tmp/dst/a.txt", root) == "x"
    assert fs.read_file("/tmp/dst/sub/b.txt", root) == "y"


# --- глоббинг --------------------------------------------------------------

def test_glob_star(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root)
    fs.create_file("/tmp/g/a.py", root, data="1")
    fs.create_file("/tmp/g/b.py", root, data="2")
    fs.create_file("/tmp/g/c.txt", root, data="3")
    assert fs.glob("/tmp/g/*.py", root) == ["/tmp/g/a.py", "/tmp/g/b.py"]


def test_glob_question_mark(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root)
    fs.create_file("/tmp/g/a1", root)
    fs.create_file("/tmp/g/a2", root)
    fs.create_file("/tmp/g/a33", root)
    assert fs.glob("/tmp/g/a?", root) == ["/tmp/g/a1", "/tmp/g/a2"]


def test_glob_hidden_not_matched_by_star(kernel, root):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root)
    fs.create_file("/tmp/g/.hidden", root)
    fs.create_file("/tmp/g/visible", root)
    assert fs.glob("/tmp/g/*", root) == ["/tmp/g/visible"]

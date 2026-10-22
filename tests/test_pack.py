"""Tests for pkg pack: build .pkg.json files from virtual directories."""

import json

import pytest

from pyos.context import Context
from pyos.errors import InvalidArgument, NoSuchFileOrDirectory, PermissionDenied
from pyos.fs import File
from pyos.shell import Shell


def _ctx(kernel, user):
    return Context(kernel, user, "/root", shell=Shell(kernel, user))


def _mkdir(kernel, user, path):
    kernel.fs.mkdir(path, user, "/", "/", parents=True)


def _write(kernel, user, path, content, mode=None):
    parent = path.rsplit("/", 1)[0] or "/"
    _mkdir(kernel, user, parent)
    kernel.fs.write_file(path, content, user, "/", "/", create=True)
    if mode is not None:
        kernel.fs.chmod(path, mode, user, "/", "/")


def _pack(ctx, root_dir, output, name="demo", version="1.0.0", extra=None):
    from pyos.commands.package_cmds import cmd_pkg

    args = ["pack", root_dir, output, "--name", name, "--version", version]
    args.extend(extra or [])
    return cmd_pkg(ctx, args, "")


def _read_package(kernel, root, path):
    return json.loads(kernel.fs.read_file(path, root, "/", "/"))


def test_pack_creates_installable_package(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/hello-root/usr/bin/hello", "echo hello\n", 0o755)
    output = "/var/cache/packages/hello-1.0.0-1-any.pkg.json"

    result = _pack(
        ctx, "/tmp/hello-root", output, "hello", "1.0.0",
        ["--release", "1", "--arch", "any", "--description", "Hello utility"])

    assert "created" in result
    data = _read_package(kernel, root, output)
    assert data["format"] == 1
    assert data["name"] == "hello"
    assert data["version"] == "1.0.0"
    assert data["release"] == 1
    assert data["arch"] == "any"
    assert data["description"] == "Hello utility"
    assert [entry["path"] for entry in data["files"]] == ["/usr/bin/hello"]

    installed = cmd_pkg(ctx, ["install", output], "")
    assert "installed" in installed
    assert kernel.fs.read_file("/usr/bin/hello", root, "/", "/") == "echo hello\n"


def test_pack_preserves_mode_owner_group(kernel, root, user):
    ctx = _ctx(kernel, root)
    path = "/tmp/owned-root/usr/bin/tool"
    _write(kernel, root, path, "tool\n", 0o750)
    kernel.fs.chown(
        path, user.uid, kernel.users.group_id("users"), root, "/", "/")

    output = "/tmp/owned.pkg.json"
    _pack(ctx, "/tmp/owned-root", output, "owned", "1.0.0")

    data = _read_package(kernel, root, output)
    entry = data["files"][0]
    assert entry["path"] == "/usr/bin/tool"
    assert entry["mode"] == "0750"
    assert entry["owner"] == "user"
    assert entry["group"] == "users"
    assert entry["encoding"] == "utf-8"
    assert entry["content"] == "tool\n"


def test_pack_rejects_symlink(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/link-root/real.txt", "real\n")
    kernel.fs.symlink(
        "/tmp/link-root/real.txt", "/tmp/link-root/link.txt", root, "/", "/")

    with pytest.raises(InvalidArgument, match="symlink"):
        _pack(ctx, "/tmp/link-root", "/tmp/link.pkg.json")


def test_pack_rejects_non_utf8_content(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/bad-root/bad.txt", "\udcff")

    with pytest.raises(InvalidArgument, match="utf-8"):
        _pack(ctx, "/tmp/bad-root", "/tmp/bad.pkg.json")


def test_pack_rejects_missing_root_dir(kernel, root):
    ctx = _ctx(kernel, root)

    with pytest.raises(InvalidArgument, match="not found"):
        _pack(ctx, "/tmp/does-not-exist", "/tmp/missing.pkg.json")


def test_pack_rejects_root_dir_not_directory(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/not-a-directory", "x")

    with pytest.raises(InvalidArgument, match="not a directory"):
        _pack(ctx, "/tmp/not-a-directory", "/tmp/notdir.pkg.json")


def test_pack_rejects_invalid_output_path(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/out-root/file.txt", "x")
    _mkdir(kernel, root, "/tmp/output-dir")

    with pytest.raises(InvalidArgument, match="absolute"):
        _pack(ctx, "/tmp/out-root", "out.pkg.json")

    with pytest.raises(InvalidArgument, match="directory"):
        _pack(ctx, "/tmp/out-root", "/tmp/output-dir")

    with pytest.raises(InvalidArgument, match="traversal"):
        _pack(ctx, "/tmp/out-root", "/tmp/../out.pkg.json")


def test_pack_requires_name_and_version(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/meta-root/file.txt", "x")

    with pytest.raises(InvalidArgument, match="name"):
        cmd_pkg(
            ctx, ["pack", "/tmp/meta-root", "/tmp/meta.pkg.json",
                  "--version", "1.0.0"], "")
    with pytest.raises(InvalidArgument, match="version"):
        cmd_pkg(
            ctx, ["pack", "/tmp/meta-root", "/tmp/meta.pkg.json",
                  "--name", "meta"], "")


def test_pack_supports_multiple_depends(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/dep-root/app", "app\n")

    _pack(
        ctx, "/tmp/dep-root", "/tmp/dep.pkg.json", "dep", "1.0.0",
        ["--depends", "libc", "--depends", "zlib"])

    data = _read_package(kernel, root, "/tmp/dep.pkg.json")
    assert data["depends"] == ["libc", "zlib"]


def test_pack_then_install_roundtrip(kernel, root, user):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    files = {
        "/tmp/round-root/usr/bin/hello": (0o755, "echo hello\n", user.uid, 100),
        "/tmp/round-root/usr/share/hello.txt": (0o640, "hello\n", 0, 0),
    }
    for path, (mode, content, uid, gid) in files.items():
        _write(kernel, root, path, content, mode)
        kernel.fs.chown(path, uid, gid, root, "/", "/")

    output = "/tmp/round.pkg.json"
    _pack(ctx, "/tmp/round-root", output, "round", "1.0.0")
    cmd_pkg(ctx, ["install", output], "")

    for source, (mode, content, uid, gid) in files.items():
        target = source[len("/tmp/round-root"):]
        node = kernel.fs.lookup(target, root, "/", "/")
        assert node.data == content
        assert node.mode == mode
        assert node.uid == uid
        assert node.gid == gid


def test_pack_then_remove_roundtrip(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/remove-root/usr/bin/hello", "hello\n", 0o755)

    output = "/tmp/remove.pkg.json"
    _pack(ctx, "/tmp/remove-root", output, "remove", "1.0.0")
    cmd_pkg(ctx, ["install", output], "")
    result = cmd_pkg(ctx, ["remove", "remove"], "")

    assert "removed" in result
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/bin/hello", root, "/", "/")


def test_pack_dry_run_does_not_write(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/dry-root/usr/bin/hello", "hello\n")
    output = "/var/cache/packages/dry.pkg.json"

    result = _pack(
        ctx, "/tmp/dry-root", output, "dry", "1.0.0", ["--dry-run"])

    assert "would create" in result
    assert "/usr/bin/hello" in result
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup(output, root, "/", "/")
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/var/cache/packages", root, "/", "/")


def test_pack_does_not_execute_package_content(kernel, root):
    ctx = _ctx(kernel, root)
    _write(
        kernel, root, "/tmp/exec-root/script.sh",
        "touch /tmp/pack-was-executed\n")

    _pack(ctx, "/tmp/exec-root", "/tmp/exec.pkg.json")

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/tmp/pack-was-executed", root, "/", "/")


def test_pack_rejects_path_traversal_in_generated_paths(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/traversal-root/safe.txt", "x")
    root_node = kernel.fs.lookup(
        "/tmp/traversal-root", "/", "/", user=root)
    bad = File("..", uid=0, gid=0, mode=0o644, data="x")
    bad.parent = root_node
    root_node.children[".."] = bad

    with pytest.raises(InvalidArgument, match="unsafe|traversal"):
        _pack(ctx, "/tmp/traversal-root", "/tmp/traversal.pkg.json")


def test_pack_output_matches_pkg_install_format(kernel, root):
    from pyos.commands.package_cmds import _validate_pkg

    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/format-root/usr/bin/tool", "tool\n", 0o755)
    output = "/tmp/format.pkg.json"

    _pack(
        ctx, "/tmp/format-root", output, "format", "1.0.0",
        ["--release", "2", "--arch", "x86_64", "--license", "MIT"])

    data = _read_package(kernel, root, output)
    assert data["format"] == 1
    assert data["release"] == 2
    assert data["arch"] == "x86_64"
    assert data["license"] == "MIT"
    assert data["provides"] == []
    assert data["conflicts"] == []
    _validate_pkg(ctx, data, output)


def test_pack_requires_root(kernel, root, user):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, user)
    _write(kernel, root, "/tmp/root-only-root/file.txt", "x")

    with pytest.raises(PermissionDenied):
        cmd_pkg(
            ctx, ["pack", "/tmp/root-only-root", "/tmp/root-only.pkg.json",
                  "--name", "root-only", "--version", "1.0.0"], "")


def test_pack_excludes_existing_output_inside_root(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/self-root/a.txt", "a\n")
    output = "/tmp/self-root/self.pkg.json"

    _pack(ctx, "/tmp/self-root", output)
    _pack(ctx, "/tmp/self-root", output)

    data = _read_package(kernel, root, output)
    assert [entry["path"] for entry in data["files"]] == ["/a.txt"]


def test_pack_rejects_hardlink(kernel, root):
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/hard-root/a.txt", "x")
    kernel.fs.link("/tmp/hard-root/a.txt", "/tmp/hard-root/b.txt", root, "/", "/")

    with pytest.raises(InvalidArgument, match="hardlink"):
        _pack(ctx, "/tmp/hard-root", "/tmp/hard.pkg.json")


def test_pkg_pack_still_detects_nlink_gt_1(kernel, root):
    # После переезда uid/gid/mode в общий FileLink nlink по-прежнему > 1.
    ctx = _ctx(kernel, root)
    _write(kernel, root, "/tmp/nlink-root/a.txt", "x")
    kernel.fs.link("/tmp/nlink-root/a.txt", "/tmp/nlink-root/b.txt", root, "/", "/")

    assert kernel.fs.lookup("/tmp/nlink-root/a.txt", root).nlink == 2
    with pytest.raises(InvalidArgument, match="hardlink"):
        _pack(ctx, "/tmp/nlink-root", "/tmp/nlink.pkg.json")



def test_pack_rejects_special_file(kernel, root):
    ctx = _ctx(kernel, root)
    _mkdir(kernel, root, "/tmp/special-root")
    kernel.fs.create_virtual_file(
        "/tmp/special-root/device", read_provider=lambda: "x",
        user=root, cwd="/", home="/")

    with pytest.raises(InvalidArgument, match="special"):
        _pack(ctx, "/tmp/special-root", "/tmp/special.pkg.json")

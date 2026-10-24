"""Тесты пакетного менеджера pyOS: makepkg и pkg."""

import json
import os
import tempfile

import pytest

from pyos.errors import (
    InvalidArgument,
    NoSuchFileOrDirectory,
    PermissionDenied,
    PyOSError,
)
from pyos.shell import Shell


# -- PKGBUILD ---------------------------------------------------------------

GOOD_PKGBUILD = """\
pkgname=hello
pkgver=1.0.0
pkgrel=1
pkgdesc="A friendly greeting"
arch=("all")
depends=()

build() {
    mkdir -p /tmp/build/hello
    echo "Hello World" > /tmp/build/hello/readme.txt
}

package() {
    mkdir -p "$pkgdir/usr/bin"
    mkdir -p "$pkgdir/usr/share"
    cp /tmp/build/hello/readme.txt "$pkgdir/usr/share/hello.txt"
    echo "#!/bin/sh" > "$pkgdir/usr/bin/hello"
    echo "echo Hello World" >> "$pkgdir/usr/bin/hello"
}
"""


def test_parse_pkgbuild():
    """Парсинг PKGBUILD возвращает корректные переменные."""
    from pyos.commands.package_cmds import _parse_pkgbuild
    vars_, build_body, package_body = _parse_pkgbuild(GOOD_PKGBUILD)
    assert vars_["pkgname"] == "hello"
    assert vars_["pkgver"] == "1.0.0"
    assert vars_["pkgrel"] == "1"
    assert vars_["pkgdesc"] == "A friendly greeting"
    assert vars_["arch"] == ["all"]
    assert vars_["depends"] == []
    assert "mkdir -p /tmp/build/hello" in build_body
    assert 'echo "Hello World" > /tmp/build/hello/readme.txt' in build_body
    assert 'mkdir -p "$pkgdir/usr/bin"' in package_body
    assert "hello.txt" in package_body


def test_parse_pkgbuild_minimal():
    """Минимальный PKGBUILD только с обязательными полями."""
    from pyos.commands.package_cmds import _parse_pkgbuild
    text = 'pkgname=test\npkgver=0.1.0\nbuild() {\necho "ok"\n}\n'
    vars_, build_body, package_body = _parse_pkgbuild(text)
    assert vars_["pkgname"] == "test"
    assert vars_["pkgver"] == "0.1.0"
    assert "echo \"ok\"" in build_body
    assert package_body == ""


def test_parse_pkgbuild_missing_required():
    """PKGBUILD без pkgname вызывает ошибку валидации."""
    from pyos.commands.package_cmds import _validate_pkg_vars
    with pytest.raises(InvalidArgument, match="pkgname"):
        _validate_pkg_vars({"pkgver": "1.0"})


def test_parse_pkgbuild_inline_brace():
    """Функция с { на той же строке."""
    from pyos.commands.package_cmds import _parse_pkgbuild
    text = 'pkgname=x\npkgver=1\nbuild() { echo hi; }\n'
    vars_, build_body, package_body = _parse_pkgbuild(text)
    assert "echo hi" in build_body


# -- makepkg ----------------------------------------------------------------

def test_makepkg_requires_root(kernel, user):
    """makepkg без root выдаёт ошибку."""
    from pyos.commands.package_cmds import cmd_makepkg
    from pyos.context import Context
    import io

    # Создаём PKGBUILD в ФС
    fs = kernel.fs
    fs.write_file("/tmp/PKGBUILD", GOOD_PKGBUILD, kernel.users.get_by_name("root"), "/", "/")

    ctx = Context(kernel, user, "/")
    ctx.shell = Shell(kernel, user)
    with pytest.raises(PyOSError, match="only root"):
        cmd_makepkg(ctx, ["/tmp/PKGBUILD"], "")


def test_makepkg_builds_package(kernel, root):
    """makepkg собирает пакет в /var/cache/packages."""
    from pyos.commands.package_cmds import cmd_makepkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    # Создаём PKGBUILD
    kernel.fs.write_file("/root/PKGBUILD", GOOD_PKGBUILD, root, "/", "/")

    result = cmd_makepkg(ctx, [], "")
    assert result is not None
    assert "hello" in result

    # Проверяем, что кэш создан
    node = kernel.fs.lookup("/var/cache/packages/hello-1.0.0", root, "/", "/")
    assert node.is_dir()

    # Проверяем METADATA
    meta = kernel.fs.read_file(
        "/var/cache/packages/hello-1.0.0/METADATA", root, "/", "/"
    )
    assert '"pkgname": "hello"' in meta
    assert '"pkgver": "1.0.0"' in meta

    # Проверяем, что файлы пакета скопированы в кэш
    node = kernel.fs.lookup(
        "/var/cache/packages/hello-1.0.0/pkg/usr/share/hello.txt", root, "/", "/"
    )
    assert node.is_file()
    assert node.data.strip() == b"Hello World"


def test_makepkg_without_pkgbuild(kernel, root):
    """makepkg без PKGBUILD выдаёт ошибку."""
    from pyos.commands.package_cmds import cmd_makepkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/tmp", shell=shell)

    with pytest.raises(InvalidArgument, match="not found"):
        cmd_makepkg(ctx, [], "")


# -- pkg install -----------------------------------------------------------

def test_pkg_install_and_list(kernel, root):
    """pkg install устанавливает пакет, pkg list показывает его."""
    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    # Собираем пакет
    kernel.fs.write_file("/root/PKGBUILD", GOOD_PKGBUILD, root, "/", "/")
    cmd_makepkg(ctx, [], "")

    # Устанавливаем
    result = cmd_pkg(ctx, ["install", "hello"], "")
    assert result is not None
    assert "installed" in result
    assert "hello" in result

    # Проверяем, что файл создан в правильном месте
    data = kernel.fs.read_file("/usr/bin/hello", root, "/", "/")
    assert "Hello World" in data

    data = kernel.fs.read_file("/usr/share/hello.txt", root, "/", "/")
    assert data.strip() == "Hello World"

    # Проверяем manifest
    import json
    manifest = json.loads(
        kernel.fs.read_file("/var/lib/packages/hello/manifest", root, "/", "/")
    )
    assert manifest["pkgname"] == "hello"
    assert manifest["pkgver"] == "1.0.0"
    assert "/usr/bin/hello" in manifest["files"]
    assert "/usr/share/hello.txt" in manifest["files"]

    # pkg list
    result = cmd_pkg(ctx, ["list"], "")
    assert "hello" in result
    assert "1.0.0" in result


def test_pkg_install_already_installed(kernel, root):
    """Повторная установка того же пакета выдаёт ошибку."""
    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    kernel.fs.write_file("/root/PKGBUILD", GOOD_PKGBUILD, root, "/", "/")
    cmd_makepkg(ctx, [], "")
    cmd_pkg(ctx, ["install", "hello"], "")

    with pytest.raises(PyOSError, match="already installed"):
        cmd_pkg(ctx, ["install", "hello"], "")


# -- pkg info ---------------------------------------------------------------

def test_pkg_info(kernel, root):
    """pkg info показывает информацию о пакете."""
    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    kernel.fs.write_file("/root/PKGBUILD", GOOD_PKGBUILD, root, "/", "/")
    cmd_makepkg(ctx, [], "")
    cmd_pkg(ctx, ["install", "hello"], "")

    result = cmd_pkg(ctx, ["info", "hello"], "")
    assert "hello" in result
    assert "1.0.0" in result
    assert "installed" in result
    assert "A friendly greeting" in result


# -- pkg remove -------------------------------------------------------------

def test_pkg_remove(kernel, root):
    """pkg remove удаляет пакет и его файлы."""
    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    kernel.fs.write_file("/root/PKGBUILD", GOOD_PKGBUILD, root, "/", "/")
    cmd_makepkg(ctx, [], "")
    cmd_pkg(ctx, ["install", "hello"], "")

    # Убедимся, что файлы существуют
    assert kernel.fs.lookup("/usr/bin/hello", root, "/", "/").is_file()

    # Удаляем
    result = cmd_pkg(ctx, ["remove", "hello"], "")
    assert "removed" in result

    # Проверяем, что файлов нет
    from pyos.errors import NoSuchFileOrDirectory
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/bin/hello", root, "/", "/")
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/share/hello.txt", root, "/", "/")

    # Проверяем, что manifest удалён
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/var/lib/packages/hello", root, "/", "/")

    # pkg list не должен показывать hello
    result = cmd_pkg(ctx, ["list"], "")
    assert "hello" not in result


def test_pkg_remove_not_installed(kernel, root):
    """Удаление неустановленного пакета выдаёт ошибку."""
    from pyos.commands.package_cmds import cmd_pkg
    from pyos.context import Context

    ctx = Context(kernel, root, "/root")
    with pytest.raises(InvalidArgument, match="not installed"):
        cmd_pkg(ctx, ["remove", "nonexistent"], "")


# -- Интеграционный тест: build + install + use ----------------------------

def test_build_install_use(kernel, root):
    """Полный цикл: собрать, установить, проверить содержимое файла."""
    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    # PKGBUILD c командой greet
    pkgbuild = """\
pkgname=greet
pkgver=2.0.0
pkgrel=1
pkgdesc="Greeting utility"
arch=("all")
depends=()

package() {
    mkdir -p "$pkgdir/usr/bin"
    echo "#!/bin/sh" > "$pkgdir/usr/bin/greet"
    echo "echo 'Hello from greet 2.0!'" >> "$pkgdir/usr/bin/greet"
}
"""
    kernel.fs.write_file("/root/PKGBUILD", pkgbuild, root, "/", "/")
    cmd_makepkg(ctx, [], "")

    # Устанавливаем
    cmd_pkg(ctx, ["install", "greet"], "")

    # Проверяем, что файл установлен и имеет верное содержимое
    data = kernel.fs.read_file("/usr/bin/greet", root, "/", "/")
    assert "Hello from greet 2.0!" in data
    assert "#!/bin/sh" in data


# -- Работа от обычного пользователя ----------------------------------------

def test_pkg_requires_root(kernel, user):
    """Изменяющие pkg-команды без root выдают ошибку."""
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, user)
    with pytest.raises(PyOSError, match="only root"):
        cmd_pkg(ctx, ["install", "demo"], "")


def test_pkg_list_available_to_non_root(kernel, user):
    from pyos.commands.package_cmds import cmd_pkg

    result = cmd_pkg(_ctx(kernel, user), ["list"], "")

    assert "no packages installed" in result or "Package" in result


def test_pkg_info_available_to_non_root(kernel, root, user):
    from pyos.commands.package_cmds import cmd_pkg

    _write_pkg(kernel, root, _make_package(), "/root/pkg.json")
    cmd_pkg(_ctx(kernel, root), ["install", "/root/pkg.json"], "")

    result = cmd_pkg(_ctx(kernel, user), ["info", "demo"], "")

    assert "Package:    demo" in result
    assert "Status: installed" in result


def test_pkg_install_still_root_only(kernel, user):
    from pyos.commands.package_cmds import cmd_pkg

    with pytest.raises(PermissionDenied):
        cmd_pkg(_ctx(kernel, user), ["install", "demo"], "")


def test_pkg_remove_still_root_only(kernel, user):
    from pyos.commands.package_cmds import cmd_pkg

    with pytest.raises(PermissionDenied):
        cmd_pkg(_ctx(kernel, user), ["remove", "demo"], "")


# -- Сравнение версий --------------------------------------------------------

def test_pkgvercmp_basic_equal():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1.0", "1.0") == 0


def test_pkgvercmp_numeric_segments():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1.2", "1.10") == -1
    assert pkgvercmp("10.0", "2.0") == 1
    assert pkgvercmp("1.0", "1.0.0") == 0


def test_pkgvercmp_1_9_vs_1_10():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1.9", "1.10") == -1
    assert pkgvercmp("1.10", "1.9") == 1


def test_pkgvercmp_1_0_vs_1_0_1():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1.0", "1.0.1") == -1


def test_pkgvercmp_release_1_0_1_vs_1_0_2():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1.0-1", "1.0-2") == -1
    assert pkgvercmp("1.0-2", "1.0-2") == 0


def test_pkgvercmp_epoch_1_vs_2():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1:1.0", "2:0.1") == -1
    assert pkgvercmp("2:0.1", "1:9.9") == 1


def _versioned_pkgbuild(version):
    return (
        "pkgname=hello\n"
        f"pkgver={version}\n"
        "pkgrel=1\n"
        'pkgdesc="versioned hello"\n'
        'arch=("all")\n'
        "depends=()\n"
        "package() {\n"
        '    mkdir -p "$pkgdir/usr/share"\n'
        f'    echo "{version}" > "$pkgdir/usr/share/hello-version.txt"\n'
        "}\n"
    )


def test_pkg_install_selects_highest_version_not_alphabetical(kernel, root):
    """1.10 должна побеждать 1.9, хотя по алфавиту 1.9 идёт позже."""
    import json

    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    for version in ("1.9", "1.10"):
        kernel.fs.write_file("/root/PKGBUILD",
                             _versioned_pkgbuild(version), root, "/", "/")
        cmd_makepkg(ctx, [], "")

    result = cmd_pkg(ctx, ["install", "hello"], "")
    assert "1.10" in result

    manifest = json.loads(
        kernel.fs.read_file("/var/lib/packages/hello/manifest", root, "/", "/")
    )
    assert manifest["pkgver"] == "1.10"

    data = kernel.fs.read_file("/usr/share/hello-version.txt", root, "/", "/")
    assert data.strip() == "1.10"


def test_pkg_info_selects_highest_cached_version(kernel, root):
    """pkg info тоже берёт старшую версию из кэша, а не первую по порядку."""
    from pyos.commands.package_cmds import cmd_makepkg, cmd_pkg
    from pyos.context import Context

    shell = Shell(kernel, root)
    ctx = Context(kernel, root, "/root", shell=shell)

    for version in ("1.9", "1.10"):
        kernel.fs.write_file("/root/PKGBUILD",
                             _versioned_pkgbuild(version), root, "/", "/")
        cmd_makepkg(ctx, [], "")

    result = cmd_pkg(ctx, ["info", "hello"], "")
    assert "Version:    1.10-1" in result
    assert "1.9" not in result


# -- .pkg.json: установка локального пакета ----------------------------------


def _make_package(name="demo", version="1.0", files=None, **meta):
    data = {
        "format": 1,
        "name": name,
        "version": version,
        "release": 1,
        "arch": "any",
        "description": f"{name} package",
        "depends": [],
        "provides": [],
        "conflicts": [],
        "files": files if files is not None else [
            {
                "path": f"/usr/bin/{name}",
                "mode": "0755",
                "owner": "root",
                "group": "root",
                "encoding": "utf-8",
                "content": f"#!/bin/sh\necho {name}\n",
            },
        ],
    }
    data.update(meta)
    return data


def _write_pkg(kernel, root, data, path="/root/pkg.json"):
    kernel.fs.write_file(path, json.dumps(data), root, "/", "/")
    return path


def _ctx(kernel, root):
    from pyos.context import Context
    return Context(kernel, root, "/root", shell=Shell(kernel, root))


def test_install_valid_package_creates_files(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    path = _write_pkg(kernel, root, _make_package())

    result = cmd_pkg(ctx, ["install", path], "")
    assert "installed" in result
    assert "demo" in result

    data = kernel.fs.read_file("/usr/bin/demo", root, "/", "/")
    assert data == "#!/bin/sh\necho demo\n"


def test_install_sets_mode_owner_group(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    files = [{
        "path": "/usr/bin/tool",
        "mode": "0750",
        "owner": "user",
        "group": "users",
        "encoding": "utf-8",
        "content": "tool",
    }]
    _write_pkg(kernel, root, _make_package(name="tool", files=files))

    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    node = kernel.fs.lookup("/usr/bin/tool", root, "/", "/")
    assert node.mode == 0o750
    assert node.uid == 1000  # user
    assert node.gid == 100   # users


def test_install_rejects_relative_path(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    files = [{
        "path": "usr/bin/demo",
        "mode": "0644",
        "owner": "root",
        "group": "root",
        "encoding": "utf-8",
        "content": "x",
    }]
    _write_pkg(kernel, root, _make_package(files=files))

    with pytest.raises(InvalidArgument, match="absolute"):
        cmd_pkg(ctx, ["install", "/root/pkg.json"], "")


def test_install_rejects_path_traversal(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    files = [{
        "path": "/etc/../tmp/evil",
        "mode": "0644",
        "owner": "root",
        "group": "root",
        "encoding": "utf-8",
        "content": "x",
    }]
    _write_pkg(kernel, root, _make_package(files=files))

    with pytest.raises(InvalidArgument, match="traversal"):
        cmd_pkg(ctx, ["install", "/root/pkg.json"], "")


def test_install_rejects_unknown_format(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(format=2))

    with pytest.raises(InvalidArgument, match="format"):
        cmd_pkg(ctx, ["install", "/root/pkg.json"], "")


def test_install_checks_dependency(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(depends=["base"]))

    with pytest.raises(InvalidArgument, match="missing dependency 'base'"):
        cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/bin/demo", root, "/", "/")


def test_install_detects_file_conflict(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    files = [{
        "path": "/usr/bin/shared",
        "mode": "0644",
        "owner": "root",
        "group": "root",
        "encoding": "utf-8",
        "content": "one",
    }]
    _write_pkg(kernel, root, _make_package(name="one", files=files),
               "/root/one.pkg.json")
    cmd_pkg(ctx, ["install", "/root/one.pkg.json"], "")

    files[0]["content"] = "two"
    _write_pkg(kernel, root, _make_package(name="two", files=files),
               "/root/two.pkg.json")
    with pytest.raises(InvalidArgument, match="file conflict") as excinfo:
        cmd_pkg(ctx, ["install", "/root/two.pkg.json"], "")
    assert "owned by one" in str(excinfo.value)

    assert kernel.fs.read_file("/usr/bin/shared", root, "/", "/") == "one"


def test_remove_deletes_owned_files(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package())
    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    result = cmd_pkg(ctx, ["remove", "demo"], "")
    assert "removed" in result

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/bin/demo", root, "/", "/")
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/var/lib/packages/installed/demo.json", root, "/", "/")
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/var/lib/packages/demo", root, "/", "/")

    owners = json.loads(
        kernel.fs.read_file("/var/lib/packages/file-owners.json", root, "/", "/")
    )
    assert "/usr/bin/demo" not in owners


def test_remove_refuses_if_reverse_dependency_exists(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(name="base"), "/root/base.pkg.json")
    cmd_pkg(ctx, ["install", "/root/base.pkg.json"], "")

    _write_pkg(kernel, root, _make_package(name="app", depends=["base"]),
               "/root/app.pkg.json")
    cmd_pkg(ctx, ["install", "/root/app.pkg.json"], "")

    with pytest.raises(InvalidArgument, match="required by app"):
        cmd_pkg(ctx, ["remove", "base"], "")

    assert kernel.fs.lookup("/usr/bin/base", root, "/", "/").is_file()


def test_remove_force_allows_broken_dependency(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(name="base"), "/root/base.pkg.json")
    cmd_pkg(ctx, ["install", "/root/base.pkg.json"], "")

    _write_pkg(kernel, root, _make_package(name="app", depends=["base"]),
               "/root/app.pkg.json")
    cmd_pkg(ctx, ["install", "/root/app.pkg.json"], "")

    result = cmd_pkg(ctx, ["remove", "base", "--force"], "")
    assert "removed" in result

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/bin/base", root, "/", "/")
    # app остаётся установленным, хотя зависимость сломана
    manifest = json.loads(
        kernel.fs.read_file("/var/lib/packages/installed/app.json",
                            root, "/", "/")
    )
    assert manifest["name"] == "app"


def test_list_shows_installed_packages(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(name="alpha", version="1.0"),
               "/root/alpha.pkg.json")
    _write_pkg(kernel, root, _make_package(name="beta", version="2.5"),
               "/root/beta.pkg.json")
    cmd_pkg(ctx, ["install", "/root/alpha.pkg.json"], "")
    cmd_pkg(ctx, ["install", "/root/beta.pkg.json"], "")

    result = cmd_pkg(ctx, ["list"], "")
    assert "alpha" in result
    assert "beta" in result
    assert "1.0-1" in result
    assert "2.5-1" in result


def test_pkg_list_shows_version_release(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(name="demo", version="2.1"),
               "/root/demo.pkg.json")
    cmd_pkg(ctx, ["install", "/root/demo.pkg.json"], "")

    result = cmd_pkg(ctx, ["list"], "")

    assert "Version-Release" in result
    assert "2.1-1" in result


def test_info_shows_package_metadata(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(
        description="Demo description",
        depends=[],
        provides=["virtual-demo"],
    ))
    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    result = cmd_pkg(ctx, ["info", "demo"], "")
    assert "Package:    demo" in result
    assert "Version:    1.0-1" in result
    assert "Demo description" in result
    assert "Provides: virtual-demo" in result
    assert "installed" in result


def test_installed_manifest_persisted(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package())
    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    manifest = json.loads(
        kernel.fs.read_file("/var/lib/packages/installed/demo.json",
                            root, "/", "/")
    )
    assert manifest["format"] == 1
    assert manifest["name"] == "demo"
    assert manifest["version"] == "1.0"
    assert manifest["release"] == "1"
    assert manifest["files"] == ["/usr/bin/demo"]


def test_file_owners_updated(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    files = [
        {
            "path": "/usr/bin/demo",
            "mode": "0755",
            "owner": "root",
            "group": "root",
            "encoding": "utf-8",
            "content": "bin",
        },
        {
            "path": "/etc/demo.conf",
            "mode": "0644",
            "owner": "root",
            "group": "root",
            "encoding": "utf-8",
            "content": "conf",
        },
    ]
    _write_pkg(kernel, root, _make_package(files=files))
    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    owners = json.loads(
        kernel.fs.read_file("/var/lib/packages/file-owners.json", root, "/", "/")
    )
    assert owners["/usr/bin/demo"] == "demo"
    assert owners["/etc/demo.conf"] == "demo"


def test_save_load_preserves_installed_package_db(kernel, root):
    from pyos.commands.memory_cmds import cmd_load, cmd_save
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package())
    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False) as f:
        tmpname = f.name

    try:
        cmd_save(ctx, [tmpname], "")
        cmd_load(ctx, [tmpname], "")

        data = kernel.fs.read_file("/usr/bin/demo", root, "/", "/")
        assert data == "#!/bin/sh\necho demo\n"

        manifest = json.loads(
            kernel.fs.read_file("/var/lib/packages/installed/demo.json",
                                root, "/", "/")
        )
        assert manifest["name"] == "demo"

        owners = json.loads(
            kernel.fs.read_file("/var/lib/packages/file-owners.json",
                                root, "/", "/")
        )
        assert owners["/usr/bin/demo"] == "demo"
    finally:
        os.unlink(tmpname)


def test_install_dry_run_does_not_write(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package())

    result = cmd_pkg(ctx, ["install", "/root/pkg.json", "--dry-run"], "")
    assert "would install" in result

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/usr/bin/demo", root, "/", "/")
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/var/lib/packages/installed/demo.json",
                         root, "/", "/")


def test_pkg_install_local_dry_run_format(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package(
        name="demo", version="2.1", release=3), "/tmp/demo.pkg.json")

    result = cmd_pkg(ctx, ["install", "/tmp/demo.pkg.json", "--dry-run"], "")

    assert result == (
        "pkg: would install 'demo' "
        "(version 2.1-3, 1 file, source /tmp/demo.pkg.json)")


def test_pkg_install_dry_run_singular_plural_files(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    two_files = [
        {"path": "/usr/bin/demo", "mode": "0755", "owner": "root",
         "group": "root", "encoding": "utf-8", "content": "a"},
        {"path": "/usr/share/demo", "mode": "0644", "owner": "root",
         "group": "root", "encoding": "utf-8", "content": "b"},
    ]
    _write_pkg(kernel, root, _make_package(name="one", version="1.0"),
               "/tmp/one.pkg.json")
    _write_pkg(kernel, root, _make_package(
        name="two", version="1.0", files=two_files), "/tmp/two.pkg.json")

    single = cmd_pkg(ctx, ["install", "/tmp/one.pkg.json", "--dry-run"], "")
    plural = cmd_pkg(ctx, ["install", "/tmp/two.pkg.json", "--dry-run"], "")

    assert "1 file" in single
    assert "1 files" not in single
    assert "2 files" in plural


def test_remove_dry_run_does_not_delete(kernel, root):
    from pyos.commands.package_cmds import cmd_pkg

    ctx = _ctx(kernel, root)
    _write_pkg(kernel, root, _make_package())
    cmd_pkg(ctx, ["install", "/root/pkg.json"], "")

    result = cmd_pkg(ctx, ["remove", "demo", "--dry-run"], "")
    assert "would remove" in result

    assert kernel.fs.lookup("/usr/bin/demo", root, "/", "/").is_file()
    assert kernel.fs.lookup("/var/lib/packages/installed/demo.json",
                            root, "/", "/").is_file()
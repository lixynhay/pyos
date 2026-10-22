"""Тесты пакетного менеджера pyOS: makepkg и pkg."""

import pytest

from pyos.errors import InvalidArgument, PyOSError
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
    assert node.data.strip() == "Hello World"


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
    """pkg без root выдаёт ошибку."""
    from pyos.commands.package_cmds import cmd_pkg
    from pyos.context import Context

    ctx = Context(kernel, user, "/home/user")
    with pytest.raises(PyOSError, match="only root"):
        cmd_pkg(ctx, ["list"], "")


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


def test_pkgvercmp_release():
    from pyos.commands.package_cmds import pkgvercmp
    assert pkgvercmp("1.0-1", "1.0-2") == -1
    assert pkgvercmp("1.0-2", "1.0-2") == 0


def test_pkgvercmp_epoch():
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
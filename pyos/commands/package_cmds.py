"""Пакетный менеджер pyOS: makepkg и pkg.

PKGBUILD-подход (как в Arch Linux):
  - PKGBUILD — shell-подобный файл с переменными и функциями build()/package()
  - makepkg собирает пакет из PKGBUILD
  - pkg устанавливает/удаляет/просматривает пакеты
"""

import json
import re

from ..errors import (
    InvalidArgument,
    IsADirectory,
    NoSuchFileOrDirectory,
    PermissionDenied,
    PyOSError,
)
from .registry import register

# -- пути в виртуальной ФС -------------------------------------------------
CACHE_ROOT = "/var/cache/packages"
LIB_ROOT = "/var/lib/packages"
BUILD_ROOT = "/var/tmp/makepkg"

# -- парсер PKGBUILD --------------------------------------------------------


def _parse_pkgbuild(text):
    """Разбирает PKGBUILD, возвращает (vars, build_body, package_body).

    vars — dict[str, str | list[str]] с метаданными пакета.
    build_body — текст тела функции build() (пустая строка, если нет).
    package_body — текст тела функции package() (пустая строка, если нет).
    """
    pkg_vars = {}
    build_body = ""
    package_body = ""

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # пустые строки и комментарии
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # функция: name() { ... }  или  name()  /  {  на следующей строке
        func_match = re.match(r"^(\w+)\s*\(\s*\)\s*\{", stripped)
        if func_match:
            body, end_i = _extract_func_body(lines, i, inline=True)
            _store_func_body(func_match.group(1), body, pkg_vars)
            i = end_i + 1
            continue

        func_match2 = re.match(r"^(\w+)\s*\(\s*\)\s*$", stripped)
        if func_match2:
            name = func_match2.group(1)
            if i + 1 < len(lines) and lines[i + 1].strip() == "{":
                body, end_i = _extract_func_body(lines, i + 1, inline=False)
                _store_func_body(name, body, pkg_vars)
                i = end_i + 1
                continue

        # переменная: name=value  или  name=("a" "b")
        var_match = re.match(r"^(\w+)=(.*)$", stripped)
        if var_match:
            name = var_match.group(1)
            value = var_match.group(2).strip()
            if value.startswith("(") and value.endswith(")"):
                inner = value[1:-1].strip()
                pkg_vars[name] = _parse_array(inner)
            else:
                pkg_vars[name] = _unquote(value)

        i += 1

    build_body = pkg_vars.pop("_build_body", "")
    package_body = pkg_vars.pop("_package_body", "")
    return pkg_vars, build_body, package_body


def _extract_func_body(lines, start, inline=False):
    """Извлекает тело функции между { и }, начиная со строки start.

    Возвращает (body_text, last_line_index).
    inline=True — открывающая { на строке start (подтверждено regex).
    """
    body_parts = []
    depth = 0
    have_open = False
    i = start

    # Проход: находим закрывающую }
    while i < len(lines):
        line = lines[i]
        for ch in line:
            if ch == "{":
                depth += 1
                have_open = True
            elif ch == "}":
                depth -= 1

        if have_open and depth == 0:
            break
        i += 1

    # Собираем строки тела (между { и })
    # i указывает на строку с закрывающей }
    for lineno in range(start, i + 1):
        line = lines[lineno]
        # Определяем, есть ли тут открывающая скобка
        brace_open_idx = line.find("{")
        brace_close_idx = line.find("}")

        # Извлекаем то, что после { и до }
        if lineno == start and brace_open_idx >= 0:
            # В первой строке: часть после {
            after = line[brace_open_idx + 1 :].strip()
            if brace_close_idx >= 0:
                # } на той же строке — берём только до }
                before_close = after[: after.find("}")].strip()
                if before_close:
                    body_parts.append(before_close)
            elif after:
                body_parts.append(after)
            continue

        if lineno == i and brace_close_idx >= 0:
            # В последней строке: часть до }
            before = line[:brace_close_idx].strip()
            if before:
                body_parts.append(before)
            continue

        # Строка целиком внутри тела
        stripped = line.strip()
        if stripped:
            body_parts.append(stripped)

    return "\n".join(body_parts), i


def _store_func_body(name, body, vars_dict):
    if name == "build":
        vars_dict["_build_body"] = body
    elif name == "package":
        vars_dict["_package_body"] = body


def _parse_array(inner):
    """Разбирает содержимое массива ("a" "b") -> ['a', 'b']."""
    result = []
    for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'', inner):
        result.append(m.group(1) or m.group(2))
    return result


def _unquote(value):
    """Снимает кавычки со строки."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


# -- утилиты файловой системы -----------------------------------------------


def _ensure_dir(fs, path, user):
    """Создаёт каталог и все родительские, если их нет."""
    # Проверяем, существует ли уже
    try:
        node = fs.lookup(path, user, "/", "/")
        if node.is_dir():
            return
        raise PyOSError(f"{path}: not a directory")
    except NoSuchFileOrDirectory:
        pass
    fs.mkdir(path, user, "/", "/", parents=True)


def _tree_walk(node, prefix=""):
    """Обходит дерево каталогов и возвращает (rel_path, File)."""
    if node.is_file():
        yield prefix, node
    elif node.is_dir():
        for name in sorted(node.children):
            child = node.children[name]
            new_prefix = prefix + "/" + name if prefix else name
            yield from _tree_walk(child, new_prefix)


def _copy_tree(fs, src_path, dst_path, user):
    """Копирует содержимое src_path в dst_path (рекурсивно)."""
    node = fs.lookup(src_path, user, "/", "/")
    if not node.is_dir():
        raise PyOSError(f"{src_path}: not a directory")

    _ensure_dir(fs, dst_path, user)
    for rel, child in _tree_walk(node):
        src_child = src_path.rstrip("/") + "/" + rel
        dst_child = dst_path.rstrip("/") + "/" + rel
        if child.is_dir():
            _ensure_dir(fs, dst_child, user)
        else:
            # Создаём родительский каталог для файла
            parent_dir = "/".join(dst_child.rstrip("/").split("/")[:-1])
            _ensure_dir(fs, parent_dir, user)
            data = fs.read_file(src_child, user, "/", "/")
            fs.write_file(dst_child, data, user, "/", "/", create=True)


def _remove_tree(fs, path, user):
    """Удаляет файл или рекурсивно каталог."""
    try:
        node = fs.lookup(path, user, "/", "/")
    except NoSuchFileOrDirectory:
        return
    if node.is_dir():
        # Удаляем детей
        for name in list(node.children):
            child_path = path.rstrip("/") + "/" + name
            _remove_tree(fs, child_path, user)
        # Удаляем сам каталог
        fs.rmdir(path, user, "/", "/")
    else:
        fs.unlink(path, user, "/", "/")


# -- выполнение shell-команд в контексте сборки -----------------------------


def _run_shell_commands(commands_text, shell, env_updates=None):
    """Выполняет многострочный shell-скрипт через handle_line().

    Возвращает (ok, error_text). При первой ошибке останавливается.
    """
    if not commands_text.strip():
        return True, ""

    save_cwd = shell.cwd
    save_env = dict(shell.env)

    try:
        if env_updates:
            shell.env.update(env_updates)

        for line in commands_text.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            shell.handle_line(stripped)
            if shell.last_code != 0:
                return False, f"Command failed with exit code {shell.last_code}: {stripped}"

        return True, ""
    finally:
        shell.cwd = save_cwd
        # Восстанавливаем только те ключи, что мы добавили
        if env_updates:
            for k in env_updates:
                if k in save_env:
                    shell.env[k] = save_env[k]
                else:
                    shell.env.pop(k, None)


# -- команда makepkg --------------------------------------------------------


def _validate_pkg_vars(vars_dict):
    """Проверяет обязательные поля."""
    required = ["pkgname", "pkgver"]
    for key in required:
        if key not in vars_dict or not vars_dict[key]:
            raise InvalidArgument(f"makepkg: PKGBUILD missing required field '{key}'")


@register("makepkg",
          usage="makepkg [PKGBUILD]",
          summary="Собрать пакет из PKGBUILD",
          category="system")
def cmd_makepkg(ctx, args, stdin):
    """Собирает пакет из PKGBUILD в текущем каталоге.

    Использование: makepkg [путь/к/PKGBUILD]
    По умолчанию ищет PKGBUILD в текущей рабочей директории.
    """
    # Определяем путь к PKGBUILD
    pkgbuild_path = args[0] if args else "PKGBUILD"
    if not pkgbuild_path.startswith("/"):
        pkgbuild_path = ctx.cwd.rstrip("/") + "/" + pkgbuild_path

    # Читаем PKGBUILD
    try:
        pkgbuild_text = ctx.fs.read_file(pkgbuild_path, ctx.user, ctx.cwd, ctx.home)
    except NoSuchFileOrDirectory:
        raise InvalidArgument(f"makepkg: '{pkgbuild_path}' not found")

    # Парсим
    pkg_vars, build_body, package_body = _parse_pkgbuild(pkgbuild_text)
    _validate_pkg_vars(pkg_vars)

    pkgname = pkg_vars["pkgname"]
    pkgver = pkg_vars.get("pkgver", "1.0.0")
    pkgrel = str(pkg_vars.get("pkgrel", "1"))
    pkgdesc = pkg_vars.get("pkgdesc", "")
    arch = pkg_vars.get("arch", ["all"])
    depends = pkg_vars.get("depends", [])

    if ctx.user.uid != 0:
        raise PermissionDenied(message="makepkg: only root can build packages")

    build_dir = f"{BUILD_ROOT}/{pkgname}-{pkgver}"
    pkgdir = build_dir + "/pkg"
    cache_dir = f"{CACHE_ROOT}/{pkgname}-{pkgver}"

    # Очистка предыдущих артефактов
    _remove_tree(ctx.fs, build_dir, ctx.user)
    _remove_tree(ctx.fs, cache_dir, ctx.user)

    try:
        # Создаём build dir
        _ensure_dir(ctx.fs, build_dir, ctx.user)
        _ensure_dir(ctx.fs, pkgdir, ctx.user)

        srcdir = build_dir
        env = {"srcdir": srcdir, "pkgdir": pkgdir}

        # build()
        if build_body:
            ok, err = _run_shell_commands(build_body, ctx.shell, env)
            if not ok:
                raise PyOSError(f"makepkg: build failed: {err}")

        # package()
        if package_body:
            ok, err = _run_shell_commands(package_body, ctx.shell, env)
            if not ok:
                raise PyOSError(f"makepkg: package failed: {err}")

        # Копируем из $pkgdir в кэш
        _ensure_dir(ctx.fs, f"{CACHE_ROOT}", ctx.user)
        _copy_tree(ctx.fs, pkgdir, cache_dir + "/pkg", ctx.user)

        # Сохраняем METADATA
        metadata = {
            "pkgname": pkgname,
            "pkgver": pkgver,
            "pkgrel": pkgrel,
            "pkgdesc": pkgdesc,
            "arch": arch,
            "depends": depends,
        }
        ctx.fs.write_file(
            cache_dir + "/METADATA",
            json.dumps(metadata, indent=2),
            ctx.user, "/", "/", create=True,
        )

        # Копируем PKGBUILD
        ctx.fs.write_file(
            cache_dir + "/PKGBUILD",
            pkgbuild_text,
            ctx.user, "/", "/", create=True,
        )

        return f"makepkg: built '{pkgname}' version {pkgver}-{pkgrel} -> {cache_dir}"

    except PyOSError:
        # Убираем за собой при ошибке
        _remove_tree(ctx.fs, build_dir, ctx.user)
        _remove_tree(ctx.fs, cache_dir, ctx.user)
        raise


# -- команда pkg ------------------------------------------------------------


def _get_installed_packages(fs, user):
    """Возвращает список установленных пакетов (имена каталогов в LIB_ROOT)."""
    try:
        node = fs.lookup(LIB_ROOT, user, "/", "/")
    except NoSuchFileOrDirectory:
        return []
    if not node.is_dir():
        return []
    return sorted(node.children.keys())


def _read_manifest(fs, user, pkgname):
    """Читает manifest установленного пакета."""
    path = f"{LIB_ROOT}/{pkgname}/manifest"
    try:
        data = fs.read_file(path, user, "/", "/")
        return json.loads(data)
    except (NoSuchFileOrDirectory, json.JSONDecodeError, PyOSError):
        return None


def _write_manifest(fs, user, pkgname, manifest):
    """Пишет manifest установленного пакета."""
    _ensure_dir(fs, f"{LIB_ROOT}", user)
    _ensure_dir(fs, f"{LIB_ROOT}/{pkgname}", user)
    fs.write_file(
        f"{LIB_ROOT}/{pkgname}/manifest",
        json.dumps(manifest, indent=2),
        user, "/", "/", create=True,
    )


def _install_from_cache(ctx, pkgname, pkgver):
    """Устанавливает пакет из кэша."""
    cache_dir = f"{CACHE_ROOT}/{pkgname}-{pkgver}"

    # Проверяем, что пакет собран
    try:
        metadata_text = ctx.fs.read_file(cache_dir + "/METADATA",
                                          ctx.user, "/", "/")
        metadata = json.loads(metadata_text)
    except (NoSuchFileOrDirectory, json.JSONDecodeError):
        raise InvalidArgument(
            f"pkg: package '{pkgname}-{pkgver}' not found in cache"
        )

    # Проверяем, не установлен ли уже
    existing = _read_manifest(ctx.fs, ctx.user, pkgname)
    if existing is not None:
        raise PyOSError(
            f"pkg: '{pkgname}' is already installed "
            f"(version {existing.get('pkgver', '?')})"
        )

    # Копируем файлы из cache/pkg/ в корневую ФС
    pkg_files_dir = cache_dir + "/pkg"
    installed_files = []

    try:
        node = ctx.fs.lookup(pkg_files_dir, ctx.user, "/", "/")
    except NoSuchFileOrDirectory:
        # Пакет без файлов — бывает
        node = None

    if node and node.is_dir():
        # Обходим файлы и устанавливаем
        _copy_pkg_files(ctx, node, pkg_files_dir, "", installed_files)

    # Сохраняем manifest
    manifest = {
        "pkgname": pkgname,
        "pkgver": pkgver,
        "pkgdesc": metadata.get("pkgdesc", ""),
        "depends": metadata.get("depends", []),
        "files": installed_files,
    }
    _write_manifest(ctx.fs, ctx.user, pkgname, manifest)

    return f"pkg: installed '{pkgname}' (version {pkgver})"


def _copy_pkg_files(ctx, node, base_path, rel_prefix, installed_files):
    """Рекурсивно копирует файлы из пакета в корневую ФС."""
    if node.is_file():
        # Вычисляем целевой путь: rel_prefix — это путь без base_path
        target = "/" + rel_prefix
        data = ctx.fs.read_file(base_path + "/" + rel_prefix, ctx.user, "/", "/")
        try:
            # Попытка создать/перезаписать файл
            ctx.fs.write_file(target, data, ctx.user, "/", "/", create=True)
        except NoSuchFileOrDirectory:
            # Нужно создать родительский каталог
            parent = "/" + "/".join(rel_prefix.split("/")[:-1])
            _ensure_dir(ctx.fs, parent, ctx.user)
            ctx.fs.write_file(target, data, ctx.user, "/", "/", create=True)
        installed_files.append(target)
    elif node.is_dir():
        for name in sorted(node.children):
            child = node.children[name]
            new_rel = rel_prefix + "/" + name if rel_prefix else name
            _copy_pkg_files(ctx, child, base_path, new_rel, installed_files)


def _remove_package(ctx, pkgname):
    """Удаляет установленный пакет."""
    manifest = _read_manifest(ctx.fs, ctx.user, pkgname)
    if manifest is None:
        raise InvalidArgument(f"pkg: package '{pkgname}' is not installed")

    files = manifest.get("files", [])
    errors = []
    for fpath in files:
        try:
            # Удаляем только если файл существует
            try:
                ctx.fs.unlink(fpath, ctx.user, "/", "/")
            except NoSuchFileOrDirectory:
                pass
            except PyOSError as e:
                errors.append(str(e))
        except PyOSError:
            errors.append(fpath)

    # Удаляем manifest
    _remove_tree(ctx.fs, f"{LIB_ROOT}/{pkgname}", ctx.user)

    # Пробуем удалить пустые родительские каталоги
    removed_dirs = set()
    for fpath in files:
        parts = fpath.strip("/").split("/")
        for i in range(len(parts) - 1, 0, -1):
            parent = "/" + "/".join(parts[:i])
            if parent in removed_dirs:
                break
            try:
                node = ctx.fs.lookup(parent, ctx.user, "/", "/")
                if node.is_dir() and not node.children:
                    ctx.fs.rmdir(parent, ctx.user, "/", "/")
                    removed_dirs.add(parent)
                else:
                    break
            except (NoSuchFileOrDirectory, PyOSError):
                break

    if errors:
        return f"pkg: removed '{pkgname}' with {len(errors)} errors:\n" + "\n".join(errors)
    return f"pkg: removed '{pkgname}'"


def _list_packages(ctx):
    """Список установленных пакетов."""
    names = _get_installed_packages(ctx.fs, ctx.user)
    if not names:
        return "pkg: no packages installed"

    lines = [f"{'Package':<20} Version"]
    lines.append("-" * 40)
    for name in names:
        manifest = _read_manifest(ctx.fs, ctx.user, name)
        ver = manifest.get("pkgver", "?") if manifest else "?"
        desc = manifest.get("pkgdesc", "") if manifest else ""
        desc_str = f"  # {desc}" if desc else ""
        lines.append(f"{name:<20} {ver}{desc_str}")
    return "\n".join(lines)


def _show_package_info(ctx, pkgname):
    """Информация об установленном пакете."""
    # Сначала проверяем установленные
    manifest = _read_manifest(ctx.fs, ctx.user, pkgname)
    if manifest:
        return _format_pkg_info(manifest, installed=True)

    # Потом смотрим в кэше
    try:
        node = ctx.fs.lookup(f"{CACHE_ROOT}", ctx.user, "/", "/")
        if node.is_dir():
            for name in node.children:
                if name.startswith(pkgname + "-"):
                    meta_text = ctx.fs.read_file(
                        f"{CACHE_ROOT}/{name}/METADATA", ctx.user, "/", "/"
                    )
                    metadata = json.loads(meta_text)
                    return _format_pkg_info(metadata, installed=False)
    except (NoSuchFileOrDirectory, PyOSError, json.JSONDecodeError):
        pass

    raise InvalidArgument(f"pkg: package '{pkgname}' not found")


def _format_pkg_info(data, installed=False):
    """Форматирует метаданные пакета для вывода."""
    lines = [
        f"Package:    {data.get('pkgname', '?')}",
        f"Version:    {data.get('pkgver', '?')}-{data.get('pkgrel', '1')}",
        f"Description: {data.get('pkgdesc', '')}",
    ]
    arch = data.get("arch")
    if arch:
        lines.append(f"Architecture: {', '.join(arch) if isinstance(arch, list) else arch}")
    depends = data.get("depends", [])
    if depends:
        lines.append(f"Depends on: {', '.join(depends)}")
    lines.append(f"Status: {'installed' if installed else 'in cache'}")
    files = data.get("files")
    if files is not None:
        lines.append(f"Files: {len(files)}")
    return "\n".join(lines)


@register("pkg",
          usage="pkg install|remove|list|info [args]",
          summary="Управление пакетами",
          category="system")
def cmd_pkg(ctx, args, stdin):
    """Управление пакетами pyOS.

    Подкоманды:
      pkg install <pkgname>   — установить пакет из кэша
      pkg remove <pkgname>    — удалить пакет
      pkg list                 — показать установленные пакеты
      pkg info <pkgname>      — информация о пакете
    """
    if ctx.user.uid != 0:
        raise PermissionDenied(message="pkg: only root can manage packages")

    if not args:
        return "Usage: pkg install|remove|list|info [args]"

    subcmd = args[0]
    cmd_args = args[1:]

    if subcmd == "install":
        if not cmd_args:
            raise InvalidArgument("pkg install: missing package name")
        pkgname = cmd_args[0]

        # Ищем в кэше: точное совпадение <name>-<ver> или последнюю версию
        try:
            cache_node = ctx.fs.lookup(f"{CACHE_ROOT}", ctx.user, "/", "/")
        except NoSuchFileOrDirectory:
            raise InvalidArgument(f"pkg: no packages in cache (run makepkg first)")

        candidates = []
        if cache_node.is_dir():
            for name in cache_node.children:
                if name == pkgname or name.startswith(pkgname + "-"):
                    candidates.append(name)

        if not candidates:
            raise InvalidArgument(
                f"pkg: package '{pkgname}' not found in cache"
            )

        # Берём последний по алфавиту (версия отсортируется корректно для x.y.z)
        chosen = sorted(candidates)[-1]
        # Извлекаем версию из имени
        ver = chosen
        if chosen.startswith(pkgname + "-"):
            ver = chosen[len(pkgname) + 1:]
        else:
            ver = pkgname
            pkgname = chosen

        return _install_from_cache(ctx, pkgname, ver)

    elif subcmd == "remove":
        if not cmd_args:
            raise InvalidArgument("pkg remove: missing package name")
        return _remove_package(ctx, cmd_args[0])

    elif subcmd == "list":
        return _list_packages(ctx)

    elif subcmd == "info":
        if not cmd_args:
            raise InvalidArgument("pkg info: missing package name")
        return _show_package_info(ctx, cmd_args[0])

    else:
        raise InvalidArgument(
            f"pkg: unknown subcommand '{subcmd}' "
            "(use: install, remove, list, info)"
        )
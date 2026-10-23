"""Пакетный менеджер pyOS: makepkg и pkg.

PKGBUILD-подход (как в Arch Linux):
  - PKGBUILD — shell-подобный файл с переменными и функциями build()/package()
  - makepkg собирает пакет из PKGBUILD
  - pkg устанавливает/удаляет/просматривает пакеты
"""

import base64
import json
import re

from ..errors import (
    InvalidArgument,
    IsADirectory,
    NoSuchFileOrDirectory,
    PermissionDenied,
    PyOSError,
)
from ..fs import VirtualFile
from .registry import register

# -- пути в виртуальной ФС -------------------------------------------------
CACHE_ROOT = "/var/cache/packages"
LIB_ROOT = "/var/lib/packages"
BUILD_ROOT = "/var/tmp/makepkg"
INSTALLED_ROOT = f"{LIB_ROOT}/installed"
OWNERS_FILE = f"{LIB_ROOT}/file-owners.json"
PKG_FORMAT = 1

PKG_USAGE = (
    "Usage: pkg install <name|name@repo|name[op]version|file.pkg.json> "
    "[--dry-run] | "
    "pkg upgrade [<name>...] [--dry-run] [--force] | "
    "pkg remove <name> [--force] [--dry-run] | "
    "pkg list | pkg info <name> | pkg search <query> | "
    "pkg repo add <name> <url> [--type http|path] [--priority N] | "
    "pkg repo remove <name> | pkg repo list | "
    "pkg repo update [<name>...] | pkg repo enable <name> | "
    "pkg repo disable <name> | "
    "pkg pack <root-dir> <output-pkg> --name <name> --version <version> "
    "[--release <n>] [--arch <arch>] [--depends <dep>]... "
    "[--description <text>] [--license <text>] [--dry-run]")

# -- сравнение версий -------------------------------------------------------


_VERSION_PART = re.compile(r"\d+|[A-Za-z]+")


def _version_segments(text):
    """Разбивает версию на числовые и нечисловые сегменты."""
    return _VERSION_PART.findall(str(text))


def _compare_segment(a, b):
    """Сравнивает два сегмента: числа как числа, строки лексикографически."""
    if a == b:
        return 0
    a_num = a.isdigit()
    b_num = b.isdigit()
    if a_num and b_num:
        ai, bi = int(a), int(b)
        return (ai > bi) - (ai < bi)
    if a_num != b_num:
        # Числовой сегмент считается новее буквенного (как в alpm).
        return 1 if a_num else -1
    return (a > b) - (a < b)


def _compare_version_parts(a, b):
    """Сравнивает version/release без epoch."""
    left = _version_segments(a)
    right = _version_segments(b)
    for x, y in zip(left, right):
        if x == y:
            continue
        return _compare_segment(x, y)
    if len(left) == len(right):
        return 0
    longer = left if len(left) > len(right) else right
    rest = longer[min(len(left), len(right)):]
    # Хвостовые нули не делают версию новее: 1.0 == 1.0.0.
    if all(part.isdigit() and int(part) == 0 for part in rest):
        return 0
    return 1 if len(left) > len(right) else -1


def _split_epoch(text):
    """Отделяет необязательный epoch ``N:``."""
    if ":" in text:
        head, tail = text.split(":", 1)
        if head.isdigit():
            return int(head), tail
    return 0, text


def pkgvercmp(a, b):
    """Сравнивает версии формата ``[epoch:]version[-release]``.

    Возвращает -1, 0 или 1. Числовые сегменты сравниваются как числа,
    поэтому ``1.9 < 1.10``, ``1.0 < 1.0.1``, ``1.0-1 < 1.0-2``.
    """
    epoch_a, rest_a = _split_epoch(str(a))
    epoch_b, rest_b = _split_epoch(str(b))
    if epoch_a != epoch_b:
        return 1 if epoch_a > epoch_b else -1

    version_a, _, release_a = rest_a.partition("-")
    version_b, _, release_b = rest_b.partition("-")

    result = _compare_version_parts(version_a, version_b)
    if result:
        return result
    return _compare_version_parts(release_a, release_b)


def _cached_version_of(pkgname, dirname):
    """Извлекает версию из имени каталога кэша ``<pkgname>-<ver>``."""
    prefix = pkgname + "-"
    return dirname[len(prefix):] if dirname.startswith(prefix) else ""


def _choose_cached_package(pkgname, candidates):
    """Выбирает каталог кэша с максимальной версией через pkgvercmp.

    Возвращает ``(имя_каталога, версия)``.
    """
    chosen = candidates[0]
    for name in candidates[1:]:
        if pkgvercmp(_cached_version_of(pkgname, name),
                     _cached_version_of(pkgname, chosen)) > 0:
            chosen = name
    if chosen.startswith(pkgname + "-"):
        return chosen, chosen[len(pkgname) + 1:]
    return chosen, pkgname


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
    """Возвращает имена установленных пакетов из новой и старой базы."""
    names = set()

    # Новая база: /var/lib/packages/installed/<name>.json.
    try:
        node = fs.lookup(INSTALLED_ROOT, user, "/", "/")
        if node.is_dir():
            for name in node.children:
                if name.endswith(".json"):
                    names.add(name[:-len(".json")])
    except (NoSuchFileOrDirectory, PyOSError):
        pass

    # Старый формат: /var/lib/packages/<name>/manifest.
    try:
        node = fs.lookup(LIB_ROOT, user, "/", "/")
        if node.is_dir():
            for name, child in node.children.items():
                if name != "installed" and child.is_dir():
                    names.add(name)
    except (NoSuchFileOrDirectory, PyOSError):
        pass

    return sorted(names)


def _read_manifest(fs, user, pkgname):
    """Читает manifest установленного пакета (новый или старый формат)."""
    for path in (
        f"{INSTALLED_ROOT}/{pkgname}.json",
        f"{LIB_ROOT}/{pkgname}/manifest",
    ):
        try:
            text = fs.read_file(path, user, "/", "/")
        except NoSuchFileOrDirectory:
            continue
        except PyOSError as exc:
            raise InvalidArgument(
                f"pkg: cannot read installed DB for '{pkgname}' "
                f"('{path}'): {exc}")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidArgument(
                f"pkg: invalid installed DB for '{pkgname}' "
                f"('{path}'): {exc.msg}")
        if not isinstance(data, dict):
            raise InvalidArgument(
                f"pkg: installed DB for '{pkgname}' ('{path}') "
                "must be a JSON object")
        return data
    return None


def _write_manifest(fs, user, pkgname, manifest):
    """Пишет manifest в новую базу и в старый путь для совместимости."""
    files = manifest.get("files")
    if not isinstance(files, list):
        raise InvalidArgument(
            f"pkg: installed DB for '{pkgname}' is missing a files list")
    _ensure_dir(fs, LIB_ROOT, user)
    _ensure_dir(fs, INSTALLED_ROOT, user)
    data = json.dumps(manifest, indent=2, ensure_ascii=False)
    fs.write_file(f"{INSTALLED_ROOT}/{pkgname}.json", data, user, "/", "/",
                  create=True)
    _ensure_dir(fs, f"{LIB_ROOT}/{pkgname}", user)
    fs.write_file(f"{LIB_ROOT}/{pkgname}/manifest", data, user, "/", "/",
                  create=True)


def _read_owners(fs, user):
    """Читает базу владельцев файлов: путь -> имя пакета."""
    try:
        text = fs.read_file(OWNERS_FILE, user, "/", "/")
    except NoSuchFileOrDirectory:
        return {}
    except PyOSError as exc:
        raise InvalidArgument(
            f"pkg: cannot read file owners DB '{OWNERS_FILE}': {exc}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidArgument(
            f"pkg: invalid file owners DB '{OWNERS_FILE}': {exc.msg}")
    if not isinstance(data, dict):
        raise InvalidArgument(
            f"pkg: file owners DB '{OWNERS_FILE}' must be a JSON object")
    return {str(path): str(name) for path, name in data.items()}


def _write_owners(fs, user, owners):
    """Сохраняет базу владельцев файлов."""
    _ensure_dir(fs, LIB_ROOT, user)
    fs.write_file(OWNERS_FILE, json.dumps(owners, indent=2, sort_keys=True),
                  user, "/", "/", create=True)


def _manifest_paths(manifest):
    """Возвращает пути файлов из manifest (строки или объекты)."""
    paths = []
    for item in manifest.get("files", []) or []:
        path = item.get("path") if isinstance(item, dict) else item
        if isinstance(path, str):
            paths.append(path)
    return paths


def _install_from_cache(ctx, pkgname, pkgver, dry_run=False):
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

    # Собираем список файлов заранее, чтобы dry-run ничего не менял.
    cached_files = []
    if node and node.is_dir():
        cached_files = ["/" + rel for rel, _ in _tree_walk(node)]

    if dry_run:
        return (f"pkg: would install '{pkgname}' "
                f"(version {pkgver}, {len(cached_files)} files)")

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

    owners = _read_owners(ctx.fs, ctx.user)
    for path in installed_files:
        owners[path] = pkgname
    _write_owners(ctx.fs, ctx.user, owners)

    return f"pkg: installed '{pkgname}' (version {pkgver})"


def _copy_pkg_files(ctx, node, base_path, rel_prefix, installed_files):
    """Рекурсивно копирует файлы из пакета в корневую ФС."""
    if node.is_file():
        # Вычисляем целевой путь: rel_prefix — это путь без base_path
        target = "/" + rel_prefix
        data = ctx.fs.read_bytes(
            base_path + "/" + rel_prefix, ctx.user, "/", "/")
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


# -- установка .pkg.json ----------------------------------------------------


def _normalize_pkg_path(raw):
    """Проверяет и нормализует абсолютный путь внутри пакета."""
    if not raw.startswith("/"):
        raise InvalidArgument(f"pkg: file path must be absolute: '{raw}'")

    parts = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise InvalidArgument(
                f"pkg: path traversal is not allowed: '{raw}'")
        parts.append(part)

    if not parts:
        raise InvalidArgument(f"pkg: invalid file path: '{raw}'")
    return "/" + "/".join(parts)


def _parse_pkg_mode(raw, path):
    """Разбирает восьмеричный режим файла (например, '0755')."""
    try:
        mode = int(raw, 8) if isinstance(raw, str) else int(raw)
    except (TypeError, ValueError):
        raise InvalidArgument(f"pkg: invalid mode for '{path}': {raw!r}")
    if not 0 <= mode <= 0o7777:
        raise InvalidArgument(f"pkg: invalid mode for '{path}': {raw!r}")
    return mode


def _validate_pkg(ctx, data, source):
    """Валидирует .pkg.json и возвращает нормализованное описание."""
    if not isinstance(data, dict):
        raise InvalidArgument(f"pkg: '{source}': package must be a JSON object")

    fmt = data.get("format")
    if fmt != PKG_FORMAT:
        raise InvalidArgument(
            f"pkg: '{source}': unsupported package format {fmt!r} "
            f"(expected {PKG_FORMAT})")

    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise InvalidArgument(f"pkg: '{source}': missing package name")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise InvalidArgument(f"pkg: '{source}': invalid package name '{name}'")

    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise InvalidArgument(f"pkg: '{source}': missing package version")

    release = data.get("release", 1)
    if isinstance(release, int):
        release = str(release)
    elif not (isinstance(release, str) and release.isdigit()):
        raise InvalidArgument(f"pkg: '{source}': invalid release {release!r}")

    arch = data.get("arch", "any")
    if not isinstance(arch, str) or not arch:
        raise InvalidArgument(f"pkg: '{source}': invalid arch {arch!r}")

    description = data.get("description", "")
    if not isinstance(description, str):
        raise InvalidArgument(f"pkg: '{source}': invalid description")

    meta = {}
    for field in ("depends", "provides", "conflicts"):
        value = data.get(field, [])
        if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value):
            raise InvalidArgument(
                f"pkg: '{source}': '{field}' must be a list of strings")
        meta[field] = list(value)

    raw_files = data.get("files", [])
    if not isinstance(raw_files, list):
        raise InvalidArgument(f"pkg: '{source}': 'files' must be a list")

    files = []
    seen = set()
    for entry in raw_files:
        if not isinstance(entry, dict):
            raise InvalidArgument(
                f"pkg: '{source}': file entry must be an object")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise InvalidArgument(
                f"pkg: '{source}': file entry without path")
        path = _normalize_pkg_path(raw_path)
        if path in seen:
            raise InvalidArgument(
                f"pkg: '{source}': duplicate file '{path}'")
        seen.add(path)

        encoding = entry.get("encoding", "utf-8")
        if not isinstance(encoding, str):
            raise InvalidArgument(
                f"pkg: '{source}': encoding must be a string")
        encoding = encoding.lower()

        content = entry.get("content", "")
        if not isinstance(content, str):
            raise InvalidArgument(
                f"pkg: '{source}': content must be a string")

        if encoding in ("utf-8", "utf8"):
            content = content.encode("utf-8")
        elif encoding == "base64":
            try:
                content = base64.b64decode(content, validate=True)
            except ValueError:
                raise InvalidArgument(
                    f"pkg: '{source}': invalid base64 content for '{path}'")
        else:
            raise InvalidArgument(
                f"pkg: '{source}': unsupported encoding {encoding!r} "
                "(expected utf-8 or base64)")

        mode = _parse_pkg_mode(entry.get("mode", "0644"), path)
        owner = entry.get("owner", "root")
        group = entry.get("group", "root")
        uid = ctx.users.resolve_owner(str(owner))
        if uid is None:
            raise InvalidArgument(
                f"pkg: '{source}': unknown owner '{owner}' for '{path}'")
        gid = ctx.users.resolve_group(str(group))
        if gid is None:
            raise InvalidArgument(
                f"pkg: '{source}': unknown group '{group}' for '{path}'")

        files.append({
            "path": path,
            "mode": mode,
            "uid": uid,
            "gid": gid,
            "content": content,
        })

    return {
        "name": name,
        "version": version,
        "release": release,
        "arch": arch,
        "description": description,
        "files": files,
        **meta,
    }


# -- pkg pack ---------------------------------------------------------------

_PACK_OPTIONS = {
    "--name": "name",
    "--version": "version",
    "--release": "release",
    "--arch": "arch",
    "--depends": "depends",
    "--description": "description",
    "--license": "license",
}

_PACK_USAGE = (
    "usage: pkg pack <root-dir> <output-pkg> --name <name> "
    "--version <version> [--release <n>] [--arch <arch>] "
    "[--depends <dep>]... [--description <text>] [--license <text>] "
    "[--dry-run]")


def _pack_usage_error(message):
    raise InvalidArgument(f"pkg pack: {message} ({_PACK_USAGE})")


def _pack_normalize_path(raw, label):
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise InvalidArgument(
            f"pkg pack: {label} must be absolute: {raw!r}")
    if raw == "/":
        return "/"

    try:
        return _normalize_pkg_path(raw)
    except InvalidArgument as exc:
        text = str(exc)
        if text.startswith("pkg: "):
            text = text[len("pkg: "):]
        raise InvalidArgument(f"pkg pack: {label}: {text}")


def _parse_pack_args(args):
    positional = []
    options = {
        "name": None,
        "version": None,
        "release": None,
        "arch": None,
        "description": None,
        "license": None,
        "depends": [],
    }
    dry_run = False
    index = 0

    while index < len(args):
        arg = args[index]
        if arg == "--dry-run":
            dry_run = True
            index += 1
            continue

        if arg.startswith("--"):
            option, sep, inline_value = arg.partition("=")
            if option not in _PACK_OPTIONS:
                _pack_usage_error(f"unknown option '{option}'")

            if sep:
                value = inline_value
            else:
                if index + 1 >= len(args):
                    _pack_usage_error(f"option '{option}' requires a value")
                value = args[index + 1]
                index += 1

            if option == "--depends":
                if not value:
                    _pack_usage_error("--depends requires a non-empty value")
                options["depends"].append(value)
            else:
                options[_PACK_OPTIONS[option]] = value
            index += 1
            continue

        positional.append(arg)
        index += 1

    if len(positional) != 2:
        _pack_usage_error("expected <root-dir> and <output-pkg>")
    if not options["name"]:
        _pack_usage_error("--name is required")
    if not options["version"]:
        _pack_usage_error("--version is required")

    return positional[0], positional[1], options, dry_run


def _pack_path_label(parts):
    return "/" + "/".join(parts) if parts else "/"


def _pack_validate_component(name, parts):
    if (not isinstance(name, str) or not name
            or name in (".", "..") or "/" in name or "\\" in name):
        raise InvalidArgument(
            f"pkg pack: path traversal or unsafe path component "
            f"{name!r} at '{_pack_path_label(parts)}'")


def _pack_read_content(path, content):
    """Возвращает ``(encoding, json_content)`` для файла пакета.

    UTF-8-совместимые данные остаются читаемым текстом, остальные
    кодируются в base64.
    """
    if isinstance(content, str):
        try:
            content = content.encode("utf-8")
        except UnicodeEncodeError:
            raise InvalidArgument(
                f"pkg pack: file is not valid utf-8 text: '{path}'")
    if not isinstance(content, bytes):
        raise InvalidArgument(
            f"pkg pack: file content is neither text nor bytes: '{path}'")
    try:
        return "utf-8", content.decode("utf-8")
    except UnicodeDecodeError:
        return "base64", base64.b64encode(content).decode("ascii")


def _pack_collect(ctx, node, parts, root_path, output_path, files):
    path = _pack_path_label(parts)
    source_path = root_path.rstrip("/") + path
    if source_path == output_path:
        return

    if node.is_link():
        raise InvalidArgument(
            f"pkg pack: symlink is not supported: '{path}'")

    if node.is_dir():
        if getattr(node, "refresh", None) is not None:
            raise InvalidArgument(
                f"pkg pack: special directory is not supported: '{path}'")
        for name in sorted(node.children):
            _pack_validate_component(name, parts)
            _pack_collect(ctx, node.children[name], parts + [name],
                          root_path, output_path, files)
        return

    if not node.is_file():
        raise InvalidArgument(
            f"pkg pack: unsupported file type: '{path}'")
    if isinstance(node, VirtualFile):
        raise InvalidArgument(
            f"pkg pack: special file is not supported: '{path}'")
    if node.nlink > 1:
        raise InvalidArgument(
            f"pkg pack: hardlink is not supported: '{path}'")

    content_encoding, content = _pack_read_content(path, node.data)
    owner = ctx.users.get_by_uid(node.uid)
    if owner is None:
        raise InvalidArgument(
            f"pkg pack: unknown owner uid {node.uid} for '{path}'")
    group = ctx.users.groups.get(node.gid)
    if group is None:
        raise InvalidArgument(
            f"pkg pack: unknown group gid {node.gid} for '{path}'")

    files.append({
        "path": path,
        "mode": f"{node.mode & 0o7777:04o}",
        "owner": owner.name,
        "group": group.name,
        "encoding": content_encoding,
        "content": content,
    })


def _pack_package(ctx, args):
    if ctx.user is None or ctx.user.uid != 0:
        raise PermissionDenied(message="pkg pack: only root can create packages")
    root_raw, output_raw, options, dry_run = _parse_pack_args(args)
    if output_raw != "/" and output_raw.endswith("/"):
        _pack_usage_error("output path must name a file, not a directory")

    root_path = _pack_normalize_path(root_raw, "root directory")
    output_path = _pack_normalize_path(output_raw, "output path")
    if output_path == "/":
        raise InvalidArgument("pkg pack: output path cannot be '/'")

    try:
        root_node = ctx.fs.lookup(
            root_path, "/", "/", follow=False, user=ctx.user)
    except NoSuchFileOrDirectory:
        raise InvalidArgument(
            f"pkg pack: root directory not found: '{root_path}'")
    if root_node.is_link():
        raise InvalidArgument(
            f"pkg pack: root directory is a symlink: '{root_path}'")
    if not root_node.is_dir():
        raise InvalidArgument(
            f"pkg pack: root directory is not a directory: '{root_path}'")

    try:
        output_node = ctx.fs.lookup(
            output_path, "/", "/", follow=False, user=ctx.user)
    except NoSuchFileOrDirectory:
        output_node = None
    if output_node is not None:
        if output_node.is_dir():
            raise InvalidArgument(
                f"pkg pack: output path is a directory: '{output_path}'")
        if output_node.is_link():
            raise InvalidArgument(
                f"pkg pack: output path is a symlink: '{output_path}'")
        if not output_node.is_file() or isinstance(output_node, VirtualFile):
            raise InvalidArgument(
                f"pkg pack: output path is not a regular file: "
                f"'{output_path}'")

    parent_path = output_path.rsplit("/", 1)[0] or "/"
    try:
        parent_node = ctx.fs.lookup(
            parent_path, "/", "/", follow=False, user=ctx.user)
    except NoSuchFileOrDirectory:
        parent_node = None
    if parent_node is not None and not parent_node.is_dir():
        raise InvalidArgument(
            f"pkg pack: output parent is not a directory: '{parent_path}'")

    files = []
    _pack_collect(ctx, root_node, [], root_path, output_path, files)

    release_raw = options["release"]
    if release_raw is None:
        release_raw = "1"
    if not release_raw.isdigit():
        _pack_usage_error("--release must be a non-negative integer")
    release = int(release_raw)

    arch = options["arch"]
    if arch is None:
        arch = "any"
    if not arch:
        _pack_usage_error("--arch must not be empty")

    data = {
        "format": PKG_FORMAT,
        "name": options["name"],
        "version": options["version"],
        "release": release,
        "arch": arch,
        "description": options["description"] or "",
        "license": options["license"] or "",
        "depends": list(options["depends"]),
        "provides": [],
        "conflicts": [],
        "files": files,
    }

    # The same validator used by pkg install guarantees a usable format.
    _validate_pkg(ctx, data, output_path)

    if dry_run:
        lines = [
            f"pkg pack: would create '{output_path}' "
            f"({len(files)} files)"
        ]
        lines.extend(entry["path"] for entry in files)
        return "\n".join(lines)

    _ensure_dir(ctx.fs, parent_path, ctx.user)
    ctx.fs.write_file(
        output_path,
        json.dumps(data, indent=2, ensure_ascii=False),
        ctx.user, "/", "/", create=True)
    return f"pkg pack: created '{output_path}' ({len(files)} files)"


def _dep_name(spec):
    """Отделяет имя пакета от ограничения версии в зависимости."""
    return re.split(r"[<>= ]", str(spec), maxsplit=1)[0].strip()


def _package_dependents(ctx, pkgname):
    """Возвращает установленные пакеты, которые зависят от pkgname."""
    dependents = []
    for other in _get_installed_packages(ctx.fs, ctx.user):
        if other == pkgname:
            continue
        manifest = _read_manifest(ctx.fs, ctx.user, other)
        if not manifest:
            continue
        depends = manifest.get("depends") or []
        if not isinstance(depends, list):
            continue
        if pkgname in {_dep_name(dep) for dep in depends}:
            dependents.append(other)
    return dependents


def _installed_satisfies(ctx, pkgname, op, version):
    """Проверяет installed DB через pkgvercmp.

    Без version-spec достаточно факта установки. Иначе сравниваются
    ``version`` и ``version-release`` установленного пакета.
    """
    manifest = _read_manifest(ctx.fs, ctx.user, pkgname)
    if manifest is None:
        return False
    if op is None:
        return True

    from ..repo import _version_matches

    installed = {
        "version": str(manifest.get("version") or manifest.get("pkgver") or ""),
        "release": manifest.get("release", manifest.get("pkgrel", 1)),
    }
    if not installed["version"]:
        return False
    return _version_matches(installed, op, version, pkgvercmp)


def _install_named_package(ctx, target, dry_run=False):
    """Устанавливает пакет по имени из репозитория или из кэша makepkg."""
    from ..repo import install_package_by_name, read_repos

    config = read_repos(ctx.fs, ctx.user)
    if config["repos"]:
        return install_package_by_name(
            ctx, target, dry_run=dry_run,
            installer=_install_package_file,
            is_installed=lambda name, op, version: _installed_satisfies(
                ctx, name, op, version))

    # Обратная совместимость: без repos.json — старый путь из кэша makepkg.
    pkgname = target
    try:
        cache_node = ctx.fs.lookup(f"{CACHE_ROOT}", ctx.user, "/", "/")
    except NoSuchFileOrDirectory:
        raise InvalidArgument("pkg: no packages in cache (run makepkg first)")

    candidates = []
    if cache_node.is_dir():
        for name in cache_node.children:
            if name == pkgname or name.startswith(pkgname + "-"):
                candidates.append(name)

    if not candidates:
        raise InvalidArgument(
            f"pkg: package '{pkgname}' not found in cache")

    chosen, ver = _choose_cached_package(pkgname, candidates)
    if not chosen.startswith(pkgname + "-"):
        pkgname = chosen

    return _install_from_cache(ctx, pkgname, ver, dry_run=dry_run)


def _install_package_file(ctx, path, dry_run=False):
    """Устанавливает локальный .pkg.json из виртуальной ФС."""
    try:
        text = ctx.fs.read_file(path, ctx.user, ctx.cwd, ctx.home)
    except NoSuchFileOrDirectory:
        raise InvalidArgument(f"pkg: package file '{path}' not found")
    except PyOSError as exc:
        raise InvalidArgument(f"pkg: cannot read '{path}': {exc}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidArgument(
            f"pkg: invalid package file '{path}': {exc.msg}")

    pkg = _validate_pkg(ctx, data, path)
    name = pkg["name"]
    version = pkg["version"]
    release = pkg["release"]

    existing = _read_manifest(ctx.fs, ctx.user, name)
    if existing is not None:
        installed_version, installed_release = _manifest_identity(existing)
        installed_full = f"{installed_version}-{installed_release}"
        candidate_full = f"{version}-{release}"
        if pkgvercmp(candidate_full, installed_full) < 0:
            raise PyOSError(
                f"pkg: downgrade refused for '{name}': installed "
                f"{installed_full}, candidate {candidate_full}")
        raise PyOSError(
            f"pkg: '{name}' is already installed "
            f"(installed {installed_full}, candidate {candidate_full})"
        )

    installed = set(_get_installed_packages(ctx.fs, ctx.user))

    for dep in pkg["depends"]:
        dep_name = _dep_name(dep)
        if dep_name and dep_name not in installed:
            raise InvalidArgument(
                f"pkg: missing dependency '{dep}' for '{name}': "
                f"package '{dep_name}' is not installed")

    for conflict in pkg["conflicts"]:
        conflict_name = _dep_name(conflict)
        if conflict_name in installed:
            raise InvalidArgument(
                f"pkg: '{name}' conflicts with installed package "
                f"'{conflict_name}'")

    owners = _read_owners(ctx.fs, ctx.user)
    file_conflicts = []
    for entry in pkg["files"]:
        target = entry["path"]
        owner = owners.get(target)
        if owner:
            file_conflicts.append(
                f"  {target} (owned by {owner}, requested by {name})")
            continue
        try:
            node = ctx.fs.lookup(target, ctx.user, "/", "/")
        except NoSuchFileOrDirectory:
            continue
        if node.is_dir():
            file_conflicts.append(f"  {target} (directory)")

    if file_conflicts:
        raise InvalidArgument(
            f"pkg: file conflict in '{name}':\n" + "\n".join(file_conflicts))

    if dry_run:
        return (f"pkg: would install '{name}' "
                f"(version {version}-{release}, {len(pkg['files'])} files)")

    for entry in pkg["files"]:
        target = entry["path"]
        parent = target.rsplit("/", 1)[0] or "/"
        _ensure_dir(ctx.fs, parent, ctx.user)
        ctx.fs.write_file(target, entry["content"], ctx.user, "/", "/",
                          create=True)
        ctx.fs.chmod(target, entry["mode"], ctx.user, "/", "/")
        ctx.fs.chown(target, uid=entry["uid"], gid=entry["gid"],
                     user=ctx.user, cwd="/", home="/")

    paths = [entry["path"] for entry in pkg["files"]]
    manifest = {
        "format": PKG_FORMAT,
        "name": name,
        "version": version,
        "release": release,
        "arch": pkg["arch"],
        "description": pkg["description"],
        "depends": pkg["depends"],
        "provides": pkg["provides"],
        "conflicts": pkg["conflicts"],
        "files": paths,
        # Совместимость со старым форматом manifest.
        "pkgname": name,
        "pkgver": version,
        "pkgrel": release,
        "pkgdesc": pkg["description"],
    }
    _write_manifest(ctx.fs, ctx.user, name, manifest)
    for target in paths:
        owners[target] = name
    _write_owners(ctx.fs, ctx.user, owners)

    return f"pkg: installed '{name}' (version {version}-{release})"


# -- pkg upgrade ------------------------------------------------------------

_UPGRADE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")


def _manifest_identity(manifest):
    """Возвращает ``(version, release)`` из нового или старого manifest."""
    version = manifest.get("version")
    release = manifest.get("release")

    if version is None:
        raw = str(manifest.get("pkgver") or "0")
        if release is None and "-" in raw:
            version, release = raw.rsplit("-", 1)
        else:
            version = raw

    if release is None:
        release = manifest.get("pkgrel", 1)
    if release is None or release == "":
        release = 1

    return str(version), str(release)


def _recover_depends_from_indexes(indexes, name, version, release):
    """Пытается восстановить depends установленной версии из индексов."""
    for _repo, index in indexes:
        for package in index["packages"]:
            if (package["name"] == name
                    and str(package["version"]) == version
                    and str(package["release"]) == release):
                return list(package.get("depends", []) or [])
    return []


def _installed_upgrade_states(ctx, indexes):
    """Читает installed DB и восстанавливает files/depends, если нужно."""
    owners = _read_owners(ctx.fs, ctx.user)
    states = {}

    for name in _get_installed_packages(ctx.fs, ctx.user):
        manifest = _read_manifest(ctx.fs, ctx.user, name)
        if manifest is None:
            continue

        version, release = _manifest_identity(manifest)

        raw_files = manifest.get("files")
        if isinstance(raw_files, list):
            files = _manifest_paths(manifest)
        else:
            files = sorted(path for path, owner in owners.items()
                           if owner == name)

        raw_depends = manifest.get("depends")
        if isinstance(raw_depends, list):
            depends = [dep for dep in raw_depends if isinstance(dep, str)]
        else:
            depends = _recover_depends_from_indexes(
                indexes, name, version, release)

        states[name] = {
            "name": name,
            "version": version,
            "release": release,
            "full": f"{version}-{release}",
            "files": files,
            "depends": depends,
            "manifest": manifest,
        }

    return states


def _state_matches(state, op, version, pkgvercmp):
    """Проверяет version-spec для установленного состояния."""
    if op is None:
        return True
    from ..repo import _version_matches

    installed = {"version": state["version"], "release": state["release"]}
    return _version_matches(installed, op, version, pkgvercmp)


def _upgrade_plan_entry(action, repo, package, old=None, required_by=None):
    """Создаёт запись плана upgrade/install."""
    return {
        "action": action,
        "repo": repo,
        "package": package,
        "old": old,
        "required_by": required_by,
    }


def _plan_upgrade(ctx, names, force):
    """Строит план upgrade и применяет ограничения обратных зависимостей.

    Возвращает ``(entries, held, warnings, planned)``. В ``entries`` только
    пакеты, которые будут применены, в топологическом порядке.
    """
    from ..repo import (
        _enabled_repos,
        _package_full_version,
        _select_package,
        _version_matches,
        find_upgrade_candidate,
        parse_dependency,
        read_cached_index,
        read_repos,
    )

    config = read_repos(ctx.fs, ctx.user)
    repos = _enabled_repos(config)
    indexes = [(repo, read_cached_index(ctx.fs, ctx.user, repo))
               for repo in repos]
    states = _installed_upgrade_states(ctx, indexes)

    requested = list(dict.fromkeys(names)) if names else None
    if requested is not None:
        missing = [name for name in requested if name not in states]
        if missing:
            raise InvalidArgument(
                f"pkg upgrade: package '{missing[0]}' is not installed")
        candidate_names = requested
    else:
        candidate_names = sorted(states)

    # Сначала выбираем корневые upgrade-кандидаты, чтобы зависимости не
    # понижали версию пакета, который пользователь тоже просил обновить.
    roots = {}
    for name in candidate_names:
        state = states[name]
        candidate = find_upgrade_candidate(indexes, name, pkgvercmp)
        if candidate is None:
            continue
        repo, package = candidate
        if pkgvercmp(_package_full_version(package), state["full"]) <= 0:
            continue
        roots[name] = _upgrade_plan_entry(
            "upgrade", repo, package, old=state)

    planned = dict(roots)
    order = []
    resolved = set()
    stack = []

    def add_dependency(raw, required_by):
        dep = parse_dependency(raw)
        dep_name = dep["name"]

        existing = planned.get(dep_name)
        if existing is not None:
            if _version_matches(existing["package"], dep["op"],
                                dep["version"], pkgvercmp):
                return dep
            raise InvalidArgument(
                f"pkg upgrade: dependency '{raw}' required by "
                f"'{required_by}' conflicts with planned '{dep_name}' "
                f"{_package_full_version(existing['package'])}")

        state = states.get(dep_name)
        if state is not None:
            if _state_matches(state, dep["op"], dep["version"], pkgvercmp):
                return dep

            repo, package = _select_package(
                indexes, dep_name, dep["op"], dep["version"], None,
                required_by, pkgvercmp)
            comparison = pkgvercmp(
                _package_full_version(package), state["full"])
            if comparison < 0:
                raise InvalidArgument(
                    f"pkg upgrade: downgrade refused for package "
                    f"'{dep_name}': installed {state['full']}, candidate "
                    f"{_package_full_version(package)} "
                    f"(required by '{required_by}', repository "
                    f"'{repo['name']}')")
            if comparison == 0:
                raise InvalidArgument(
                    f"pkg upgrade: dependency '{raw}' required by "
                    f"'{required_by}' is not satisfied by installed "
                    f"version {state['full']}")

            planned[dep_name] = _upgrade_plan_entry(
                "upgrade", repo, package, old=state,
                required_by=required_by)
            return dep

        repo, package = _select_package(
            indexes, dep_name, dep["op"], dep["version"], None,
            required_by, pkgvercmp)
        planned[dep_name] = _upgrade_plan_entry(
            "install", repo, package, required_by=required_by)
        return dep

    def resolve(name):
        if name in resolved:
            return
        if name in stack:
            start = stack.index(name)
            chain = stack[start:] + [name]
            raise InvalidArgument(
                f"pkg upgrade: dependency cycle: {' -> '.join(chain)}")

        entry = planned[name]
        stack.append(name)
        try:
            for raw in entry["package"].get("depends", []) or []:
                dep = add_dependency(raw, name)
                if dep["name"] in planned:
                    resolve(dep["name"])
        finally:
            stack.pop()

        resolved.add(name)
        order.append(name)

    for name in roots:
        resolve(name)

    active = set(planned)
    held = {}
    warnings = []

    def effective_depends(dependent):
        if dependent in active:
            return list(
                planned[dependent]["package"].get("depends", []) or [])
        state = states.get(dependent)
        return list(state["depends"]) if state is not None else []

    def reverse_break(name):
        entry = planned[name]
        if entry["action"] != "upgrade":
            return None
        package = entry["package"]

        for dependent in sorted(states):
            if dependent == name:
                continue
            for raw in effective_depends(dependent):
                try:
                    dep = parse_dependency(raw)
                except InvalidArgument:
                    continue
                if dep["name"] != name:
                    continue
                if not _version_matches(
                        package, dep["op"], dep["version"], pkgvercmp):
                    return (f"would break reverse dependency: "
                            f"{dependent} requires {raw}")
        return None

    def dependency_problem(name):
        entry = planned[name]
        for raw in entry["package"].get("depends", []) or []:
            dep = parse_dependency(raw)
            dep_name = dep["name"]

            if dep_name in active:
                planned_dep = planned[dep_name]["package"]
                if not _version_matches(
                        planned_dep, dep["op"], dep["version"], pkgvercmp):
                    return (f"dependency '{raw}' required by '{name}' is "
                            f"not satisfied by planned '{dep_name}' "
                            f"{_package_full_version(planned_dep)}")
                continue

            state = states.get(dep_name)
            if state is not None:
                if _state_matches(
                        state, dep["op"], dep["version"], pkgvercmp):
                    continue
                return (f"dependency '{raw}' required by '{name}' is not "
                        f"installed or does not satisfy the requirement "
                        f"(installed {state['full']})")

            return (f"dependency '{raw}' required by '{name}' is not "
                    "installed")
        return None

    warned = set()
    while True:
        changed = False

        if force:
            for name in order:
                if name not in active:
                    continue
                if planned[name]["action"] != "upgrade":
                    continue
                reason = reverse_break(name)
                if reason is not None and name not in warned:
                    entry = planned[name]
                    warnings.append(
                        f"upgrade {name} {entry['old']['full']} -> "
                        f"{_package_full_version(entry['package'])}: "
                        f"{reason}")
                    warned.add(name)
        else:
            for name in list(order):
                if name not in active:
                    continue
                if planned[name]["action"] != "upgrade":
                    continue
                reason = reverse_break(name)
                if reason is None:
                    continue
                if requested is not None:
                    entry = planned[name]
                    raise InvalidArgument(
                        f"pkg upgrade: upgrade '{name}' "
                        f"{entry['old']['full']} -> "
                        f"{_package_full_version(entry['package'])} "
                        f"refused: {reason}")
                held[name] = reason
                active.remove(name)
                changed = True

        for name in list(order):
            if name not in active:
                continue
            reason = dependency_problem(name)
            if reason is None:
                continue

            entry = planned[name]
            old_text = (entry["old"]["full"] if entry["old"] is not None
                        else "not installed")
            new_text = _package_full_version(entry["package"])

            if force:
                raise InvalidArgument(
                    f"pkg upgrade: '{name}' {old_text} -> {new_text} "
                    f"cannot be applied: {reason}")
            if requested is not None:
                raise InvalidArgument(
                    f"pkg upgrade: upgrade '{name}' {old_text} -> "
                    f"{new_text} cannot be applied: {reason}")

            held[name] = reason
            active.remove(name)
            changed = True

        if not changed:
            break

    # Убираем из плана пакеты, которые больше не нужны после held back.
    wanted = set()

    def mark_needed(name):
        if name in wanted or name not in active:
            return
        wanted.add(name)
        for raw in planned[name]["package"].get("depends", []) or []:
            dep = parse_dependency(raw)
            if dep["name"] in active:
                mark_needed(dep["name"])

    for name in roots:
        if name in active:
            mark_needed(name)

    entries = [planned[name] for name in order if name in wanted]
    return entries, held, warnings, planned


def _format_upgrade_plan(entries, held, warnings, planned):
    """Форматирует dry-run план upgrade."""
    lines = ["plan:"]
    if not entries:
        lines.append("  (no changes)")
    for entry in entries:
        package = entry["package"]
        name = package["name"]
        full = f"{package['version']}-{package['release']}"
        if entry["action"] == "upgrade":
            lines.append(
                f"  upgrade {name} {entry['old']['full']} -> {full} "
                f"({entry['repo']['name']})")
        else:
            parent = entry["required_by"]
            suffix = (f"dependency of {parent}" if parent is not None
                      else entry["repo"]["name"])
            lines.append(f"  install {name} {full} ({suffix})")

    lines.append("held back:")
    if not held:
        lines.append("  (none)")
    else:
        for name in sorted(held):
            entry = planned[name]
            package = entry["package"]
            full = f"{package['version']}-{package['release']}"
            if entry["action"] == "upgrade":
                lines.append(
                    f"  {name} {entry['old']['full']} -> {full} "
                    f"({held[name]})")
            else:
                lines.append(f"  install {name} {full} ({held[name]})")

    lines.append("warnings:")
    if not warnings:
        lines.append("  none")
    else:
        lines.extend(f"  {warning}" for warning in warnings)

    if not entries and not held:
        lines.append("pkg: nothing to do")
    return "\n".join(lines)


def _upgrade_notice_lines(held, warnings, planned):
    """Секции held back/warnings для реального применения."""
    lines = []
    if held:
        lines.append("held back:")
        for name in sorted(held):
            entry = planned[name]
            package = entry["package"]
            full = f"{package['version']}-{package['release']}"
            if entry["action"] == "upgrade":
                lines.append(
                    f"  {name} {entry['old']['full']} -> {full} "
                    f"({held[name]})")
            else:
                lines.append(f"  install {name} {full} ({held[name]})")
    if warnings:
        lines.append("warnings:")
        lines.extend(f"  {warning}" for warning in warnings)
    return lines


def _validate_upgrade_package(entry, package):
    """Проверяет, что скачанный .pkg.json соответствует записи индекса."""
    index_package = entry["package"]
    name = entry["package"]["name"]
    if package["name"] != name:
        raise InvalidArgument(
            f"pkg upgrade: downloaded package name '{package['name']}' "
            f"does not match index name '{name}'")
    if str(package["version"]) != str(index_package["version"]):
        raise InvalidArgument(
            f"pkg upgrade: '{name}' downloaded version "
            f"{package['version']} does not match index "
            f"{index_package['version']}")
    if str(package["release"]) != str(index_package["release"]):
        raise InvalidArgument(
            f"pkg upgrade: '{name}' downloaded release "
            f"{package['release']} does not match index "
            f"{index_package['release']}")


def _upgrade_precheck_files(ctx, targets, owners):
    """Проверяет файловые конфликты для всей транзакции до записи."""
    by_path = {}
    for name, paths in targets.items():
        for path in paths:
            by_path.setdefault(path, set()).add(name)

    for path, names in sorted(by_path.items()):
        if len(names) > 1:
            raise InvalidArgument(
                f"pkg upgrade: file conflict in transaction: '{path}' "
                f"is provided by {', '.join(sorted(names))}")

    transaction = set(targets)
    for name in sorted(targets):
        for path in targets[name]:
            owner = owners.get(path)
            if owner is not None and owner != name and owner not in transaction:
                raise InvalidArgument(
                    f"pkg upgrade: file conflict for '{name}': '{path}' "
                    f"is owned by '{owner}' (requested by '{name}')")
            if owner is None:
                try:
                    node = ctx.fs.lookup(path, ctx.user, "/", "/")
                except NoSuchFileOrDirectory:
                    continue
                if node.is_dir():
                    raise InvalidArgument(
                        f"pkg upgrade: file conflict for '{name}': "
                        f"'{path}' is a directory")


def _apply_upgrade_package(ctx, entry, package, targets):
    """Безопасно применяет upgrade существующего пакета.

    Новые файлы пишутся, manifest и owners обновляются, затем удаляются
    только старые файлы этого же пакета, если они не стали чужими.
    """
    name = package["name"]
    old = entry["old"]
    new_paths = [item["path"] for item in package["files"]]

    for item in package["files"]:
        target = item["path"]
        parent = target.rsplit("/", 1)[0] or "/"
        try:
            _ensure_dir(ctx.fs, parent, ctx.user)
            ctx.fs.write_file(target, item["content"], ctx.user, "/", "/",
                              create=True)
            ctx.fs.chmod(target, item["mode"], ctx.user, "/", "/")
            ctx.fs.chown(target, uid=item["uid"], gid=item["gid"],
                         user=ctx.user, cwd="/", home="/")
        except PyOSError as exc:
            raise PyOSError(
                f"pkg upgrade: failed to write '{target}' for '{name}': "
                f"{exc}")

    old_manifest = old.get("manifest") or {}
    manifest = {
        "format": PKG_FORMAT,
        "name": name,
        "version": package["version"],
        "release": package["release"],
        "arch": package["arch"],
        "description": package["description"],
        "depends": package["depends"],
        "provides": package["provides"],
        "conflicts": package["conflicts"],
        "files": new_paths,
        "pkgname": name,
        "pkgver": package["version"],
        "pkgrel": package["release"],
        "pkgdesc": package["description"],
    }
    if "installed_at" in old_manifest:
        manifest["installed_at"] = old_manifest["installed_at"]

    try:
        _write_manifest(ctx.fs, ctx.user, name, manifest)
    except PyOSError as exc:
        raise PyOSError(
            f"pkg upgrade: failed to update installed DB for '{name}': "
            f"{exc}")

    owners = _read_owners(ctx.fs, ctx.user)
    for path in new_paths:
        owners[path] = name

    claimed_by_others = set()
    for other, paths in targets.items():
        if other != name:
            claimed_by_others.update(paths)

    pruned = 0
    for path in sorted(set(old["files"])):
        if path in new_paths:
            continue
        if path in claimed_by_others:
            continue
        if owners.get(path) != name:
            continue
        try:
            node = ctx.fs.lookup(path, ctx.user, "/", "/")
        except NoSuchFileOrDirectory:
            node = None
        if node is not None and node.is_dir():
            continue
        try:
            ctx.fs.unlink(path, ctx.user, "/", "/")
        except NoSuchFileOrDirectory:
            pass
        except PyOSError as exc:
            raise PyOSError(
                f"pkg upgrade: failed to prune '{path}' for '{name}': "
                f"{exc}")
        owners.pop(path, None)
        pruned += 1

    try:
        _write_owners(ctx.fs, ctx.user, owners)
    except PyOSError as exc:
        raise PyOSError(
            f"pkg upgrade: failed to update file owners for '{name}': "
            f"{exc}")

    return pruned


def _upgrade_packages(ctx, names, dry_run=False, force=False):
    """Выполняет pkg upgrade после полной проверки плана."""
    from ..repo import _cache_package

    entries, held, warnings, planned = _plan_upgrade(ctx, names, force)

    if dry_run:
        return _format_upgrade_plan(entries, held, warnings, planned)

    notice_lines = _upgrade_notice_lines(held, warnings, planned)
    if not entries:
        lines = list(notice_lines)
        lines.append("pkg: nothing to do")
        return "\n".join(lines)

    # Скачиваем и валидируем весь план до первой записи в installed DB/ФС.
    download_lines = []
    package_data = {}
    cache_paths = {}

    for entry in entries:
        repo = entry["repo"]
        package = entry["package"]
        name = package["name"]

        line = (f"downloading {package['filename']} from "
                f"{repo['name']}")
        cache_path, reused = _cache_package(
            ctx, repo, package, with_status=True)
        if reused:
            line += " (cached)"
        download_lines.append(line)
        download_lines.append(
            f"verifying sha256 {package['filename']}")

        try:
            text = ctx.fs.read_file(cache_path, ctx.user, "/", "/")
        except PyOSError as exc:
            raise InvalidArgument(
                f"pkg upgrade: cannot read cached package for "
                f"'{name}': {exc}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidArgument(
                f"pkg upgrade: invalid package file for '{name}': "
                f"{exc.msg}")

        validated = _validate_pkg(ctx, data, cache_path)
        _validate_upgrade_package(entry, validated)
        package_data[name] = validated
        cache_paths[name] = cache_path

    targets = {
        name: [item["path"] for item in package["files"]]
        for name, package in package_data.items()
    }
    owners = _read_owners(ctx.fs, ctx.user)
    _upgrade_precheck_files(ctx, targets, owners)

    lines = list(notice_lines) + download_lines
    for entry in entries:
        package = entry["package"]
        name = package["name"]
        full = f"{package['version']}-{package['release']}"

        if entry["action"] == "install":
            lines.append(f"installing {name} {full}")
            lines.append(
                _install_package_file(ctx, cache_paths[name], dry_run=False))
            continue

        lines.append(f"upgrading {name} {entry['old']['full']} -> {full}")
        pruned = _apply_upgrade_package(
            ctx, entry, package_data[name], targets)
        if pruned:
            suffix = "file" if pruned == 1 else "files"
            lines.append(f"pruning {pruned} old {suffix} owned by {name}")

    lines.append("done")
    return "\n".join(lines)


def _remove_package(ctx, pkgname, force=False, dry_run=False):
    """Удаляет установленный пакет."""
    manifest = _read_manifest(ctx.fs, ctx.user, pkgname)
    if manifest is None:
        raise InvalidArgument(f"pkg: package '{pkgname}' is not installed")

    dependents = _package_dependents(ctx, pkgname)
    if dependents and not force:
        raise InvalidArgument(
            f"pkg: cannot remove '{pkgname}': required by "
            f"{', '.join(dependents)} (use --force to override)")

    if dry_run:
        return f"pkg: would remove '{pkgname}'"

    owners = _read_owners(ctx.fs, ctx.user)
    if isinstance(manifest.get("files"), list):
        files = _manifest_paths(manifest)
    else:
        files = [path for path, owner in owners.items() if owner == pkgname]
        if not files:
            raise InvalidArgument(
                f"pkg: installed DB for '{pkgname}' is missing a files list "
                "and has no owned files; refusing to remove")
    errors = []
    for fpath in files:
        owner = owners.get(fpath)
        if owner is not None and owner != pkgname:
            continue
        removed = False
        try:
            # Удаляем только если файл существует
            ctx.fs.unlink(fpath, ctx.user, "/", "/")
            removed = True
        except NoSuchFileOrDirectory:
            removed = True
        except PyOSError as exc:
            errors.append(str(exc))
        if removed:
            owners.pop(fpath, None)

    # Удаляем manifest обоих форматов.
    try:
        ctx.fs.unlink(f"{INSTALLED_ROOT}/{pkgname}.json", ctx.user, "/", "/")
    except NoSuchFileOrDirectory:
        pass
    except PyOSError:
        pass
    _remove_tree(ctx.fs, f"{LIB_ROOT}/{pkgname}", ctx.user)
    _write_owners(ctx.fs, ctx.user, owners)

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

    # Потом смотрим в кэше — выбираем самую новую версию.
    try:
        node = ctx.fs.lookup(f"{CACHE_ROOT}", ctx.user, "/", "/")
        if node.is_dir():
            candidates = [name for name in node.children
                          if name.startswith(pkgname + "-")]
            if candidates:
                name, _ = _choose_cached_package(pkgname, candidates)
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
    name = data.get("pkgname", data.get("name", "?"))
    version = data.get("pkgver", data.get("version", "?"))
    release = data.get("pkgrel", data.get("release", "1"))
    description = data.get("pkgdesc", data.get("description", ""))
    lines = [
        f"Package:    {name}",
        f"Version:    {version}-{release}",
        f"Description: {description}",
    ]
    arch = data.get("arch")
    if arch:
        lines.append(f"Architecture: {', '.join(arch) if isinstance(arch, list) else arch}")
    depends = data.get("depends", [])
    if depends:
        lines.append(f"Depends on: {', '.join(depends)}")
    provides = data.get("provides", [])
    if provides:
        lines.append(f"Provides: {', '.join(provides)}")
    conflicts = data.get("conflicts", [])
    if conflicts:
        lines.append(f"Conflicts: {', '.join(conflicts)}")
    lines.append(f"Status: {'installed' if installed else 'in cache'}")
    files = data.get("files")
    if files is not None:
        lines.append(f"Files: {len(files)}")
    return "\n".join(lines)


@register("pkg",
          usage=PKG_USAGE,
          summary="Управление пакетами",
          category="system")
def cmd_pkg(ctx, args, stdin):
    """Управление пакетами pyOS.

    Подкоманды:
      pkg install <path>      — установить локальный .pkg.json из виртуальной ФС
      pkg install <name>      — установить из репозитория (или кэша makepkg)
      pkg install <name>@<repo> — только из указанного репозитория
      pkg install <name>=<version> / >= / <= / > / < — version-spec
      pkg upgrade [<name>...] [--dry-run] [--force] — обновить пакеты
      pkg remove <pkgname>    — удалить пакет
      pkg list                — показать установленные пакеты
      pkg info <pkgname>      — информация о пакете
      pkg search <query>      — поиск по name/description в кэшах репозиториев
      pkg repo ...            — управление репозиториями
    """
    if not args:
        return PKG_USAGE

    subcmd = args[0]
    cmd_args = args[1:]

    # pkg repo list и pkg search доступны всем, остальные pkg — только root.
    repo_list = subcmd == "repo" and cmd_args[:1] == ["list"]
    read_only = (repo_list or subcmd == "search"
                 or subcmd in ("help", "--help", "-h"))
    if not read_only and ctx.user.uid != 0:
        raise PermissionDenied(message="pkg: only root can manage packages")

    if subcmd in ("help", "--help", "-h"):
        return PKG_USAGE

    if subcmd == "repo":
        # Ленивый импорт: разрывает цикл repo -> commands.network_cmds ->
        # commands.__init__ -> package_cmds -> repo.
        from ..repo import handle_repo_command

        return handle_repo_command(ctx, cmd_args)

    if subcmd == "search":
        if not cmd_args:
            raise InvalidArgument(
                "pkg search: missing query (usage: pkg search <query>)")
        if len(cmd_args) > 1:
            raise InvalidArgument(
                "pkg search: too many arguments (usage: pkg search <query>)")
        query = cmd_args[0].strip()
        if not query:
            raise InvalidArgument(
                "pkg search: empty query (usage: pkg search <query>)")
        from ..repo import search_packages

        return search_packages(ctx, query)

    if subcmd == "install":
        dry_run = "--dry-run" in cmd_args
        unknown = [a for a in cmd_args
                   if a.startswith("--") and a != "--dry-run"]
        if unknown:
            raise InvalidArgument(f"pkg install: unknown option '{unknown[0]}'")

        positional = [a for a in cmd_args if not a.startswith("--")]
        if not positional:
            raise InvalidArgument(
                "pkg install: missing package name or file "
                "(usage: pkg install <name|file.pkg.json> [--dry-run])")
        if len(positional) > 1:
            raise InvalidArgument(
                "pkg install: too many arguments "
                "(usage: pkg install <name|file.pkg.json> [--dry-run])")

        target = positional[0]
        if "/" in target or target.endswith(".json"):
            return _install_package_file(ctx, target, dry_run=dry_run)

        return _install_named_package(ctx, target, dry_run=dry_run)

    elif subcmd == "upgrade":
        allowed = {"--dry-run", "--force"}
        unknown = [a for a in cmd_args
                   if a.startswith("--") and a not in allowed]
        if unknown:
            raise InvalidArgument(
                f"pkg upgrade: unknown option '{unknown[0]}' "
                "(usage: pkg upgrade [<name>...] [--dry-run] [--force])")

        positional = [a for a in cmd_args if not a.startswith("--")]
        for name in positional:
            if not _UPGRADE_NAME_RE.match(name):
                raise InvalidArgument(
                    f"pkg upgrade: invalid package name '{name}' "
                    "(version specs are not supported)")

        return _upgrade_packages(
            ctx, positional,
            dry_run="--dry-run" in cmd_args,
            force="--force" in cmd_args)

    elif subcmd == "remove":
        allowed = {"--force", "--dry-run"}
        unknown = [a for a in cmd_args
                   if a.startswith("--") and a not in allowed]
        if unknown:
            raise InvalidArgument(
                f"pkg remove: unknown option '{unknown[0]}' "
                "(usage: pkg remove <name> [--force] [--dry-run])")
        positional = [a for a in cmd_args if not a.startswith("--")]
        if not positional:
            raise InvalidArgument(
                "pkg remove: missing package name "
                "(usage: pkg remove <name> [--force] [--dry-run])")
        if len(positional) > 1:
            raise InvalidArgument(
                "pkg remove: too many arguments "
                "(usage: pkg remove <name> [--force] [--dry-run])")
        return _remove_package(ctx, positional[0],
                               force="--force" in cmd_args,
                               dry_run="--dry-run" in cmd_args)

    elif subcmd == "list":
        return _list_packages(ctx)

    elif subcmd == "info":
        if not cmd_args:
            raise InvalidArgument(
                "pkg info: missing package name (usage: pkg info <name>)")
        return _show_package_info(ctx, cmd_args[0])

    elif subcmd == "pack":
        return _pack_package(ctx, cmd_args)

    else:
        raise InvalidArgument(
            f"pkg: unknown subcommand '{subcmd}' "
            "(use: install, upgrade, search, remove, list, info, repo, "
            "pack, help)")
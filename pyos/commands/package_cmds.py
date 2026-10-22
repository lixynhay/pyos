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
INSTALLED_ROOT = f"{LIB_ROOT}/installed"
OWNERS_FILE = f"{LIB_ROOT}/file-owners.json"
PKG_FORMAT = 1

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
            return json.loads(fs.read_file(path, user, "/", "/"))
        except (NoSuchFileOrDirectory, json.JSONDecodeError, PyOSError):
            continue
    return None


def _write_manifest(fs, user, pkgname, manifest):
    """Пишет manifest в новую базу и в старый путь для совместимости."""
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
        data = json.loads(fs.read_file(OWNERS_FILE, user, "/", "/"))
    except (NoSuchFileOrDirectory, json.JSONDecodeError, PyOSError):
        return {}
    if not isinstance(data, dict):
        return {}
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
        if not isinstance(encoding, str) or encoding.lower() not in (
                "utf-8", "utf8"):
            raise InvalidArgument(
                f"pkg: '{source}': unsupported encoding {encoding!r} "
                "(only utf-8 is supported)")

        content = entry.get("content", "")
        if not isinstance(content, str):
            raise InvalidArgument(
                f"pkg: '{source}': content must be a string")

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
        raise PyOSError(
            f"pkg: '{name}' is already installed "
            f"(version {existing.get('pkgver', existing.get('version', '?'))})"
        )

    installed = set(_get_installed_packages(ctx.fs, ctx.user))

    for dep in pkg["depends"]:
        dep_name = _dep_name(dep)
        if dep_name and dep_name not in installed:
            raise InvalidArgument(
                f"pkg: missing dependency '{dep}' for '{name}'")

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
            file_conflicts.append(f"  {target} (owned by {owner})")
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

    files = _manifest_paths(manifest)
    owners = _read_owners(ctx.fs, ctx.user)
    errors = []
    for fpath in files:
        owner = owners.get(fpath)
        if owner is not None and owner != pkgname:
            continue
        try:
            # Удаляем только если файл существует
            ctx.fs.unlink(fpath, ctx.user, "/", "/")
        except NoSuchFileOrDirectory:
            pass
        except PyOSError as exc:
            errors.append(str(exc))
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
          usage=("pkg install <name|file.pkg.json> [--dry-run] | "
                 "pkg remove <name> [--force] [--dry-run] | "
                 "pkg list | pkg info <name>"),
          summary="Управление пакетами",
          category="system")
def cmd_pkg(ctx, args, stdin):
    """Управление пакетами pyOS.

    Подкоманды:
      pkg install <path>      — установить локальный .pkg.json из виртуальной ФС
      pkg install <pkgname>   — установить пакет из кэша makepkg
      pkg remove <pkgname>    — удалить пакет
      pkg list                — показать установленные пакеты
      pkg info <pkgname>      — информация о пакете
    """
    if ctx.user.uid != 0:
        raise PermissionDenied(message="pkg: only root can manage packages")

    if not args:
        return "Usage: pkg install|remove|list|info [args]"

    subcmd = args[0]
    cmd_args = args[1:]

    if subcmd == "install":
        dry_run = "--dry-run" in cmd_args
        unknown = [a for a in cmd_args
                   if a.startswith("--") and a != "--dry-run"]
        if unknown:
            raise InvalidArgument(f"pkg install: unknown option '{unknown[0]}'")

        positional = [a for a in cmd_args if not a.startswith("--")]
        if not positional:
            raise InvalidArgument("pkg install: missing package name or file")
        if len(positional) > 1:
            raise InvalidArgument("pkg install: too many arguments")

        target = positional[0]
        if "/" in target or target.endswith(".json"):
            return _install_package_file(ctx, target, dry_run=dry_run)

        pkgname = target

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

        # Сравниваем версии численно: 1.10 новее 1.9.
        def candidate_version(name):
            prefix = pkgname + "-"
            return name[len(prefix):] if name.startswith(prefix) else ""

        chosen = candidates[0]
        for name in candidates[1:]:
            if pkgvercmp(candidate_version(name),
                         candidate_version(chosen)) > 0:
                chosen = name
        # Извлекаем версию из имени
        ver = chosen
        if chosen.startswith(pkgname + "-"):
            ver = chosen[len(pkgname) + 1:]
        else:
            ver = pkgname
            pkgname = chosen

        return _install_from_cache(ctx, pkgname, ver, dry_run=dry_run)

    elif subcmd == "remove":
        allowed = {"--force", "--dry-run"}
        unknown = [a for a in cmd_args
                   if a.startswith("--") and a not in allowed]
        if unknown:
            raise InvalidArgument(f"pkg remove: unknown option '{unknown[0]}'")
        positional = [a for a in cmd_args if not a.startswith("--")]
        if not positional:
            raise InvalidArgument("pkg remove: missing package name")
        if len(positional) > 1:
            raise InvalidArgument("pkg remove: too many arguments")
        return _remove_package(ctx, positional[0],
                               force="--force" in cmd_args,
                               dry_run="--dry-run" in cmd_args)

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
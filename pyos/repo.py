"""Конфигурация репозиториев пакетов и чтение index.json.

``type="path"`` читается из виртуальной ФС, ``type="http"`` — через общий
сетевой helper из ``commands.network_cmds``.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from functools import cmp_to_key

from .commands import network_cmds
from .errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError

REPOS_CONFIG = "/etc/pyos/repos.json"
REPO_CACHE_ROOT = "/var/cache/repos"
PACKAGE_CACHE_ROOT = "/var/cache/packages"
# Максимальный размер index.json (4 MiB). Больше — не парсим.
REPO_INDEX_MAX_BYTES = 4 * 1024 * 1024

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DEP_RE = re.compile(r"^([A-Za-z0-9@._+-]+)\s*(?:([<>=]+)\s*(\S+))?$")
_INSTALL_SPEC_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9+._-]*)"
    r"(?:@(?P<repo>[a-z0-9]+(?:-[a-z0-9]+)*))?"
    r"(?:(?P<op>>=|<=|=|>|<)(?P<version>.+))?$"
)
_DEP_SPEC_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9+._-]*)"
    r"(?:\s*(?P<op>>=|<=|=|>|<)\s*(?P<version>[^\s]+))?$"
)


# -- утилиты ----------------------------------------------------------------


def _ensure_dir(fs, path, user):
    """Создаёт каталог и родителей, если их ещё нет."""
    try:
        node = fs.lookup(path, user, "/", "/")
        if node.is_dir():
            return
        raise InvalidArgument(f"repo: '{path}' is not a directory")
    except NoSuchFileOrDirectory:
        pass
    fs.mkdir(path, user, "/", "/", parents=True)


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- repos.json -------------------------------------------------------------


def _validate_repo_name(name):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise InvalidArgument(
            f"repo: invalid name {name!r} "
            "(expected lowercase letters, digits and dashes)")


def _validate_repo_url(name, url, repo_type):
    if not isinstance(url, str) or not url:
        raise InvalidArgument(f"repo: missing url for '{name}'")

    if repo_type == "http":
        if url.startswith("http://"):
            raise InvalidArgument(
                f"repo: '{name}': http:// is not allowed, use https://")
        if not url.startswith("https://"):
            raise InvalidArgument(
                f"repo: '{name}': URL must start with https://")
        if not url.endswith("/"):
            raise InvalidArgument(
                f"repo: '{name}': URL must end with '/'")
        return

    if not url.startswith("/"):
        raise InvalidArgument(
            f"repo: '{name}': path URL must be an absolute virtual path")
    if not url.endswith("/"):
        raise InvalidArgument(
            f"repo: '{name}': path URL must end with '/'")
    if ".." in url.split("/"):
        raise InvalidArgument(
            f"repo: '{name}': path traversal is not allowed")


def _validate_repo_entry(entry):
    if not isinstance(entry, dict):
        raise InvalidArgument("repo: repository entry must be a JSON object")

    name = entry.get("name")
    _validate_repo_name(name)

    repo_type = entry.get("type", "http")
    if not isinstance(repo_type, str) or repo_type not in ("http", "path"):
        raise InvalidArgument(
            f"repo: invalid type {repo_type!r} for '{name}' "
            "(expected 'http' or 'path')")

    url = entry.get("url")
    _validate_repo_url(name, url, repo_type)

    priority = entry.get("priority", 10)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise InvalidArgument(
            f"repo: invalid priority {priority!r} for '{name}' (expected integer)")

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        raise InvalidArgument(
            f"repo: invalid enabled {enabled!r} for '{name}' (expected boolean)")

    return {
        "name": name,
        "url": url,
        "type": repo_type,
        "enabled": enabled,
        "priority": priority,
    }


def read_repos(fs, user):
    """Читает и валидирует repos.json. Отсутствие файла — пустой список."""
    try:
        text = fs.read_file(REPOS_CONFIG, user, "/", "/")
    except NoSuchFileOrDirectory:
        return {"format": 1, "repos": []}
    except PyOSError as exc:
        raise InvalidArgument(f"repo: cannot read '{REPOS_CONFIG}': {exc}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidArgument(
            f"repo: invalid '{REPOS_CONFIG}': {exc.msg}")

    if not isinstance(data, dict):
        raise InvalidArgument(f"repo: '{REPOS_CONFIG}' must be a JSON object")

    fmt = data.get("format")
    if fmt != 1:
        raise InvalidArgument(
            f"repo: unsupported repos.json format {fmt!r} (expected 1)")

    raw_repos = data.get("repos", [])
    if not isinstance(raw_repos, list):
        raise InvalidArgument(f"repo: '{REPOS_CONFIG}': 'repos' must be a list")

    repos = []
    seen = set()
    for entry in raw_repos:
        repo = _validate_repo_entry(entry)
        if repo["name"] in seen:
            raise InvalidArgument(
                f"repo: duplicate repository name '{repo['name']}'")
        seen.add(repo["name"])
        repos.append(repo)

    return {"format": 1, "repos": repos}


def _write_repos(fs, user, config):
    _ensure_dir(fs, "/etc/pyos", user)
    fs.write_file(REPOS_CONFIG, json.dumps(config, indent=2, ensure_ascii=False),
                  user, "/", "/", create=True)


def add_repo(ctx, name, url, repo_type="http", priority=10):
    """Добавляет репозиторий в конфиг (сеть не используется)."""
    repo = _validate_repo_entry({
        "name": name,
        "url": url,
        "type": repo_type,
        "priority": priority,
    })

    config = read_repos(ctx.fs, ctx.user)
    if any(item["name"] == repo["name"] for item in config["repos"]):
        raise InvalidArgument(f"repo: '{repo['name']}' already exists")

    config["repos"].append(repo)
    _write_repos(ctx.fs, ctx.user, config)
    return f"repo: added '{repo['name']}'"


def remove_repo(ctx, name):
    """Удаляет репозиторий из конфига."""
    config = read_repos(ctx.fs, ctx.user)
    remaining = [repo for repo in config["repos"] if repo["name"] != name]
    if len(remaining) == len(config["repos"]):
        raise InvalidArgument(f"repo: '{name}' not found")

    config["repos"] = remaining
    _write_repos(ctx.fs, ctx.user, config)
    return f"repo: removed '{name}'"


def list_repos(ctx):
    """Печатает репозитории из конфига."""
    config = read_repos(ctx.fs, ctx.user)
    if not config["repos"]:
        return "repo: no repositories configured"

    lines = [f"{'NAME':<16} {'TYPE':<5} {'ENABLED':<8} {'PRIORITY':<9} URL"]
    lines.append("-" * 72)
    for repo in config["repos"]:
        lines.append(
            f"{repo['name']:<16} {repo['type']:<5} "
            f"{str(repo['enabled']):<8} {repo['priority']:<9} {repo['url']}")
    return "\n".join(lines)


# -- index.json -------------------------------------------------------------


def parse_dep_spec(spec):
    """Разбирает строку зависимости/провижена в структуру (без резолва)."""
    text = str(spec).strip()
    match = _DEP_RE.match(text)
    if not match:
        return {"raw": spec, "name": text, "op": None, "version": None}
    return {
        "raw": spec,
        "name": match.group(1),
        "op": match.group(2),
        "version": match.group(3),
    }


def _validate_relative_ref(value, label, pkgname):
    if not isinstance(value, str) or not value:
        raise InvalidArgument(
            f"repo: index: package '{pkgname}': missing {label}")
    if _SCHEME_RE.match(value) or value.startswith("/"):
        raise InvalidArgument(
            f"repo: index: package '{pkgname}': {label} must be relative: "
            f"'{value}'")
    if ".." in value.split("/"):
        raise InvalidArgument(
            f"repo: index: package '{pkgname}': {label} traversal is not "
            f"allowed: '{value}'")
    return value


def _join_package_url(base, relative):
    base_norm = base.rstrip("/") + "/"
    resolved = base_norm + relative
    if not resolved.startswith(base_norm):
        raise InvalidArgument(
            f"repo: index: package url escapes repository base: '{relative}'")
    return resolved


def _dep_list(entry, field, pkgname):
    value = entry.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value):
        raise InvalidArgument(
            f"repo: index: package '{pkgname}': '{field}' must be a list of "
            "strings")
    return list(value)


def _validate_index_package(entry, repo):
    if not isinstance(entry, dict):
        raise InvalidArgument(
            f"repo: index: package entry must be a JSON object")

    for field in ("name", "version", "sha256", "url", "filename"):
        if field not in entry or entry[field] in (None, ""):
            raise InvalidArgument(
                f"repo: index: package entry missing '{field}'")

    name = entry["name"]
    if not isinstance(name, str):
        raise InvalidArgument("repo: index: package 'name' must be a string")

    version = entry["version"]
    if isinstance(version, int) and not isinstance(version, bool):
        version = str(version)
    if not isinstance(version, str) or not version:
        raise InvalidArgument(
            f"repo: index: package '{name}': invalid version")

    release = entry.get("release", 1)
    if isinstance(release, int) and not isinstance(release, bool):
        release = str(release)
    if not isinstance(release, str) or not release.isdigit():
        raise InvalidArgument(
            f"repo: index: package '{name}': invalid release {release!r}")

    arch = entry.get("arch", "any")
    if not isinstance(arch, str) or not arch:
        raise InvalidArgument(
            f"repo: index: package '{name}': invalid arch {arch!r}")

    sha256 = entry["sha256"]
    if not isinstance(sha256, str) or not sha256.strip():
        raise InvalidArgument(
            f"repo: index: package '{name}': missing sha256")

    url = _validate_relative_ref(entry["url"], "url", name)
    filename = _validate_relative_ref(entry["filename"], "filename", name)

    size = entry.get("size")
    if size is not None and (
            isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise InvalidArgument(
            f"repo: index: package '{name}': invalid size {size!r}")

    description = entry.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise InvalidArgument(
            f"repo: index: package '{name}': invalid description")

    license_ = entry.get("license", "")
    if license_ is None:
        license_ = ""
    if not isinstance(license_, str):
        raise InvalidArgument(
            f"repo: index: package '{name}': invalid license")

    depends = _dep_list(entry, "depends", name)
    provides = _dep_list(entry, "provides", name)
    conflicts = _dep_list(entry, "conflicts", name)

    return {
        "name": name,
        "version": version,
        "release": release,
        "arch": arch,
        "filename": filename,
        "url": url,
        "resolved_url": _join_package_url(repo["url"], url),
        "sha256": sha256,
        "size": size,
        "depends": depends,
        "provides": provides,
        "conflicts": conflicts,
        "depends_parsed": [parse_dep_spec(item) for item in depends],
        "provides_parsed": [parse_dep_spec(item) for item in provides],
        "conflicts_parsed": [parse_dep_spec(item) for item in conflicts],
        "description": description,
        "license": license_,
        "signature": entry.get("signature"),
    }


def _parse_index(text, source, repo):
    """Валидирует текст index.json и возвращает нормализованный индекс."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidArgument(
            f"repo: invalid index '{source}': {exc.msg}")

    if not isinstance(data, dict):
        raise InvalidArgument(
            f"repo: index '{source}' must be a JSON object")

    fmt = data.get("format")
    if fmt != 1:
        raise InvalidArgument(
            f"repo: unsupported index format {fmt!r} (expected 1)")

    repo_field = data.get("repo")
    if repo_field is not None and not isinstance(repo_field, str):
        raise InvalidArgument(
            f"repo: index '{source}': 'repo' must be a string")

    raw_packages = data.get("packages", [])
    if not isinstance(raw_packages, list):
        raise InvalidArgument(
            f"repo: index '{source}': 'packages' must be a list")

    packages = []
    seen = set()
    for entry in raw_packages:
        package = _validate_index_package(entry, repo)
        key = (package["name"], package["version"],
               package["release"], package["arch"])
        if key in seen:
            raise InvalidArgument(
                f"repo: index '{source}': duplicate package "
                f"{key[0]}-{key[1]}-{key[2]}-{key[3]}")
        seen.add(key)
        packages.append(package)

    return {
        "format": 1,
        "repo": repo_field if repo_field is not None else repo["name"],
        "generated": data.get("generated"),
        "packages": packages,
    }


def read_index(fs, user, repo):
    """Читает и валидирует ``<url>index.json`` из виртуальной ФС.

    Возвращает ``(index, raw_text)``; raw_text нужен для sha256 в meta.
    """
    source = repo["url"] + "index.json"
    try:
        text = fs.read_file(source, user, "/", "/")
    except NoSuchFileOrDirectory:
        raise InvalidArgument(f"repo: index not found: '{source}'")
    except PyOSError as exc:
        raise InvalidArgument(f"repo: cannot read index '{source}': {exc}")

    return _parse_index(text, source, repo), text


def _clean_error(exc):
    """Убирает служебный префикс из сообщения для warning-строки."""
    text = str(exc)
    for prefix in ("curl: ", "repo: "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _write_cache(ctx, repo, index, raw, source):
    cache_dir = f"{REPO_CACHE_ROOT}/{repo['name']}"
    _ensure_dir(ctx.fs, cache_dir, ctx.user)

    ctx.fs.write_file(cache_dir + "/index.json", raw,
                      ctx.user, "/", "/", create=True)

    meta = {
        "fetched_at": _utc_now(),
        "source_url": source,
        "type": repo["type"],
        "index_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "package_count": len(index["packages"]),
        "repo": index["repo"],
        "repo_matches": index["repo"] == repo["name"],
    }
    ctx.fs.write_file(cache_dir + "/index.meta.json",
                      json.dumps(meta, indent=2, ensure_ascii=False),
                      ctx.user, "/", "/", create=True)
    return meta


def update_repos(ctx, names=None):
    """Обновляет кэш индексов.

    ``type="path"`` читается из виртуальной ФС, ``type="http"`` качается
    через общий network helper. Ошибка одного репозитория не мешает
    обновлению остальных.
    """
    config = read_repos(ctx.fs, ctx.user)
    if names:
        known = {repo["name"]: repo for repo in config["repos"]}
        missing = [name for name in names if name not in known]
        if missing:
            raise InvalidArgument(f"repo: '{missing[0]}' not found")
        targets = [known[name] for name in names]
    else:
        targets = config["repos"]

    if not targets:
        return "repo: no repositories configured"

    lines = []
    for repo in targets:
        source = repo["url"] + "index.json"

        if repo["type"] == "http":
            try:
                raw = network_cmds._http_get_text(
                    source,
                    follow_redirects=True,
                    timeout=network_cmds.REQUEST_TIMEOUT,
                    raise_for_status=True,
                )
            except (PyOSError, InvalidArgument) as exc:
                lines.append(
                    f"repo: warning: '{repo['name']}': cannot fetch "
                    f"'{source}': {_clean_error(exc)}")
                continue

            size = getattr(raw, "raw_size", None)
            if size is None:
                size = len(raw.encode("utf-8"))
            if size > REPO_INDEX_MAX_BYTES:
                lines.append(
                    f"repo: warning: '{repo['name']}': index too large "
                    f"({size} bytes > {REPO_INDEX_MAX_BYTES})")
                continue

            try:
                index = _parse_index(raw, source, repo)
            except InvalidArgument as exc:
                lines.append(
                    f"repo: warning: '{repo['name']}': {_clean_error(exc)}")
                continue
        else:
            try:
                index, raw = read_index(ctx.fs, ctx.user, repo)
            except InvalidArgument as exc:
                lines.append(
                    f"repo: warning: '{repo['name']}': {_clean_error(exc)}")
                continue

        meta = _write_cache(ctx, repo, index, raw, source)
        lines.append(
            f"repo: updated '{repo['name']}' "
            f"({meta['package_count']} packages)")
        if not meta["repo_matches"]:
            lines.append(
                f"repo: warning: index repo '{index['repo']}' does not match "
                f"'{repo['name']}'")

    return "\n".join(lines)


# -- установка по имени -----------------------------------------------------


def _parse_install_spec(target):
    """Разбирает ``name[@repo][op version]``."""
    match = _INSTALL_SPEC_RE.match(target)
    if not match:
        if "~=" in target:
            raise InvalidArgument(
                f"pkg: '~=' version spec is not supported: '{target}'")
        raise InvalidArgument(f"pkg: invalid package spec '{target}'")
    return {
        "name": match.group("name"),
        "repo": match.group("repo"),
        "op": match.group("op"),
        "version": match.group("version"),
    }


def parse_dependency(spec):
    """Разбирает строку зависимости вида ``name``/``name>=1.2``.

    Поддержаны операторы ``=``, ``>=``, ``<=``, ``>``, ``<``.
    """
    if not isinstance(spec, str) or not spec:
        raise InvalidArgument(f"pkg: invalid dependency {spec!r}")
    match = _DEP_SPEC_RE.match(spec.strip())
    if not match:
        raise InvalidArgument(f"pkg: invalid dependency '{spec}'")
    return {
        "name": match.group("name"),
        "op": match.group("op"),
        "version": match.group("version"),
    }


def _package_full_version(package):
    """Возвращает ``version-release`` для pkgvercmp."""
    return f"{package['version']}-{package['release']}"


def _version_matches(package, op, version, pkgvercmp):
    """Проверяет version-spec через pkgvercmp.

    Сравниваются и ``version``, и ``version-release``, чтобы работали
    и ``name=1.0``, и ``name=1.0-2``.
    """
    if op is None:
        return True

    actuals = [package["version"]]
    full = _package_full_version(package)
    if full != package["version"]:
        actuals.append(full)

    for actual in actuals:
        result = pkgvercmp(actual, version)
        if op == "=" and result == 0:
            return True
        if op == ">=" and result >= 0:
            return True
        if op == "<=" and result <= 0:
            return True
        if op == ">" and result > 0:
            return True
        if op == "<" and result < 0:
            return True
    return False


def _max_package(packages, pkgvercmp):
    """Выбирает максимальную версию через pkgvercmp."""
    chosen = packages[0]
    for candidate in packages[1:]:
        if pkgvercmp(_package_full_version(candidate),
                     _package_full_version(chosen)) > 0:
            chosen = candidate
    return chosen


def read_cached_index(fs, user, repo):
    """Читает кэшированный индекс репозитория (без сети)."""
    path = f"{REPO_CACHE_ROOT}/{repo['name']}/index.json"
    try:
        text = fs.read_file(path, user, "/", "/")
    except NoSuchFileOrDirectory:
        raise InvalidArgument(
            f"repo: no cached index for '{repo['name']}', "
            "run pkg repo update first")
    except PyOSError as exc:
        raise InvalidArgument(
            f"repo: cannot read cached index '{path}': {exc}")
    return _parse_index(text, path, repo)


def search_packages(ctx, query):
    """Ищет подстроку в name/description кэшированных индексов.

    Read-only: сеть не используется, installed DB не читается. Репозитории
    без кэша пропускаются с warning, поиск продолжается по остальным.
    """
    from .commands.package_cmds import pkgvercmp

    config = read_repos(ctx.fs, ctx.user)
    repos = [repo for repo in config["repos"] if repo["enabled"]]
    if not repos:
        return "pkg: no enabled repositories"

    needle = query.casefold()
    hits = []
    warnings = []

    for repo in sorted(repos, key=lambda item: item["name"]):
        try:
            index = read_cached_index(ctx.fs, ctx.user, repo)
        except InvalidArgument as exc:
            message = _clean_error(exc)
            if "run pkg repo update" not in message:
                message += " (run pkg repo update)"
            warnings.append(
                f"pkg: warning: skipping repo '{repo['name']}': {message}")
            continue

        for package in index["packages"]:
            if (needle in package["name"].casefold()
                    or needle in package["description"].casefold()):
                hits.append((repo["name"], package))

    def compare(left, right):
        left_repo, left_pkg = left
        right_repo, right_pkg = right
        if left_repo != right_repo:
            return (left_repo > right_repo) - (left_repo < right_repo)
        if left_pkg["name"] != right_pkg["name"]:
            return ((left_pkg["name"] > right_pkg["name"])
                    - (left_pkg["name"] < right_pkg["name"]))
        return -pkgvercmp(
            _package_full_version(left_pkg),
            _package_full_version(right_pkg))

    hits.sort(key=cmp_to_key(compare))

    lines = list(warnings)
    for repo_name, package in hits:
        description = package["description"].replace("\n", " ")
        lines.append("  ".join([
            repo_name,
            package["name"],
            _package_full_version(package),
            package["arch"],
            description,
        ]))

    if not lines:
        return f"pkg: no matches for '{query}'"
    return "\n".join(lines)


def _enabled_repos(config):
    """Возвращает включённые репозитории в порядке priority desc, name asc."""
    repos = [repo for repo in config["repos"] if repo["enabled"]]
    if not repos:
        raise InvalidArgument("pkg: no enabled repositories")
    repos.sort(key=lambda repo: (-repo["priority"], repo["name"]))
    return repos


def _explicit_repo(config, name):
    """Проверяет и возвращает репозиторий, указанный через ``@repo``."""
    explicit = [repo for repo in config["repos"] if repo["name"] == name]
    if not explicit:
        raise InvalidArgument(f"pkg: repository '{name}' not found")
    if not explicit[0]["enabled"]:
        raise InvalidArgument(f"pkg: repository '{name}' is disabled")
    return explicit[0]


def _select_package(indexes, name, op, version, repo_filter, required_by,
                    pkgvercmp):
    """Выбирает лучший пакет по имени/version-spec среди индексов."""
    found = []
    for repo, index in indexes:
        if repo_filter is not None and repo["name"] != repo_filter:
            continue
        seen_keys = set()
        candidates = []
        for package in index["packages"]:
            if package["name"] != name:
                continue
            key = (package["name"], package["version"],
                   package["release"], package["arch"])
            if key in seen_keys:
                raise InvalidArgument(
                    f"pkg: duplicate package "
                    f"{key[0]}-{key[1]}-{key[2]}-{key[3]} "
                    f"in repository '{repo['name']}'")
            seen_keys.add(key)
            if _version_matches(package, op, version, pkgvercmp):
                candidates.append(package)

        if candidates:
            found.append((repo, _max_package(candidates, pkgvercmp)))

    if not found:
        if required_by is not None:
            spec_text = f"{name}{op or ''}{version or ''}"
            raise InvalidArgument(
                f"pkg: unsatisfied dependency '{spec_text}' "
                f"required by '{required_by}'")
        where = f" in repository '{repo_filter}'" if repo_filter else ""
        raise InvalidArgument(f"pkg: package '{name}' not found{where}")

    if repo_filter is not None:
        return found[0]

    top_priority = max(repo["priority"] for repo, _ in found)
    top = [(repo, package) for repo, package in found
           if repo["priority"] == top_priority]
    if len(top) > 1:
        details = ", ".join(
            f"{repo['name']} ({_package_full_version(package)})"
            for repo, package in top)
        raise InvalidArgument(
            f"pkg: ambiguous package '{name}' at priority "
            f"{top_priority}: {details}")
    return top[0]


def find_upgrade_candidate(indexes, name, pkgvercmp):
    """Выбирает лучший кандидат для upgrade с учётом priority.

    Возвращает ``(repo, package)`` или ``None``. Если лучший NVR доступен
    в нескольких репозиториях с одинаковым максимальным priority — ошибка
    ambiguity.
    """
    by_repo = []
    for repo, index in indexes:
        candidates = [package for package in index["packages"]
                      if package["name"] == name]
        if candidates:
            by_repo.append((repo, candidates))

    if not by_repo:
        return None

    top_priority = max(repo["priority"] for repo, _ in by_repo)
    top = [(repo, package)
           for repo, candidates in by_repo
           if repo["priority"] == top_priority
           for package in candidates]
    best = _max_package([package for _, package in top], pkgvercmp)
    best_full = _package_full_version(best)
    same = [(repo, package) for repo, package in top
            if _package_full_version(package) == best_full]
    repo_names = {repo["name"] for repo, _ in same}
    if len(repo_names) > 1:
        details = ", ".join(
            f"{repo['name']} ({_package_full_version(package)})"
            for repo, package in same)
        raise InvalidArgument(
            f"pkg: ambiguous package '{name}' at priority "
            f"{top_priority}: {details}")
    return same[0]


class _DependencyResolver:
    """Жадный DFS-резолвер с post-order планом установки."""

    def __init__(self, indexes, pkgvercmp, dependency_indexes=None):
        self.indexes = indexes
        self.dependency_indexes = dependency_indexes
        self.pkgvercmp = pkgvercmp
        self.resolved = set()
        self.stack = []
        self.plan = []

    def _indexes_for(self, required_by):
        if required_by is None or self.dependency_indexes is None:
            return self.indexes
        if callable(self.dependency_indexes):
            self.dependency_indexes = self.dependency_indexes()
        return self.dependency_indexes

    def resolve(self, name, op, version, repo_filter, required_by):
        if name in self.stack:
            start = self.stack.index(name)
            chain = self.stack[start:] + [name]
            raise InvalidArgument(
                f"pkg: dependency cycle: {' -> '.join(chain)}")
        if name in self.resolved:
            return

        repo, package = _select_package(
            self._indexes_for(required_by), name, op, version, repo_filter,
            required_by, self.pkgvercmp)

        self.stack.append(name)
        try:
            for raw in package.get("depends", []) or []:
                dependency = parse_dependency(raw)
                self.resolve(
                    dependency["name"], dependency["op"],
                    dependency["version"], None, name)
        finally:
            self.stack.pop()

        self.resolved.add(name)
        self.plan.append((repo, package, op, version, required_by))


def resolve_package(ctx, target):
    """Резолвит один пакет ``name[@repo][op version]`` по кэшам индексов.

    Возвращает ``(repo, package_entry)``. Зависимости не обходит;
    для них используйте :func:`resolve_package_plan`.
    """
    from .commands.package_cmds import pkgvercmp

    spec = _parse_install_spec(target)
    config = read_repos(ctx.fs, ctx.user)

    if spec["repo"] is not None:
        repos = [_explicit_repo(config, spec["repo"])]
    else:
        repos = _enabled_repos(config)

    indexes = [(repo, read_cached_index(ctx.fs, ctx.user, repo))
               for repo in repos]
    return _select_package(
        indexes, spec["name"], spec["op"], spec["version"], spec["repo"],
        None, pkgvercmp)


def resolve_package_plan(ctx, target):
    """Строит post-order план установки пакета и его зависимостей.

    ``@repo`` ограничивает только корневой пакет; зависимости ищутся
    по всем включённым репозиториям.
    """
    from .commands.package_cmds import pkgvercmp

    spec = _parse_install_spec(target)
    config = read_repos(ctx.fs, ctx.user)

    if spec["repo"] is not None:
        explicit = _explicit_repo(config, spec["repo"])
        root_indexes = [(explicit, read_cached_index(
            ctx.fs, ctx.user, explicit))]

        def load_dependency_indexes():
            return [(repo, read_cached_index(ctx.fs, ctx.user, repo))
                    for repo in _enabled_repos(config)]

        resolver = _DependencyResolver(
            root_indexes, pkgvercmp, load_dependency_indexes)
    else:
        repos = _enabled_repos(config)
        indexes = [(repo, read_cached_index(ctx.fs, ctx.user, repo))
                   for repo in repos]
        resolver = _DependencyResolver(indexes, pkgvercmp)

    resolver.resolve(
        spec["name"], spec["op"], spec["version"], spec["repo"], None)
    return resolver.plan


def _safe_package_url(repo, package):
    """Повторно проверяет url пакета и склеивает его с base репозитория."""
    relative = package["url"]
    name = package.get("name", "?")
    base = repo["url"].rstrip("/") + "/"

    if (not isinstance(relative, str) or not relative
            or relative.startswith("/")
            or _SCHEME_RE.match(relative)
            or ".." in relative.split("/")):
        raise InvalidArgument(
            f"pkg: unsafe package url '{relative}' for '{name}'")

    resolved = base + relative
    if not resolved.startswith(base):
        raise InvalidArgument(
            f"pkg: package url escapes repository base: '{relative}'")
    return resolved


def _package_cache_path(repo, package):
    """Возвращает безопасный путь кэша пакета."""
    filename = package["filename"]
    if (not isinstance(filename, str) or not filename
            or "/" in filename or "\\" in filename
            or filename in (".", "..")):
        raise InvalidArgument(
            f"pkg: unsafe package filename {filename!r}")
    return f"{PACKAGE_CACHE_ROOT}/{repo['name']}/{filename}"


def _fetch_package_text(ctx, repo, package, url):
    """Читает пакет из path-репо или качает через общий network helper."""
    if repo["type"] == "http":
        try:
            return network_cmds._http_get_text(
                url, follow_redirects=True,
                timeout=network_cmds.REQUEST_TIMEOUT,
                raise_for_status=True)
        except (PyOSError, InvalidArgument) as exc:
            raise PyOSError(
                f"pkg: failed to download '{package['name']}': "
                f"{_clean_error(exc)}")

    try:
        return ctx.fs.read_file(url, ctx.user, "/", "/")
    except NoSuchFileOrDirectory:
        raise InvalidArgument(f"pkg: package file not found: '{url}'")
    except PyOSError as exc:
        raise InvalidArgument(f"pkg: cannot read package '{url}': {exc}")


def _remove_cached_file(ctx, path):
    try:
        ctx.fs.unlink(path, ctx.user, "/", "/")
    except PyOSError:
        pass


def _cache_package(ctx, repo, package, with_status=False):
    """Скачивает/переиспользует пакет в кэше и проверяет sha256/size.

    При ``with_status=True`` возвращает ``(cache_path, reused)``, где reused
    означает, что подходящий файл уже был в кэше.
    """
    cache_path = _package_cache_path(repo, package)
    expected_sha = str(package["sha256"]).lower()
    # URL проверяем всегда, даже если файл уже лежит в кэше: индекс с
    # path traversal не должен приниматься только из-за совпадения sha.
    url = _safe_package_url(repo, package)

    try:
        cached = ctx.fs.read_file(cache_path, ctx.user, "/", "/")
    except NoSuchFileOrDirectory:
        cached = None
    except PyOSError as exc:
        raise InvalidArgument(
            f"pkg: cannot read cached package '{cache_path}': {exc}")

    if (cached is not None
            and hashlib.sha256(cached.encode("utf-8")).hexdigest().lower()
            == expected_sha):
        size = package.get("size")
        cached_size = len(cached.encode("utf-8"))
        if size is not None and cached_size > size:
            _remove_cached_file(ctx, cache_path)
            raise PyOSError(
                f"pkg: package '{package['name']}' exceeds declared size "
                f"({cached_size} > {size})")
        return (cache_path, True) if with_status else cache_path

    text = _fetch_package_text(ctx, repo, package, url)

    _ensure_dir(ctx.fs, f"{PACKAGE_CACHE_ROOT}/{repo['name']}", ctx.user)
    ctx.fs.write_file(cache_path, text, ctx.user, "/", "/", create=True)

    actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if actual_sha.lower() != expected_sha:
        _remove_cached_file(ctx, cache_path)
        raise PyOSError(
            f"pkg: sha256 mismatch for '{package['name']}': "
            f"expected {expected_sha}, got {actual_sha}")

    size = package.get("size")
    actual_size = len(text.encode("utf-8"))
    if size is not None and actual_size > size:
        _remove_cached_file(ctx, cache_path)
        raise PyOSError(
            f"pkg: package '{package['name']}' exceeds declared size "
            f"({actual_size} > {size})")

    return (cache_path, False) if with_status else cache_path


def install_package_by_name(ctx, target, *, dry_run=False, installer,
                            is_installed=None):
    """Резолвит пакет с зависимостями и устанавливает их в post-order.

    ``installer`` — существующая функция ``_install_package_file(ctx, path,
    dry_run=...)``; установка файлов/DB не дублируется.

    ``is_installed(name, op, version)`` проверяет installed DB. Если пакет
    уже установлен с удовлетворяющей версией, он не скачивается и не
    переустанавливается.
    """
    plan = resolve_package_plan(ctx, target)
    if is_installed is None:
        def is_installed(name, op, version):
            return False

    if dry_run:
        lines = ["pkg: would install:"]
        for repo, package, op, version, _required_by in plan:
            marker = ""
            if is_installed(package["name"], op, version):
                marker = " (already installed)"
            lines.append(
                f"  {package['name']} {_package_full_version(package)} "
                f"[{repo['name']}]{marker}")
        return "\n".join(lines)

    results = []
    for repo, package, op, version, _required_by in plan:
        if is_installed(package["name"], op, version):
            results.append(f"pkg: '{package['name']}' already installed")
            continue
        cache_path = _cache_package(ctx, repo, package)
        results.append(installer(ctx, cache_path, dry_run=False))
    return "\n".join(results)


# -- CLI pkg repo -----------------------------------------------------------


def _parse_options(args, allowed):
    """Разбирает позиционные аргументы и опции ``--key value``/``--key=value``."""
    positional = []
    options = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                key, value = key.split("=", 1)
            else:
                if i + 1 >= len(args):
                    raise InvalidArgument(
                        f"pkg repo: option '--{key}' requires an argument")
                value = args[i + 1]
                i += 1
            if key not in allowed:
                raise InvalidArgument(f"pkg repo: unknown option '--{key}'")
            options[key] = value
        else:
            positional.append(arg)
        i += 1
    return positional, options


def _parse_priority(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidArgument(f"repo: invalid priority {value!r} (expected integer)")


def handle_repo_command(ctx, args):
    """Обрабатывает ``pkg repo ...``."""
    if not args:
        return ("Usage: pkg repo add <name> <url> [--type http|path] "
                "[--priority N] | remove <name> | list | update [<name>...]")

    action = args[0]
    rest = args[1:]

    if action == "add":
        positional, options = _parse_options(rest, {"type", "priority"})
        if len(positional) != 2:
            raise InvalidArgument("pkg repo add: expected <name> <url>")
        repo_type = options.get("type", "http")
        priority = _parse_priority(options.get("priority", "10"))
        return add_repo(ctx, positional[0], positional[1],
                        repo_type=repo_type, priority=priority)

    if action == "remove":
        positional, options = _parse_options(rest, set())
        if options:
            raise InvalidArgument("pkg repo remove: unexpected option")
        if len(positional) != 1:
            raise InvalidArgument("pkg repo remove: expected <name>")
        return remove_repo(ctx, positional[0])

    if action == "list":
        if rest:
            raise InvalidArgument("pkg repo list: unexpected arguments")
        return list_repos(ctx)

    if action == "update":
        positional, options = _parse_options(rest, set())
        if options:
            raise InvalidArgument("pkg repo update: unexpected option")
        return update_repos(ctx, positional or None)

    raise InvalidArgument(
        f"pkg repo: unknown action '{action}' (use: add, remove, list, update)")

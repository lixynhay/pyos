"""Тесты конфигурации репозиториев и чтения index.json (без сети)."""

import hashlib
import json

import pytest

from pyos.commands.package_cmds import cmd_pkg
from pyos.context import Context
from pyos.errors import InvalidArgument, NoSuchFileOrDirectory, PermissionDenied, PyOSError
from pyos.repo import REPOS_CONFIG, read_index
from pyos.shell import Shell


def _ctx(kernel, user):
    return Context(kernel, user, user.home, shell=Shell(kernel, user))


def _ensure(kernel, root, path):
    try:
        kernel.fs.mkdir(path, root, "/", "/", parents=True)
    except PyOSError:
        pass


def _write_config(kernel, root, repos, fmt=1):
    _ensure(kernel, root, "/etc/pyos")
    data = {"format": fmt, "repos": repos}
    kernel.fs.write_file(REPOS_CONFIG, json.dumps(data), root, "/", "/")
    return data


def _repo(name="local", url="/srv/repo/", repo_type="path", enabled=True,
          priority=100):
    return {
        "name": name,
        "url": url,
        "type": repo_type,
        "enabled": enabled,
        "priority": priority,
    }


def _entry(name="hello", version="1.0", release=1, arch="any", **extra):
    data = {
        "name": name,
        "version": version,
        "release": release,
        "arch": arch,
        "filename": f"{name}-{version}.pkg.json",
        "url": f"packages/{name}-{version}.pkg.json",
        "sha256": "a" * 64,
        "size": 1234,
        "depends": [],
        "provides": [],
        "conflicts": [],
        "description": "test package",
        "license": "MIT",
        "signature": None,
    }
    data.update(extra)
    return data


def _write_index(kernel, root, repo_path, data):
    _ensure(kernel, root, repo_path.rstrip("/"))
    raw = json.dumps(data)
    kernel.fs.write_file(repo_path + "index.json", raw, root, "/", "/")
    return raw


def _index(packages, repo="local", fmt=1):
    return {
        "format": fmt,
        "repo": repo,
        "generated": "2026-10-23T22:40:00Z",
        "packages": packages,
    }


# -- pkg repo add/remove/list -----------------------------------------------


def test_repo_add_writes_config(kernel, root):
    ctx = _ctx(kernel, root)

    result = cmd_pkg(ctx, ["repo", "add", "core",
                           "https://example.com/packages/"], "")
    assert "added" in result

    config = json.loads(
        kernel.fs.read_file(REPOS_CONFIG, root, "/", "/")
    )
    assert config["format"] == 1
    assert config["repos"] == [{
        "name": "core",
        "url": "https://example.com/packages/",
        "type": "http",
        "enabled": True,
        "priority": 10,
    }]


def test_repo_add_rejects_http_scheme(kernel, root):
    ctx = _ctx(kernel, root)

    with pytest.raises(InvalidArgument, match="https"):
        cmd_pkg(ctx, ["repo", "add", "core", "http://example.com/p/"], "")

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup(REPOS_CONFIG, root, "/", "/")


def test_repo_add_rejects_invalid_name(kernel, root):
    ctx = _ctx(kernel, root)

    with pytest.raises(InvalidArgument, match="name"):
        cmd_pkg(ctx, ["repo", "add", "Core", "https://example.com/p/"], "")

    with pytest.raises(InvalidArgument, match="name"):
        cmd_pkg(ctx, ["repo", "add", "co_re", "https://example.com/p/"], "")


def test_repo_add_rejects_path_without_leading_slash(kernel, root):
    ctx = _ctx(kernel, root)

    with pytest.raises(InvalidArgument, match="absolute"):
        cmd_pkg(ctx, ["repo", "add", "local", "var/cache/repos/",
                      "--type", "path"], "")


def test_repo_add_requires_root(kernel, user):
    ctx = _ctx(kernel, user)

    with pytest.raises(PermissionDenied):
        cmd_pkg(ctx, ["repo", "add", "core", "https://example.com/p/"], "")


def test_repo_remove(kernel, root):
    ctx = _ctx(kernel, root)
    cmd_pkg(ctx, ["repo", "add", "core", "https://example.com/p/"], "")
    cmd_pkg(ctx, ["repo", "add", "local", "/srv/repo/",
                  "--type", "path"], "")

    result = cmd_pkg(ctx, ["repo", "remove", "core"], "")
    assert "removed" in result

    config = json.loads(kernel.fs.read_file(REPOS_CONFIG, root, "/", "/"))
    assert [repo["name"] for repo in config["repos"]] == ["local"]

    with pytest.raises(InvalidArgument, match="not found"):
        cmd_pkg(ctx, ["repo", "remove", "core"], "")


def test_repo_list_shows_entries(kernel, root, user):
    ctx = _ctx(kernel, root)
    cmd_pkg(ctx, ["repo", "add", "core", "https://example.com/p/",
                  "--priority", "5"], "")
    cmd_pkg(ctx, ["repo", "add", "local", "/srv/repo/",
                  "--type", "path", "--priority", "100"], "")

    # list доступен обычному пользователю
    result = cmd_pkg(_ctx(kernel, user), ["repo", "list"], "")
    for expected in ("core", "local", "https", "/srv/repo/", "path",
                     "True", "5", "100"):
        assert expected in result


# -- чтение index.json ------------------------------------------------------


def test_read_index_valid_path_repo(kernel, root):
    repo = _repo()
    index_data = _index([_entry(
        depends=["base>=1.0"],
        provides=["virtual-hello=1.0"],
    )])
    raw = _write_index(kernel, root, repo["url"], index_data)

    index, read_raw = read_index(kernel.fs, root, repo)

    assert read_raw == raw
    assert index["format"] == 1
    assert index["repo"] == "local"
    package = index["packages"][0]
    assert package["name"] == "hello"
    assert package["version"] == "1.0"
    assert package["release"] == "1"
    assert package["sha256"] == "a" * 64
    assert package["resolved_url"] == "/srv/repo/packages/hello-1.0.pkg.json"
    assert package["depends"] == ["base>=1.0"]
    assert package["depends_parsed"] == [{
        "raw": "base>=1.0", "name": "base", "op": ">=", "version": "1.0",
    }]


def test_read_index_rejects_unknown_format(kernel, root):
    repo = _repo()
    _write_index(kernel, root, repo["url"], _index([_entry()], fmt=2))

    with pytest.raises(InvalidArgument, match="format"):
        read_index(kernel.fs, root, repo)


def test_read_index_rejects_missing_sha256(kernel, root):
    repo = _repo()
    entry = _entry()
    del entry["sha256"]
    _write_index(kernel, root, repo["url"], _index([entry]))

    with pytest.raises(InvalidArgument, match="sha256"):
        read_index(kernel.fs, root, repo)


def test_read_index_rejects_traversal_url(kernel, root):
    repo = _repo()
    _write_index(kernel, root, repo["url"],
                 _index([_entry(url="../evil.pkg.json")]))

    with pytest.raises(InvalidArgument, match="traversal"):
        read_index(kernel.fs, root, repo)


def test_read_index_rejects_absolute_url(kernel, root):
    repo = _repo()
    _write_index(kernel, root, repo["url"],
                 _index([_entry(url="https://evil.example/x.pkg.json")]))

    with pytest.raises(InvalidArgument, match="relative"):
        read_index(kernel.fs, root, repo)


def test_read_index_rejects_duplicate_name_version_release_arch(kernel, root):
    repo = _repo()
    _write_index(kernel, root, repo["url"],
                 _index([_entry(), _entry(description="second")]))

    with pytest.raises(InvalidArgument, match="duplicate"):
        read_index(kernel.fs, root, repo)


# -- pkg repo update --------------------------------------------------------


def test_repo_update_path_populates_cache_and_meta(kernel, root):
    ctx = _ctx(kernel, root)
    _write_config(kernel, root, [_repo()])
    raw = _write_index(kernel, root, "/srv/repo/", _index([_entry()]))

    result = cmd_pkg(ctx, ["repo", "update", "local"], "")
    assert "updated 'local'" in result
    assert "1 packages" in result

    cached = kernel.fs.read_file(
        "/var/cache/repos/local/index.json", root, "/", "/")
    assert json.loads(cached) == _index([_entry()])

    meta = json.loads(kernel.fs.read_file(
        "/var/cache/repos/local/index.meta.json", root, "/", "/"))
    assert meta["source_url"] == "/srv/repo/index.json"
    assert meta["type"] == "path"
    assert meta["package_count"] == 1
    assert meta["index_sha256"] == hashlib.sha256(
        raw.encode("utf-8")).hexdigest()
    assert meta["fetched_at"]
    assert meta["repo_matches"] is True


def test_repo_update_path_records_repo_mismatch(kernel, root):
    ctx = _ctx(kernel, root)
    _write_config(kernel, root, [_repo()])
    _write_index(kernel, root, "/srv/repo/",
                 _index([_entry()], repo="other"))

    result = cmd_pkg(ctx, ["repo", "update"], "")
    assert "warning" in result
    assert "does not match" in result

    meta = json.loads(kernel.fs.read_file(
        "/var/cache/repos/local/index.meta.json", root, "/", "/"))
    assert meta["repo"] == "other"
    assert meta["repo_matches"] is False


def test_repo_update_http_is_stub_error_not_crash(kernel, root):
    ctx = _ctx(kernel, root)
    _write_config(kernel, root, [
        _repo("core", "https://example.com/p/", "http"),
    ])

    result = cmd_pkg(ctx, ["repo", "update"], "")
    assert "network repos not enabled yet" in result

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/var/cache/repos/core", root, "/", "/")


# -- repo-команды не трогают installed/owners DB ----------------------------


def test_installed_and_owners_db_untouched_by_repo_commands(kernel, root):
    ctx = _ctx(kernel, root)

    package = {
        "format": 1,
        "name": "demo",
        "version": "1.0",
        "release": 1,
        "arch": "any",
        "description": "demo",
        "depends": [],
        "provides": [],
        "conflicts": [],
        "files": [{
            "path": "/usr/bin/demo",
            "mode": "0755",
            "owner": "root",
            "group": "root",
            "encoding": "utf-8",
            "content": "demo",
        }],
    }
    kernel.fs.write_file("/root/demo.pkg.json", json.dumps(package),
                         root, "/", "/")
    cmd_pkg(ctx, ["install", "/root/demo.pkg.json"], "")

    manifest_path = "/var/lib/packages/installed/demo.json"
    owners_path = "/var/lib/packages/file-owners.json"
    manifest_before = kernel.fs.read_file(manifest_path, root, "/", "/")
    owners_before = kernel.fs.read_file(owners_path, root, "/", "/")

    cmd_pkg(ctx, ["repo", "add", "local", "/srv/repo/",
                  "--type", "path"], "")
    _write_index(kernel, root, "/srv/repo/", _index([_entry()]))
    cmd_pkg(ctx, ["repo", "update"], "")
    cmd_pkg(ctx, ["repo", "list"], "")

    assert kernel.fs.read_file(manifest_path, root, "/", "/") == manifest_before
    assert kernel.fs.read_file(owners_path, root, "/", "/") == owners_before
    assert kernel.fs.read_file("/usr/bin/demo", root, "/", "/") == "demo"

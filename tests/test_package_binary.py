"""BS-4: binary files in .pkg.json via base64, and byte-exact repo sha256."""

import base64
import hashlib
import json

import pytest

from pyos.commands.package_cmds import _validate_pkg, cmd_pkg
from pyos.context import Context
from pyos.errors import InvalidArgument, PyOSError
from pyos.shell import Shell

REPOS_CONFIG = "/etc/pyos/repos.json"
PAYLOAD = b"\x00\xff\x10binary\x80"


# -- helpers ----------------------------------------------------------------


def _ctx(kernel, user):
    return Context(kernel, user, user.home, shell=Shell(kernel, user))


def _ensure(kernel, user, path):
    try:
        kernel.fs.mkdir(path, user, "/", "/", parents=True)
    except PyOSError:
        pass


def _package(name="binpkg", version="1.0", files=None, **extra):
    data = {
        "format": 1,
        "name": name,
        "version": version,
        "release": 1,
        "arch": "any",
        "description": "",
        "depends": [],
        "provides": [],
        "conflicts": [],
        "files": list(files or []),
    }
    data.update(extra)
    return data


def _file(path, content, encoding="utf-8", mode="0644"):
    return {
        "path": path,
        "mode": mode,
        "owner": "root",
        "group": "root",
        "encoding": encoding,
        "content": content,
    }


def _install_path(kernel, root, data, path="/root/pkg.pkg.json"):
    kernel.fs.write_file(path, json.dumps(data), root, "/", "/", create=True)
    return cmd_pkg(_ctx(kernel, root), ["install", path], "")


def _entry(data, filename, raw, **extra):
    return {
        "name": data["name"],
        "version": data["version"],
        "release": data["release"],
        "arch": data["arch"],
        "filename": filename,
        "url": f"packages/{filename}",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "depends": list(data.get("depends", [])),
        "provides": [],
        "conflicts": [],
        "description": "",
        "license": "",
        "signature": None,
        **extra,
    }


def _use_repo(kernel, root, name="local", url=None, priority=100):
    url = url or f"/srv/{name}/"
    _ensure(kernel, root, "/etc/pyos")
    kernel.fs.write_file(REPOS_CONFIG, json.dumps({
        "format": 1,
        "repos": [{
            "name": name,
            "url": url,
            "type": "path",
            "enabled": True,
            "priority": priority,
        }],
    }), root, "/", "/")


def _publish(kernel, root, data, repo="local", url=None):
    url = url or f"/srv/{repo}/"
    filename = f"{data['name']}-{data['version']}.pkg.json"
    raw = json.dumps(data).encode("utf-8")
    _ensure(kernel, root, url.rstrip("/") + "/packages")
    kernel.fs.write_file(url + "packages/" + filename, raw, root, "/", "/")
    return _entry(data, filename, raw)


def _write_index(kernel, root, entries, repo="local"):
    _ensure(kernel, root, f"/var/cache/repos/{repo}")
    kernel.fs.write_file(
        f"/var/cache/repos/{repo}/index.json",
        json.dumps({
            "format": 1,
            "repo": repo,
            "generated": "2026-10-23T22:40:00Z",
            "packages": entries,
        }),
        root, "/", "/")


def _manifest(kernel, root, name):
    return json.loads(kernel.fs.read_file(
        f"/var/lib/packages/installed/{name}.json", root, "/", "/"))


# -- format validation ------------------------------------------------------


def test_pkg_json_accepts_base64_encoding(kernel, root):
    ctx = _ctx(kernel, root)
    data = _package(files=[_file(
        "/usr/bin/blob", base64.b64encode(PAYLOAD).decode("ascii"),
        encoding="base64")])

    pkg = _validate_pkg(ctx, data, "/root/pkg.pkg.json")

    assert pkg["files"][0]["content"] == PAYLOAD
    assert isinstance(pkg["files"][0]["content"], bytes)


def test_pkg_json_rejects_invalid_base64(kernel, root):
    ctx = _ctx(kernel, root)
    data = _package(files=[_file(
        "/usr/bin/blob", "not-valid-base64!!!", encoding="base64")])

    with pytest.raises(InvalidArgument, match="base64"):
        _validate_pkg(ctx, data, "/root/pkg.pkg.json")


def test_pkg_json_rejects_unknown_encoding(kernel, root):
    ctx = _ctx(kernel, root)
    data = _package(files=[_file(
        "/usr/bin/blob", "payload", encoding="hex")])

    with pytest.raises(InvalidArgument, match="encoding"):
        _validate_pkg(ctx, data, "/root/pkg.pkg.json")


# -- install ----------------------------------------------------------------


def test_pkg_install_base64_writes_exact_bytes(kernel, root):
    data = _package(files=[_file(
        "/usr/bin/blob", base64.b64encode(PAYLOAD).decode("ascii"),
        encoding="base64")])

    result = _install_path(kernel, root, data)

    assert "installed 'binpkg'" in result
    assert kernel.fs.read_bytes("/usr/bin/blob", root, "/", "/") == PAYLOAD
    with pytest.raises(PyOSError, match="not valid UTF-8"):
        kernel.fs.read_file("/usr/bin/blob", root, "/", "/")


# -- pack -------------------------------------------------------------------


def _write(kernel, root, path, content, mode=None):
    parent = path.rsplit("/", 1)[0] or "/"
    _ensure(kernel, root, parent)
    kernel.fs.write_file(path, content, root, "/", "/", create=True)
    if mode is not None:
        kernel.fs.chmod(path, mode, root, "/", "/")


def _pack(kernel, root, root_dir, output, name="demo", version="1.0.0"):
    return cmd_pkg(
        _ctx(kernel, root),
        ["pack", root_dir, output, "--name", name, "--version", version],
        "")


def _read_package(kernel, root, path):
    return json.loads(kernel.fs.read_file(path, root, "/", "/"))


def test_pkg_pack_binary_file_uses_base64(kernel, root):
    _write(kernel, root, "/tmp/bin-root/usr/share/blob.bin", PAYLOAD)

    _pack(kernel, root, "/tmp/bin-root", "/tmp/bin.pkg.json")

    data = _read_package(kernel, root, "/tmp/bin.pkg.json")
    entry = data["files"][0]
    assert entry["encoding"] == "base64"
    assert base64.b64decode(entry["content"]) == PAYLOAD


def test_pkg_pack_text_file_still_uses_utf8(kernel, root):
    _write(kernel, root, "/tmp/text-root/usr/share/readme.txt",
           "h\u00e9llo\n".encode("utf-8"))

    _pack(kernel, root, "/tmp/text-root", "/tmp/text.pkg.json")

    entry = _read_package(kernel, root, "/tmp/text.pkg.json")["files"][0]
    assert entry["encoding"] == "utf-8"
    assert entry["content"] == "h\u00e9llo\n"


def test_pkg_pack_install_binary_roundtrip(kernel, root):
    _write(kernel, root, "/tmp/rt-root/usr/share/blob.bin", PAYLOAD)
    _write(kernel, root, "/tmp/rt-root/usr/share/readme.txt", "text\n")

    _pack(kernel, root, "/tmp/rt-root", "/tmp/rt.pkg.json",
          name="roundtrip", version="1.0.0")
    result = _install_path(kernel, root, _read_package(
        kernel, root, "/tmp/rt.pkg.json"), path="/tmp/rt.pkg.json")

    assert "installed 'roundtrip'" in result
    assert kernel.fs.read_bytes(
        "/usr/share/blob.bin", root, "/", "/") == PAYLOAD
    assert kernel.fs.read_file(
        "/usr/share/readme.txt", root, "/", "/") == "text\n"


# -- repo upgrade -----------------------------------------------------------


def _binary_repo_data(version, content=PAYLOAD, name="binpkg"):
    return _package(
        name=name, version=version,
        files=[_file("/usr/share/blob.bin",
                     base64.b64encode(content).decode("ascii"),
                     encoding="base64")])


def test_pkg_upgrade_base64_package_roundtrip(kernel, root):
    _use_repo(kernel, root)
    old = _binary_repo_data("1.0", content=b"old\x00")
    _write_index(kernel, root, [_publish(kernel, root, old)])

    ctx = _ctx(kernel, root)
    cmd_pkg(ctx, ["install", "binpkg"], "")
    assert kernel.fs.read_bytes(
        "/usr/share/blob.bin", root, "/", "/") == b"old\x00"

    new = _binary_repo_data("2.0")
    _write_index(kernel, root, [_publish(kernel, root, new)])
    result = cmd_pkg(ctx, ["upgrade"], "")

    assert "upgrading binpkg 1.0-1 -> 2.0-1" in result
    assert kernel.fs.read_bytes(
        "/usr/share/blob.bin", root, "/", "/") == PAYLOAD
    assert _manifest(kernel, root, "binpkg")["version"] == "2.0"


# -- repo sha256 uses exact file bytes --------------------------------------


def test_repo_package_sha256_matches_file_bytes_if_changed(kernel, root):
    _use_repo(kernel, root)
    data = _package(name="binpkg", version="1.0",
                    files=[_file("/usr/share/blob.bin",
                                 base64.b64encode(PAYLOAD).decode("ascii"),
                                 encoding="base64")])
    entry = _publish(kernel, root, data)
    _write_index(kernel, root, [entry])

    # Меняем байты файла пакета, оставляя sha256 в индексе старым.
    raw = json.dumps(data).encode("utf-8")
    changed = raw + b"\n"
    kernel.fs.write_file(
        "/srv/local/packages/binpkg-1.0.pkg.json", changed, root, "/", "/")

    ctx = _ctx(kernel, root)
    with pytest.raises(PyOSError, match="sha256 mismatch") as exc:
        cmd_pkg(ctx, ["install", "binpkg"], "")
    assert hashlib.sha256(changed).hexdigest() in str(exc.value)

    # Индекс с sha256 ровно по новым байтам снова принимается.
    _write_index(kernel, root, [_entry(data, entry["filename"], changed)])
    assert "installed 'binpkg'" in cmd_pkg(ctx, ["install", "binpkg"], "")


# -- compatibility ----------------------------------------------------------


def test_old_utf8_packages_still_install(kernel, root):
    implicit = _package(name="legacy", files=[{
        "path": "/usr/bin/legacy",
        "mode": "0755",
        "owner": "root",
        "group": "root",
        "content": "legacy\n",
    }])
    explicit = _package(name="legacy8", files=[
        _file("/usr/bin/legacy8", "utf8\n", encoding="utf8")])

    assert "installed 'legacy'" in _install_path(
        kernel, root, implicit, path="/root/legacy.pkg.json")
    assert "installed 'legacy8'" in _install_path(
        kernel, root, explicit, path="/root/legacy8.pkg.json")

    assert kernel.fs.read_file(
        "/usr/bin/legacy", root, "/", "/") == "legacy\n"
    assert kernel.fs.read_file(
        "/usr/bin/legacy8", root, "/", "/") == "utf8\n"


def test_installed_db_and_owners_db_unchanged_for_binary_package(kernel, root):
    _install_path(kernel, root, _package(
        name="textpkg", files=[_file("/usr/bin/textpkg", "text\n")]),
        path="/root/textpkg.pkg.json")
    _install_path(kernel, root, _package(
        name="binpkg", files=[_file(
            "/usr/bin/binpkg", base64.b64encode(PAYLOAD).decode("ascii"),
            encoding="base64")]),
        path="/root/binpkg.pkg.json")

    text_manifest = _manifest(kernel, root, "textpkg")
    bin_manifest = _manifest(kernel, root, "binpkg")

    # Формат installed DB одинаков: те же ключи и типы, файлы — пути.
    assert set(bin_manifest) == set(text_manifest)
    assert bin_manifest["files"] == ["/usr/bin/binpkg"]
    assert bin_manifest["format"] == 1
    assert isinstance(bin_manifest["pkgname"], str)

    owners = json.loads(kernel.fs.read_file(
        "/var/lib/packages/file-owners.json", root, "/", "/"))
    assert owners == {
        "/usr/bin/textpkg": "textpkg",
        "/usr/bin/binpkg": "binpkg",
    }

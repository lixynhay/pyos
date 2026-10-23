"""Управление памятью pyOS: save/load состояния ФС и ulimit.

save — сохраняет ФС + пользователей в JSON-файл на хосте.
load — загружает состояние из JSON-файла.
ulimit — управляет ограничениями ресурсов (ulimit -f).
"""

import base64
import json
import os

from ..errors import InvalidArgument, NoSuchFileOrDirectory, PyOSError
from ..fs import S_IFDIR, S_IFLNK, S_IFREG
from .registry import register

# ============================================================================
# save — сохранение состояния
# ============================================================================


# /proc и /dev наполняются ядром при загрузке: в снапшот они не попадают,
# а после load пересоздаются через Kernel.reset_virtual_fs().
VOLATILE_ROOTS = ("/dev", "/proc")


def _is_virtual_path(path):
    return any(path == root or path.startswith(root + "/")
               for root in VOLATILE_ROOTS)


def _serialize_node(path, node):
    """Сериализует один узел ФС в словарь."""
    entry = {
        "type": "dir" if node.is_dir() else ("link" if node.is_link() else "file"),
        "uid": node.uid,
        "gid": node.gid,
        "mode": node.mode,
    }
    if node.is_file():
        data = node.data
        try:
            entry["data"] = data.decode("utf-8")
        except UnicodeDecodeError:
            entry["encoding"] = "base64"
            entry["data"] = base64.b64encode(data).decode("ascii")
    elif node.is_link():
        entry["target"] = node.target
    return entry


def _walk_and_serialize(fs):
    """Обходит всё дерево ФС, возвращает список записей {path: ...}."""
    entries = {}

    def walk(node, prefix=""):
        if prefix and _is_virtual_path(prefix):
            return
        if node.is_dir():
            entries[prefix] = _serialize_node(prefix, node)
            for name in sorted(node.children):
                child = node.children[name]
                child_path = (prefix.rstrip("/") + "/" + name) if prefix else name
                walk(child, child_path)
        elif node.is_file():
            entries[prefix] = _serialize_node(prefix, node)
        elif node.is_link():
            entries[prefix] = _serialize_node(prefix, node)

    walk(fs.root, "/")
    return entries


def _serialize_users(users):
    """Сериализует пользователей и группы."""
    return {
        "users": [
            {
                "name": u.name,
                "uid": u.uid,
                "gid": u.gid,
                "home": u.home,
                "groups": list(u.groups),
                "shell": u.shell,
                "password_hash": u.password_hash,
                "comment": u.comment,
            }
            for u in sorted(users.users.values(), key=lambda u: u.uid)
        ],
        "groups": [
            {"name": g.name, "gid": g.gid, "members": list(g.members)}
            for g in sorted(users.groups.values(), key=lambda g: g.gid)
        ],
    }


@register("save",
          usage="save <filename>",
          summary="Сохранить состояние ФС и пользователей в JSON",
          category="system")
def cmd_save(ctx, args, stdin):
    """Сохраняет текущее состояние ФС и пользователей в JSON-файл на хосте.

    Использование: save /path/to/snapshot.json
    """
    if not args:
        raise InvalidArgument("save: missing filename")

    filename = args[0]
    state = {
        "version": 3,
        "hostname": ctx.kernel.hostname,
        "users": _serialize_users(ctx.users),
        "filesystem": _walk_and_serialize(ctx.fs),
    }

    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        raise PyOSError(f"save: cannot write '{filename}': {exc}")

    node_count = len(state["filesystem"])
    return f"save: saved {node_count} nodes to '{filename}'"


# ============================================================================
# load — загрузка состояния
# ============================================================================


def _restore_users(users, data):
    """Восстанавливает пользователей и группы из данных JSON."""
    # Полностью очищаем и пересоздаём
    old_users = dict(users.users)
    old_groups = dict(users.groups)

    try:
        # Очищаем
        users.users.clear()
        users.by_uid.clear()
        users.groups.clear()

        # Восстанавливаем группы
        from ..users import Group as GroupClass
        for g in data.get("groups", []):
            grp = GroupClass(gid=g["gid"], name=g["name"])
            grp.members = list(g.get("members", []))
            users.groups[g["gid"]] = grp

        # Восстанавливаем пользователей
        for u_data in data.get("users", []):
            from ..users import User
            user = User(
                uid=u_data["uid"],
                name=u_data["name"],
                gid=u_data["gid"],
                home=u_data["home"],
                groups=list(u_data.get("groups", [])),
                shell=u_data.get("shell", "/bin/pyos"),
                password_hash=u_data.get("password_hash", ""),
                comment=u_data.get("comment", ""),
            )
            users.users[user.name] = user
            users.by_uid[user.uid] = user

    except Exception as exc:
        # При ошибке восстанавливаем старых пользователей
        users.users.clear()
        users.by_uid.clear()
        users.groups.clear()
        users.users.update(old_users)
        users.by_uid.update({u.uid: u for u in old_users.values()})
        users.groups.update(old_groups)
        raise PyOSError(f"load: failed to restore users: {exc}")


def _restore_fs(fs, entries):
    """Восстанавливает ФС из данных JSON.

    entries — dict {path: entry}.
    Всегда начинаем с очистки ФС, затем создаём записи в порядке путей.
    """
    # Сохраняем старый root для восстановления при ошибке
    old_root = fs.root

    # /proc и /dev не восстанавливаем из снапшота (в т.ч. старые снапшоты,
    # где они могли оказаться обычными файлами) — их пересоздаёт ядро.
    entries = {p: e for p, e in entries.items() if not _is_virtual_path(p)}

    try:
        # Создаём новое пустое дерево
        from ..fs import Dir, File, Symlink
        fs.root = Dir("/", uid=0, gid=0, mode=0o755)
        fs.root.parent = fs.root
        fs._inode_counter = 0
        fs._new_inode(fs.root)

        # Сортируем пути: родители перед детьми
        sorted_paths = sorted(entries.keys(), key=len)

        for path in sorted_paths:
            entry = entries[path]
            node_type = entry["type"]
            uid = entry.get("uid", 0)
            gid = entry.get("gid", 0)
            mode = entry.get("mode", 0o644)

            if node_type == "dir":
                if path == "/":
                    # root уже создан, обновляем метаданные
                    fs.root.uid = uid
                    fs.root.gid = gid
                    fs.root.mode = mode
                    continue
                fs.mkdir(path, None, "/", "/", mode=mode)
                # mkdir устанавливает uid/gid из user, обновляем
                node = fs.lookup(path, None, "/", "/")
                node.uid = uid
                node.gid = gid
                node.mode = mode

            elif node_type == "file":
                data = entry.get("data", "")
                encoding = entry.get("encoding", "utf-8")
                if encoding in ("utf-8", "utf8", None):
                    if not isinstance(data, str):
                        raise InvalidArgument(
                            f"load: text data must be a string for '{path}'")
                    raw = data.encode("utf-8")
                elif encoding == "base64":
                    if not isinstance(data, str):
                        raise InvalidArgument(
                            f"load: base64 data must be a string for '{path}'")
                    try:
                        raw = base64.b64decode(data, validate=True)
                    except ValueError:
                        raise InvalidArgument(
                            f"load: invalid base64 data for '{path}'")
                else:
                    raise InvalidArgument(
                        f"load: unsupported file encoding {encoding!r} "
                        f"for '{path}'")
                # Создаём родительские каталоги если нужно
                parent = "/".join(path.rstrip("/").split("/")[:-1])
                if parent:
                    try:
                        fs.lookup(parent, None, "/", "/")
                    except NoSuchFileOrDirectory:
                        fs.mkdir(parent, None, "/", "/", parents=True)
                fs.write_file(path, raw, None, "/", "/", create=True)
                node = fs.lookup(path, None, "/", "/")
                node.uid = uid
                node.gid = gid
                node.mode = mode

            elif node_type == "link":
                target = entry.get("target", "")
                parent, name = path.rsplit("/", 1)
                if parent:
                    try:
                        fs.lookup(parent, None, "/", "/")
                    except NoSuchFileOrDirectory:
                        fs.mkdir(parent, None, "/", "/", parents=True)
                link = Symlink(name, target, uid=uid, gid=gid)
                link.mode = mode
                parent_node = fs.lookup(parent, None, "/", "/")
                link.parent = parent_node
                parent_node.children[name] = link
                parent_node.touch_mtime()
                fs._new_inode(link)

    except Exception as exc:
        # Восстанавливаем старую ФС
        fs.root = old_root
        raise PyOSError(f"load: failed to restore filesystem: {exc}")


@register("load",
          usage="load <filename>",
          summary="Загрузить состояние ФС и пользователей из JSON",
          category="system")
def cmd_load(ctx, args, stdin):
    """Загружает состояние ФС и пользователей из JSON-файла хоста.

    Внимание: текущее состояние ПОЛНОСТЬЮ заменяется.
    Использование: load /path/to/snapshot.json
    """
    if ctx.user.uid != 0:
        raise PyOSError("load: only root can load state")

    if not args:
        raise InvalidArgument("load: missing filename")

    filename = args[0]

    try:
        with open(filename, "r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        raise NoSuchFileOrDirectory(filename)
    except json.JSONDecodeError as exc:
        raise InvalidArgument(f"load: invalid JSON: {exc}")
    except OSError as exc:
        raise PyOSError(f"load: cannot read '{filename}': {exc}")

    version = state.get("version", 1)
    if version not in (1, 2, 3):
        raise InvalidArgument(f"load: unsupported snapshot version {version}")

    # Восстанавливаем hostname
    if "hostname" in state:
        ctx.kernel.hostname = state["hostname"]

    # Пользователи
    if "users" in state:
        _restore_users(ctx.users, state["users"])
        # Обновляем текущего пользователя в контексте
        restored = ctx.users.get_by_name(ctx.user.name)
        if restored:
            ctx.user = restored

    # Файловая система
    if "filesystem" in state:
        _restore_fs(ctx.fs, state["filesystem"])
        ctx.kernel.reset_virtual_fs()

    node_count = len(state.get("filesystem", {}))
    return f"load: loaded ({node_count} nodes) from '{filename}'"


# ============================================================================
# ulimit — ограничения ресурсов
# ============================================================================


@register("ulimit",
          usage="ulimit [-f [blocks]]",
          summary="Управление лимитами ресурсов (ulimit -f)",
          category="system")
def cmd_ulimit(ctx, args, stdin):
    """Управление ограничениями ресурсов шелла.

    Опции:
      -f n     установить максимальный размер файла в 1K-блоках
      -f       показать текущий лимит размера файла
      (без опций) показать все лимиты
    """
    shell = ctx.shell
    if shell is None:
        raise PyOSError("ulimit: not running in a shell")

    if not args:
        return _show_all_limits(shell)

    if args[0] == "-f":
        if len(args) > 1:
            if args[1] == "unlimited":
                shell.ulimit["file_size"] = -1
                return None
            try:
                blocks = int(args[1])
                shell.ulimit["file_size"] = blocks * 1024
                return None
            except ValueError:
                raise InvalidArgument(
                    f"ulimit: invalid number '{args[1]}'")
        else:
            limit = shell.ulimit.get("file_size", -1)
            if limit < 0:
                return "unlimited"
            return str(limit // 1024)

    raise InvalidArgument(f"ulimit: unknown option '{args[0]}'")


def _show_all_limits(shell):
    """Показывает все установленные лимиты."""
    lines = []
    fs = shell.ulimit.get("file_size", -1)
    if fs < 0:
        lines.append("file size (-f) unlimited")
    else:
        lines.append(f"file size (-f) {fs} bytes ({fs // 1024} KiB)")
    return "\n".join(lines)
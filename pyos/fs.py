"""Виртуальная файловая система (ВФС).

Дерево узлов живёт в памяти процесса. Поддерживаются:

* обычные файлы и каталоги;
* права доступа rwx для владельца/группы/остальных (как в POSIX);
* символические ссылки (``ln -s``);
* относительные и абсолютные пути, ``.``, ``..``, ``~``.

Модель прав упрощена: root обходит все проверки. Для операций над файлом
дополнительно требуется право ``x`` (и ``w`` для создания/удаления) на
родительском каталоге.
"""

import fnmatch
import time

from .errors import (
    DirectoryNotEmpty,
    FileExists,
    InvalidArgument,
    IsADirectory,
    NoSuchFileOrDirectory,
    NotADirectory,
    PermissionDenied,
    PyOSError,
)

R_BIT = 4
W_BIT = 2
X_BIT = 1
STICKY_BIT = 0o1000

S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000

MAX_SYMLINK_DEPTH = 40


def _to_bytes(data) -> bytes:
    """Нормализует содержимое файла к bytes.

    ``str`` кодируется строго в UTF-8, ``bytes`` сохраняются как есть.
    """
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    raise InvalidArgument(
        f"file data must be str or bytes, got {type(data).__name__}")


# ---------------------------------------------------------------------------
# Узлы файловой системы
# ---------------------------------------------------------------------------
class Node:
    kind = "node"
    # число жёстких ссылок на узел; у каталогов и симлинков всегда 1,
    # у файлов переопределяется свойством (см. File).
    nlink = 1

    def __init__(self, name, uid=0, gid=0, mode=0o644):
        self.name = name
        self.uid = uid
        self.gid = gid
        self.mode = mode & 0o7777
        now = time.time()
        self.atime = now
        self.mtime = now
        self.ctime = now
        self.parent: "Dir | None" = None
        self.inode: int | None = None

    # типы
    def is_dir(self) -> bool:
        return False

    def is_file(self) -> bool:
        return False

    def is_link(self) -> bool:
        return False

    @property
    def type_char(self) -> str:
        return "-"

    @property
    def st_mode(self) -> int:
        if self.is_dir():
            return S_IFDIR | self.mode
        if self.is_link():
            return S_IFLNK | self.mode
        return S_IFREG | self.mode

    def perm_string(self) -> str:
        """Возвращает строку вида ``-rwxr-xr-x``."""
        out = [self.type_char]
        for shift in (6, 3, 0):
            bits = (self.mode >> shift) & 7
            out.append("r" if bits & R_BIT else "-")
            out.append("w" if bits & W_BIT else "-")
            out.append("x" if bits & X_BIT else "-")
        return "".join(out)

    def touch_mtime(self):
        self.mtime = time.time()

    def __repr__(self):
        return f"<{self.kind} {self.name!r} mode={oct(self.mode)}>"


class FileLink:
    """Совместно используемое состояние файла (inode).

    Несколько жёстких ссылок (имён каталога) указывают на один ``FileLink``,
    поэтому данные, ``chmod`` и ``chown`` через одну ссылку видны через
    остальные.
    """

    __slots__ = ("_data", "nlink", "uid", "gid", "mode")

    def __init__(self, data=b"", uid=0, gid=0, mode=0o644):
        self.data = data
        self.nlink = 1
        self.uid = uid
        self.gid = gid
        self.mode = mode & 0o7777

    @property
    def data(self) -> bytes:
        return self._data

    @data.setter
    def data(self, value):
        self._data = _to_bytes(value)


class File(Node):
    kind = "file"

    def __init__(self, name, uid=0, gid=0, mode=0o644, data="", link=None):
        if link is None:
            link = FileLink(data, uid, gid, mode)
        self._link = link
        # uid/gid/mode всегда берутся из общего inode; аргументы значимы
        # только при создании нового FileLink (link is None).
        super().__init__(name, link.uid, link.gid, link.mode)

    @property
    def uid(self) -> int:
        return self._link.uid

    @uid.setter
    def uid(self, value: int):
        self._link.uid = value

    @property
    def gid(self) -> int:
        return self._link.gid

    @gid.setter
    def gid(self, value: int):
        self._link.gid = value

    @property
    def mode(self) -> int:
        return self._link.mode

    @mode.setter
    def mode(self, value: int):
        self._link.mode = value & 0o7777

    def is_file(self) -> bool:
        return True

    @property
    def data(self) -> bytes:
        return self._link.data

    @data.setter
    def data(self, value):
        self._link.data = value

    @property
    def nlink(self) -> int:
        return self._link.nlink

    @property
    def type_char(self) -> str:
        return "-"

    @property
    def size(self) -> int:
        return len(self.data)


class VirtualFile(File):
    """Файл с динамическим содержимым (например, в /proc или /dev).

    Чтение вызывает ``read_provider``; запись передаётся в ``write_sink``
    (если он задан), иначе молча игнорируется. Режим по умолчанию 0444.
    """

    def __init__(self, name, read_provider=None, write_sink=None,
                 uid=0, gid=0, mode=0o444):
        super().__init__(name, uid, gid, mode, data="")
        self._read_provider = read_provider
        self._write_sink = write_sink

    @property
    def data(self) -> bytes:
        if self._read_provider is not None:
            return _to_bytes(self._read_provider())
        return self._link.data

    @data.setter
    def data(self, value):
        if self._write_sink is not None:
            self._write_sink(_to_bytes(value))

    @property
    def size(self) -> int:
        # виртуальные файлы не занимают места в ФС (как /proc и /dev в Linux);
        # заодно не вызывают провайдеры при подсчёте размера дерева (df/free),
        # что разрывает потенциальную рекурсию (например, /proc/meminfo).
        return 0


class Dir(Node):
    kind = "dir"

    def __init__(self, name, uid=0, gid=0, mode=0o755):
        super().__init__(name, uid, gid, mode)
        self.children: dict[str, Node] = {}
        # необязательный колбэк пересборки содержимого (например, /proc)
        self.refresh = None

    def is_dir(self) -> bool:
        return True

    @property
    def type_char(self) -> str:
        return "d"

    @property
    def size(self) -> int:
        # условный размер каталога, как это часто делают в учебных ФС
        return 4096


class Symlink(Node):
    kind = "link"

    def __init__(self, name, target, uid=0, gid=0):
        super().__init__(name, uid, gid, 0o777)
        self.target = target

    def is_link(self) -> bool:
        return True

    @property
    def type_char(self) -> str:
        return "l"

    @property
    def size(self) -> int:
        return len(self.target)


# ---------------------------------------------------------------------------
# Файловая система
# ---------------------------------------------------------------------------
class FileSystem:
    def __init__(self):
        self.root = Dir("/", uid=0, gid=0, mode=0o755)
        self.root.parent = self.root
        self._inode_counter = 0
        self._new_inode(self.root)
        self._init_tree()

    # -- inode ---------------------------------------------------------------
    def _new_inode(self, node: Node) -> int:
        self._inode_counter += 1
        node.inode = self._inode_counter
        return node.inode

    # -- начальное дерево ----------------------------------------------------
    def _init_tree(self):
        for d in ("bin", "dev", "etc", "home", "proc", "root", "tmp", "usr", "var"):
            self._mkdir_raw(self.root, d, uid=0, gid=0, mode=0o755)

        home = self.root.children["home"]
        for name, uid, gid in (("user", 1000, 1000), ("guest", 1001, 1001)):
            d = self._mkdir_raw(home, name, uid=uid, gid=gid, mode=0o755)
            # в домашнем каталоге создаём файл-приветствие
            self._create_raw(
                d, ".welcome", uid=uid, gid=gid, mode=0o644,
                data=f"Welcome, {name}! This is pyOS.\n",
            )

        etc = self.root.children["etc"]
        self._create_raw(
            etc, "hostname", uid=0, gid=0, mode=0o644, data="pyos\n",
        )
        self._create_raw(
            etc, "os-release", uid=0, gid=0, mode=0o644,
            data='NAME="pyOS"\nVERSION="0.1.0"\nID=pyos\n',
        )

        tmp = self.root.children["tmp"]
        tmp.mode = 0o1777  # как /tmp в Linux: sticky + rwx для всех
        # домашний каталог root закрыт для остальных, как в Linux
        self.root.children["root"].mode = 0o700

    # -- низкоуровневые помощники (без проверки прав) ------------------------
    def _mkdir_raw(self, parent: Dir, name, uid, gid, mode) -> Dir:
        d = Dir(name, uid=uid, gid=gid, mode=mode)
        d.parent = parent
        parent.children[name] = d
        self._new_inode(d)
        return d

    def _create_raw(self, parent: Dir, name, uid, gid, mode, data="") -> File:
        f = File(name, uid=uid, gid=gid, mode=mode, data=data)
        f.parent = parent
        parent.children[name] = f
        self._new_inode(f)
        return f

    # -- работа с путями -----------------------------------------------------
    @staticmethod
    def _components(path: str):
        return [c for c in path.split("/") if c not in ("", ".")]

    def normalize(self, path: str, cwd: str = "/", home: str = "/") -> str:
        """Приводит путь к абсолютному нормализованному виду."""
        if path is None:
            path = ""
        path = str(path)
        if path == "":
            return self.normalize(cwd, "/", home)
        if path == "~" or path.startswith("~/"):
            path = home + path[1:]
        elif path.startswith("~"):
            # ~user требует users.py; на этом этапе не поддерживаем
            raise InvalidArgument(
                f"unsupported home user expansion: {path.split('/')[0]}")

        if not path.startswith("/"):
            base = cwd if cwd.endswith("/") else cwd + "/"
            path = base + path

        parts: list[str] = []
        for c in path.split("/"):
            if c in ("", "."):
                continue
            if c == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(c)
        return "/" + "/".join(parts)

    def path_of(self, node: Node) -> str:
        if node is self.root:
            return "/"
        parts = []
        cur = node
        while cur is not self.root and cur.parent is not None:
            parts.append(cur.name)
            cur = cur.parent
        return "/" + "/".join(reversed(parts))

    def lookup(self, path, cwd="/", home="/", follow=True, user=None) -> Node:
        """Находит узел по пути, разыменовывая симлинки."""
        return self._walk(path, cwd, home, follow, 0, user)

    def _walk(self, path, cwd, home, follow, depth, user=None) -> Node:
        raw = "" if path is None else str(path)
        trailing_slash = raw.endswith("/") and raw != "/"
        norm = self.normalize(path, cwd, home)
        if norm == "/":
            return self.root
        node = self.root
        comps = norm.strip("/").split("/")
        for c in comps:
            node = self._deref(node, depth, user)
            if not node.is_dir():
                raise NotADirectory(norm)
            # для прохода сквозь каталог нужно право x (как в POSIX)
            if user is not None:
                self._require(node, user, X_BIT, self.path_of(node))
            self._refresh(node)
            child = node.children.get(c)
            if child is None:
                raise NoSuchFileOrDirectory(norm)
            node = child
        if follow:
            node = self._deref(node, depth, user)
        # завершающий слэш требует, чтобы конечный узел был каталогом
        if trailing_slash and not node.is_dir():
            raise NotADirectory(norm)
        return node

    def _deref(self, node: Node, depth: int, user=None) -> Node:
        while node.is_link():
            if depth > MAX_SYMLINK_DEPTH:
                raise InvalidArgument("too many levels of symbolic links")
            depth += 1
            base = self.path_of(node.parent)
            target = node.target
            if not target.startswith("/"):
                target = base.rstrip("/") + "/" + target
            node = self._walk(target, "/", "/", True, depth, user)
        return node

    def _parent_and_name(self, path, cwd, home, user=None):
        norm = self.normalize(path, cwd, home)
        if norm == "/":
            raise InvalidArgument("cannot operate on root")
        head, _, tail = norm.rpartition("/")
        head = head or "/"
        parent = self._walk(head, "/", "/", True, 0, user)
        return parent, tail, norm

    # -- права ---------------------------------------------------------------
    def has_perm(self, node: Node, user, bits: int) -> bool:
        if user is None:
            return True
        if getattr(user, "is_root", False):
            return True
        if node.uid == user.uid:
            p = (node.mode >> 6) & 7
        elif user.in_group(node.gid):
            p = (node.mode >> 3) & 7
        else:
            p = node.mode & 7
        return (p & bits) == bits

    def _require(self, node, user, bits, path):
        if not self.has_perm(node, user, bits):
            raise PermissionDenied(path)

    def _require_parent(self, parent, user, bits, path):
        self._require(parent, user, bits, path)

    def _sticky_allows_removal(self, parent: Node, node: Node, user) -> bool:
        """POSIX sticky: кто вправе удалить/переименовать запись ``node``.

        Проверяется только непосредственный родитель ``parent``. Разрешено
        внутренним вызовам (``user is None``), root, владельцу каталога и
        владельцу самой записи (для hardlink это uid общего inode, для
        symlink — uid ссылки).
        """
        if user is None or getattr(user, "is_root", False):
            return True
        if not (parent.mode & STICKY_BIT):
            return True
        return parent.uid == user.uid or node.uid == user.uid

    def _require_sticky(self, parent: Node, node: Node, user, path):
        if not self._sticky_allows_removal(parent, node, user):
            raise PermissionDenied(path)

    @staticmethod
    def _refresh(node: Node):
        """Пересобирает содержимое каталога, если задан refresh (/proc и т.п.)."""
        if getattr(node, "refresh", None) is not None:
            node.refresh()

    # -- публичный API -------------------------------------------------------
    def stat(self, path, user=None, cwd="/", home="/", follow=True) -> Node:
        return self._walk(path, cwd, home, follow, 0, user)

    def listdir(self, path, user=None, cwd="/", home="/") -> list[Node]:
        node = self._walk(path, cwd, home, True, 0, user)
        if not node.is_dir():
            raise NotADirectory(path)
        self._require(node, user, R_BIT | X_BIT, path)
        self._refresh(node)
        return sorted(node.children.values(), key=lambda n: n.name)

    def read_file(self, path, user=None, cwd="/", home="/") -> str:
        data = self.read_bytes(path, user, cwd, home)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidArgument(f"{path}: not valid UTF-8 text")

    def read_bytes(self, path, user=None, cwd="/", home="/") -> bytes:
        node = self._walk(path, cwd, home, True, 0, user)
        if node.is_dir():
            raise IsADirectory(path)
        self._require(node, user, R_BIT, path)
        node.atime = time.time()
        return node.data

    def _creation_target(self, path, cwd, home, user) -> str:
        """Путь создания при записи: висячий symlink ведёт к своей цели.

        Как POSIX open(O_CREAT): если последний компонент — symlink, создаётся
        его цель (включая цепочку ссылок), а не новый файл поверх ссылки.
        """
        cur = self.normalize(path, cwd, home)
        for _ in range(MAX_SYMLINK_DEPTH + 1):
            try:
                node = self._walk(cur, "/", "/", False, 0, user)
            except NoSuchFileOrDirectory:
                return cur
            if not node.is_link():
                return cur
            target = node.target
            if not target.startswith("/"):
                target = self.path_of(node.parent).rstrip("/") + "/" + target
            cur = self.normalize(target, "/", "/")
        raise InvalidArgument("too many levels of symbolic links")

    def write_file(self, path, data, user=None, cwd="/", home="/",
                   append=False, create=True, mode=None) -> Node:
        data = _to_bytes(data)
        try:
            node = self._walk(path, cwd, home, True, 0, user)
        except NoSuchFileOrDirectory:
            node = None
        if node is not None:
            if node.is_dir():
                raise IsADirectory(path)
            self._require(node, user, W_BIT, path)
            if append:
                node.data += data
            else:
                node.data = data
            node.touch_mtime()
            return node
        if not create:
            raise NoSuchFileOrDirectory(path)
        target = self._creation_target(path, cwd, home, user)
        extra = {} if mode is None else {"mode": mode}
        return self.create_file(target, user, cwd, home, data=data, **extra)

    def touch(self, path, user=None, cwd="/", home="/") -> Node:
        """Обновляет mtime узла; чужой файл требует root, владельца или W."""
        try:
            node = self._walk(path, cwd, home, True, 0, user)
        except NoSuchFileOrDirectory:
            target = self._creation_target(path, cwd, home, user)
            return self.create_file(target, user, cwd, home)
        if user is not None and not getattr(user, "is_root", False):
            if node.uid != user.uid and not self.has_perm(node, user, W_BIT):
                raise PermissionDenied(path)
        node.touch_mtime()
        return node

    def create_file(self, path, user=None, cwd="/", home="/", data="",
                    mode=0o644) -> File:
        parent, name, norm = self._parent_and_name(path, cwd, home, user)
        if not parent.is_dir():
            raise NotADirectory(norm)
        self._require_parent(parent, user, W_BIT | X_BIT, norm)
        existing = parent.children.get(name)
        if existing is not None:
            raise FileExists(norm)
        uid = user.uid if user else 0
        gid = user.gid if user else 0
        f = File(name, uid=uid, gid=gid, mode=mode, data=data)
        f.parent = parent
        parent.children[name] = f
        parent.touch_mtime()
        self._new_inode(f)
        return f

    def create_virtual_file(self, path, read_provider=None, write_sink=None,
                            user=None, cwd="/", home="/",
                            mode=0o444) -> VirtualFile:
        """Создаёт файл с динамическим содержимым (например, в /proc, /dev)."""
        parent, name, norm = self._parent_and_name(path, cwd, home, user)
        if not parent.is_dir():
            raise NotADirectory(norm)
        uid = user.uid if user else 0
        gid = user.gid if user else 0
        vf = VirtualFile(name, read_provider, write_sink, uid=uid, gid=gid,
                         mode=mode)
        vf.parent = parent
        parent.children[name] = vf
        parent.touch_mtime()
        self._new_inode(vf)
        return vf

    def mkdir(self, path, user=None, cwd="/", home="/", parents=False,
              mode=0o755) -> Dir:
        norm = self.normalize(path, cwd, home)
        if norm == "/":
            raise FileExists(norm)
        if parents:
            node = self.root
            comps = norm.strip("/").split("/")
            for i, c in enumerate(comps):
                if user is not None:
                    self._require(node, user, X_BIT, self.path_of(node))
                nxt = node.children.get(c)
                if nxt is None:
                    self._require_parent(node, user, W_BIT | X_BIT, norm)
                    uid = user.uid if user else 0
                    gid = user.gid if user else 0
                    d = Dir(c, uid=uid, gid=gid, mode=mode)
                    d.parent = node
                    node.children[c] = d
                    self._new_inode(d)
                    node.touch_mtime()
                    nxt = d
                elif not nxt.is_dir():
                    raise NotADirectory(norm)
                node = nxt
            return node

        parent, name, norm = self._parent_and_name(path, cwd, home, user)
        if not parent.is_dir():
            raise NotADirectory(norm)
        self._require_parent(parent, user, W_BIT | X_BIT, norm)
        if name in parent.children:
            raise FileExists(norm)
        uid = user.uid if user else 0
        gid = user.gid if user else 0
        d = Dir(name, uid=uid, gid=gid, mode=mode)
        d.parent = parent
        parent.children[name] = d
        parent.touch_mtime()
        self._new_inode(d)
        return d

    def _drop_entry(self, parent: Dir, name: str):
        """Удаляет запись каталога, уменьшая счётчик ссылок файла."""
        node = parent.children.get(name)
        if node is None:
            return
        if node.is_file():
            node._link.nlink -= 1
        parent.children.pop(name, None)

    def unlink(self, path, user=None, cwd="/", home="/", recursive=False):
        parent, name, norm = self._parent_and_name(path, cwd, home, user)
        node = parent.children.get(name)
        if node is None:
            raise NoSuchFileOrDirectory(norm)
        if str(path).endswith("/") and not node.is_dir():
            raise NotADirectory(norm)
        self._require_parent(parent, user, W_BIT | X_BIT, norm)
        if node.is_dir():
            if not recursive:
                raise IsADirectory(norm)
            self._check_rmtree(node, user, norm)
        self._require_sticky(parent, node, user, norm)
        if node.is_dir():
            self._rmtree(node)
        self._drop_entry(parent, name)
        parent.touch_mtime()

    def _check_rmtree(self, node: Dir, user, path):
        """Проверяет права на всё дерево до удаления (без частичного rm -r).

        Sticky-проверка выполняется для каждого потомка относительно его
        непосредственного родителя: иначе ``rm -r`` мог бы обойти защиту
        вложенного sticky-каталога. W|X-логика для пустых подкаталогов
        оставлена как есть (известный долг).
        """
        if user is None or getattr(user, "is_root", False):
            return
        if node.children:
            self._require(node, user, W_BIT | X_BIT, path)
        for child in node.children.values():
            if child.is_dir():
                self._check_rmtree(child, user, self.path_of(child))
            if not self._sticky_allows_removal(node, child, user):
                raise PermissionDenied(self.path_of(child))

    def _rmtree(self, node: Dir):
        for child in list(node.children.values()):
            if child.is_dir():
                self._rmtree(child)
            self._drop_entry(node, child.name)

    def rmdir(self, path, user=None, cwd="/", home="/"):
        parent, name, norm = self._parent_and_name(path, cwd, home, user)
        node = parent.children.get(name)
        if node is None:
            raise NoSuchFileOrDirectory(norm)
        if not node.is_dir():
            raise NotADirectory(norm)
        self._require_parent(parent, user, W_BIT | X_BIT, norm)
        self._require_sticky(parent, node, user, norm)
        if node.children:
            raise DirectoryNotEmpty(norm)
        del parent.children[name]
        parent.touch_mtime()

    def chmod(self, path, mode, user=None, cwd="/", home="/", follow=True) -> Node:
        node = self._walk(path, cwd, home, follow, 0, user)
        if user is not None and not getattr(user, "is_root", False):
            if node.uid != user.uid:
                raise PermissionDenied(path)
        node.mode = mode & 0o7777
        node.ctime = time.time()
        return node

    def chown(self, path, uid=None, gid=None, user=None, cwd="/", home="/") -> Node:
        node = self._walk(path, cwd, home, True, 0, user)
        if user is not None and not getattr(user, "is_root", False):
            raise PermissionDenied(path)
        if uid is not None:
            node.uid = uid
        if gid is not None:
            node.gid = gid
        node.ctime = time.time()
        return node

    def symlink(self, target, linkpath, user=None, cwd="/", home="/") -> Symlink:
        parent, name, norm = self._parent_and_name(linkpath, cwd, home, user)
        self._require_parent(parent, user, W_BIT | X_BIT, norm)
        if name in parent.children:
            raise FileExists(norm)
        uid = user.uid if user else 0
        gid = user.gid if user else 0
        ln = Symlink(name, target, uid=uid, gid=gid)
        ln.parent = parent
        parent.children[name] = ln
        parent.touch_mtime()
        self._new_inode(ln)
        return ln

    def readlink(self, path, user=None, cwd="/", home="/") -> str:
        node = self._walk(path, cwd, home, False, 0, user)
        if not node.is_link():
            raise InvalidArgument(f"{path}: not a symbolic link")
        return node.target

    def rename(self, src, dst, user=None, cwd="/", home="/"):
        sparent, sname, snorm = self._parent_and_name(src, cwd, home, user)
        node = sparent.children.get(sname)
        if node is None:
            raise NoSuchFileOrDirectory(snorm)
        self._require_parent(sparent, user, W_BIT | X_BIT, snorm)

        dparent, dname, dnorm = self._parent_and_name(dst, cwd, home, user)
        self._require_parent(dparent, user, W_BIT | X_BIT, dnorm)

        if node.is_dir() and self._is_within(node, dparent):
            raise InvalidArgument(
                f"cannot move '{snorm}' into its own subdirectory")

        existing = dparent.children.get(dname)
        if existing is not None:
            if existing is node:
                return node
            if existing.is_dir() and not node.is_dir():
                raise IsADirectory(dnorm)
            if node.is_dir() and not existing.is_dir():
                raise NotADirectory(dnorm)
            if existing.is_dir() and existing.children:
                raise DirectoryNotEmpty(dnorm)
            self._require_sticky(dparent, existing, user, dnorm)
        self._require_sticky(sparent, node, user, snorm)
        if existing is not None:
            self._drop_entry(dparent, dname)

        del sparent.children[sname]
        node.name = dname
        node.parent = dparent
        dparent.children[dname] = node
        sparent.touch_mtime()
        dparent.touch_mtime()
        return node

    def _is_within(self, node: Node, candidate: Node) -> bool:
        """True, если ``candidate`` совпадает с ``node`` или лежит внутри него."""
        cur: Node | None = candidate
        while cur is not None:
            if cur is node:
                return True
            if cur is self.root:
                break
            cur = cur.parent
        return False

    def _existing_prefix(self, norm: str, user=None) -> Node:
        """Самый глубокий существующий узел пути ``norm`` (с учётом symlink)."""
        node = self.root
        if norm == "/":
            return node
        for comp in norm.strip("/").split("/"):
            node = self._deref(node, 0, user)
            if not node.is_dir():
                return node
            child = node.children.get(comp)
            if child is None:
                return node
            node = child
        return node

    # -- жёсткие ссылки ------------------------------------------------------
    def link(self, src, dst, user=None, cwd="/", home="/") -> File:
        """Создаёт жёсткую ссылку ``dst`` на файл ``src`` (общий inode)."""
        snode = self._walk(src, cwd, home, True, 0, user)
        if snode.is_dir():
            raise PermissionDenied(src)
        if not snode.is_file():
            raise InvalidArgument(f"{src}: not a regular file")
        parent, name, norm = self._parent_and_name(dst, cwd, home, user)
        if not parent.is_dir():
            raise NotADirectory(norm)
        self._require_parent(parent, user, W_BIT | X_BIT, norm)
        if name in parent.children:
            raise FileExists(norm)
        # метаданные (uid/gid/mode) принадлежат inode, а не имени: новое имя
        # не получает владельца-вызывающего
        new = File(name, link=snode._link)
        new.parent = parent
        new.inode = snode.inode
        new._link.nlink += 1
        parent.children[name] = new
        parent.touch_mtime()
        return new

    # -- рекурсивное копирование --------------------------------------------
    def copy_tree(self, src, dst, user=None, cwd="/", home="/"):
        """Рекурсивно копирует файл или каталог из ``src`` в ``dst``."""
        snode = self._walk(src, cwd, home, True, 0, user)
        dnorm = self.normalize(dst, cwd, home)
        if snode.is_file():
            data = self.read_bytes(src, user, cwd, home)
            self.write_file(dnorm, data, user, cwd, home, mode=snode.mode)
            return self._walk(dnorm, "/", "/", True, 0, user)
        if not snode.is_dir():
            raise InvalidArgument(f"{src}: unsupported file type")

        snorm = self.path_of(snode)
        dprefix = self._existing_prefix(dnorm, user)
        if self._is_within(snode, dprefix):
            raise InvalidArgument(f"cannot copy '{snorm}' into itself")

        self._require(snode, user, R_BIT | X_BIT, snorm)

        try:
            dnode = self._walk(dnorm, "/", "/", True, 0, user)
        except NoSuchFileOrDirectory:
            dnode = self.mkdir(dnorm, user, "/", "/", parents=True,
                               mode=snode.mode)
        if not dnode.is_dir():
            raise NotADirectory(dnorm)
        for child in snode.children.values():
            target = self.path_of(dnode).rstrip("/") + "/" + child.name
            if child.is_dir():
                self.copy_tree(self.path_of(child), target, user)
            elif child.is_file():
                data = self.read_bytes(self.path_of(child), user, "/", "/")
                self.write_file(target, data, user, "/", "/",
                                mode=child.mode)
            else:  # символическая ссылка
                self.symlink(child.target, target, user, "/", "/")
        return dnode

    # -- глоббинг ------------------------------------------------------------
    def _match_component(self, name: str, pattern: str) -> bool:
        if not fnmatch.fnmatchcase(name, pattern):
            return False
        # подстановки '*'/'?'/'[' не совпадают с ведущей точкой
        if name.startswith(".") and not pattern.startswith("."):
            return False
        return True

    def glob(self, pattern, user=None, cwd="/", home="/") -> list[str]:
        """Раскрывает ``*``, ``?`` и ``[...]`` в список абсолютных путей."""
        pat = str(pattern)
        if pat.startswith("~"):
            pat = home + pat[1:]
        if not pat.startswith("/"):
            pat = cwd.rstrip("/") + "/" + pat
        comps = [c for c in pat.split("/") if c not in ("", ".")]
        nodes = [self.root]
        for comp in comps:
            nxt = []
            for node in nodes:
                try:
                    node = self._deref(node, 0, user)
                except PyOSError:
                    continue
                if not node.is_dir():
                    continue
                # без x нельзя даже войти в каталог, без r — перечислить имена
                if user is not None and not self.has_perm(node, user, X_BIT):
                    continue
                self._refresh(node)
                if comp == "..":
                    nxt.append(node.parent if node.parent is not None else node)
                    continue
                if user is not None and not self.has_perm(node, user, R_BIT):
                    continue
                for child in node.children.values():
                    if self._match_component(child.name, comp):
                        nxt.append(child)
            nodes = nxt
            if not nodes:
                break
        return sorted(self.path_of(n) for n in nodes)

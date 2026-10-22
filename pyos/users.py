"""Пользователи и группы.

Упрощённая модель: у каждого пользователя есть uid, основная группа gid и
список дополнительных групп. root (uid=0) обходит проверки прав. Пароли
хранятся как SHA-256 хэши; пустой пароль означает вход без пароля.
"""

import hashlib
from dataclasses import dataclass, field

from .errors import UserError


def hash_password(password: str) -> str:
    if not password:
        return ""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@dataclass
class User:
    uid: int
    name: str
    gid: int
    home: str
    groups: list = field(default_factory=list)
    shell: str = "/bin/pyos"
    password_hash: str = ""
    comment: str = ""

    @property
    def is_root(self) -> bool:
        return self.uid == 0

    def in_group(self, gid: int) -> bool:
        return gid == self.gid or gid in self.groups

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return True
        return self.password_hash == hash_password(password)


@dataclass
class Group:
    gid: int
    name: str
    members: list = field(default_factory=list)


class UserManager:
    """Реестр пользователей и групп."""

    def __init__(self):
        self.users: dict[str, User] = {}
        self.by_uid: dict[int, User] = {}
        self.groups: dict[int, Group] = {}
        self._init_defaults()

    # -- создание стандартных учётных записей --------------------------------
    def _add_group(self, gid: int, name: str) -> Group:
        g = Group(gid=gid, name=name)
        self.groups[gid] = g
        return g

    def _add_user(self, user: User) -> User:
        self.users[user.name] = user
        self.by_uid[user.uid] = user
        grp = self.groups.get(user.gid)
        if grp and user.name not in grp.members:
            grp.members.append(user.name)
        return user

    def _init_defaults(self):
        self._add_group(0, "root")
        self._add_group(100, "users")
        self._add_group(1000, "user")
        self._add_group(1001, "guest")

        self._add_user(User(
            uid=0, name="root", gid=0, home="/root",
            groups=[0], comment="System administrator",
        ))
        self._add_user(User(
            uid=1000, name="user", gid=1000, home="/home/user",
            groups=[1000, 100], comment="Regular user",
        ))
        self._add_user(User(
            uid=1001, name="guest", gid=1001, home="/home/guest",
            groups=[1001, 100], comment="Guest account",
        ))

    # -- поиск ---------------------------------------------------------------
    def get_by_name(self, name: str) -> User | None:
        return self.users.get(name)

    def get_by_uid(self, uid: int) -> User | None:
        return self.by_uid.get(uid)

    def group_name(self, gid: int) -> str:
        g = self.groups.get(gid)
        return g.name if g else str(gid)

    def group_id(self, name: str) -> int | None:
        for gid, g in self.groups.items():
            if g.name == name:
                return gid
        return None

    def resolve_owner(self, spec: str):
        """Преобразует 'name' или 'uid' в uid. Возвращает None, если не найден."""
        if spec.isdigit():
            uid = int(spec)
            return uid if uid in self.by_uid else None
        u = self.users.get(spec)
        return u.uid if u else None

    def resolve_group(self, spec: str):
        if spec.isdigit():
            gid = int(spec)
            return gid if gid in self.groups else None
        return self.group_id(spec)

    # -- аутентификация ------------------------------------------------------
    def authenticate(self, name: str, password: str = "") -> User:
        user = self.users.get(name)
        if user is None:
            raise UserError(f"login: unknown user '{name}'")
        if not user.check_password(password):
            raise UserError("login: incorrect password")
        return user

    def add_user(self, name, uid=None, gid=100, home=None, password="", comment=""):
        if name in self.users:
            raise UserError(f"user '{name}' already exists")
        if uid is None:
            uid = max(self.by_uid) + 1 if self.by_uid else 1000
        if uid in self.by_uid:
            raise UserError(f"uid {uid} already in use")
        home = home or f"/home/{name}"
        user = User(uid=uid, name=name, gid=gid, home=home,
                    groups=[gid], password_hash=hash_password(password),
                    comment=comment)
        return self._add_user(user)

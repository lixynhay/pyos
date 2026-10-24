"""Команды для работы с файловой системой."""

import time

from ..errors import (
    DirectoryNotEmpty,
    FileExists,
    InvalidArgument,
    IsADirectory,
    NoSuchFileOrDirectory,
    NotADirectory,
    PermissionDenied,
    PyOSError,
)
from .registry import register


def _fmt_time(ts: float) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(ts))


def _resolve_args(ctx, args):
    """Разделяет args на опции и позиционные аргументы."""
    opts = []
    pos = []
    for a in args:
        if a.startswith("-") and a != "-":
            opts.append(a)
        else:
            pos.append(a)
    return opts, pos


@register("ls", usage="ls [-l] [-a] [-h] [path ...]",
          summary="Список содержимого каталога", category="filesystem")
def cmd_ls(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    long_fmt = any(o in ("-l", "-la", "-al", "-lh", "-lah") or
                   ("l" in o and o.startswith("-")) for o in opts)
    show_all = any("a" in o for o in opts if o.startswith("-"))
    human = any("h" in o for o in opts if o.startswith("-"))
    if not paths:
        paths = [ctx.cwd]

    out_lines = []
    multi = len(paths) > 1
    for idx, path in enumerate(paths):
        try:
            # ls -l показывает сам symlink (lstat), не разыменовывая его
            node = ctx.fs.lookup(path, ctx.cwd, ctx.home,
                                 follow=not long_fmt, user=ctx.user)
        except PyOSError as exc:
            out_lines.append(f"ls: cannot access '{path}': {exc.message}")
            continue

        if node.is_dir():
            try:
                entries = ctx.fs.listdir(path, ctx.user, ctx.cwd, ctx.home)
            except PyOSError as exc:
                out_lines.append(f"ls: cannot open directory '{path}': {exc.message}")
                continue
            if not show_all:
                entries = [e for e in entries if not e.name.startswith(".")]
            if multi:
                if out_lines:
                    out_lines.append("")
                out_lines.append(f"{path}:")
            if long_fmt:
                total = sum(getattr(e, "size", 0) for e in entries)
                out_lines.append(f"total {_fmt_size(total, human)}")
                for e in entries:
                    out_lines.append(_long_line(ctx, e, human))
            else:
                out_lines.append("  ".join(e.name for e in entries))
        else:
            if long_fmt:
                out_lines.append(_long_line(ctx, node, human))
            else:
                out_lines.append(node.name)
    return "\n".join(out_lines)


def _fmt_size(size, human=False):
    if not human:
        return str(size)
    value = float(size)
    for unit in ("", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            if not unit:
                return str(int(value))
            return f"{value:.1f}{unit}"
        value /= 1024
    return str(int(value))


def _long_line(ctx, node, human=False):
    owner = ctx.users.get_by_uid(node.uid)
    owner_name = owner.name if owner else str(node.uid)
    group_name = ctx.users.group_name(node.gid)
    name = node.name
    if node.is_link():
        name = f"{node.name} -> {node.target}"
    size = getattr(node, "size", 0)
    return (f"{node.perm_string()} {node.nlink:>2} {owner_name:<8} "
            f"{group_name:<8} {_fmt_size(size, human):>6} "
            f"{_fmt_time(node.mtime)} {name}")


@register("cd", usage="cd [path]", summary="Сменить рабочий каталог",
          category="filesystem")
def cmd_cd(ctx, args, stdin):
    target = args[0] if args else ctx.home
    node = ctx.fs.lookup(target, ctx.cwd, ctx.home, user=ctx.user)
    if not node.is_dir():
        raise NotADirectory(target)
    if not ctx.fs.has_perm(node, ctx.user, 1):  # x
        raise PermissionDenied(target)
    new_cwd = ctx.fs.path_of(node)
    if ctx.shell is not None:
        ctx.shell.cwd = new_cwd
    ctx.cwd = new_cwd
    return None


@register("pwd", usage="pwd", summary="Показать рабочий каталог",
          category="filesystem")
def cmd_pwd(ctx, args, stdin):
    return ctx.cwd


@register("cat", usage="cat [file ...]", summary="Вывести файлы",
          category="filesystem")
def cmd_cat(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    if not paths:
        return stdin
    parts = []
    for path in paths:
        if path == "-":
            parts.append(stdin)
            continue
        raw = ctx.fs.read_bytes(path, ctx.user, ctx.cwd, ctx.home)
        try:
            parts.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            raise InvalidArgument(
                f"cat: binary file: {path} (not valid UTF-8)")
    text = "".join(parts)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


@register("touch", usage="touch file ...", summary="Создать пустой файл",
          category="filesystem")
def cmd_touch(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    if not paths:
        raise InvalidArgument("touch: missing file operand")
    for path in paths:
        ctx.fs.touch(path, ctx.user, ctx.cwd, ctx.home)
    return None


@register("mkdir", usage="mkdir [-p] dir ...",
          summary="Создать каталог", category="filesystem")
def cmd_mkdir(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    parents = any("p" in o for o in opts if o.startswith("-"))
    if not paths:
        raise InvalidArgument("mkdir: missing operand")
    for path in paths:
        ctx.fs.mkdir(path, ctx.user, ctx.cwd, ctx.home, parents=parents)
    return None


@register("rmdir", usage="rmdir dir ...", summary="Удалить пустой каталог",
          category="filesystem")
def cmd_rmdir(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    if not paths:
        raise InvalidArgument("rmdir: missing operand")
    for path in paths:
        ctx.fs.rmdir(path, ctx.user, ctx.cwd, ctx.home)
    return None


@register("rm", usage="rm [-r] [-f] path ...", summary="Удалить файлы",
          category="filesystem")
def cmd_rm(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    recursive = any("r" in o for o in opts if o.startswith("-"))
    force = any("f" in o for o in opts if o.startswith("-"))
    if not paths and not force:
        raise InvalidArgument("rm: missing operand")
    errors = []
    for path in paths:
        try:
            ctx.fs.unlink(path, ctx.user, ctx.cwd, ctx.home, recursive=recursive)
        except IsADirectory:
            errors.append(f"rm: cannot remove '{path}': Is a directory")
        except DirectoryNotEmpty:
            errors.append(f"rm: cannot remove '{path}': Directory not empty")
        except NoSuchFileOrDirectory:
            if not force:
                errors.append(f"rm: cannot remove '{path}': No such file or directory")
        except PyOSError as exc:
            errors.append(f"rm: cannot remove '{path}': {exc.message}")
    if errors:
        return ("\n".join(errors), 1)
    return None


@register("cp", usage="cp [-r] src dst", summary="Копировать файл или каталог",
          category="filesystem")
def cmd_cp(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    recursive = any("r" in o or "R" in o for o in opts if o.startswith("-"))
    if len(paths) < 2:
        raise InvalidArgument("cp: missing destination file operand")
    src, dst = paths[0], paths[1]

    # если dst — существующий каталог, копируем внутрь
    try:
        dnode = ctx.fs.lookup(dst, ctx.cwd, ctx.home)
        if dnode.is_dir():
            base = src.rstrip("/").split("/")[-1]
            dst = ctx.fs.path_of(dnode).rstrip("/") + "/" + base
    except NoSuchFileOrDirectory:
        pass

    snode = ctx.fs.lookup(src, ctx.cwd, ctx.home)
    if snode.is_dir():
        if not recursive:
            raise IsADirectory(src)
        ctx.fs.copy_tree(src, dst, ctx.user, ctx.cwd, ctx.home)
        return None

    data = ctx.fs.read_bytes(src, ctx.user, ctx.cwd, ctx.home)
    try:
        ctx.fs.write_file(dst, data, ctx.user, ctx.cwd, ctx.home,
                          mode=snode.mode)
    except IsADirectory:
        raise InvalidArgument(f"cp: '{dst}' is a directory")
    return None


@register("mv", usage="mv src dst", summary="Переместить/переименовать",
          category="filesystem")
def cmd_mv(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    if len(paths) < 2:
        raise InvalidArgument("mv: missing destination file operand")
    src, dst = paths[0], paths[1]
    try:
        dnode = ctx.fs.lookup(dst, ctx.cwd, ctx.home)
        if dnode.is_dir():
            base = src.rstrip("/").split("/")[-1]
            dst = ctx.fs.path_of(dnode).rstrip("/") + "/" + base
    except NoSuchFileOrDirectory:
        pass
    ctx.fs.rename(src, dst, ctx.user, ctx.cwd, ctx.home)
    return None


@register("ln", usage="ln [-s] target linkname", summary="Создать ссылку",
          category="filesystem")
def cmd_ln(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    symbolic = any("s" in o for o in opts if o.startswith("-"))
    if len(paths) < 2:
        raise InvalidArgument("ln: missing file operand")
    target, linkname = paths[0], paths[1]
    if symbolic:
        ctx.fs.symlink(target, linkname, ctx.user, ctx.cwd, ctx.home)
    else:
        ctx.fs.link(target, linkname, ctx.user, ctx.cwd, ctx.home)
    return None


@register("chmod", usage="chmod mode path ...", summary="Изменить права",
          category="filesystem")
def cmd_chmod(ctx, args, stdin):
    if len(args) < 2:
        raise InvalidArgument("chmod: missing operand")
    spec, paths = args[0], args[1:]
    for path in paths:
        if spec.isdigit():
            try:
                new_mode = int(spec, 8)
            except ValueError:
                raise InvalidArgument(f"chmod: invalid mode '{spec}'")
            if not 0 <= new_mode <= 0o7777:
                raise InvalidArgument(f"chmod: invalid mode '{spec}'")
        else:
            node = ctx.fs.lookup(path, ctx.cwd, ctx.home, user=ctx.user)
            import re
            new_mode = node.mode
            for clause in spec.split(","):
                m = re.fullmatch(r"([ugoa]*)([+-=])([rwx]+)", clause)
                if not m:
                    raise InvalidArgument(f"invalid mode: '{spec}'")
                who, op, perms = m.groups()
                who = who or "a"
                bits = (4 if "r" in perms else 0) | (2 if "w" in perms else 0) \
                    | (1 if "x" in perms else 0)
                shifts = []
                if "u" in who or "a" in who:
                    shifts.append(6)
                if "g" in who or "a" in who:
                    shifts.append(3)
                if "o" in who or "a" in who:
                    shifts.append(0)
                for sh in shifts:
                    mask = 7 << sh
                    if op == "+":
                        new_mode |= bits << sh
                    elif op == "-":
                        new_mode &= ~(bits << sh)
                    else:  # =
                        new_mode = (new_mode & ~mask) | (bits << sh)
        ctx.fs.chmod(path, new_mode, ctx.user, ctx.cwd, ctx.home)
    return None


@register("chown", usage="chown owner[:group] path ...",
          summary="Изменить владельца", category="filesystem")
def cmd_chown(ctx, args, stdin):
    if len(args) < 2:
        raise InvalidArgument("chown: missing operand")
    spec, paths = args[0], args[1:]
    owner_spec, _, group_spec = spec.partition(":")
    uid = gid = None
    if owner_spec:
        uid = ctx.users.resolve_owner(owner_spec)
        if uid is None:
            raise InvalidArgument(f"chown: invalid user: '{owner_spec}'")
    if group_spec:
        gid = ctx.users.resolve_group(group_spec)
        if gid is None:
            raise InvalidArgument(f"chown: invalid group: '{group_spec}'")
    for path in paths:
        ctx.fs.chown(path, uid, gid, ctx.user, ctx.cwd, ctx.home)
    return None


@register("stat", usage="stat path", summary="Показать метаданные файла",
          category="filesystem")
def cmd_stat(ctx, args, stdin):
    opts, paths = _resolve_args(ctx, args)
    if not paths:
        raise InvalidArgument("stat: missing operand")
    lines = []
    for path in paths:
        node = ctx.fs.lookup(path, ctx.cwd, ctx.home, follow=False,
                             user=ctx.user)
        owner = ctx.users.get_by_uid(node.uid)
        lines.append(f"  File: {ctx.fs.path_of(node)}")
        lines.append(f"  Size: {getattr(node, 'size', 0)}")
        lines.append(f" Inode: {node.inode}  Links: {node.nlink}  Type: {node.kind}")
        lines.append(f"Access: ({node.mode:04o}/{node.perm_string()})  "
                     f"Uid: ({node.uid}/{owner.name if owner else '?'})  "
                     f"Gid: ({node.gid}/{ctx.users.group_name(node.gid)})")
        lines.append(f"Modify: {_fmt_time(node.mtime)}")
    return "\n".join(lines)

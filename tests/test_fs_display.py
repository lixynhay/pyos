"""FS-HARD-1/2: отображение sticky bit и ``ls -d``.

Sticky display исправлен в FS-HARD-2: ``perm_string()`` рендерит ``t``/``T``
для mode с битом ``0o1000``; ``setuid``/``setgid`` по-прежнему не рендерятся
(они не enforced).

``ls -d`` всё ещё не реализован: флаг принимается, но игнорируется. Эти
тесты остаются characterization до FS-HARD-3.

Тесты детерминированы: без сети, без wall-clock. Время модификации в
длинном формате ``ls`` не проверяется — только структура строк.
"""

from pyos.shell import Shell


# ============================================================================
# perm_string: sticky bit рендерится как t/T (FIXED в FS-HARD-2)
# ============================================================================


def test_fixed_perm_string_tmp_has_sticky_t(kernel, root):
    # FIXED (FS-HARD-2): /tmp создаётся с mode 1777; в FS-HARD-1 тест
    # фиксировал drwxrwxrwx. Покрытие сохранено, assert инвертирован.
    node = kernel.fs.lookup("/tmp", root)
    assert node.mode == 0o1777
    assert node.perm_string() == "drwxrwxrwt"


def test_fixed_perm_string_1644_renders_T(kernel, root):
    # FIXED (FS-HARD-2): sticky у файла без other-x → -rw-r--r-T.
    fs = kernel.fs
    fs.create_file("/tmp/sticky_file_1644", root, "/", "/", data="x",
                   mode=0o1644)
    assert fs.lookup("/tmp/sticky_file_1644", root).perm_string() == \
        "-rw-r--r-T"


def test_fixed_perm_string_1755_renders_t(kernel, root):
    # FIXED (FS-HARD-2): sticky-каталог с other-x → drwxr-xr-t.
    fs = kernel.fs
    fs.mkdir("/tmp/sticky_dir_1755", root, "/", "/", mode=0o1755)
    assert fs.lookup("/tmp/sticky_dir_1755", root).perm_string() == \
        "drwxr-xr-t"


def test_fixed_perm_string_1700_renders_T(kernel, root):
    # FIXED (FS-HARD-2): sticky-каталог без other-x → drwx-----T.
    fs = kernel.fs
    fs.mkdir("/tmp/sticky_dir_1700", root, "/", "/", mode=0o1700)
    assert fs.lookup("/tmp/sticky_dir_1700", root).perm_string() == \
        "drwx-----T"


# ============================================================================
# perm_string: baseline для setuid/setgid и обычных режимов
# ============================================================================


def test_current_setuid_setgid_modes_do_not_render_s(kernel, root):
    # BASELINE (останется и после FS-HARD-2): setuid/setgid не enforced,
    # поэтому s/S не рендерятся; числовой mode при этом хранится.
    fs = kernel.fs
    fs.create_file("/tmp/suid_file", root, "/", "/", data="x", mode=0o4755)
    fs.create_file("/tmp/sgid_file", root, "/", "/", data="x", mode=0o2755)

    suid = fs.lookup("/tmp/suid_file", root)
    sgid = fs.lookup("/tmp/sgid_file", root)

    assert suid.mode == 0o4755
    assert sgid.mode == 0o2755
    assert suid.perm_string() == "-rwxr-xr-x"
    assert sgid.perm_string() == "-rwxr-xr-x"
    for perm in (suid.perm_string(), sgid.perm_string()):
        assert "s" not in perm
        assert "S" not in perm


def test_current_perm_string_plain_modes_have_no_special_chars(kernel, root):
    # BASELINE: обычные режимы не содержат t/T/s/S.
    fs = kernel.fs
    fs.create_file("/tmp/plain_755", root, "/", "/", data="x", mode=0o755)
    fs.create_file("/tmp/plain_644", root, "/", "/", data="x", mode=0o644)
    fs.mkdir("/tmp/plain_777", root, "/", "/", mode=0o777)

    assert fs.lookup("/tmp/plain_755", root).perm_string() == "-rwxr-xr-x"
    assert fs.lookup("/tmp/plain_644", root).perm_string() == "-rw-r--r--"
    assert fs.lookup("/tmp/plain_777", root).perm_string() == "drwxrwxrwx"


# ============================================================================
# пользовательски наблюдаемое отображение: ls -l / и stat /tmp
# ============================================================================


def test_fixed_ls_l_tmp_shows_sticky_t(kernel, root, capsys):
    # FIXED (FS-HARD-2): ls -l / показывает /tmp как drwxrwxrwt.
    Shell(kernel, root).handle_line("ls -l /")
    captured = capsys.readouterr()

    lines = [line for line in captured.out.splitlines()
             if line.endswith(" tmp")]
    assert len(lines) == 1
    perm = lines[0].split()[0]
    assert perm == "drwxrwxrwt"


def test_fixed_stat_tmp_access_has_numeric_1777_and_t(kernel, root, capsys):
    # FIXED (FS-HARD-2): числовой mode остаётся 1777, perm_string даёт t.
    Shell(kernel, root).handle_line("stat /tmp")
    out = capsys.readouterr().out

    assert "1777" in out
    access_line = next(line for line in out.splitlines()
                       if line.startswith("Access:"))
    # формат: ``Access: (1777/drwxrwxrwt)  Uid: (0/root)  Gid: ...``
    perm = access_line.split("Access: (")[1].split(")")[0]
    assert perm == "1777/drwxrwxrwt"


# ============================================================================
# ls -d: текущее поведение (флаг игнорируется)
# ============================================================================


def test_current_ls_d_prints_empty_line(kernel, root, capsys):
    # CHARACTERIZE: -d принимается, но игнорируется; /tmp пуст, поэтому
    # ls -d /tmp печатает пустую строку, а не саму запись.
    sh = Shell(kernel, root)
    sh.handle_line("ls -d /tmp")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0


def test_current_ls_ld_prints_total_and_contents_not_entry(kernel, root,
                                                           capsys):
    # CHARACTERIZE: ls -ld DIR печатает total и содержимое каталога, а не
    # одну строку самого DIR. После FS-HARD-3 ожидается одна запись.
    sh = Shell(kernel, root)
    sh.handle_line("mkdir -p /tmp/displaydir")
    sh.handle_line("echo x > /tmp/displaydir/file")
    sh.handle_line("ls -ld /tmp/displaydir")
    captured = capsys.readouterr()

    lines = captured.out.splitlines()
    assert lines[0].startswith("total ")
    assert any(line.endswith(" file") for line in lines)
    assert all(not line.endswith(" displaydir") for line in lines)
    assert captured.err == ""
    assert sh.last_code == 0


def test_current_ls_d_missing_path_current_behavior(kernel, root, capsys):
    # CHARACTERIZE: путь не существует — обычная ошибка ls, код 2.
    # Поведение не должно измениться и после FS-HARD-3.
    sh = Shell(kernel, root)
    sh.handle_line("ls -d /tmp/does-not-exist")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err.strip() == (
        "ls: cannot access '/tmp/does-not-exist': No such file or directory")
    assert sh.last_code == 2


def test_current_ls_d_symlink_current_behavior(kernel, root, capsys):
    # CHARACTERIZE: -ld показывает сам symlink (длинный формат делает lstat);
    # короткий -d разыменовывает ссылку и печатает имя цели, а не операнд.
    sh = Shell(kernel, root)
    sh.handle_line("echo target > /tmp/display_target")
    sh.handle_line("ln -s /tmp/display_target /tmp/display_link")

    sh.handle_line("ls -ld /tmp/display_link")
    long_out = capsys.readouterr().out
    long_lines = long_out.splitlines()
    assert len(long_lines) == 1
    assert long_lines[0].startswith("lrwxrwxrwx")
    assert "display_link -> /tmp/display_target" in long_lines[0]
    assert not long_out.startswith("total")

    sh.handle_line("ls -d /tmp/display_link")
    short_out = capsys.readouterr().out
    assert short_out.strip() == "display_target"
    assert sh.last_code == 0


# ============================================================================
# FS-HARD-2: t/T, baseline setuid/setgid и обычных режимов
# ============================================================================


def test_perm_string_sticky_exec_other_renders_lowercase_t(kernel, root):
    # sticky + other-x → t на месте other-execute.
    fs = kernel.fs
    fs.create_file("/tmp/sticky_exec", root, "/", "/", data="x",
                   mode=0o1775)
    assert fs.lookup("/tmp/sticky_exec", root).perm_string() == "-rwxrwxr-t"


def test_perm_string_sticky_no_exec_other_renders_uppercase_T(kernel, root):
    # sticky без other-x → T на месте other-execute.
    fs = kernel.fs
    fs.create_file("/tmp/sticky_noexec", root, "/", "/", data="x",
                   mode=0o1664)
    assert fs.lookup("/tmp/sticky_noexec", root).perm_string() == "-rw-rw-r-T"


def test_perm_string_tmp_renders_drwxrwxrwt(kernel, root):
    node = kernel.fs.lookup("/tmp", root)
    assert node.mode == 0o1777
    assert node.perm_string() == "drwxrwxrwt"


def test_ls_l_tmp_shows_sticky_t(kernel, root, capsys):
    Shell(kernel, root).handle_line("ls -l /")
    captured = capsys.readouterr()

    perms = [line.split()[0] for line in captured.out.splitlines()
             if line.endswith(" tmp")]
    assert perms == ["drwxrwxrwt"]
    assert "t" in perms[0]


def test_stat_tmp_shows_numeric_1777_and_t_in_perm_string(kernel, root,
                                                          capsys):
    Shell(kernel, root).handle_line("stat /tmp")
    out = capsys.readouterr().out

    assert "1777" in out
    assert "(1777/drwxrwxrwt)" in out


def test_perm_string_setuid_does_not_render_s(kernel, root):
    # setuid не enforced → s не рендерится; числовой mode сохраняется.
    fs = kernel.fs
    fs.create_file("/tmp/suid_4755", root, "/", "/", data="x", mode=0o4755)
    fs.create_file("/tmp/suid_sgid_6755", root, "/", "/", data="x",
                   mode=0o6755)

    suid = fs.lookup("/tmp/suid_4755", root)
    both = fs.lookup("/tmp/suid_sgid_6755", root)

    assert suid.mode == 0o4755
    assert both.mode == 0o6755
    assert suid.perm_string() == "-rwxr-xr-x"
    assert both.perm_string() == "-rwxr-xr-x"
    assert "s" not in suid.perm_string()
    assert "s" not in both.perm_string()


def test_perm_string_setgid_does_not_render_S(kernel, root):
    fs = kernel.fs
    fs.create_file("/tmp/sgid_2755", root, "/", "/", data="x", mode=0o2755)
    node = fs.lookup("/tmp/sgid_2755", root)

    assert node.mode == 0o2755
    assert node.perm_string() == "-rwxr-xr-x"
    assert "S" not in node.perm_string()


def test_perm_string_sticky_and_setuid_renders_only_t_not_s(kernel, root):
    # 0o5755: sticky рендерится как t, setuid игнорируется.
    fs = kernel.fs
    fs.create_file("/tmp/sticky_suid", root, "/", "/", data="x", mode=0o5755)
    perm = fs.lookup("/tmp/sticky_suid", root).perm_string()

    assert perm == "-rwxr-xr-t"
    assert "t" in perm
    assert "s" not in perm
    assert "S" not in perm


def test_regular_modes_unchanged_by_sticky_display(kernel, root):
    # Без sticky бита в mode t/T не появляются.
    fs = kernel.fs
    fs.create_file("/tmp/plain_640", root, "/", "/", data="x", mode=0o640)
    fs.create_file("/tmp/plain_600", root, "/", "/", data="x", mode=0o600)
    fs.mkdir("/tmp/plain_700", root, "/", "/", mode=0o700)

    for path, expected in (
            ("/tmp/plain_640", "-rw-r-----"),
            ("/tmp/plain_600", "-rw-------"),
            ("/tmp/plain_700", "drwx------"),
    ):
        perm = fs.lookup(path, root).perm_string()
        assert perm == expected
        assert "t" not in perm
        assert "T" not in perm


def test_symlink_perm_string_unchanged_if_no_sticky(kernel, root):
    # symlink имеет mode 0o777 и не sticky → обычный lrwxrwxrwx.
    fs = kernel.fs
    fs.create_file("/tmp/link_target", root, "/", "/", data="x")
    fs.symlink("/tmp/link_target", "/tmp/link_entry", root, "/", "/")

    link = fs.lookup("/tmp/link_entry", root, follow=False)
    assert link.mode == 0o777
    assert link.perm_string() == "lrwxrwxrwx"
    assert "t" not in link.perm_string()
    assert "T" not in link.perm_string()

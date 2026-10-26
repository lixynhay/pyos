"""FS-HARD-1: characterization display-долгов ФС.

Фиксируется **текущее** поведение до исправлений:

* ``perm_string()`` не показывает sticky bit (``t``/``T``), хотя sticky уже
  enforced (STICKY-2);
* ``perm_string()`` не показывает ``setuid``/``setgid`` (``s``/``S``);
* ``ls -d`` не реализован: флаг принимается, но игнорируется.

Тесты намеренно фиксируют дефекты, а не желаемое поведение:

* ``perm_string`` sticky будет инвертирован в FS-HARD-2;
* ``ls -d`` / ``ls -ld`` будут инвертированы в FS-HARD-3.

Тесты детерминированы: без сети, без wall-clock. Время модификации в
длинном формате ``ls`` не проверяется — только структура строк.
"""

from pyos.shell import Shell


# ============================================================================
# perm_string: текущее отображение sticky bit
# ============================================================================


def test_current_perm_string_tmp_lacks_sticky_char(kernel, root):
    # CHARACTERIZE: /tmp создаётся с mode 1777, но perm_string показывает
    # только 9 rwx-бит. После FS-HARD-2 ожидается drwxrwxrwt.
    node = kernel.fs.lookup("/tmp", root)
    assert node.mode == 0o1777
    assert node.perm_string() == "drwxrwxrwx"
    assert "t" not in node.perm_string()
    assert "T" not in node.perm_string()


def test_current_perm_string_1644_lacks_T(kernel, root):
    # CHARACTERIZE: sticky у файла без other-x. После FS-HARD-2: -rw-r--r-T.
    fs = kernel.fs
    fs.create_file("/tmp/sticky_file_1644", root, "/", "/", data="x",
                   mode=0o1644)
    assert fs.lookup("/tmp/sticky_file_1644", root).perm_string() == \
        "-rw-r--r--"


def test_current_perm_string_1755_lacks_t(kernel, root):
    # CHARACTERIZE: sticky-каталог с other-x. После FS-HARD-2: drwxr-xr-t.
    fs = kernel.fs
    fs.mkdir("/tmp/sticky_dir_1755", root, "/", "/", mode=0o1755)
    assert fs.lookup("/tmp/sticky_dir_1755", root).perm_string() == \
        "drwxr-xr-x"


def test_current_perm_string_1700_lacks_T(kernel, root):
    # CHARACTERIZE: sticky-каталог без other-x. После FS-HARD-2: drwx------T.
    fs = kernel.fs
    fs.mkdir("/tmp/sticky_dir_1700", root, "/", "/", mode=0o1700)
    assert fs.lookup("/tmp/sticky_dir_1700", root).perm_string() == \
        "drwx------"


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


def test_current_ls_l_tmp_lacks_t_char(kernel, root, capsys):
    # CHARACTERIZE: ls -l / показывает /tmp как drwxrwxrwx, без t.
    # После FS-HARD-2 ожидается drwxrwxrwt.
    Shell(kernel, root).handle_line("ls -l /")
    captured = capsys.readouterr()

    lines = [line for line in captured.out.splitlines()
             if line.endswith(" tmp")]
    assert len(lines) == 1
    perm = lines[0].split()[0]
    assert perm == "drwxrwxrwx"
    assert "t" not in perm
    assert "T" not in perm


def test_current_stat_tmp_access_has_numeric_1777_but_no_t(kernel, root,
                                                           capsys):
    # CHARACTERIZE: числовой mode честный (1777), а perm_string — без t.
    # После FS-HARD-2 ожидается Access: (1777/drwxrwxrwt).
    Shell(kernel, root).handle_line("stat /tmp")
    out = capsys.readouterr().out

    assert "1777" in out
    access_line = next(line for line in out.splitlines()
                       if line.startswith("Access:"))
    # формат: ``Access: (1777/drwxrwxrwx)  Uid: (0/root)  Gid: ...``
    perm = access_line.split("Access: (")[1].split(")")[0]
    assert perm == "1777/drwxrwxrwx"
    assert "t" not in perm
    assert "T" not in perm


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

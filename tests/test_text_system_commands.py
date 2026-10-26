"""Интеграционные тесты текстовых и системных команд: grep, wc, head, tail, sort, uniq, rev, tr, cut, whoami, id, uname, ps, kill, uptime, df, free, help, man, which, date, groups, hostname, env."""

import pytest

from pyos.errors import NoSuchFileOrDirectory
from pyos.shell import Shell


# ============================================================================
# Текстовые команды (grep, wc, head, tail, sort, uniq, rev, tr, cut)
# ============================================================================

# -- grep -------------------------------------------------------------------


def test_grep_basic(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/grep_test.txt", root, data="apple\nbanana\ncherry\n")
    Shell(kernel, root).handle_line("grep banana /tmp/grep_test.txt")
    assert capsys.readouterr().out.strip() == "banana"


def test_grep_no_match(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/grep_none.txt", root, data="apple\nbanana\n")
    Shell(kernel, root).handle_line("grep cherry /tmp/grep_none.txt")
    assert capsys.readouterr().out.strip() == ""


def test_grep_case_insensitive(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/grep_ci.txt", root, data="Apple\nbanana\n")
    Shell(kernel, root).handle_line("grep -i apple /tmp/grep_ci.txt")
    assert capsys.readouterr().out.strip() == "Apple"


def test_grep_invert(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/grep_inv.txt", root, data="a\nb\nc\n")
    Shell(kernel, root).handle_line("grep -v b /tmp/grep_inv.txt")
    assert capsys.readouterr().out.strip() == "a\nc"


def test_grep_case_insensitive_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'Apple\nbanana' | grep -i apple")
    out = capsys.readouterr().out.strip()
    assert out == "Apple" or "Apple" in out


def test_grep_pipe(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/grep_pipe.txt", root, data="red\ngreen\nblue\n")
    Shell(kernel, root).handle_line("cat /tmp/grep_pipe.txt | grep blue")
    assert capsys.readouterr().out.strip() == "blue"


# -- wc ---------------------------------------------------------------------


def test_wc_basic(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/wc_test.txt", root, data="hello world\nfoo bar baz\n")
    Shell(kernel, root).handle_line("wc /tmp/wc_test.txt")
    out = capsys.readouterr().out.strip()
    assert "2" in out and "5" in out  # 2 lines, 5 words


def test_wc_lines(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/wc_lines.txt", root, data="a\nb\nc\nd\n")
    Shell(kernel, root).handle_line("wc -l /tmp/wc_lines.txt")
    assert capsys.readouterr().out.strip() == "4"


def test_wc_words(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/wc_words.txt", root, data="one two three four")
    Shell(kernel, root).handle_line("wc -w /tmp/wc_words.txt")
    assert capsys.readouterr().out.strip() == "4"


def test_wc_pipe_chars(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'abcde' | wc -c")
    out = capsys.readouterr().out.strip()
    assert out.isdigit() and int(out) > 0


def test_wc_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'a b c' | wc -w")
    assert capsys.readouterr().out.strip() == "3"


# -- head -------------------------------------------------------------------


def test_head_default(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/head_test.txt", root, data="1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n")
    Shell(kernel, root).handle_line("head /tmp/head_test.txt")
    out = capsys.readouterr().out.strip()
    assert out == "1\n2\n3\n4\n5\n6\n7\n8\n9\n10"


def test_head_custom(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/head3.txt", root, data="a\nb\nc\nd\ne\n")
    Shell(kernel, root).handle_line("head -n 3 /tmp/head3.txt")
    assert capsys.readouterr().out.strip() == "a\nb\nc"


def test_head_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'a\nb\nc\nd\ne' | head -n 2")
    out = capsys.readouterr().out.strip()
    lines = out.split("\n")
    assert len(lines) == 2


# -- tail -------------------------------------------------------------------


def test_tail_default(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/tail_test.txt", root, data="1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n")
    Shell(kernel, root).handle_line("tail /tmp/tail_test.txt")
    out = capsys.readouterr().out.strip()
    lines = out.split("\n")
    assert len(lines) == 10


def test_tail_custom(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/tail2.txt", root, data="a\nb\nc\nd\ne\n")
    Shell(kernel, root).handle_line("tail -n 2 /tmp/tail2.txt")
    assert capsys.readouterr().out.strip() == "d\ne"


def test_tail_small_file(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/tail_small.txt", root, data="hello\n")
    Shell(kernel, root).handle_line("tail /tmp/tail_small.txt")
    assert capsys.readouterr().out.strip() == "hello"


# -- sort -------------------------------------------------------------------


def test_sort_basic(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/sort_test.txt", root, data="c\nb\na\n")
    Shell(kernel, root).handle_line("sort /tmp/sort_test.txt")
    assert capsys.readouterr().out.strip() == "a\nb\nc"


def test_sort_reverse(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/sort_rev.txt", root, data="a\nc\nb\n")
    Shell(kernel, root).handle_line("sort -r /tmp/sort_rev.txt")
    assert capsys.readouterr().out.strip() == "c\nb\na"


def test_sort_unique(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/sort_uniq.txt", root, data="a\nb\na\nc\nb\n")
    Shell(kernel, root).handle_line("sort -u /tmp/sort_uniq.txt")
    assert capsys.readouterr().out.strip() == "a\nb\nc"


def test_sort_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'z\nb\na' | sort")
    assert capsys.readouterr().out.strip() == "a\nb\nz"


# -- uniq -------------------------------------------------------------------


def test_uniq_basic(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/uniq_test.txt", root, data="a\na\nb\nb\nc\na\n")
    Shell(kernel, root).handle_line("uniq /tmp/uniq_test.txt")
    assert capsys.readouterr().out.strip() == "a\nb\nc\na"


def test_uniq_empty(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo '' | uniq")
    assert capsys.readouterr().out.strip() == ""


def test_uniq_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'a\na\nb' | uniq")
    assert capsys.readouterr().out.strip() == "a\nb"


# -- rev --------------------------------------------------------------------


def test_rev_basic(kernel, root, capsys):
    fs = kernel.fs
    fs.create_file("/tmp/rev_test.txt", root, data="abc\n123\nhello")
    Shell(kernel, root).handle_line("rev /tmp/rev_test.txt")
    assert capsys.readouterr().out.strip() == "cba\n321\nolleh"


def test_rev_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'hello' | rev")
    assert capsys.readouterr().out.strip() == "olleh"


# -- tr ---------------------------------------------------------------------


def test_tr_substitute(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'hello' | tr 'ae' 'AE'")
    assert capsys.readouterr().out.strip() == "hEllo"


# -- tr: диапазоны -----------------------------------------------------------


def test_tr_range_uppercase(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'hello' | tr a-z A-Z")
    assert capsys.readouterr().out.strip() == "HELLO"


def test_tr_range_digits(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'a1b2c3' | tr 0-9 X")
    assert capsys.readouterr().out.strip() == "aXbXcX"


def test_tr_range_padding(kernel, root, capsys):
    # SET2 короче SET1 — последний символ повторяется (как в coreutils)
    Shell(kernel, root).handle_line("echo 'abc' | tr a-z X")
    assert capsys.readouterr().out.strip() == "XXX"


def test_tr_range_descending_error(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'abc' | tr z-a X")
    assert "reverse collating" in capsys.readouterr().err


def test_tr_expand_set_ranges():
    from pyos.commands.text_cmds import _expand_set
    assert _expand_set("a-z") == "abcdefghijklmnopqrstuvwxyz"
    assert _expand_set("0-9") == "0123456789"
    assert _expand_set("a-fx") == "abcdefx"
    assert _expand_set("-a") == "-a"
    assert _expand_set("a-") == "a-"


def test_tr_expand_set_escapes():
    from pyos.commands.text_cmds import _expand_set
    assert _expand_set(r"\n\t") == "\n\t"
    assert _expand_set(r"\\") == "\\"


# -- cut --------------------------------------------------------------------


def test_cut_fields_pipe(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo 'a:b:c' | cut -d: -f2")
    assert capsys.readouterr().out.strip() == "b"


# ============================================================================
# Системные команды (whoami, id, groups, uname, hostname, date, which, df, free, help, man, ps, kill, uptime, env)
# ============================================================================

# -- whoami -----------------------------------------------------------------


def test_whoami_root(kernel, root, capsys):
    Shell(kernel, root).handle_line("whoami")
    assert capsys.readouterr().out.strip() == "root"


def test_whoami_user(kernel, root, user, capsys):
    Shell(kernel, user).handle_line("whoami")
    assert capsys.readouterr().out.strip() == "user"


# -- id ---------------------------------------------------------------------


def test_id_root(kernel, root, capsys):
    Shell(kernel, root).handle_line("id")
    out = capsys.readouterr().out.strip()
    assert "uid=0(root)" in out


def test_id_specific_user(kernel, root, capsys):
    Shell(kernel, root).handle_line("id user")
    out = capsys.readouterr().out.strip()
    assert "uid=1000(user)" in out


# -- groups -----------------------------------------------------------------


def test_groups_root(kernel, root, capsys):
    Shell(kernel, root).handle_line("groups")
    out = capsys.readouterr().out.strip()
    assert "root" in out


# -- uname ------------------------------------------------------------------


def test_uname(kernel, root, capsys):
    Shell(kernel, root).handle_line("uname")
    assert capsys.readouterr().out.strip() == "pyOS"


def test_uname_all(kernel, root, capsys):
    Shell(kernel, root).handle_line("uname -a")
    out = capsys.readouterr().out.strip()
    assert "pyOS" in out


def test_uname_release(kernel, root, capsys):
    Shell(kernel, root).handle_line("uname -r")
    out = capsys.readouterr().out.strip()
    assert out  # non-empty


# -- hostname ----------------------------------------------------------------


def test_hostname_default(kernel, root, capsys):
    Shell(kernel, root).handle_line("hostname")
    assert capsys.readouterr().out.strip() == "pyos"


def test_hostname_set(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("hostname mybox")
    sh.handle_line("hostname")
    assert capsys.readouterr().out.strip() == "mybox"


# -- date -------------------------------------------------------------------


def test_date(kernel, root, capsys):
    Shell(kernel, root).handle_line("date")
    out = capsys.readouterr().out.strip()
    assert out  # non-empty


# -- env --------------------------------------------------------------------


def test_env_has_vars(kernel, root, capsys):
    Shell(kernel, root).handle_line("env")
    out = capsys.readouterr().out.strip()
    assert "HOME=/root" in out
    assert "USER=root" in out


# -- true / false ------------------------------------------------------------


def test_true_succeeds(kernel, root):
    out, err, code = Shell(kernel, root).run_single(["true"], "")
    assert code == 0
    assert out == ""


def test_false_fails(kernel, root):
    out, err, code = Shell(kernel, root).run_single(["false"], "")
    assert code == 1


def test_true_false_in_and_or_chain(kernel, root, capsys):
    Shell(kernel, root).handle_line("true && echo ran && false || echo fallback")
    assert capsys.readouterr().out.strip() == "ran\nfallback"


def test_true_registered():
    from pyos.commands import get
    assert get("true") is not None


def test_false_registered():
    from pyos.commands import get
    assert get("false") is not None


# -- which ------------------------------------------------------------------


def test_which_found_stdout_zero(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("which ls")
    captured = capsys.readouterr()
    assert captured.out.strip() == "/bin/ls"
    assert captured.err == ""
    assert sh.last_code == 0


def test_which_not_found_stderr_one(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("which nonexistent_cmd_xyz")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "which: no nonexistent_cmd_xyz" in captured.err
    assert sh.last_code == 1


# -- df ---------------------------------------------------------------------


def test_df(kernel, root, capsys):
    Shell(kernel, root).handle_line("df")
    out = capsys.readouterr().out.strip()
    assert "pyfs" in out
    assert "Mounted" in out


# -- free -------------------------------------------------------------------


def test_free(kernel, root, capsys):
    Shell(kernel, root).handle_line("free")
    out = capsys.readouterr().out.strip()
    assert "Mem" in out
    assert "total" in out


# -- ps ---------------------------------------------------------------------


def test_ps_has_init(kernel, root, capsys):
    Shell(kernel, root).handle_line("ps")
    out = capsys.readouterr().out.strip()
    assert "init" in out or "PID" in out


def test_ps_aux(kernel, root, capsys):
    Shell(kernel, root).handle_line("ps aux")
    out = capsys.readouterr().out.strip()
    assert "init" in out


# -- kill -------------------------------------------------------------------


def test_kill_missing_process_error_to_stderr_and_nonzero(kernel, root,
                                                          capsys):
    sh = Shell(kernel, root)
    sh.handle_line("kill 99999")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No such process" in captured.err
    assert sh.last_code != 0


def _spawn_victim(kernel):
    """Создаёт живой процесс, которого можно убить."""
    return kernel.create_process(name="victim", remaining=10**9)


@pytest.mark.parametrize("signal_spec", ["9", "TERM", "SIGTERM", "KILL", "SIGKILL"])
def test_kill_accepts_signal_specs(kernel, root, capsys, signal_spec):
    """kill понимает номера и имена сигналов."""
    proc = _spawn_victim(kernel)
    Shell(kernel, root).handle_line(f"kill -{signal_spec} {proc.pid}")
    captured = capsys.readouterr()
    assert kernel.get_process(proc.pid) is None
    assert captured.err == ""


def test_kill_invalid_signal_still_stderr(kernel, root, capsys):
    """Неизвестный сигнал — ошибка invalid signal specification в stderr."""
    proc = _spawn_victim(kernel)
    sh = Shell(kernel, root)
    sh.handle_line(f"kill -BOGUS {proc.pid}")
    captured = capsys.readouterr()
    assert "invalid signal specification" in captured.err
    assert sh.last_code != 0
    assert kernel.get_process(proc.pid) is proc


@pytest.mark.parametrize("signal_spec", ["STOP", "SIGSTOP", "CONT", "SIGCONT",
                                         "TSTP", "SIGTSTP"])
def test_kill_rejects_unsupported_signals(kernel, root, capsys, signal_spec):
    """Остановка/продолжение не реализованы в симуляторе."""
    proc = _spawn_victim(kernel)
    sh = Shell(kernel, root)
    sh.handle_line(f"kill -{signal_spec} {proc.pid}")
    captured = capsys.readouterr()
    assert "not supported" in captured.err
    assert sh.last_code != 0
    assert kernel.get_process(proc.pid) is proc


def test_kill_named_signal_logs_number_in_dmesg(kernel, root, capsys):
    """Имя сигнала разворачивается в номер в журнале ядра."""
    proc = _spawn_victim(kernel)
    sh = Shell(kernel, root)
    sh.handle_line(f"kill -KILL {proc.pid}")
    sh.handle_line("dmesg")
    assert f"signal 9 sent to pid {proc.pid}" in capsys.readouterr().out


def test_kill_l_prints_supported_signals(kernel, root, capsys):
    """kill -l перечисляет поддерживаемые сигналы."""
    Shell(kernel, root).handle_line("kill -l")
    tokens = capsys.readouterr().out.split()
    for token in ("TERM", "SIGTERM", "KILL", "SIGKILL"):
        assert token in tokens


def test_kill_l_does_not_include_unsupported_stop_signals(kernel, root, capsys):
    """STOP/TSTP/CONT не выдаются как поддерживаемые."""
    Shell(kernel, root).handle_line("kill -l")
    tokens = capsys.readouterr().out.split()
    for token in ("STOP", "SIGSTOP", "CONT", "SIGCONT",
                  "TSTP", "SIGTSTP"):
        assert token not in tokens


def test_kill_l_name_returns_number(kernel, root, capsys):
    """kill -l <имя> печатает номер сигнала."""
    Shell(kernel, root).handle_line("kill -l TERM")
    assert capsys.readouterr().out.strip() == "15"


def test_kill_l_number_returns_name(kernel, root, capsys):
    """kill -l <номер> печатает имя сигнала."""
    Shell(kernel, root).handle_line("kill -l 9")
    assert capsys.readouterr().out.strip() == "KILL"


def test_kill_l_invalid_name(kernel, root, capsys):
    """kill -l с неизвестным сигналом — ошибка."""
    sh = Shell(kernel, root)
    sh.handle_line("kill -l BOGUS")
    captured = capsys.readouterr()
    assert "invalid signal specification" in captured.err
    assert sh.last_code != 0


def test_kill_l_with_pid_args(kernel, root, capsys):
    """kill -l принимает только один сигнал, PID-подобный аргумент — ошибка."""
    sh = Shell(kernel, root)
    sh.handle_line("kill -l 123")
    captured = capsys.readouterr()
    assert "invalid signal specification" in captured.err
    assert sh.last_code != 0

    sh.handle_line("kill -l 123 456")
    captured = capsys.readouterr()
    assert "too many arguments" in captured.err
    assert sh.last_code != 0


@pytest.mark.parametrize("signal_spec", [None, "-9", "-TERM"])
def test_kill_still_works_after_l_change(kernel, root, capsys,
                                         signal_spec):
    """Обычная отправка сигналов не сломалась."""
    proc = _spawn_victim(kernel)
    line = f"kill {signal_spec} {proc.pid}" if signal_spec else f"kill {proc.pid}"
    Shell(kernel, root).handle_line(line)
    assert kernel.get_process(proc.pid) is None


def test_kill_rejects_numeric_unsupported_signal(kernel, root, capsys):
    """Числовая форма STOP тоже отклоняется."""
    proc = _spawn_victim(kernel)
    sh = Shell(kernel, root)
    sh.handle_line(f"kill -19 {proc.pid}")
    captured = capsys.readouterr()
    assert "not supported" in captured.err
    assert sh.last_code != 0
    assert kernel.get_process(proc.pid) is proc


# -- uptime -----------------------------------------------------------------


def test_uptime(kernel, root, capsys):
    Shell(kernel, root).handle_line("uptime")
    out = capsys.readouterr().out.strip()
    assert "up" in out or "uptime" in out


# -- help / man -------------------------------------------------------------


def test_help_no_args(kernel, root, capsys):
    Shell(kernel, root).handle_line("help")
    out = capsys.readouterr().out.strip()
    assert "pyOS" in out or "команд" in out or "commands" in out


def test_help_command(kernel, root, capsys):
    Shell(kernel, root).handle_line("help echo")
    out = capsys.readouterr().out.strip()
    assert "echo" in out


def test_man(kernel, root, capsys):
    Shell(kernel, root).handle_line("man ls")
    out = capsys.readouterr().out.strip()
    assert "ls" in out


# ============================================================================
# Команды зарегистрированы
# ============================================================================


def test_grep_registered():
    from pyos.commands import get
    assert get("grep") is not None


def test_wc_registered():
    from pyos.commands import get
    assert get("wc") is not None


def test_head_registered():
    from pyos.commands import get
    assert get("head") is not None


def test_tail_registered():
    from pyos.commands import get
    assert get("tail") is not None


def test_sort_registered():
    from pyos.commands import get
    assert get("sort") is not None


def test_uniq_registered():
    from pyos.commands import get
    assert get("uniq") is not None


def test_rev_registered():
    from pyos.commands import get
    assert get("rev") is not None


def test_tr_registered():
    from pyos.commands import get
    assert get("tr") is not None


def test_cut_registered():
    from pyos.commands import get
    assert get("cut") is not None


def test_whoami_registered():
    from pyos.commands import get
    assert get("whoami") is not None


def test_id_registered():
    from pyos.commands import get
    assert get("id") is not None


def test_groups_registered():
    from pyos.commands import get
    assert get("groups") is not None


def test_uname_registered():
    from pyos.commands import get
    assert get("uname") is not None


def test_hostname_registered():
    from pyos.commands import get
    assert get("hostname") is not None


def test_date_registered():
    from pyos.commands import get
    assert get("date") is not None


def test_env_registered():
    from pyos.commands import get
    assert get("env") is not None


def test_which_registered():
    from pyos.commands import get
    assert get("which") is not None


def test_df_registered():
    from pyos.commands import get
    assert get("df") is not None


def test_free_registered():
    from pyos.commands import get
    assert get("free") is not None


def test_ps_registered():
    from pyos.commands import get
    assert get("ps") is not None


def test_kill_registered():
    from pyos.commands import get
    assert get("kill") is not None


def test_uptime_registered():
    from pyos.commands import get
    assert get("uptime") is not None


def test_help_registered():
    from pyos.commands import get
    assert get("help") is not None


def test_man_registered():
    from pyos.commands import get
    assert get("man") is not None
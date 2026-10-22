"""Тесты разбора командной строки и раскрытий в шелле."""

import pytest

from pyos.errors import InvalidArgument
from pyos.shell import Shell, parse_pipeline, split_jobs, tokenize


def words(line, env=None):
    return [str(t) for t in tokenize(line, env)]


# --- токенизация и операторы -----------------------------------------------

def test_tokenize_basic():
    assert words("ls -l /tmp") == ["ls", "-l", "/tmp"]


def test_pipeline_redirect_tokens():
    assert words("cat a | grep b > out") == [
        "cat", "a", "|", "grep", "b", ">", "out"]


def test_operator_inside_quotes_is_literal():
    assert words("echo '|'") == ["echo", "|"]


def test_append_operator():
    assert words("echo a >> f") == ["echo", "a", ">>", "f"]


# --- раскрытие переменных --------------------------------------------------

def test_variable_expansion_unquoted():
    assert words("echo $HOME", {"HOME": "/root"}) == ["echo", "/root"]


def test_variable_expansion_braces():
    assert words("echo ${HOME}/x", {"HOME": "/root"}) == ["echo", "/root/x"]


def test_single_quote_blocks_expansion():
    assert words("echo '$HOME'", {"HOME": "/root"}) == ["echo", "$HOME"]


def test_double_quote_allows_expansion():
    assert words('echo "a $HOME b"', {"HOME": "/root"}) == [
        "echo", "a /root b"]


def test_escape_blocks_expansion():
    assert words(r"echo \$HOME", {"HOME": "/root"}) == ["echo", "$HOME"]


def test_quoted_flag_marks_tokens():
    toks = tokenize("echo '$HOME' $HOME", {"HOME": "/root"})
    assert toks[1].quoted is True
    assert toks[2].quoted is False


# --- разбор конвейеров и заданий -------------------------------------------

def test_parse_pipeline():
    cmds = parse_pipeline(tokenize("cat a | grep b > out"))
    assert cmds[0] == (["cat", "a"], [])
    assert cmds[1] == (["grep", "b"], [(">", "out")])


def test_split_jobs():
    jobs = split_jobs(tokenize("a ; b & c"))
    assert jobs == [(["a"], False), (["b"], True), (["c"], False)]


# --- глоббинг --------------------------------------------------------------

def test_glob_expands_unquoted(kernel, root, capsys):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root)
    fs.create_file("/tmp/g/a.py", root)
    fs.create_file("/tmp/g/b.py", root)
    sh = Shell(kernel, root)
    sh.handle_line("echo /tmp/g/*.py")
    assert capsys.readouterr().out.strip() == "/tmp/g/a.py /tmp/g/b.py"


def test_glob_quoted_is_not_expanded(kernel, root, capsys):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root)
    fs.create_file("/tmp/g/a.py", root)
    sh = Shell(kernel, root)
    sh.handle_line("echo '/tmp/g/*.py'")
    assert capsys.readouterr().out.strip() == "/tmp/g/*.py"


def test_glob_without_match_keeps_literal(kernel, root, capsys):
    fs = kernel.fs
    fs.mkdir("/tmp/g", root)
    sh = Shell(kernel, root)
    sh.handle_line("echo /tmp/g/*.nomatch")
    assert capsys.readouterr().out.strip() == "/tmp/g/*.nomatch"


# --- nice: разбор аргументов -----------------------------------------------


def test_parse_nice_n_flag():
    assert Shell._parse_nice(["nice", "-n", "5", "echo"]) == (5, ["echo"])


def test_parse_nice_compact_form():
    assert Shell._parse_nice(["nice", "-n5", "echo"]) == (5, ["echo"])


def test_parse_nice_bare_number():
    assert Shell._parse_nice(["nice", "5", "echo"]) == (5, ["echo"])


def test_parse_nice_bare_negative_number():
    assert Shell._parse_nice(["nice", "-5", "echo"]) == (-5, ["echo"])


def test_parse_nice_default_increment():
    assert Shell._parse_nice(["nice", "echo"]) == (10, ["echo"])


def test_parse_nice_invalid_number():
    with pytest.raises(InvalidArgument, match="invalid adjustment 'abc'"):
        Shell._parse_nice(["nice", "-n", "abc", "echo"])


def test_parse_nice_missing_value():
    with pytest.raises(InvalidArgument, match="requires an argument"):
        Shell._parse_nice(["nice", "-n"])


def test_parse_nice_out_of_range_high():
    with pytest.raises(InvalidArgument, match=r"expected -20\.\.19"):
        Shell._parse_nice(["nice", "-n", "100", "echo"])


def test_parse_nice_out_of_range_low():
    with pytest.raises(InvalidArgument, match=r"expected -20\.\.19"):
        Shell._parse_nice(["nice", "-n", "-25", "echo"])


def test_parse_nice_missing_command_after_n():
    with pytest.raises(InvalidArgument, match="missing command operand"):
        Shell._parse_nice(["nice", "-n", "5"])


def test_parse_nice_missing_command_after_bare_number():
    with pytest.raises(InvalidArgument, match="missing command operand"):
        Shell._parse_nice(["nice", "5"])


# --- nice: интеграция через шелл -------------------------------------------


def test_nice_bare_number_runs_command(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice 5 echo hi")
    captured = capsys.readouterr()
    assert "hi" in captured.out
    assert sh.last_code == 0


def test_nice_negative_bare_number_runs_command(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice -5 echo hi")
    captured = capsys.readouterr()
    assert "hi" in captured.out
    assert sh.last_code == 0


def test_nice_invalid_number_reports_error(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice -n abc echo hi")
    captured = capsys.readouterr()
    assert "invalid adjustment 'abc'" in captured.err
    assert sh.last_code != 0
    assert "hi" not in captured.out


def test_nice_out_of_range_reports_error(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice -n 100 echo hi")
    captured = capsys.readouterr()
    assert "expected -20..19" in captured.err
    assert sh.last_code != 0
    assert "hi" not in captured.out


def test_nice_missing_value_reports_error(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice -n")
    captured = capsys.readouterr()
    assert "requires an argument" in captured.err
    assert sh.last_code != 0


def test_nice_background_invalid_does_not_crash(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice -n abc sleep 1 &")
    captured = capsys.readouterr()
    assert "invalid adjustment" in captured.err
    assert sh.last_code != 0
    assert sh.jobs == []


def test_nice_background_applies_value(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice -n 5 sleep 3 &")
    capsys.readouterr()
    assert len(sh.jobs) == 1
    proc = kernel.get_process(sh.jobs[-1]["pid"])
    assert proc is not None
    assert proc.nice == 5


def test_nice_background_bare_number_applies_value(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("nice 5 sleep 3 &")
    capsys.readouterr()
    assert len(sh.jobs) == 1
    proc = kernel.get_process(sh.jobs[-1]["pid"])
    assert proc is not None
    assert proc.nice == 5

"""Тесты разбора командной строки и раскрытий в шелле."""

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

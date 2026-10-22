"""Тесты логических операторов ``&&`` и ``||``."""

from pyos.shell import Shell, split_and_or, split_jobs, tokenize


def words(line, env=None):
    return [str(t) for t in tokenize(line, env)]


# --- разбор ----------------------------------------------------------------

def test_tokenize_and_or_operators():
    assert words("a && b || c") == ["a", "&&", "b", "||", "c"]


def test_single_ampersand_still_background():
    assert words("a & b") == ["a", "&", "b"]


def test_split_jobs_keeps_and_or_inside_job():
    jobs = split_jobs(tokenize("a && b ; c"))
    assert jobs == [(["a", "&&", "b"], False), (["c"], False)]


def test_split_jobs_backgrounds_whole_chain():
    jobs = split_jobs(tokenize("a && b & c"))
    assert jobs == [(["a", "&&", "b"], True), (["c"], False)]


def test_split_and_or():
    assert split_and_or(tokenize("a && b || c")) == [
        (["a"], "&&"), (["b"], "||"), (["c"], None)]


# --- поведение -------------------------------------------------------------

def test_and_runs_both_on_success(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo one && echo two")
    assert capsys.readouterr().out.strip() == "one\ntwo"


def test_and_skips_on_failure(kernel, root, capsys):
    Shell(kernel, root).handle_line("no_such_cmd && echo two")
    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert "command not found" in captured.err


def test_or_runs_on_failure(kernel, root, capsys):
    Shell(kernel, root).handle_line("no_such_cmd || echo fallback")
    assert capsys.readouterr().out.strip() == "fallback"


def test_or_skips_on_success(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo one || echo two")
    assert capsys.readouterr().out.strip() == "one"


def test_and_or_chained(kernel, root, capsys):
    Shell(kernel, root).handle_line("echo one && no_such_cmd || echo done")
    assert capsys.readouterr().out.strip() == "one\ndone"


def test_semicolon_runs_regardless(kernel, root, capsys):
    Shell(kernel, root).handle_line("no_such_cmd ; echo after")
    assert capsys.readouterr().out.strip() == "after"


def test_background_runs_whole_chain(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("echo bg1 && echo bg2 &")
    assert capsys.readouterr().out.strip().startswith("[1]")
    kernel.scheduler.drain()
    out = capsys.readouterr().out
    assert "bg1" in out and "bg2" in out
    assert len(kernel.processes) == 1

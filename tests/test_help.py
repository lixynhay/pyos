"""Тесты help/man для встроенных команд шелла."""

from pyos.shell import Shell


def _help(kernel, root, capsys, name):
    Shell(kernel, root).handle_line(f"help {name}")
    return capsys.readouterr().out


def test_help_wait_contains_synopsis(kernel, root, capsys):
    out = _help(kernel, root, capsys, "wait")

    assert "wait" in out
    assert "SYNOPSIS" in out
    assert "wait [pid|%job]" in out


def test_help_bg_contains_synopsis(kernel, root, capsys):
    out = _help(kernel, root, capsys, "bg")

    assert "bg" in out
    assert "SYNOPSIS" in out
    assert "bg [%job|pid]" in out


def test_help_nice_contains_synopsis(kernel, root, capsys):
    out = _help(kernel, root, capsys, "nice")

    assert "nice" in out
    assert "SYNOPSIS" in out
    assert "nice [-n N] command [args...]" in out
    assert "nice N command [args...]" in out


def test_help_waitpid_jobs_fg_contain_synopsis(kernel, root, capsys):
    for name, usage in (
            ("waitpid", "waitpid pid"),
            ("jobs", "jobs"),
            ("fg", "fg [%job|pid]")):
        out = _help(kernel, root, capsys, name)
        assert "SYNOPSIS" in out
        assert usage in out


def test_help_unknown_builtin_still_works_or_gives_clear_error(
        kernel, root, capsys):
    Shell(kernel, root).handle_line("help frobnicate")
    captured = capsys.readouterr()

    assert "no manual entry" in captured.err
    assert "frobnicate" in captured.err
    assert captured.out == ""


def test_help_other_builtin_still_works(kernel, root, capsys):
    out = _help(kernel, root, capsys, "export")

    assert "export" in out
    assert "встроенная команда шелла" in out

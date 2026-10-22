"""Stage 2a: background pipelines/redirects and the bare-sleep fast path."""

import pytest

from pyos.errors import NoSuchFileOrDirectory
from pyos.process import ProcessState
from pyos.scheduler import TICKS_PER_SECOND
from pyos.shell import Shell


def _output_lines(out):
    """Строки вывода без служебных строк job'а вида ``[1] ...``."""
    return [line for line in out.splitlines() if not line.startswith("[")]


def test_background_pipeline_executes(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("echo hello | cat &")
    capsys.readouterr()
    job = sh.jobs[0]

    kernel.scheduler.drain()
    out = capsys.readouterr().out

    assert _output_lines(out) == ["hello"]
    assert job["done"] is True
    assert job["proc"].exit_code == 0

    # sleep первым в pipeline тоже должен идти общим путём.
    sh.handle_line("sleep 1 | echo piped &")
    capsys.readouterr()
    job = sh.jobs[1]

    kernel.scheduler.drain()
    out = capsys.readouterr().out

    assert _output_lines(out) == ["piped"]
    assert job["done"] is True
    assert job["proc"].exit_code == 0
    assert len(kernel.processes) == 1


def test_background_redirect_executes(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("sleep 1 > /tmp/bg_redirect &")
    capsys.readouterr()
    job = sh.jobs[0]

    kernel.scheduler.drain()
    capsys.readouterr()

    assert kernel.fs.lookup("/tmp/bg_redirect", user=root).is_file()
    assert job["done"] is True
    assert job["proc"].exit_code == 0
    assert len(kernel.processes) == 1


def test_background_bare_sleep_still_blocks(kernel, root, capsys):
    sh = Shell(kernel, root)

    clock_before = kernel.scheduler.clock
    sh.handle_line("sleep 2 &")
    capsys.readouterr()
    job = sh.jobs[0]
    proc = job["proc"]

    assert proc.state is ProcessState.BLOCKED
    assert proc.wake_tick == clock_before + 2 * TICKS_PER_SECOND
    assert proc.cpu_ticks == 0
    assert proc.remaining == 0
    assert proc in kernel.scheduler.blocked
    assert proc not in kernel.scheduler.ready
    assert job["done"] is False


def test_background_logical_ops_execute(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false && touch /tmp/bg_skipped &")
    capsys.readouterr()
    skipped = sh.jobs[0]
    kernel.scheduler.drain()
    capsys.readouterr()

    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/tmp/bg_skipped", user=root)
    assert skipped["done"] is True
    assert skipped["proc"].exit_code == 1

    sh.handle_line("false || touch /tmp/bg_fallback &")
    capsys.readouterr()
    fallback = sh.jobs[1]
    kernel.scheduler.drain()
    capsys.readouterr()

    assert kernel.fs.lookup("/tmp/bg_fallback", user=root).is_file()
    assert fallback["done"] is True
    assert fallback["proc"].exit_code == 0

    sh.handle_line("true && touch /tmp/bg_and &")
    capsys.readouterr()
    and_job = sh.jobs[2]
    kernel.scheduler.drain()
    capsys.readouterr()

    assert kernel.fs.lookup("/tmp/bg_and", user=root).is_file()
    assert and_job["done"] is True
    assert and_job["proc"].exit_code == 0
    assert len(kernel.processes) == 1

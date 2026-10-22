"""Тесты ядра: таблица процессов и её очистка."""

from pyos.shell import Shell


def test_init_process_present(kernel):
    assert kernel.init.pid == 1
    assert kernel.get_process(1) is kernel.init


def test_foreground_processes_are_reaped(kernel, root):
    sh = Shell(kernel, root)
    for _ in range(20):
        sh.handle_line("echo x")
    # завершённые процессы не должны накапливаться: остаётся только init
    assert len(kernel.processes) == 1


def test_background_job_runs_and_leaves_table_clean(kernel, root):
    sh = Shell(kernel, root)
    sh.handle_line("true &")
    kernel.scheduler.drain()
    assert len(kernel.processes) == 1

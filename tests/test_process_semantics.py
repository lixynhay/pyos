"""Characterization-тесты текущей семантики процессов, jobs и планировщика.

Эти тесты фиксируют поведение как есть, включая известные баги. На этапе 2/3
они должны меняться вместе со спекой, а не тихо удаляться. Никакой production
код здесь не проверяется "как надо" — только как он реально работает сейчас.
"""

import pytest

from pyos.errors import NoSuchFileOrDirectory
from pyos.process import ProcessState
from pyos.signals import SIGCHLD, SIGTERM
from pyos.shell import Shell


# ---------------------------------------------------------------------------
# Процессы / создание
# ---------------------------------------------------------------------------

def test_create_process_initial_fields_and_monotonic_pid(kernel):
    assert kernel.init.pid == 1

    first = kernel.create_process(name="first")
    assert first.pid == 2
    assert first.ppid == 0
    assert first.uid == 0
    assert first.gid == 0
    assert first.exit_code == 0
    assert first.start_time == kernel.scheduler.clock == 0
    assert first.state is ProcessState.NEW
    assert first.cmdline == "first"
    assert first.cwd == "/"

    kernel.scheduler.tick(3)
    second = kernel.create_process(name="second")
    assert second.pid > first.pid
    assert second.start_time == kernel.scheduler.clock == 3


def test_init_is_pid_1_and_not_killable(kernel):
    init = kernel.init
    assert init.pid == 1
    assert init.ppid == 0
    assert init.is_kernel is True
    assert init.state is ProcessState.RUNNING

    assert kernel.kill(1, SIGTERM) is False
    assert kernel.kill(1, 9) is False
    assert kernel.get_process(1) is init
    assert init.state is ProcessState.RUNNING


def test_create_process_accepts_out_of_range_nice_currently(kernel):
    # CHARACTERIZATION: kernel.create_process() does not range-check nice;
    # only the shell-level nice builtin validates it. Kernel-boundary
    # validation is separate debt (stage 2/3 by decision).
    proc = kernel.create_process(name="weird", nice=100)
    assert proc.nice == 100
    assert kernel.get_process(proc.pid) is proc
    assert kernel.scheduler.weight(proc) > 0.0

    too_low = kernel.create_process(name="weird-low", nice=-100)
    assert too_low.nice == -100


# ---------------------------------------------------------------------------
# Завершение / reap
# ---------------------------------------------------------------------------

def test_foreground_command_sets_last_code_but_not_proc_exit_code(
        kernel, root, monkeypatch, capsys):
    captured = []
    original_reap = kernel.reap

    def spy(proc):
        captured.append(proc)
        original_reap(proc)

    monkeypatch.setattr(kernel, "reap", spy)
    sh = Shell(kernel, root)

    sh.handle_line("false")
    capsys.readouterr()

    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert len(captured) == 1
    proc = captured[0]
    # CHARACTERIZATION: foreground run_single() never stores the command
    # status in the process; exit_code stays at the constructor default 0.
    # fixed in stage 2.
    assert proc.exit_code == 0
    assert proc.state is ProcessState.TERMINATED
    assert kernel.get_process(proc.pid) is None
    assert len(kernel.processes) == 1


def test_kill_sets_128_plus_signum_and_reaps_immediately(kernel):
    proc = kernel.create_process(name="victim", remaining=100)
    kernel.scheduler.add(proc)

    assert kernel.kill(proc.pid, SIGTERM) is True
    assert proc.exit_code == 128 + SIGTERM == 143
    assert proc.state is ProcessState.TERMINATED
    assert kernel.get_process(proc.pid) is None
    assert proc not in kernel.scheduler.ready
    assert proc not in kernel.scheduler.blocked
    assert len(kernel.processes) == 1


def test_kill_allows_ign_signals_but_still_terminates(kernel):
    proc = kernel.create_process(name="victim", remaining=100)

    assert kernel.kill(proc.pid, SIGCHLD) is True
    # CHARACTERIZATION: SIGCHLD has DEFAULT_ACTION "ign", but kernel.kill()
    # terminates the process and reports 128+signum anyway. Signal semantics
    # are separate debt.
    assert proc.exit_code == 128 + SIGCHLD == 145
    assert proc.state is ProcessState.TERMINATED
    assert kernel.get_process(proc.pid) is None


def test_zombie_state_is_never_assigned_today(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false")
    capsys.readouterr()

    victim = kernel.create_process(name="victim", remaining=100)
    kernel.kill(victim.pid, SIGTERM)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]
    kernel.scheduler.drain()
    capsys.readouterr()

    # CHARACTERIZATION: ProcessState.ZOMBIE is a dead enum value; every
    # termination path goes straight to TERMINATED and is reaped at once.
    # There is no zombie before wait today. fixed in stage 2.
    assert victim.state is ProcessState.TERMINATED
    assert job["proc"].state is ProcessState.TERMINATED
    assert job["proc"].is_alive is False
    assert all(p.state is not ProcessState.ZOMBIE
               for p in kernel.processes.values())
    assert len(kernel.processes) == 1


# ---------------------------------------------------------------------------
# Фон / jobs / fg / bg / $?
# ---------------------------------------------------------------------------

def test_background_launch_keeps_previous_last_code(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false")
    capsys.readouterr()
    assert sh.last_code == 1

    sh.handle_line("true &")
    capsys.readouterr()

    job = sh.jobs[0]
    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert job["done"] is False
    assert job["proc"].is_alive is True

    kernel.scheduler.drain()


def test_background_completion_sets_proc_exit_code_asynchronously(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()

    job = sh.jobs[0]
    pid = job["pid"]
    assert sh.last_code == 0
    assert job["done"] is False
    assert kernel.get_process(pid) is job["proc"]

    kernel.scheduler.drain()
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 1
    assert kernel.get_process(pid) is None
    assert sh.last_code == 1
    assert len(kernel.processes) == 1


def test_worker_overwrites_global_last_code_currently(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line("true")
    capsys.readouterr()
    assert sh.last_code == 0
    assert job["done"] is False

    kernel.scheduler.drain()
    capsys.readouterr()

    # CHARACTERIZATION: the background worker runs _execute_segments() and
    # writes the global last_code/exit_code as a side effect, overwriting the
    # status of the later foreground command. fixed in stage 2.
    assert sh.last_code == 1
    assert job["proc"].exit_code == 1


def test_fg_returns_zero_for_failed_job(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line("fg")
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 1
    # CHARACTERIZATION: fg is not a real wait and always returns code 0,
    # even when the job failed. fixed in stage 2.
    assert sh.last_code == 0
    assert sh.exit_code == 0


def test_jobs_killed_job_still_reports_running(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("sleep 100 &")
    capsys.readouterr()
    job = sh.jobs[0]
    assert kernel.get_process(job["pid"]) is job["proc"]

    sh.handle_line(f"kill {job['pid']}")
    capsys.readouterr()

    # CHARACTERIZATION: kill removes the process and sets exit_code, but the
    # job bookkeeping is never updated, so jobs still prints Running.
    # fixed in stage 2.
    assert job["done"] is False
    assert job["proc"].exit_code == 143
    assert kernel.get_process(job["pid"]) is None

    sh.handle_line("jobs")
    out = capsys.readouterr().out
    assert "Running" in out
    assert "Done" not in out


def test_bg_only_prints_and_does_not_resume(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("sleep 100 &")
    capsys.readouterr()
    job = sh.jobs[0]
    proc = job["proc"]

    clock_before = kernel.scheduler.clock
    wake_before = proc.wake_tick
    assert proc.state is ProcessState.BLOCKED

    sh.handle_line("bg")
    out = capsys.readouterr().out

    # CHARACTERIZATION: bg only prints the job line; it does not resume or
    # requeue anything. separate debt (real job control is out of stage 2).
    assert f"[{job['id']}] sleep 100 &" in out
    assert proc.state is ProcessState.BLOCKED
    assert proc.wake_tick == wake_before
    assert kernel.scheduler.clock == clock_before
    assert proc not in kernel.scheduler.ready
    assert job["done"] is False


def test_wait_waitpid_absent_today(kernel, root, capsys):
    sh = Shell(kernel, root)

    assert "wait" not in sh.builtins
    assert "waitpid" not in sh.builtins

    sh.handle_line("wait")
    err = capsys.readouterr().err
    assert "wait: command not found" in err
    assert sh.last_code == 127

    sh.handle_line("waitpid")
    err = capsys.readouterr().err
    assert "waitpid: command not found" in err
    assert sh.last_code == 127
    # CHARACTERIZATION: wait/waitpid are not implemented at all. fixed in
    # stage 2.


# ---------------------------------------------------------------------------
# Фоновый pipeline (is_sleep)
# ---------------------------------------------------------------------------

def test_background_sleep_pipeline_current_behavior(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("sleep 1 | touch /tmp/pipeline_marker &")
    capsys.readouterr()
    job = sh.jobs[0]
    assert job["proc"].state is ProcessState.BLOCKED

    kernel.scheduler.drain()
    capsys.readouterr()

    # CHARACTERIZATION: is_sleep looks only at the first argv and does not
    # check that the job is a single-command pipeline, so the rest of the
    # pipeline never executes. separate debt (not part of stage 2 by default).
    with pytest.raises(NoSuchFileOrDirectory):
        kernel.fs.lookup("/tmp/pipeline_marker", user=root)
    assert job["done"] is True
    assert job["proc"].exit_code == 0
    assert len(kernel.processes) == 1


# ---------------------------------------------------------------------------
# Планировщик
# ---------------------------------------------------------------------------

def test_scheduler_quantum_preemption_and_switches(kernel):
    sched = kernel.scheduler
    proc = kernel.create_process(name="long", remaining=10**9)
    sched.add(proc)

    sched.tick(12)

    assert sched.quantum == 5
    assert proc.cpu_ticks == 12
    assert proc.quantum_used == 2
    assert sched.switches == 3


def test_scheduler_weight_values(kernel):
    sched = kernel.scheduler
    zero = kernel.create_process(name="zero", nice=0)
    high = kernel.create_process(name="high", nice=-20)
    low = kernel.create_process(name="low", nice=19)

    assert sched.weight(zero) == pytest.approx(1.0)
    assert sched.weight(high) == pytest.approx(32.0)
    assert sched.weight(low) == pytest.approx(2.0 ** (-19 / 4))

    sched.add(zero)
    sched.tick(1)
    assert zero.vruntime == pytest.approx(1.0)

    sched.remove(zero)
    sched.add(high)
    sched.tick(1)
    assert high.vruntime == pytest.approx(1.0 / 32.0)

    sched.remove(high)
    sched.add(low)
    sched.tick(1)
    assert low.vruntime == pytest.approx(2.0 ** (19 / 4))


def test_scheduler_blocked_wake_no_duplicate(kernel):
    sched = kernel.scheduler
    proc = kernel.create_process(name="sleeper", remaining=10**9)

    sched.block(proc, ticks=3, reason="sleep")
    assert proc.state is ProcessState.BLOCKED
    assert sched.ready.count(proc) == 0

    sched.tick(3)

    assert proc.wake_tick is None
    assert proc.state is ProcessState.RUNNING
    assert sched.blocked == []
    assert sched.ready.count(proc) + int(sched.current is proc) == 1


def test_scheduler_block_does_not_remove_from_ready_currently(kernel):
    sched = kernel.scheduler
    proc = kernel.create_process(name="queued", remaining=10**9)
    sched.add(proc)
    assert proc in sched.ready

    sched.block(proc, ticks=10, reason="sleep")

    # CHARACTERIZATION: block() adds the process to blocked but leaves it in
    # ready, so it can be picked from ready while still tracked as blocked.
    # separate debt / stage 3 (scheduler structures).
    assert proc.state is ProcessState.BLOCKED
    assert proc in sched.ready
    assert proc in sched.blocked


def test_scheduler_drain_and_run_until_have_tick_limit(kernel):
    sched = kernel.scheduler
    proc = kernel.create_process(name="stuck", remaining=10**9)
    sched.add(proc)

    assert sched.drain(limit=7) == 7
    assert kernel.clock == 7
    assert proc.is_alive

    # CHARACTERIZATION: both helpers silently stop at their tick limit and do
    # not report whether the predicate became true. fg relies on this.
    assert sched.run_until(lambda: False, limit=9) == 9
    assert kernel.clock == 16
    assert proc.is_alive


def test_fg_run_until_may_return_before_completion(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false")
    capsys.readouterr()
    assert sh.last_code == 1

    sh.handle_line("sleep 20000 &")
    capsys.readouterr()
    job = sh.jobs[0]
    proc = job["proc"]
    assert proc.state is ProcessState.BLOCKED

    sh.handle_line("fg")
    capsys.readouterr()

    # CHARACTERIZATION: fg uses run_until(default limit=100000) but never
    # checks that the predicate became true. The job is still blocked, yet fg
    # returns success and resets last_code to 0. separate debt / stage 2.
    assert job["done"] is False
    assert proc.is_alive is True
    assert proc.state is ProcessState.BLOCKED
    assert sh.last_code == 0
    assert sh.exit_code == 0

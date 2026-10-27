"""TRAP-1: characterize the signal, jobs and exit model before ``trap``.

These tests pin down behavior that exists before ``trap`` is implemented. They
are intended to be inverted or extended in TRAP-2..TRAP-5; nothing here should
be softened on the way.

Determinism notes:

* ``sleep 100 &`` is used for background victims, so no job can finish on its
  own clock;
* ignored signals are never waited on (that job would need 100 seconds);
* no real network, no wall-clock assertions.
"""

from pyos.commands import get as get_command
from pyos.process import ProcessState
from pyos.shell import Shell


def _start_sleep_job(sh, capsys):
    """Starts ``sleep 100 &`` and returns its job dict."""
    sh.handle_line("sleep 100 &")
    captured = capsys.readouterr()
    assert captured.err == ""
    job = sh.jobs[0]
    assert captured.out == f"[{job['id']}] {job['pid']}\n"
    return job


def _assert_signal_terminates_job(kernel, root, capsys, spec, expected):
    """kill -SPEC on a background job: Done(N), wait N, process reaped."""
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -{spec} {job['pid']}")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    # kill itself reports success; the signal code is only visible via jobs,
    # wait or fg.
    assert sh.last_code == 0

    sh.handle_line("jobs")
    out = capsys.readouterr().out
    assert f"[{job['id']}] Done({expected}) sleep 100" in out
    assert "Running" not in out

    sh.handle_line(f"wait {job['pid']}")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == expected
    assert sh.exit_code == expected
    assert job["proc"].exit_code == expected
    assert job["proc"].state is ProcessState.TERMINATED
    assert job["proc"].is_alive is False
    assert kernel.get_process(job["pid"]) is None


# -- A. trap is absent -------------------------------------------------------


def test_current_trap_command_not_found(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: command not found\n"
    assert sh.last_code == 127
    assert sh.exit_code == 127
    assert sh.running is True
    # Baseline for TRAP-2: trap is neither a builtin nor a registered command.
    assert "trap" not in sh.builtins
    assert get_command("trap") is None


def test_current_trap_with_signal_command_not_found(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo hi' INT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: command not found\n"
    assert sh.last_code == 127
    assert sh.exit_code == 127


def test_current_trap_exit_command_not_found(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo bye' EXIT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: command not found\n"
    assert sh.last_code == 127
    assert sh.exit_code == 127


# -- B. terminating signals on background jobs -------------------------------


def test_current_kill_term_background_job_sets_done_143(kernel, root,
                                                        capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "TERM", 143)


def test_current_kill_int_background_job_sets_done_130(kernel, root,
                                                       capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "INT", 130)


def test_current_kill_hup_background_job_sets_done_129(kernel, root,
                                                       capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "HUP", 129)


def test_current_kill_usr1_background_job_sets_done_138(kernel, root,
                                                        capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "USR1", 138)


def test_current_kill_pipe_background_job_sets_done_141(kernel, root,
                                                        capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "PIPE", 141)


def test_current_kill_alrm_background_job_sets_done_142(kernel, root,
                                                        capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "ALRM", 142)


def test_current_kill_quit_background_job_sets_done_131(kernel, root,
                                                        capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "QUIT", 131)


# -- C. ignored and unsupported signals --------------------------------------


def _assert_ignored_signal(kernel, root, capsys, spec):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)
    proc = job["proc"]

    sh.handle_line(f"kill -{spec} {proc.pid}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0
    assert sh.exit_code == 0
    # The signal is accepted but not delivered: the process keeps running.
    assert kernel.get_process(proc.pid) is proc
    assert proc.is_alive is True
    assert proc.exit_code == 0
    assert proc.state is ProcessState.BLOCKED

    sh.handle_line("jobs")
    out = capsys.readouterr().out
    assert f"[{job['id']}] Running sleep 100" in out
    assert "Done(" not in out


def test_current_kill_chld_is_ignored(kernel, root, capsys):
    _assert_ignored_signal(kernel, root, capsys, "CHLD")


def test_current_kill_winch_is_ignored(kernel, root, capsys):
    _assert_ignored_signal(kernel, root, capsys, "WINCH")


def _assert_unsupported_signal(kernel, root, capsys, spec):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -{spec} {job['pid']}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        f"kill: -{spec}: signal is not supported by pyos simulator\n")
    assert sh.last_code == 22
    assert sh.exit_code == 22
    assert job["proc"].is_alive is True

    sh.handle_line("jobs")
    assert "Running sleep 100" in capsys.readouterr().out


def test_current_kill_stop_is_unsupported(kernel, root, capsys):
    _assert_unsupported_signal(kernel, root, capsys, "STOP")


def test_current_kill_tstp_is_unsupported(kernel, root, capsys):
    _assert_unsupported_signal(kernel, root, capsys, "TSTP")


def test_current_kill_cont_is_unsupported(kernel, root, capsys):
    _assert_unsupported_signal(kernel, root, capsys, "CONT")


def test_current_kill_unknown_signal_is_error(kernel, root, capsys):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -NOPE {job['pid']}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "kill: -NOPE: invalid signal specification\n"
    assert sh.last_code == 22
    assert sh.exit_code == 22
    assert job["proc"].is_alive is True


# -- D. SIGKILL and init/$$ --------------------------------------------------


def test_current_kill_kill_background_job_sets_done_137(kernel, root,
                                                        capsys):
    _assert_signal_terminates_job(kernel, root, capsys, "KILL", 137)


def test_current_kill_init_pid_does_not_deliver_shell_trap_context(
        kernel, root, capsys):
    sh = Shell(kernel, root)
    assert kernel.init.pid == 1

    sh.handle_line("echo $$")
    assert capsys.readouterr().out == "1\n"

    # $$ is the init PID: the shell has no process of its own, so there is no
    # shell-side trap context to deliver to. TRAP-MVP must keep this.
    sh.handle_line("kill -TERM $$")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "kill: (1) - No such process\n"
    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert sh.running is True
    assert kernel.init.is_alive is True
    assert kernel.init.exit_code == 0


def test_current_kill_pid_1_is_not_shell_trap_delivery(kernel, root,
                                                       capsys):
    sh = Shell(kernel, root)

    sh.handle_line("kill -TERM 1")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "kill: (1) - No such process\n"
    assert sh.last_code == 1
    # init is protected: it is never turned into a killable shell process.
    assert kernel.get_process(1) is kernel.init
    assert kernel.init.is_alive is True
    assert kernel.init.exit_code == 0


# -- E. exit codes and jobs/wait/fg ------------------------------------------


def test_current_kill_command_itself_returns_zero_on_successful_delivery(
        kernel, root, capsys):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -TERM {job['pid']}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0
    assert sh.exit_code == 0

    # $? still reports the kill status, not the victim's signal code.
    sh.handle_line("echo status-$?")
    assert capsys.readouterr().out == "status-0\n"
    assert sh.last_code == 0

    sh.handle_line(f"wait {job['pid']}")
    assert sh.last_code == 143


def test_current_wait_after_kill_returns_signal_code(kernel, root, capsys):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -TERM {job['pid']}")
    capsys.readouterr()

    sh.handle_line(f"wait {job['pid']}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 143
    assert sh.exit_code == 143

    sh.handle_line("echo status-$?")
    assert capsys.readouterr().out == "status-143\n"


def test_current_fg_after_kill_returns_signal_code(kernel, root, capsys):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -TERM {job['pid']}")
    capsys.readouterr()

    sh.handle_line(f"fg {job['pid']}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 143
    assert sh.exit_code == 143

    sh.handle_line("echo fg-status-$?")
    assert capsys.readouterr().out == "fg-status-143\n"

    sh.handle_line("jobs")
    assert "Done(143)" in capsys.readouterr().out


def test_current_jobs_history_after_kill_remains_documented_limit(
        kernel, root, capsys):
    sh = Shell(kernel, root)
    job = _start_sleep_job(sh, capsys)

    sh.handle_line(f"kill -TERM {job['pid']}")
    capsys.readouterr()
    sh.handle_line(f"wait {job['pid']}")
    capsys.readouterr()

    # Documented limit: finished jobs are not dropped from the job table, and
    # kill does not set job["done"]; the status is derived from proc.is_alive.
    assert len(sh.jobs) == 1
    assert job["done"] is False
    assert job["proc"].exit_code == 143
    assert kernel.get_process(job["pid"]) is None

    sh.handle_line("jobs")
    out = capsys.readouterr().out
    assert f"[{job['id']}] Done(143) sleep 100" in out


# -- F. current exit behavior ------------------------------------------------


def test_current_exit_sets_shell_not_running(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("exit")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 0
    assert sh.last_code == 0


def test_current_exit_with_code_sets_exit_code(kernel, root, capsys):
    """CHARACTERIZE current behavior: the requested code is not preserved.

    ``_bi_exit`` assigns ``exit_code = 7``, but ``handle_line`` overwrites
    ``exit_code`` with the segment status (0). Public behavior is therefore 0,
    not 7; TRAP-3 has to decide the target explicitly instead of assuming 7.
    """
    sh = Shell(kernel, root)

    sh.handle_line("exit 7")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    assert sh.last_code == 0
    assert sh.exit_code == 0  # NOT 7: current bug, characterization only


def test_current_exit_does_not_stop_rest_of_line_current_bug(kernel, root,
                                                             capsys):
    sh = Shell(kernel, root)

    # CHARACTERIZE current bug, will be fixed in TRAP-3: the segments after
    # exit still run because handle_line does not check shell.running.
    sh.handle_line("exit; echo after")
    captured = capsys.readouterr()

    assert captured.out == "after\n"
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 0


def test_current_exit_in_pipeline_or_segment_current_behavior(kernel, root,
                                                              capsys):
    sh = Shell(kernel, root)
    sh.handle_line("true && exit; echo after")
    captured = capsys.readouterr()

    # exit inside && still lets the following top-level segment run.
    assert captured.out == "after\n"
    assert captured.err == ""
    assert sh.running is False

    piped = Shell(kernel, root)
    piped.handle_line("exit | cat")
    captured = capsys.readouterr()

    # exit runs in-process inside the pipeline stage: no output, shell stops.
    assert captured.out == ""
    assert captured.err == ""
    assert piped.running is False
    assert piped.exit_code == 0


# -- G. background isolation baseline ---------------------------------------


def test_current_background_job_does_not_have_trap_builtin(kernel, root,
                                                           capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo hi' INT &")
    captured = capsys.readouterr()
    job = sh.jobs[0]

    assert captured.err == ""
    assert captured.out == f"[{job['id']}] {job['pid']}\n"

    sh.handle_line(f"wait {job['pid']}")
    captured = capsys.readouterr()

    # The background worker uses the same Shell and hits the same missing
    # command. TRAP-5 target: an explicit background restriction instead of
    # command-not-found.
    assert captured.err == "trap: command not found\n"
    assert f"Done(127) {job['cmdline']}" in captured.out
    assert sh.last_code == 127
    assert job["proc"].exit_code == 127
    assert job["proc"].is_alive is False

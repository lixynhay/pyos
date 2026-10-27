"""TRAP-1/TRAP-2: signal model characterization and the EXIT-only trap.

TRAP-1 tests pin down behavior that existed before ``trap``. TRAP-2 inverts
the four trap/exit expectations and adds the EXIT trap contract: storage,
validation, printing, background guard and one-shot execution.

Real signals are still not supported: ``trap`` rejects them explicitly. Signal
delivery, ``kill`` and the process model are intentionally untouched.

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


# -- A. trap builtin basics --------------------------------------------------


def test_fixed_trap_builtin_bare_prints_nothing(kernel, root, capsys):
    sh = Shell(kernel, root)
    assert "trap" in sh.builtins
    # trap is a shell builtin, not a registry command.
    assert get_command("trap") is None

    sh.handle_line("trap")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0
    assert sh.exit_code == 0
    assert sh.running is True


def test_fixed_trap_signal_reports_unsupported(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo hi' INT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: signal is not supported yet: INT\n"
    assert sh.last_code == 2
    assert sh.exit_code == 2
    assert sh._exit_trap is None


def test_fixed_trap_exit_sets_exit_trap(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo bye' EXIT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0
    assert sh._exit_trap == "echo bye"


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


# -- F. EXIT trap storage, printing and validation ---------------------------


def test_trap_exit_set_and_print(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo bye' EXIT")
    assert capsys.readouterr().err == ""
    assert sh._exit_trap == "echo bye"

    sh.handle_line("trap")
    captured = capsys.readouterr()

    assert captured.out == "trap -- 'echo bye' EXIT\n"
    assert captured.err == ""
    assert sh.last_code == 0


def test_trap_bare_prints_nothing_when_no_exit_trap(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0


def test_trap_exit_reset(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo bye' EXIT")
    capsys.readouterr()

    sh.handle_line("trap - EXIT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0
    assert sh._exit_trap is None

    sh.handle_line("trap")
    assert capsys.readouterr().out == ""


def test_trap_escapes_single_quote_in_exit_handler(kernel, root, capsys):
    sh = Shell(kernel, root)

    # Shell-level handler value: it's
    sh.handle_line("trap 'it'\\''s' EXIT")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert sh._exit_trap == "it's"

    sh.handle_line("trap")
    captured = capsys.readouterr()
    assert captured.out == "trap -- 'it'\\''s' EXIT\n"


def test_trap_double_quoted_handler_expanded_at_install(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line('X=hi; trap "echo $X" EXIT')
    captured = capsys.readouterr()
    assert captured.err == ""
    # Double quotes expand at install time, so the handler is already "echo hi".
    assert sh._exit_trap == "echo hi"


def test_trap_duplicate_exit_signals_allowed(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo once' EXIT EXIT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.last_code == 0
    assert sh._exit_trap == "echo once"


def test_trap_invalid_signal_does_not_set_exit_trap(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo hi' NOPE")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: invalid signal specification: NOPE\n"
    assert sh.last_code == 2
    assert sh._exit_trap is None


def test_trap_mixed_exit_and_int_is_atomic_failure(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo keep' EXIT")
    capsys.readouterr()

    sh.handle_line("trap 'echo new' EXIT INT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: signal is not supported yet: INT\n"
    assert sh.last_code == 2
    # Atomic: EXIT was not overwritten by the failing command.
    assert sh._exit_trap == "echo keep"

    sh.handle_line("trap - EXIT INT")
    captured = capsys.readouterr()

    assert captured.err == "trap: signal is not supported yet: INT\n"
    assert sh.last_code == 2
    assert sh._exit_trap == "echo keep"


def test_trap_ignore_exit_rejected(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap '' EXIT")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: cannot ignore EXIT\n"
    assert sh.last_code == 2
    assert sh.exit_code == 2
    assert sh._exit_trap is None


def test_trap_missing_signal_rejected(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo hi'")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: EXIT expected\n"
    assert sh.last_code == 2
    assert sh._exit_trap is None


def test_trap_reset_missing_signal_rejected(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo keep' EXIT")
    capsys.readouterr()

    sh.handle_line("trap -")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: EXIT expected\n"
    assert sh.last_code == 2
    assert sh._exit_trap == "echo keep"


# -- G. real signals are explicitly unsupported ------------------------------


def _assert_trap_signal_unsupported(kernel, root, capsys, spec, canonical):
    sh = Shell(kernel, root)

    sh.handle_line(f"trap 'echo hi' {spec}")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        f"trap: signal is not supported yet: {canonical}\n")
    assert sh.last_code == 2
    assert sh.exit_code == 2
    assert sh._exit_trap is None


def test_trap_int_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "INT", "INT")


def test_trap_term_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "TERM", "TERM")


def test_trap_hup_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "HUP", "HUP")


def test_trap_kill_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "KILL", "KILL")


def test_trap_stop_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "STOP", "STOP")


def test_trap_chld_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "CHLD", "CHLD")


def test_trap_numeric_signal_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "15", "TERM")


def test_trap_sig_prefix_unsupported_yet(kernel, root, capsys):
    _assert_trap_signal_unsupported(kernel, root, capsys, "SIGINT", "INT")


def test_trap_unknown_signal_invalid(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo hi' NOPE")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "trap: invalid signal specification: NOPE\n"
    assert sh.last_code == 2
    assert sh._exit_trap is None


# -- H. background guard -----------------------------------------------------


def test_trap_in_background_job_is_rejected(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("trap 'echo bg' EXIT &")
    captured = capsys.readouterr()
    job = sh.jobs[0]
    assert captured.err == ""
    assert captured.out == f"[{job['id']}] {job['pid']}\n"

    sh.handle_line(f"wait {job['pid']}")
    captured = capsys.readouterr()

    assert captured.err == "trap: not supported in background job\n"
    assert "Done(2)" in captured.out
    assert job["proc"].exit_code == 2
    assert sh._exit_trap is None


def test_trap_in_background_does_not_modify_parent_exit_trap(kernel, root,
                                                             capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo parent' EXIT")
    capsys.readouterr()

    sh.handle_line("trap 'echo bg' EXIT &")
    capsys.readouterr()
    job = sh.jobs[0]
    sh.handle_line(f"wait {job['pid']}")
    captured = capsys.readouterr()

    assert captured.err == "trap: not supported in background job\n"
    assert sh._exit_trap == "echo parent"


# -- I. EXIT trap execution --------------------------------------------------


def test_exit_trap_runs_on_exit(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo bye' EXIT")
    capsys.readouterr()

    sh.handle_line("exit")
    captured = capsys.readouterr()

    assert captured.out == "bye\n"
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 0
    assert sh._exit_trap_done is True


def test_exit_trap_runs_once(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo first; trap \"echo second\" EXIT' EXIT")
    capsys.readouterr()

    sh.handle_line("exit")
    captured = capsys.readouterr()

    assert captured.out == "first\n"
    assert captured.err == ""
    assert sh.exit_code == 0


def test_exit_trap_sees_exit_code_from_exit_argument(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo code-$?' EXIT")
    capsys.readouterr()

    sh.handle_line("exit 7")
    captured = capsys.readouterr()

    assert captured.out == "code-7\n"
    assert sh.exit_code == 7


def test_exit_trap_sees_last_code_on_bare_exit(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo code-$?' EXIT")
    capsys.readouterr()

    sh.handle_line("false")
    capsys.readouterr()
    sh.handle_line("exit")
    captured = capsys.readouterr()

    assert captured.out == "code-1\n"
    assert sh.exit_code == 1


def test_exit_trap_can_change_final_code_via_exit(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'exit 9' EXIT")
    capsys.readouterr()

    sh.handle_line("exit 3")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 9


def test_exit_trap_reset_prevents_execution(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo bye' EXIT")
    capsys.readouterr()
    sh.handle_line("trap - EXIT")
    capsys.readouterr()

    sh.handle_line("exit")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.exit_code == 0


def test_exit_trap_runs_on_eof_if_testable(kernel, root, capsys, monkeypatch):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo bye' EXIT")
    capsys.readouterr()

    def eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    code = sh.run()
    captured = capsys.readouterr()

    assert code == 0
    assert "bye\n" in captured.out
    assert captured.err == ""
    assert sh._exit_trap_done is True


def test_exit_trap_handler_does_not_enter_history(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo bye' EXIT")
    capsys.readouterr()

    sh.handle_line("exit")
    capsys.readouterr()

    assert sh.history == ["trap 'echo bye' EXIT", "exit"]
    assert "echo bye" not in sh.history


def test_exit_trap_handler_background_ampersand_rejected_or_safe(
        kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("trap 'echo hi &' EXIT")
    capsys.readouterr()

    sh.handle_line("exit")
    captured = capsys.readouterr()

    assert "hi" not in captured.out
    assert "background jobs are not allowed" in captured.err
    assert sh.jobs == []
    assert sh.exit_code == 0


# -- J. exit stops the rest of the line --------------------------------------


def test_exit_stops_remaining_semicolon_segments(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("exit; echo after")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 0


def test_exit_with_code_stops_remaining_semicolon_segments(kernel, root,
                                                           capsys):
    sh = Shell(kernel, root)

    sh.handle_line("exit 3; echo after")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 3


def test_exit_after_and_operator_stops_rest(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("true && exit; echo after")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 0


def test_exit_after_or_operator_stops_rest(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false || exit; echo after")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert sh.running is False
    # bare exit keeps $? from the preceding false.
    assert sh.exit_code == 1


def test_exit_in_first_job_stops_remaining_jobs(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("echo one; exit; echo after")
    captured = capsys.readouterr()

    assert captured.out == "one\n"
    assert captured.err == ""
    assert sh.running is False
    assert sh.exit_code == 0


# -- K. help/usage -----------------------------------------------------------


def test_help_mentions_trap(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("help")
    out = capsys.readouterr().out
    assert "trap" in out

    sh.handle_line("help trap")
    captured = capsys.readouterr()
    assert "SYNOPSIS" in captured.out
    assert "trap [action] EXIT" in captured.out


def test_trap_usage_does_not_promise_real_signals(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("help trap")
    out = capsys.readouterr().out

    assert "EXIT" in out
    for name in ("INT", "TERM", "HUP", "USR1"):
        assert name not in out

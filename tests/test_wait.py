"""Stage 2: wait/waitpid, deterministic background status, jobs/fg.

Positive tests for the new shell-level job status model. Source of truth is
job["proc"].exit_code; a job is finished when job["done"] is true or the
process is no longer alive. No kernel zombies are involved.
"""

from pyos.shell import Shell


# ---------------------------------------------------------------------------
# wait / waitpid
# ---------------------------------------------------------------------------

def test_wait_no_args_waits_all_active_jobs_and_returns_last_code(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("true &")
    sh.handle_line("false &")
    capsys.readouterr()
    first, second = sh.jobs
    assert first["done"] is False
    assert second["done"] is False

    sh.handle_line("wait")
    capsys.readouterr()

    assert first["done"] is True
    assert second["done"] is True
    assert first["proc"].exit_code == 0
    assert second["proc"].exit_code == 1
    assert sh.last_code == 1
    assert len(kernel.processes) == 1


def test_wait_no_args_returns_zero_when_no_active_jobs(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("wait")
    capsys.readouterr()
    assert sh.last_code == 0
    assert sh.exit_code == 0
    assert sh.env["?"] == "0"

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]
    kernel.scheduler.drain()
    capsys.readouterr()
    assert job["done"] is True

    sh.handle_line("wait")
    capsys.readouterr()
    assert sh.last_code == 0
    assert sh.env["?"] == "0"


def test_wait_pid_waits_specific_job_returns_code(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line(f"wait {job['pid']}")
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 1
    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert len(kernel.processes) == 1


def test_wait_percent_job_waits_specific_job_returns_code(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line(f"wait %{job['id']}")
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 1
    assert sh.last_code == 1


def test_wait_already_completed_job_returns_code_without_advancing_scheduler(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]
    kernel.scheduler.drain()
    capsys.readouterr()
    assert job["done"] is True

    clock_before = kernel.scheduler.clock
    sh.handle_line(f"wait {job['pid']}")
    capsys.readouterr()

    assert kernel.scheduler.clock == clock_before
    assert sh.last_code == 1
    assert job["proc"].exit_code == 1


def test_wait_sets_last_code_exit_code_and_question_mark(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()

    sh.handle_line("wait")
    capsys.readouterr()

    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert sh.env["?"] == "1"

    sh.handle_line("echo $?")
    assert capsys.readouterr().out.strip() == "1"


# ---------------------------------------------------------------------------
# fg
# ---------------------------------------------------------------------------

def test_fg_returns_job_exit_code_not_zero(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line("fg")
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 1
    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert sh.env["?"] == "1"
    assert len(kernel.processes) == 1


def test_fg_waits_until_done_not_blind_run_until(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("sleep 1 &")
    capsys.readouterr()
    job = sh.jobs[0]
    clock_before = kernel.scheduler.clock
    assert job["done"] is False

    sh.handle_line("fg")
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 0
    assert kernel.scheduler.clock > clock_before
    assert sh.last_code == 0


# ---------------------------------------------------------------------------
# bg
# ---------------------------------------------------------------------------

def test_bg_without_jobs_writes_stderr_and_returns_one(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("bg")
    captured = capsys.readouterr()

    assert "bg: no current job" in captured.err
    assert sh.last_code == 1


def test_bg_without_jobs_stdout_empty(kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("bg")
    captured = capsys.readouterr()

    assert captured.out == ""


# ---------------------------------------------------------------------------
# jobs output
# ---------------------------------------------------------------------------

def test_jobs_shows_terminated_with_code_for_killed_job(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("sleep 100 &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line(f"kill {job['pid']}")
    capsys.readouterr()
    assert job["done"] is False
    assert job["proc"].is_alive is False
    assert job["proc"].exit_code == 143

    sh.handle_line("jobs")
    out = capsys.readouterr().out

    assert "Running" not in out
    assert "Done(143)" in out


def test_jobs_shows_done_with_code_for_normal_background_completion(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    kernel.scheduler.drain()
    capsys.readouterr()
    assert job["done"] is True
    assert job["proc"].exit_code == 1

    sh.handle_line("jobs")
    out = capsys.readouterr().out

    assert "Running" not in out
    assert "Done(1)" in out


# ---------------------------------------------------------------------------
# worker isolation / foreground exit status
# ---------------------------------------------------------------------------

def test_worker_does_not_overwrite_global_last_code_after_drain(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line("true")
    capsys.readouterr()
    assert sh.last_code == 0

    kernel.scheduler.drain()
    capsys.readouterr()

    assert sh.last_code == 0
    assert sh.exit_code == 0
    assert job["proc"].exit_code == 1


def test_worker_still_sets_proc_exit_code_and_job_done_after_drain(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]
    pid = job["pid"]
    assert job["done"] is False

    kernel.scheduler.drain()
    capsys.readouterr()

    assert job["done"] is True
    assert job["proc"].exit_code == 1
    assert kernel.get_process(pid) is None
    assert len(kernel.processes) == 1
    assert sh.last_code == 0


def test_run_single_writes_actual_code_to_proc_exit_code_for_foreground(
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
    sh.handle_line("true")
    capsys.readouterr()

    assert [(p.name, p.exit_code) for p in captured] == [
        ("false", 1), ("true", 0)]
    assert sh.last_code == 0


def test_foreground_last_code_still_set_by_execute_pipeline(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false")
    capsys.readouterr()
    assert sh.last_code == 1
    assert sh.exit_code == 1

    sh.handle_line("echo $?")
    assert capsys.readouterr().out.strip() == "1"


# ---------------------------------------------------------------------------
# waitpid / deterministic $?
# ---------------------------------------------------------------------------

def test_waitpid_alias_or_variant_behaves_like_wait_pid(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    sh.handle_line(f"waitpid {job['pid']}")
    capsys.readouterr()

    assert job["done"] is True
    assert sh.last_code == 1
    assert sh.exit_code == 1
    assert sh.env["?"] == "1"


def test_background_then_wait_then_question_mark_is_deterministic(
        kernel, root, capsys):
    sh = Shell(kernel, root)

    sh.handle_line("false &")
    capsys.readouterr()
    job = sh.jobs[0]

    # Foreground traffic between launch and wait must not leak into $? after
    # the explicit wait.
    sh.handle_line("true")
    capsys.readouterr()
    assert sh.last_code == 0

    sh.handle_line("wait")
    capsys.readouterr()
    assert sh.last_code == 1
    assert sh.env["?"] == "1"

    sh.handle_line("echo $?")
    assert capsys.readouterr().out.strip() == "1"
    assert job["proc"].exit_code == 1

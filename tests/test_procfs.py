"""Тесты виртуальной ФС /proc и буфера сообщений ядра (dmesg)."""

from pyos.shell import Shell


# --- /proc/meminfo, /proc/uptime и т.п. ------------------------------------


def test_proc_meminfo(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /proc/meminfo")
    out = capsys.readouterr().out
    assert "MemTotal" in out
    assert "MemFree" in out


def test_proc_uptime(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /proc/uptime")
    out = capsys.readouterr().out.strip()
    parts = out.split()
    assert len(parts) == 2
    assert float(parts[0]) >= 0


def test_proc_cpuinfo(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /proc/cpuinfo")
    out = capsys.readouterr().out
    assert "processor" in out


def test_proc_version(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /proc/version")
    out = capsys.readouterr().out
    assert "pyOS" in out


# --- каталоги pid ----------------------------------------------------------


def test_proc_pid_status(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /proc/1/status")
    out = capsys.readouterr().out
    assert "Pid:\t1" in out
    assert "init" in out
    assert "State:" in out


def test_proc_self_status(kernel, root, capsys):
    Shell(kernel, root).handle_line("cat /proc/self/status")
    out = capsys.readouterr().out
    assert "Pid:" in out


def test_proc_ls_shows_pids(kernel, root, capsys):
    Shell(kernel, root).handle_line("ls /proc")
    names = capsys.readouterr().out.split()
    assert "1" in names          # init
    assert "self" in names
    assert "meminfo" in names


def test_proc_refresh_drops_dead_pids(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("sleep 100 &")
    pid = str(sh.jobs[-1]["pid"])
    # процесс жив — виден в /proc
    sh.handle_line("ls /proc")
    assert pid in capsys.readouterr().out.split()
    # убиваем — исчезает из /proc
    sh.handle_line(f"kill {pid}")
    sh.handle_line("ls /proc")
    assert pid not in capsys.readouterr().out.split()


# --- dmesg ------------------------------------------------------------------


def test_dmesg_boot_message(kernel, root, capsys):
    Shell(kernel, root).handle_line("dmesg")
    assert "booted" in capsys.readouterr().out


def test_dmesg_logs_kill(kernel, root, capsys):
    sh = Shell(kernel, root)
    sh.handle_line("sleep 100 &")
    pid = str(sh.jobs[-1]["pid"])
    sh.handle_line(f"kill {pid}")
    sh.handle_line("dmesg")
    assert f"pid {pid}" in capsys.readouterr().out


def test_dmesg_registered():
    from pyos.commands import get
    assert get("dmesg") is not None
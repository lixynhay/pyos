"""Ядро pyOS — связывает файловую систему, пользователей и планировщик.

Ядро предоставляет примитивы, похожие на системные вызовы: создание и
завершение процессов, работу со временем, доступ к текущему пользователю.
Также наполняет виртуальные ФС /proc и /dev.
"""

import random
from collections import deque

from . import KERNEL_NAME, KERNEL_RELEASE, __version__
from .errors import NoSpaceLeft, NoSuchFileOrDirectory
from .fs import Dir, FileSystem, VirtualFile
from .process import Process, ProcessState
from .scheduler import Scheduler, TICKS_PER_SECOND
from .users import UserManager


class Kernel:
    def __init__(self, hostname="pyos"):
        self.name = KERNEL_NAME
        self.release = KERNEL_RELEASE
        self.version = __version__
        self.hostname = hostname

        self.fs = FileSystem()
        self.users = UserManager()
        self.scheduler = Scheduler(self)

        self.processes: dict[int, Process] = {}
        self._next_pid = 1

        # кольцевой буфер сообщений ядра (команда dmesg)
        self.log_buffer = deque(maxlen=200)
        # «текущий» процесс (для /proc/self); выставляется шеллом
        self.current_proc: Process | None = None

        # PID 1 — "init"/ядро, всегда присутствует
        self.init = self.create_process(
            name="init", uid=0, gid=0, cwd="/", cmdline="/sbin/init",
            is_kernel=True, remaining=0,
        )
        self.init.state = ProcessState.RUNNING

        self.log(f"{self.name} {self.release} booted (hostname={hostname})")
        self._setup_dev()
        self._setup_proc()

    # -- идентификаторы ------------------------------------------------------
    def allocate_pid(self) -> int:
        pid = self._next_pid
        self._next_pid += 1
        return pid

    # -- процессы ------------------------------------------------------------
    def create_process(self, name, uid=0, gid=0, ppid=0, cwd="/", cmdline="",
                       args=None, env=None, nice=0, remaining=0,
                       is_kernel=False) -> Process:
        pid = self.allocate_pid()
        proc = Process(
            pid=pid, name=name, uid=uid, gid=gid, ppid=ppid, cwd=cwd,
            cmdline=cmdline or name, args=args, env=env, nice=nice,
            remaining=remaining, is_kernel=is_kernel,
        )
        proc.start_time = self.scheduler.clock
        self.processes[pid] = proc
        return proc

    def on_process_exit(self, proc: Process):
        """Хук планировщика: процесс завершился."""
        proc.state = ProcessState.TERMINATED
        if proc.worker is not None:
            # выполнить отложенную работу фонового задания
            try:
                proc.worker(proc)
            except Exception as exc:  # noqa: BLE001 - хотим устойчивость ядра
                proc.stderr += f"{exc}\n"
                proc.exit_code = 1
            finally:
                proc.worker = None
        self.reap(proc)

    def reap(self, proc: Process):
        """Убирает завершённый процесс из таблицы (init остаётся всегда)."""
        if not proc.is_kernel:
            self.processes.pop(proc.pid, None)

    def live_processes(self) -> list[Process]:
        return [p for p in self.processes.values() if p.is_alive]

    def get_process(self, pid: int) -> Process | None:
        return self.processes.get(pid)

    def kill(self, pid: int, signum: int = 15) -> bool:
        proc = self.processes.get(pid)
        if proc is None or not proc.is_alive:
            return False
        if proc.is_kernel:
            return False
        self.log(f"signal {signum} sent to pid {pid} ({proc.name})")
        self.scheduler.remove(proc)
        proc.exit_code = 128 + signum
        proc.state = ProcessState.TERMINATED
        self.reap(proc)
        return True

    # -- время ---------------------------------------------------------------
    @property
    def clock(self) -> int:
        return self.scheduler.clock

    @property
    def uptime_seconds(self) -> float:
        return self.scheduler.clock / TICKS_PER_SECOND

    def sleep_ticks(self, ticks: int):
        """Продвигает время вперёд (используется командой sleep)."""
        self.scheduler.tick(ticks)

    def idle(self, ticks: int = 1):
        """Даёт планировщику поработать, пока шелл ждёт ввод."""
        self.scheduler.tick(ticks)

    # -- информация о системе ------------------------------------------------
    def uname(self) -> str:
        return f"{self.name} {self.hostname} {self.release} #1 pyOS {self.version}"

    # -- dmesg ---------------------------------------------------------------
    def log(self, message: str):
        """Пишет сообщение в кольцевой буфер ядра (команда dmesg)."""
        self.log_buffer.append(f"[{self.scheduler.clock:>6}] {message}")

    # -- /proc и /dev --------------------------------------------------------
    def reset_virtual_fs(self):
        """Пересоздаёт /dev и /proc как виртуальные подсистемы.

        Используется после ``load``: виртуальные узлы не сериализуются,
        поэтому их нужно восстановить поверх загруженного дерева ФС.
        """
        fs = self.fs
        for path in ("/dev", "/proc"):
            try:
                fs.lookup(path, None, "/", "/")
            except NoSuchFileOrDirectory:
                pass
            else:
                fs.unlink(path, None, "/", "/", recursive=True)
            fs.mkdir(path, None, "/", "/")
        self._setup_dev()
        self._setup_proc()

    def _tree_size(self, node) -> int:
        if node.is_file():
            return node.size
        if node.is_dir():
            return sum(self._tree_size(c) for c in node.children.values())
        return 0

    def _setup_dev(self):
        """Наполняет /dev виртуальными устройствами."""
        fs = self.fs
        fs.create_virtual_file("/dev/null", lambda: "", mode=0o666)
        fs.create_virtual_file("/dev/zero", lambda: "0" * 4096, mode=0o666)
        rng = random.Random()
        fs.create_virtual_file(
            "/dev/random",
            lambda: "".join(chr(rng.randrange(32, 127)) for _ in range(16)),
            mode=0o666,
        )

        def full_write(value):
            raise NoSpaceLeft("/dev/full")

        fs.create_virtual_file("/dev/full", lambda: "0" * 4096,
                               write_sink=full_write, mode=0o666)

    def _proc_meminfo(self) -> str:
        total = 256 * 1024  # условные 256 МБ, как в free
        used = len(self.processes) * 1024 + self._tree_size(self.fs.root)
        free = max(0, total - used)
        return (f"MemTotal:       {total:>8} kB\n"
                f"MemFree:        {free:>8} kB\n"
                f"MemAvailable:   {free:>8} kB\n")

    def _proc_uptime(self) -> str:
        up = self.uptime_seconds
        busy = sum(p.cpu_ticks for p in self.processes.values()) / TICKS_PER_SECOND
        idle = max(0.0, up - busy)
        return f"{up:.2f} {idle:.2f}\n"

    def _proc_cpuinfo(self) -> str:
        return ("processor\t: 0\n"
                "vendor_id\t: pyOS Virtual\n"
                "model name\t: pyOS Educational CPU\n"
                "cpu MHz\t\t: 2400.00\n"
                "cache size\t: 64 KB\n")

    def _proc_version(self) -> str:
        return self.uname() + "\n"

    def _proc_status(self, proc: Process) -> str:
        state_names = {
            ProcessState.NEW: "new",
            ProcessState.READY: "running",
            ProcessState.RUNNING: "running",
            ProcessState.BLOCKED: "sleeping",
            ProcessState.ZOMBIE: "zombie",
            ProcessState.TERMINATED: "terminated",
        }
        user = self.users.get_by_uid(proc.uid)
        owner = user.name if user else str(proc.uid)
        return "\n".join([
            f"Name:\t{proc.name}",
            f"Pid:\t{proc.pid}",
            f"PPid:\t{proc.ppid}",
            f"State:\t{proc.state_code} "
            f"({state_names.get(proc.state, '?')})",
            f"Uid:\t{proc.uid}\t({owner})",
            f"Gid:\t{proc.gid}",
            f"Nice:\t{proc.nice}",
            f"VmSize:\t{4096 + proc.cpu_ticks * 8} kB",
            f"Cmdline:\t{proc.cmdline or proc.name}",
        ]) + "\n"

    def _setup_proc(self):
        """Наполняет /proc виртуальными файлами и живыми каталогами pid."""
        proc = self.fs.root.children["proc"]
        proc.mode = 0o555
        self.fs.create_virtual_file("/proc/meminfo", self._proc_meminfo)
        self.fs.create_virtual_file("/proc/uptime", self._proc_uptime)
        self.fs.create_virtual_file("/proc/cpuinfo", self._proc_cpuinfo)
        self.fs.create_virtual_file("/proc/version", self._proc_version)

        # /proc/self — каталог статуса «текущего» процесса
        self_dir = Dir("self", uid=0, gid=0, mode=0o555)
        self_dir.parent = proc
        proc.children["self"] = self_dir
        self.fs._new_inode(self_dir)
        self_dir_status = VirtualFile(
            "status",
            read_provider=lambda: self._proc_status(
                self.current_proc or self.init),
            uid=0, gid=0, mode=0o444,
        )
        self_dir_status.parent = self_dir
        self_dir.children["status"] = self_dir_status
        self.fs._new_inode(self_dir_status)

        def refresh():
            # пересобираем каталоги pid по живой таблице процессов
            for name in [n for n in proc.children if n.isdigit()]:
                del proc.children[name]
            for p in sorted(self.processes.values(), key=lambda x: x.pid):
                if not p.is_alive:
                    continue
                d = Dir(str(p.pid), uid=0, gid=0, mode=0o555)
                d.parent = proc
                proc.children[str(p.pid)] = d
                self.fs._new_inode(d)
                st = VirtualFile(
                    "status",
                    read_provider=lambda p=p: self._proc_status(p),
                    uid=0, gid=0, mode=0o444,
                )
                st.parent = d
                d.children["status"] = st
                self.fs._new_inode(st)

        proc.refresh = refresh
        refresh()

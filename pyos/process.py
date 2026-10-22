"""Модель процесса и его состояний.

Процессы симулируются: у каждого есть PID, владелец, состояние, приоритет
(nice) и счётчики CPU. Реальная многопоточность не используется — планировщик
"прогоняет" процессы по тикам.
"""

from enum import Enum


class ProcessState(Enum):
    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    ZOMBIE = "ZOMBIE"
    TERMINATED = "TERMINATED"

    def __str__(self):
        return self.value


# короткие коды состояний, как в ps
STATE_CODE = {
    ProcessState.NEW: "N",
    ProcessState.READY: "R",
    ProcessState.RUNNING: "R",
    ProcessState.BLOCKED: "S",
    ProcessState.ZOMBIE: "Z",
    ProcessState.TERMINATED: "T",
}


class Process:
    def __init__(self, pid, name, uid=0, gid=0, ppid=0, cwd="/",
                 cmdline="", args=None, env=None, nice=0, remaining=0,
                 is_kernel=False):
        self.pid = pid
        self.ppid = ppid
        self.name = name
        self.uid = uid
        self.gid = gid
        self.cwd = cwd
        self.cmdline = cmdline or name
        self.args = list(args or [])
        self.env = dict(env or {})
        self.nice = nice
        self.is_kernel = is_kernel

        self.state = ProcessState.NEW
        self.remaining = remaining          # сколько тиков CPU ещё нужно
        self.quantum_used = 0
        self.cpu_ticks = 0
        self.total_ticks = 0
        self.vruntime = 0.0                 # виртуальное время для планировщика
        self.start_time = None
        self.exit_code = 0

        # блокировка (например, sleep)
        self.wake_tick = None
        self.block_reason = ""

        # отложенное задание для фоновых процессов
        self.worker = None

        # поток ошибок процесса
        self.stderr = ""

    @property
    def state_code(self):
        return STATE_CODE.get(self.state, "?")

    @property
    def is_alive(self) -> bool:
        return self.state not in (ProcessState.TERMINATED, ProcessState.ZOMBIE)

    def __repr__(self):
        return f"<Process pid={self.pid} name={self.name!r} state={self.state}>"

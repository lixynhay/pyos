"""Round-robin планировщик.

Модель тиков: каждая единица ``tick`` — условный квант времени. Процессы в
состоянии READY стоят в очереди; планировщик выдаёт каждому квант ``quantum``
тиков, после чего процесс возвращается в конец очереди (preemption).
Заблокированные процессы (например, ``sleep``) просыпаются по ``wake_tick``.
"""

from collections import deque

from .process import Process, ProcessState

# сколько тиков приходится на одну "секунду" симулированного времени
TICKS_PER_SECOND = 10


class Scheduler:
    def __init__(self, kernel, quantum=5):
        self.kernel = kernel
        self.quantum = quantum
        self.ready: deque[Process] = deque()
        self.blocked: list[Process] = []
        self.clock = 0            # тиков прошло с момента загрузки
        self._current: Process | None = None
        self.switches = 0

    # -- очередь -------------------------------------------------------------
    @staticmethod
    def weight(proc: Process) -> float:
        """Вес процесса: чем меньше nice, тем выше приоритет."""
        return 2.0 ** (-proc.nice / 4.0)

    def _min_vruntime(self) -> float:
        candidates = list(self.ready)
        if self._current is not None:
            candidates.append(self._current)
        if not candidates:
            return 0.0
        return min(p.vruntime for p in candidates)

    def add(self, proc: Process):
        proc.state = ProcessState.READY
        proc.quantum_used = 0
        proc.vruntime = self._min_vruntime()
        if proc not in self.ready:
            self.ready.append(proc)

    def block(self, proc: Process, ticks: int, reason=""):
        proc.state = ProcessState.BLOCKED
        proc.wake_tick = self.clock + max(1, ticks)
        proc.block_reason = reason
        if proc not in self.blocked:
            self.blocked.append(proc)
        if self._current is proc:
            self._current = None

    def remove(self, proc: Process):
        if proc in self.ready:
            self.ready.remove(proc)
        if proc in self.blocked:
            self.blocked.remove(proc)
        if self._current is proc:
            self._current = None

    # -- планирование --------------------------------------------------------
    def _finish(self, proc: Process):
        proc.state = ProcessState.TERMINATED
        if proc.wake_tick is not None:
            proc.wake_tick = None
        self.kernel.on_process_exit(proc)

    def _pick_next(self):
        if not self.ready:
            self._current = None
            return None
        nxt = min(self.ready, key=lambda p: (p.vruntime, p.pid))
        self.ready.remove(nxt)
        nxt.state = ProcessState.RUNNING
        nxt.quantum_used = 0
        self._current = nxt
        self.switches += 1
        return nxt

    def _wake_blocked(self):
        still = []
        for p in self.blocked:
            if p.wake_tick is not None and self.clock >= p.wake_tick:
                p.wake_tick = None
                p.block_reason = ""
                p.state = ProcessState.READY
                p.vruntime = self._min_vruntime()
                self.ready.append(p)
            else:
                still.append(p)
        self.blocked = still

    def tick(self, n=1):
        """Продвигает симулированное время на ``n`` тиков."""
        for _ in range(n):
            self.clock += 1
            self._wake_blocked()

            proc = self._current
            if proc is not None and (proc.state != ProcessState.RUNNING
                                     or proc.quantum_used >= self.quantum):
                if proc.state == ProcessState.RUNNING:
                    proc.state = ProcessState.READY
                    self.ready.append(proc)
                proc = None
                self._current = None

            if proc is None:
                proc = self._pick_next()
            if proc is None:
                continue

            proc.cpu_ticks += 1
            proc.total_ticks += 1
            proc.quantum_used += 1
            proc.vruntime += 1.0 / self.weight(proc)
            if proc.remaining > 0:
                proc.remaining -= 1

            if proc.remaining <= 0:
                self._finish(proc)
                self._current = None

    def run_until(self, predicate, limit=100000):
        """Крутит тики, пока условие не станет истинным (или не выйдет лимит)."""
        steps = 0
        while not predicate() and steps < limit:
            self.tick(1)
            steps += 1
        return steps

    def drain(self, limit=100000):
        """Выполняет все готовые процессы до опустошения очереди."""
        steps = 0
        while (self.ready or self.blocked or self._current) and steps < limit:
            self.tick(1)
            steps += 1
        return steps

    @property
    def current(self):
        return self._current

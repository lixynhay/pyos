"""Тесты round-robin планировщика и приоритетов."""

from pyos.process import ProcessState
from pyos.scheduler import TICKS_PER_SECOND


def test_round_robin_equal_nice(kernel):
    a = kernel.create_process(name="a", nice=0, remaining=10**9)
    b = kernel.create_process(name="b", nice=0, remaining=10**9)
    kernel.scheduler.add(a)
    kernel.scheduler.add(b)
    kernel.scheduler.tick(400)
    assert abs(a.cpu_ticks - b.cpu_ticks) <= 2


def test_nice_affects_cpu_share(kernel):
    hi = kernel.create_process(name="hi", nice=-5, remaining=10**9)
    lo = kernel.create_process(name="lo", nice=10, remaining=10**9)
    kernel.scheduler.add(hi)
    kernel.scheduler.add(lo)
    kernel.scheduler.tick(400)
    assert hi.cpu_ticks > lo.cpu_ticks * 2


def test_sleep_blocks_and_wakes(kernel):
    p = kernel.create_process(name="s", remaining=10**9)
    kernel.scheduler.block(p, ticks=5 * TICKS_PER_SECOND, reason="sleep")
    assert p.state is ProcessState.BLOCKED
    kernel.scheduler.tick(5 * TICKS_PER_SECOND)
    assert p.state is not ProcessState.BLOCKED
    assert p.wake_tick is None

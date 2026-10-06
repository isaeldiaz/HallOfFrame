"""Deterministic test doubles for the Phase 0 injection seams (step 0.4)."""
from __future__ import annotations


class _Scheduled:
    """Handle returned by :class:`FakeScheduler` (mirrors ``threading.Timer``)."""

    def __init__(self, when: float, callback):
        self.when = when
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeScheduler:
    """Callable scheduler: ``scheduler(delay_s, callback)`` returns a handle.

    ``advance(seconds)`` fires every callback whose due time has passed, in
    scheduled-time order, moving the virtual clock as it goes.
    """

    def __init__(self, start: float = 0.0):
        self._now = start
        self._items: list[_Scheduled] = []

    def __call__(self, delay_s: float, callback) -> _Scheduled:
        item = _Scheduled(self._now + delay_s, callback)
        self._items.append(item)
        return item

    def advance(self, seconds: float) -> None:
        target = self._now + seconds
        while True:
            due = [i for i in self._items if not i.cancelled and i.when <= target]
            if not due:
                break
            item = min(due, key=lambda i: i.when)
            self._items.remove(item)
            if item.when > self._now:
                self._now = item.when
            item.callback()
        self._now = target


class FakeClock:
    """Callable monotonic clock: ``clock()`` reads now, ``advance`` moves it."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now

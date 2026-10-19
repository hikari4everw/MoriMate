"""One generation/TTS slot; foreground owner, guests, then background work."""

import asyncio
import itertools
from contextlib import asynccontextmanager


class GenerationScheduler:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.sequence = itertools.count()
        self.waiters = []
        self.active = None

    @asynccontextmanager
    async def slot(self, priority):
        ticket = (priority, next(self.sequence), asyncio.current_task())
        async with self.condition:
            self.waiters.append(ticket)
            if priority < 2 and self.active and self.active[0] == 2:
                self.active[2].cancel()
            try:
                await self.condition.wait_for(
                    lambda: self.active is None and min(self.waiters) == ticket
                )
            except BaseException:
                self.waiters.remove(ticket)
                self.condition.notify_all()
                raise
            self.waiters.remove(ticket)
            self.active = ticket
        try:
            yield
        finally:
            async with self.condition:
                self.active = None
                self.condition.notify_all()

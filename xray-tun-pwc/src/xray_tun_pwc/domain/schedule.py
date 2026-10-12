import random
from datetime import timedelta

from attrs import field, frozen
from pydantic import TypeAdapter

ISO_8601 = TypeAdapter(timedelta)


@frozen
class CheckSchedule:
    interval: timedelta
    jitter: timedelta
    rng: random.Random = field(factory=random.Random, eq=False)

    def next_delay(self) -> timedelta:
        return self.interval + self.jitter * self.rng.random()

    def interval_iso(self) -> str:
        return ISO_8601.dump_python(self.interval, mode="json")

    def jitter_iso(self) -> str:
        return ISO_8601.dump_python(self.jitter, mode="json")

    def __str__(self) -> str:
        return f"every {self.interval_iso()} + up to {self.jitter_iso()}"

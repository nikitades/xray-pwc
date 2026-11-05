import pytest
from attrs import define, field

from xray_pwc_probe.ports.visiting import Backoff, GaveUp, VisitFailed


@define
class RecordedSleep:
    slept: list[float] = field(factory=list)

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


@define
class FlakyAction:
    failures_before_success: int
    reason: str = "HTTP 503"
    calls: int = 0

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise VisitFailed(self.reason)


async def test_backoff_doubles_the_delay_until_success():
    sleep = RecordedSleep()
    backoff = Backoff(max_attempts=4, base_delay=2.0, sleep=sleep)
    assert await backoff.run(FlakyAction(failures_before_success=2)) == 3
    assert sleep.slept == [2.0, 4.0]


async def test_backoff_gives_up_with_the_first_line_of_the_last_error():
    backoff = Backoff(max_attempts=3, base_delay=1.0, sleep=RecordedSleep())
    always_failing = FlakyAction(failures_before_success=99, reason="HTTP 429\ncall log")
    with pytest.raises(GaveUp) as exc:
        await backoff.run(always_failing)
    assert (exc.value.attempts, exc.value.reason) == (3, "HTTP 429")
    assert always_failing.calls == 3

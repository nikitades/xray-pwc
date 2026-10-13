import random
from datetime import timedelta

from xray_pwc_probe.domain.schedule import CheckSchedule

FIVE_MINUTES, TWO_MINUTES = timedelta(minutes=5), timedelta(minutes=2)


def test_delay_is_the_interval_plus_up_to_the_jitter():
    schedule = CheckSchedule(FIVE_MINUTES, TWO_MINUTES, rng=random.Random(7))
    delays = [schedule.next_delay() for _ in range(200)]
    assert all(FIVE_MINUTES <= d <= FIVE_MINUTES + TWO_MINUTES for d in delays)
    assert len(set(delays)) > 100


def test_schedule_describes_itself_in_iso_8601():
    assert str(CheckSchedule(FIVE_MINUTES, TWO_MINUTES)) == "every PT5M + up to PT2M"

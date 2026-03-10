from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo


DENVER = ZoneInfo("America/Denver")
PICKUP_START = time(hour=15, minute=0)
PICKUP_END = time(hour=16, minute=0)


@dataclass(frozen=True)
class PickupWindow:
    starts_at: datetime
    ends_at: datetime

    @property
    def date_label(self) -> str:
        return f"{self.starts_at.strftime('%A, %B')} {self.starts_at.day}, {self.starts_at.year}"

    @property
    def time_label(self) -> str:
        return "3:00 PM - 4:00 PM"

    @property
    def timezone_label(self) -> str:
        return self.starts_at.tzname() or "MT"


def local_now() -> datetime:
    return datetime.now(DENVER)


def next_pickup_window(now: datetime | None = None) -> PickupWindow:
    current = now.astimezone(DENVER) if now else local_now()
    days_until_wednesday = (2 - current.weekday()) % 7
    pickup_date = current.date() + timedelta(days=days_until_wednesday)
    starts_at = datetime.combine(pickup_date, PICKUP_START, tzinfo=DENVER)
    ends_at = datetime.combine(pickup_date, PICKUP_END, tzinfo=DENVER)

    if current >= ends_at:
        pickup_date = pickup_date + timedelta(days=7)
        starts_at = datetime.combine(pickup_date, PICKUP_START, tzinfo=DENVER)
        ends_at = datetime.combine(pickup_date, PICKUP_END, tzinfo=DENVER)

    return PickupWindow(starts_at=starts_at, ends_at=ends_at)

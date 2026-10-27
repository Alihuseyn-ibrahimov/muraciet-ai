"""İş təqvimi: qanuni müddətlər iş günü, eskalasiyalar isə iş saatı ilə hesablanır.

Funksiyalar now() çağırmır, vaxtı parametr kimi alır, ona görə istənilən anı test etmək olur.
Datetime-lar vaxt zonası ilə olmalıdır və Bakı vaxtına çevrilir.
"""
import logging
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from app.yaml_config import SlaConfig, get_config

log = logging.getLogger(__name__)


class WorkCalendar:
    def __init__(self, sla: SlaConfig, holidays: Iterable[date], complete_years: Iterable[int] = ()):
        self.sla = sla
        self.tz = sla.tz
        self._holidays = frozenset(holidays)
        self._complete_years = frozenset(complete_years)
        self._workdays = sla.workday_indexes
        self._start = sla.work_hours.start
        self._end = sla.work_hours.end
        self._warned_years: set[int] = set()

    # ---------- köməkçilər ----------
    def _local(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            raise ValueError("datetime vaxt zonası ilə olmalıdır (naive datetime qəbul olunmur)")
        return moment.astimezone(self.tz)

    def _check_year(self, day: date) -> None:
        year = day.year
        if self._complete_years and year not in self._complete_years and year not in self._warned_years:
            self._warned_years.add(year)
            log.warning(
                "%s-ci il üçün bayram siyahısı təsdiqlənməyib (config/holidays.yaml): "
                "müddətlər bayramsız, yəni ehtiyatla erkən hesablanır",
                year,
            )

    def _window(self, day: date) -> tuple[datetime, datetime]:
        return datetime.combine(day, self._start, self.tz), datetime.combine(day, self._end, self.tz)

    # ---------- iş günləri ----------
    def is_workday(self, day: date) -> bool:
        self._check_year(day)
        return day.weekday() in self._workdays and day not in self._holidays

    def add_workdays(self, start: date, n: int, *, count_start: bool) -> date:
        """n-ci iş gününü qaytarır. count_start=True olanda start (iş günüdürsə) 1-ci gün sayılır."""
        if n < 1:
            raise ValueError("iş günlərinin sayı müsbət olmalıdır")
        current, remaining = start, n
        if count_start and self.is_workday(current):
            remaining -= 1
        while remaining:
            current += timedelta(days=1)
            if self.is_workday(current):
                remaining -= 1
        return current

    def legal_due_date(self, received_at: datetime, workdays: int | None = None) -> date:
        """Qanuni son tarix (m. 10.1, 10.5): standart olaraq 15 iş günü."""
        legal = self.sla.legal
        return self.add_workdays(
            self._local(received_at).date(), workdays or legal.review_workdays, count_start=legal.count_receipt_day
        )

    def urgent_deadline(self, received_at: datetime) -> datetime:
        """Təcili müraciət (m. 10.4): iş saatı yox, təqvim saatı."""
        return self._local(received_at) + timedelta(hours=self.sla.legal.urgent_hours)

    # ---------- iş saatları ----------
    def is_work_time(self, moment: datetime) -> bool:
        local = self._local(moment)
        return self.is_workday(local.date()) and self._start <= local.time() < self._end

    def work_hours_between(self, start: datetime, end: datetime) -> float:
        a, b = self._local(start), self._local(end)
        if b <= a:
            return 0.0
        seconds = 0.0
        day = a.date()
        while day <= b.date():
            if self.is_workday(day):
                window_start, window_end = self._window(day)
                overlap = (min(b, window_end) - max(a, window_start)).total_seconds()
                seconds += max(overlap, 0)
            day += timedelta(days=1)
        return seconds / 3600

    def add_work_hours(self, start: datetime, hours: float) -> datetime:
        """start-dan `hours` iş saatı sonrakı an. Məsələn, cümə 17:30 + 1 saat = bazar ertəsi 09:30."""
        if hours < 0:
            raise ValueError("saat mənfi ola bilməz")
        cursor = self._local(start)
        remaining = timedelta(hours=hours)
        while True:
            day = cursor.date()
            if self.is_workday(day):
                window_start, window_end = self._window(day)
                cursor = max(cursor, window_start)
                if cursor < window_end:
                    available = window_end - cursor
                    if remaining <= available:
                        return cursor + remaining
                    remaining -= available
            cursor = datetime.combine(day + timedelta(days=1), time(0), self.tz)


@lru_cache
def get_calendar() -> WorkCalendar:
    cfg = get_config()
    return WorkCalendar(cfg.sla, cfg.holidays.dates, cfg.holidays.complete_years)

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.workflow.calendar import WorkCalendar
from app.yaml_config import load_config

pytestmark = pytest.mark.unit

BAKU = ZoneInfo("Asia/Baku")


def at(y, m, d, h=0, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=BAKU)


@pytest.fixture(scope="module")
def cal() -> WorkCalendar:
    cfg = load_config()
    return WorkCalendar(cfg.sla, cfg.holidays.dates, cfg.holidays.complete_years)


# ---- iş günləri ----
def test_2026_has_241_working_days_like_official_production_calendar(cal):
    # sosial.gov.az: 2026-cı ildə beşgünlük iş həftəsində 241 iş günü
    assert sum(cal.is_workday(date(2026, 1, 1) + timedelta(n)) for n in range(365)) == 241


def test_weekend_holiday_and_normal_day(cal):
    assert not cal.is_workday(date(2026, 10, 24))  # şənbə
    assert not cal.is_workday(date(2026, 11, 10))  # keçirilmiş istirahət günü
    assert cal.is_workday(date(2026, 10, 21))      # çərşənbə


def test_add_workdays_counting_receipt_day(cal):
    # 19.10 bazar ertəsi 1-ci gündür: 19–23, 26–30 oktyabr, 2–6 noyabr → 6 noyabr
    assert cal.add_workdays(date(2026, 10, 19), 15, count_start=True) == date(2026, 11, 6)


def test_add_workdays_not_counting_receipt_day(cal):
    # 20.10 1-ci gündür; 9 və 10 noyabr qeyri-iş günləridir → 11 noyabr
    assert cal.add_workdays(date(2026, 10, 19), 15, count_start=False) == date(2026, 11, 11)


def test_add_workdays_skips_holidays(cal):
    # 5, 6 noyabr; 9 və 10 noyabr bayram/keçirilmiş gün → 3-cü gün 11 noyabr
    assert cal.add_workdays(date(2026, 11, 5), 3, count_start=True) == date(2026, 11, 11)


def test_receipt_on_weekend_starts_counting_from_next_workday(cal):
    assert cal.add_workdays(date(2026, 10, 24), 1, count_start=True) == date(2026, 10, 26)


def test_add_workdays_rejects_non_positive(cal):
    with pytest.raises(ValueError):
        cal.add_workdays(date(2026, 10, 19), 0, count_start=True)


def test_legal_due_date_uses_config(cal):
    # gecə 23:30-da daxil olsa da həmin iş günü 1-ci gün sayılır (ehtiyatlı şərh, sla.yaml)
    assert cal.legal_due_date(at(2026, 10, 19, 23, 30)) == date(2026, 11, 6)


def test_incomplete_year_logs_warning_once(caplog):
    cfg = load_config()
    fresh = WorkCalendar(cfg.sla, cfg.holidays.dates, cfg.holidays.complete_years)  # paylaşılan vəziyyət olmasın
    with caplog.at_level("WARNING"):
        due = fresh.add_workdays(date(2026, 12, 21), 15, count_start=True)
        fresh.add_workdays(date(2026, 12, 22), 15, count_start=True)
    # 2027 bayramları hələ təsdiqlənməyib: 1 yanvar iş günü sayılır, son tarix ehtiyatla erkən çıxır
    assert due == date(2027, 1, 11)
    assert caplog.text.count("2027") == 1


# ---- iş saatları ----
def test_is_work_time(cal):
    assert cal.is_work_time(at(2026, 10, 21, 9, 0))
    assert not cal.is_work_time(at(2026, 10, 21, 18, 0))
    assert not cal.is_work_time(at(2026, 10, 24, 12, 0))


def test_work_hours_same_day(cal):
    assert cal.work_hours_between(at(2026, 10, 21, 10), at(2026, 10, 21, 12, 30)) == pytest.approx(2.5)


def test_work_hours_over_weekend(cal):
    # cümə 17:00 → bazar ertəsi 10:00: 1 + 1 saat
    assert cal.work_hours_between(at(2026, 10, 23, 17), at(2026, 10, 26, 10)) == pytest.approx(2)


def test_work_hours_outside_working_time_is_zero(cal):
    assert cal.work_hours_between(at(2026, 10, 21, 19), at(2026, 10, 22, 8)) == 0


def test_work_hours_over_holidays(cal):
    # cümə 6 noyabr 17:00 → çərşənbə 11 noyabr 10:00
    assert cal.work_hours_between(at(2026, 11, 6, 17), at(2026, 11, 11, 10)) == pytest.approx(2)


def test_add_work_hours_over_weekend(cal):
    assert cal.add_work_hours(at(2026, 10, 23, 17, 30), 1) == at(2026, 10, 26, 9, 30)


def test_add_work_hours_before_opening(cal):
    assert cal.add_work_hours(at(2026, 10, 21, 7, 0), 1) == at(2026, 10, 21, 10, 0)


def test_add_work_hours_skips_holidays(cal):
    assert cal.add_work_hours(at(2026, 11, 6, 17, 0), 4) == at(2026, 11, 11, 12, 0)


def test_urgent_deadline_counts_calendar_hours(cal):
    # m. 10.4: 24 saat, həftə sonu da sayılır
    assert cal.urgent_deadline(at(2026, 10, 23, 17, 0)) == at(2026, 10, 24, 17, 0)


def test_naive_datetime_is_rejected(cal):
    with pytest.raises(ValueError):
        cal.is_work_time(datetime(2026, 10, 21, 10))


def test_other_timezone_is_converted_to_baku(cal):
    assert cal.is_work_time(datetime(2026, 10, 21, 6, 0, tzinfo=ZoneInfo("UTC")))  # Bakı 10:00

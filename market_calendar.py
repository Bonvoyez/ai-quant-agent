# market_calendar.py
"""
US Equity Market (NYSE/NASDAQ) Holiday & Trading Day Calendar.
Provides lightweight, pure-Python calculation of official US stock market holidays
and daylight saving time awareness without heavy external calendar dependencies.
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def get_easter_date(year: int) -> date:
    """Meeus/Jones/Butcher algorithm for Gregorian Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Returns the n-th occurrence of a weekday in a given month (1-indexed)."""
    first_day = date(year, month, 1)
    days_ahead = (weekday - first_day.weekday()) % 7
    first_occurrence = first_day + timedelta(days=days_ahead)
    return first_occurrence + timedelta(weeks=n - 1)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Returns the last occurrence of a weekday in a given month."""
    # Start from last day of month
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = next_month_first - timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_back)


def _observed_fixed_holiday(d: date) -> date:
    """
    US Market Observed Rule:
    If a holiday falls on Saturday, it is observed on Friday.
    If a holiday falls on Sunday, it is observed on Monday.
    """
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    elif d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def get_us_market_holidays(year: int) -> Dict[date, str]:
    """
    Returns all official US equity market holidays for a given year.
    Official NYSE/NASDAQ holidays:
    1. New Year's Day
    2. Martin Luther King Jr. Day (3rd Monday in Jan)
    3. Washington's Birthday / Presidents' Day (3rd Monday in Feb)
    4. Good Friday (Friday before Easter)
    5. Memorial Day (Last Monday in May)
    6. Juneteenth National Independence Day (June 19, observed)
    7. Independence Day (July 4, observed)
    8. Labor Day (1st Monday in Sep)
    9. Thanksgiving Day (4th Thursday in Nov)
    10. Christmas Day (Dec 25, observed)
    """
    holidays: Dict[date, str] = {}

    # 1. New Year's Day (Jan 1)
    nyd = _observed_fixed_holiday(date(year, 1, 1))
    # If Jan 1 falls on a Saturday, the preceding Friday (Dec 31 of prior year) is observed.
    # We only include it if it falls in the current calendar year.
    if nyd.year == year:
        holidays[nyd] = "New Year's Day"

    # Also check if next year's Jan 1 is observed on Dec 31 of current year
    next_nyd = _observed_fixed_holiday(date(year + 1, 1, 1))
    if next_nyd.year == year:
        holidays[next_nyd] = "New Year's Day (Observed)"

    # 2. Martin Luther King, Jr. Day (3rd Monday in January)
    mlk = _nth_weekday_of_month(year, 1, weekday=0, n=3)
    holidays[mlk] = "Martin Luther King Jr. Day"

    # 3. Washington's Birthday / Presidents' Day (3rd Monday in February)
    pres = _nth_weekday_of_month(year, 2, weekday=0, n=3)
    holidays[pres] = "Presidents' Day"

    # 4. Good Friday (2 days before Easter)
    easter = get_easter_date(year)
    good_friday = easter - timedelta(days=2)
    holidays[good_friday] = "Good Friday"

    # 5. Memorial Day (Last Monday in May)
    memorial = _last_weekday_of_month(year, 5, weekday=0)
    holidays[memorial] = "Memorial Day"

    # 6. Juneteenth (June 19, observed)
    june = _observed_fixed_holiday(date(year, 6, 19))
    holidays[june] = "Juneteenth National Independence Day"

    # 7. Independence Day (July 4, observed)
    indep = _observed_fixed_holiday(date(year, 7, 4))
    holidays[indep] = "Independence Day"

    # 8. Labor Day (1st Monday in September)
    labor = _nth_weekday_of_month(year, 9, weekday=0, n=1)
    holidays[labor] = "Labor Day"

    # 9. Thanksgiving Day (4th Thursday in November)
    thanksgiving = _nth_weekday_of_month(year, 11, weekday=3, n=4)
    holidays[thanksgiving] = "Thanksgiving Day"

    # 10. Christmas Day (Dec 25, observed)
    xmas = _observed_fixed_holiday(date(year, 12, 25))
    holidays[xmas] = "Christmas Day"

    return holidays


def is_us_market_holiday(target_date: Optional[date] = None) -> Tuple[bool, Optional[str]]:
    """
    Checks if a given date (default: today in New York) is a US equity market holiday.
    Returns (True, "Holiday Name") if holiday, else (False, None).
    """
    if target_date is None:
        target_date = datetime.now(NEW_YORK).date()

    holidays = get_us_market_holidays(target_date.year)
    if target_date in holidays:
        return True, holidays[target_date]
    return False, None


def is_trading_day(dt: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    Determines whether US markets are open on the given datetime (or now).
    Considers weekends and official NYSE/NASDAQ holidays.
    """
    if dt is None:
        dt = datetime.now(NEW_YORK)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=NEW_YORK)
    else:
        dt = dt.astimezone(NEW_YORK)

    # Check weekend (0=Mon, ..., 4=Fri, 5=Sat, 6=Sun)
    if dt.weekday() >= 5:
        day_name = "Saturday" if dt.weekday() == 5 else "Sunday"
        return False, f"Weekend ({day_name})"

    # Check holiday
    is_holiday, holiday_name = is_us_market_holiday(dt.date())
    if is_holiday:
        return False, f"US Market Holiday ({holiday_name})"

    return True, "Regular Trading Day"

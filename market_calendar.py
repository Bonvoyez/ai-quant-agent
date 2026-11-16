# market_calendar.py
"""
US Equity Market (NYSE/NASDAQ) Holiday & Trading Day Calendar.
Provides lightweight, pure-Python calculation of official US stock market holidays
and daylight saving time awareness without heavy external calendar dependencies.
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple
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


def get_market_session_info(dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Returns real-time US Equity Market session status and countdown to next event.
    Sessions:
    - 'REGULAR': 09:30 ~ 16:00 NY (정규장)
    - 'PRE_MARKET': 04:00 ~ 09:30 NY (프리마켓)
    - 'AFTER_HOURS': 16:00 ~ 20:00 NY (애프터마켓)
    - 'CLOSED': 장마감 / 야간 대기
    - 'HOLIDAY': 공식 공휴일 휴장
    - 'WEEKEND': 주말 휴장
    """
    if dt is None:
        dt = datetime.now(NEW_YORK)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=NEW_YORK)
    else:
        dt = dt.astimezone(NEW_YORK)

    kst_dt = dt.astimezone(ZoneInfo("Asia/Seoul"))
    today_ny = dt.date()
    is_trading, status_msg = is_trading_day(dt)

    def _find_next_trading_day(start_date: date) -> date:
        curr = start_date
        while True:
            curr += timedelta(days=1)
            t_dt = datetime(curr.year, curr.month, curr.day, 12, 0, tzinfo=NEW_YORK)
            trading, _ = is_trading_day(t_dt)
            if trading:
                return curr

    if not is_trading:
        next_trade_date = _find_next_trading_day(today_ny)
        next_open_dt = datetime(next_trade_date.year, next_trade_date.month, next_trade_date.day, 9, 30, tzinfo=NEW_YORK)
        diff_sec = max(0, int((next_open_dt - dt).total_seconds()))
        hours, rem = divmod(diff_sec, 3600)
        mins, secs = divmod(rem, 60)

        is_hol, hol_name = is_us_market_holiday(today_ny)
        code = "HOLIDAY" if is_hol else "WEEKEND"
        label = f"휴장일 ({hol_name})" if is_hol else f"주말 ({status_msg})"

        return {
            "session": code,
            "session_label": label,
            "is_regular_open": False,
            "next_event": "정규장 개장까지",
            "seconds_to_next": diff_sec,
            "countdown": f"{hours:02d}:{mins:02d}:{secs:02d}",
            "ny_time": dt.strftime("%Y-%m-%d %H:%M:%S EST/EDT"),
            "kst_time": kst_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
        }

    # Trading Day Time Boundaries (NY time)
    t_pre_open = datetime(today_ny.year, today_ny.month, today_ny.day, 4, 0, tzinfo=NEW_YORK)
    t_reg_open = datetime(today_ny.year, today_ny.month, today_ny.day, 9, 30, tzinfo=NEW_YORK)
    t_reg_close = datetime(today_ny.year, today_ny.month, today_ny.day, 16, 0, tzinfo=NEW_YORK)
    t_after_close = datetime(today_ny.year, today_ny.month, today_ny.day, 20, 0, tzinfo=NEW_YORK)

    if dt < t_pre_open:
        diff_sec = max(0, int((t_pre_open - dt).total_seconds()))
        hours, rem = divmod(diff_sec, 3600)
        mins, secs = divmod(rem, 60)
        return {
            "session": "CLOSED",
            "session_label": "개장 전 대기",
            "is_regular_open": False,
            "next_event": "프리마켓 개장까지",
            "seconds_to_next": diff_sec,
            "countdown": f"{hours:02d}:{mins:02d}:{secs:02d}",
            "ny_time": dt.strftime("%Y-%m-%d %H:%M:%S EST/EDT"),
            "kst_time": kst_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
        }
    elif t_pre_open <= dt < t_reg_open:
        diff_sec = max(0, int((t_reg_open - dt).total_seconds()))
        hours, rem = divmod(diff_sec, 3600)
        mins, secs = divmod(rem, 60)
        return {
            "session": "PRE_MARKET",
            "session_label": "프리마켓 진행 중",
            "is_regular_open": False,
            "next_event": "정규장 개장까지",
            "seconds_to_next": diff_sec,
            "countdown": f"{hours:02d}:{mins:02d}:{secs:02d}",
            "ny_time": dt.strftime("%Y-%m-%d %H:%M:%S EST/EDT"),
            "kst_time": kst_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
        }
    elif t_reg_open <= dt < t_reg_close:
        diff_sec = max(0, int((t_reg_close - dt).total_seconds()))
        hours, rem = divmod(diff_sec, 3600)
        mins, secs = divmod(rem, 60)
        return {
            "session": "REGULAR",
            "session_label": "정규장 진행 중",
            "is_regular_open": True,
            "next_event": "정규장 마감까지",
            "seconds_to_next": diff_sec,
            "countdown": f"{hours:02d}:{mins:02d}:{secs:02d}",
            "ny_time": dt.strftime("%Y-%m-%d %H:%M:%S EST/EDT"),
            "kst_time": kst_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
        }
    elif t_reg_close <= dt < t_after_close:
        next_trade_date = _find_next_trading_day(today_ny)
        next_pre_dt = datetime(next_trade_date.year, next_trade_date.month, next_trade_date.day, 4, 0, tzinfo=NEW_YORK)
        diff_sec = max(0, int((next_pre_dt - dt).total_seconds()))
        hours, rem = divmod(diff_sec, 3600)
        mins, secs = divmod(rem, 60)
        return {
            "session": "AFTER_HOURS",
            "session_label": "애프터마켓 진행 중",
            "is_regular_open": False,
            "next_event": "익일 프리마켓까지",
            "seconds_to_next": diff_sec,
            "countdown": f"{hours:02d}:{mins:02d}:{secs:02d}",
            "ny_time": dt.strftime("%Y-%m-%d %H:%M:%S EST/EDT"),
            "kst_time": kst_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
        }
    else:  # dt >= t_after_close
        next_trade_date = _find_next_trading_day(today_ny)
        next_pre_dt = datetime(next_trade_date.year, next_trade_date.month, next_trade_date.day, 4, 0, tzinfo=NEW_YORK)
        diff_sec = max(0, int((next_pre_dt - dt).total_seconds()))
        hours, rem = divmod(diff_sec, 3600)
        mins, secs = divmod(rem, 60)
        return {
            "session": "CLOSED",
            "session_label": "정규장 및 애프터마켓 마감",
            "is_regular_open": False,
            "next_event": "익일 프리마켓까지",
            "seconds_to_next": diff_sec,
            "countdown": f"{hours:02d}:{mins:02d}:{secs:02d}",
            "ny_time": dt.strftime("%Y-%m-%d %H:%M:%S EST/EDT"),
            "kst_time": kst_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
        }

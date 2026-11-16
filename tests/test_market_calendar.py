# test_market_calendar.py
from datetime import date, datetime
from zoneinfo import ZoneInfo
from market_calendar import (
    get_us_market_holidays,
    is_us_market_holiday,
    is_trading_day,
)

NEW_YORK = ZoneInfo("America/New_York")


def test_2024_us_holidays():
    holidays_2024 = get_us_market_holidays(2024)
    # Check known 2024 NYSE holidays:
    # 1. New Year's Day: Mon Jan 1
    assert holidays_2024[date(2024, 1, 1)] == "New Year's Day"
    # 2. MLK Day: Mon Jan 15
    assert holidays_2024[date(2024, 1, 15)] == "Martin Luther King Jr. Day"
    # 3. Presidents' Day: Mon Feb 19
    assert holidays_2024[date(2024, 2, 19)] == "Presidents' Day"
    # 4. Good Friday: Fri Mar 29
    assert holidays_2024[date(2024, 3, 29)] == "Good Friday"
    # 5. Memorial Day: Mon May 27
    assert holidays_2024[date(2024, 5, 27)] == "Memorial Day"
    # 6. Juneteenth: Wed Jun 19
    assert holidays_2024[date(2024, 6, 19)] == "Juneteenth National Independence Day"
    # 7. Independence Day: Thu Jul 4
    assert holidays_2024[date(2024, 7, 4)] == "Independence Day"
    # 8. Labor Day: Mon Sep 2
    assert holidays_2024[date(2024, 9, 2)] == "Labor Day"
    # 9. Thanksgiving: Thu Nov 28
    assert holidays_2024[date(2024, 11, 28)] == "Thanksgiving Day"
    # 10. Christmas: Wed Dec 25
    assert holidays_2024[date(2024, 12, 25)] == "Christmas Day"


def test_2025_us_holidays():
    holidays_2025 = get_us_market_holidays(2025)
    # Good Friday in 2025 is April 18
    assert holidays_2025[date(2025, 4, 18)] == "Good Friday"
    # Thanksgiving in 2025 is Nov 27
    assert holidays_2025[date(2025, 11, 27)] == "Thanksgiving Day"


def test_is_trading_day_weekend():
    # Saturday
    sat = datetime(2024, 1, 6, 10, 0, tzinfo=NEW_YORK)
    is_open, reason = is_trading_day(sat)
    assert not is_open
    assert "Weekend" in reason


def test_is_trading_day_holiday():
    # Christmas 2024
    xmas = datetime(2024, 12, 25, 10, 0, tzinfo=NEW_YORK)
    is_open, reason = is_trading_day(xmas)
    assert not is_open
    assert "Christmas Day" in reason


def test_is_trading_day_regular_day():
    # Regular Tuesday (2024-01-09)
    tue = datetime(2024, 1, 9, 10, 0, tzinfo=NEW_YORK)
    is_open, reason = is_trading_day(tue)
    assert is_open
    assert reason == "Regular Trading Day"

from datetime import timedelta


def next_settlement_date(day):
    # BUG: weekends are treated as settlement days.
    return day + timedelta(days=1)

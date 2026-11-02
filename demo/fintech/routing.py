def route_payout(currency, country):
    # BUG: non-USD payouts fall through to a default rail with the wrong currency.
    if currency == "USD":
        return "us-bank"
    if country == "US":
        return "us-bank"
    return "default-bank"

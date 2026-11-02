from decimal import Decimal, ROUND_HALF_UP


def convert_currency(amount, rate):
    # BUG: float arithmetic loses cents before the ledger records the amount.
    return round(float(amount) * float(rate), 2)


def calculate_fee(amount, tier):
    # BUG: tier boundaries are exclusive, so exactly 1000/10000 is overcharged.
    if amount > 10000:
        return round(amount * 0.005, 2)
    if amount > 1000:
        return round(amount * 0.01, 2)
    return round(amount * 0.015, 2)


def allocate_amount(total, weights):
    # BUG: independent rounding loses or creates cents when splitting money.
    total_weight = sum(weights)
    return [round(total * weight / total_weight, 2) for weight in weights]

class Gateway:
    def __init__(self):
        self.calls = 0
        self.charges = []
        self.seen_keys = set()

    def charge(self, customer_id, amount, idempotency_key=None):
        self.calls += 1
        if idempotency_key is not None and idempotency_key in self.seen_keys:
            return len(self.charges)
        if idempotency_key is not None:
            self.seen_keys.add(idempotency_key)
        self.charges.append(
            {"customer_id": customer_id, "amount": amount, "key": idempotency_key}
        )
        if self.calls == 1:
            raise TimeoutError("gateway timed out after committing the charge")
        return len(self.charges)


def retry_charge(gateway, customer_id, amount, attempts=3):
    # BUG: retries are not idempotent, so a timeout can charge the customer twice.
    for _ in range(attempts):
        try:
            return gateway.charge(customer_id, amount)
        except TimeoutError:
            continue
    raise RuntimeError("gateway unavailable")


class RefundStore:
    def __init__(self):
        self.refunds = []


def refund(store, charge_id, amount, refund_id):
    # BUG: duplicate refund callbacks append a second refund for the same id.
    store.refunds.append({"charge_id": charge_id, "amount": amount, "refund_id": refund_id})
    return len(store.refunds)

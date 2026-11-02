import unittest
from datetime import date

from ledger import LedgerStore, append_entries, settle_batch
from money import allocate_amount, calculate_fee, convert_currency
from payments import Gateway, RefundStore, refund, retry_charge
from risk import fraud_score
from routing import route_payout
from scheduling import next_settlement_date


class FinanceTests(unittest.TestCase):
    def test_convert_currency_rounds_half_up(self):
        self.assertEqual(convert_currency("1.005", "1"), 1.01)

    def test_fee_boundary_is_inclusive(self):
        self.assertEqual(calculate_fee(1000, "gold"), 10.0)
        self.assertEqual(calculate_fee(10000, "platinum"), 50.0)

    def test_allocate_amount_preserves_total(self):
        shares = allocate_amount(0.05, [1, 1, 1])
        self.assertEqual(round(sum(shares), 2), 0.05)

    def test_retry_charge_is_idempotent(self):
        gateway = Gateway()
        retry_charge(gateway, "cust-1", 99.0)
        self.assertEqual(len(gateway.charges), 1)

    def test_refund_is_idempotent(self):
        store = RefundStore()
        refund(store, "ch-1", 20.0, "rf-1")
        refund(store, "ch-1", 20.0, "rf-1")
        self.assertEqual(len(store.refunds), 1)

    def test_settle_batch_reads_all_pages(self):
        pages = {0: [1, 2], 1: [3], 2: []}
        self.assertEqual(settle_batch(lambda page, size: pages.get(page, [])), [1, 2, 3])

    def test_append_entries_is_atomic(self):
        store = LedgerStore()
        with self.assertRaises(ValueError):
            append_entries(store, [{"id": 1}, {"id": 2, "fail": True}, {"id": 3}])
        self.assertEqual(store.entries, [])

    def test_route_payout_uses_local_rail(self):
        self.assertEqual(route_payout("EUR", "DE"), "eu-bank")
        self.assertEqual(route_payout("GBP", "GB"), "uk-bank")

    def test_fraud_score_degrades_on_timeout(self):
        class Provider:
            def score(self, txn):
                raise TimeoutError("risk provider timed out")

        result = fraud_score(Provider(), {"id": "txn-1"})
        self.assertEqual(result, {"score": 50, "degraded": True})

    def test_next_settlement_date_skips_weekend(self):
        self.assertEqual(next_settlement_date(date(2026, 11, 6)), date(2026, 11, 9))


if __name__ == "__main__":
    unittest.main()

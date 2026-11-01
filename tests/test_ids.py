import unittest

from opsgraph import ids


class IdTests(unittest.TestCase):
    def test_stable_32_hex(self):
        first = ids.service_id("checkout-service")
        second = ids.service_id("checkout-service")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in first))

    def test_type_scoping(self):
        self.assertNotEqual(ids.service_id("payment-gateway"), ids.external_id("payment-gateway"))
        self.assertNotEqual(
            ids.endpoint_id("checkout-service", "POST", "/pay"),
            ids.endpoint_id("checkout-service", "GET", "/pay"),
        )


if __name__ == "__main__":
    unittest.main()

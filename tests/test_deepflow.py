import unittest

from opsgraph.deepflow import DeepFlowClient, normalize_record


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(self.payload)


class DeepFlowTests(unittest.TestCase):
    def test_normalize_external(self):
        record = normalize_record(
            {
                "time": "1793548800",
                "pod_service_0": "checkout-service",
                "pod_0": "checkout-service-1",
                "pod_ns_0": "shop",
                "pod_node_0": "node-1",
                "pod_service_1": "",
                "request_domain": "api.promo-flash.com",
                "endpoint": "/promo",
                "request_type": "POST",
                "l7_protocol_str": "HTTP",
                "request": "600",
                "response": "600",
                "rrt_sum": "6000000",
                "rrt_count": "600",
                "rrt_p99": "900",
                "client_error": "0",
                "server_error": "24",
                "byte_tx": "1000",
                "byte_rx": "2000",
                "is_internet_1": "1",
            }
        )
        self.assertIsNotNone(record)
        self.assertTrue(record.external)
        self.assertEqual(record.server_service, "api")
        self.assertAlmostEqual(record.rps, 10.0)
        self.assertGreater(record.error_rate, 0.03)

    def test_client_parses_columns_values(self):
        payload = {
            "result": {
                "columns": ["time", "pod_service_0", "pod_service_1", "request"],
                "values": [[1793548800, "a", "b", 60]],
            }
        }
        session = FakeSession(payload)
        client = DeepFlowClient("http://deepflow.example", session=session)
        rows = client.query("SELECT 1")
        self.assertEqual(rows[0]["pod_service_0"], "a")
        self.assertEqual(session.calls[0]["url"], "http://deepflow.example/v1/query/")
        self.assertEqual(session.calls[0]["headers"]["X-ORG-ID"], "1")


if __name__ == "__main__":
    unittest.main()

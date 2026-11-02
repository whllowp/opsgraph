import tempfile
import unittest
from pathlib import Path

from opsgraph.ebpf_stacks import load_stacks, parse_collapsed, parse_perf_script


class EBPFStackTests(unittest.TestCase):
    def test_folded_stacks(self):
        samples = parse_collapsed(
            "server.handle_request;payments.py:22 40\nserver.handle_request;money.py:5 10\n",
            case_id="external",
        )
        self.assertEqual(len(samples), 50)
        self.assertEqual(samples[0]["stack"][-1]["file"], "payments.py")
        self.assertEqual(samples[0]["stack"][-1]["line"], 22)
        self.assertEqual(samples[0]["probe"], "ebpf.folded")

    def test_perf_script(self):
        text = """python 1234 [001] 1.000: 100000 cycles:
    ffffffff81000000 native_write_msr+0x10
    PyEval_EvalFrameDefault+0x20
        payments.py:22

"""
        samples = parse_perf_script(text)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["pid"], 1234)
        self.assertEqual(samples[0]["stack"][-1]["file"], "payments.py")
        self.assertEqual(samples[0]["stack"][-1]["line"], 22)

    def test_auto_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stacks.txt"
            path.write_text("a;b.py:3 7\n", encoding="utf-8")
            samples = load_stacks(path)
            self.assertEqual(len(samples), 7)
            self.assertEqual(samples[0]["stack"][-1]["file"], "b.py")


if __name__ == "__main__":
    unittest.main()

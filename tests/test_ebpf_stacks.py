import tempfile
import unittest
from pathlib import Path

from opsgraph.code_cases import CASES, FINTECH_ROOT
from opsgraph.code_rca import analyze_code
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
    PyEval_EvalFrameDefault+0x20
        payments.py:22
    ffffffff81000000 native_write_msr+0x10

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

    def test_real_perf_fixture(self):
        path = Path(__file__).resolve().parent.parent / "data" / "code_rca" / "perf-sample.txt"
        samples = load_stacks(path)
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["pid"], 952)
        self.assertEqual(samples[0]["stack"][-1]["symbol"], "retry_charge")
        self.assertEqual(samples[0]["stack"][-1]["file"], "fintech_demo.c")
        self.assertEqual(samples[0]["stack"][-1]["line"], 5)

    def test_real_perf_stack_maps_to_code_rca(self):
        path = Path(__file__).resolve().parent.parent / "data" / "code_rca" / "perf-sample.txt"
        samples = load_stacks(path)
        rca = analyze_code(samples, FINTECH_ROOT, CASES[0])
        self.assertEqual(rca["finding"]["file"], "fintech_demo.c")
        self.assertEqual(rca["finding"]["function"], "retry_charge")
        self.assertEqual(rca["finding"]["line"], 5)


if __name__ == "__main__":
    unittest.main()

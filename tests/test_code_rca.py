import json
import tempfile
import unittest
from pathlib import Path

from opsgraph.code_cases import CASES, FINTECH_ROOT
from opsgraph.code_rca import (
    apply_patch,
    analyze_code,
    generate_telemetry,
    run_rea_loop,
    verify_after,
)


class CodeRCATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.telemetry = generate_telemetry()

    def test_all_cases_point_to_the_expected_code(self):
        for case in CASES:
            rca = analyze_code(self.telemetry["cases"][case["id"]]["before"], FINTECH_ROOT, case)
            finding = rca["finding"]
            self.assertEqual(finding["file"], case["file"], case["id"])
            self.assertEqual(finding["function"], case["function"], case["id"])
            self.assertGreater(finding["confidence"], 0.7)
            self.assertLessEqual(
                finding["line"], len((FINTECH_ROOT / case["file"]).read_text().splitlines())
            )

    def test_patch_and_verification(self):
        case = CASES[0]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "fintech"
            import shutil

            shutil.copytree(FINTECH_ROOT, repo)
            before = (repo / case["file"]).read_text(encoding="utf-8")
            apply_patch(repo, case)
            after = (repo / case["file"]).read_text(encoding="utf-8")
            self.assertNotEqual(before, after)
            result = verify_after(case, self.telemetry["cases"][case["id"]]["after"])
            self.assertTrue(result["passed"])
            self.assertLess(result["p99_after_ms"], result["p99_before_ms"])

    def test_ten_iteration_rea_loop(self):
        result = run_rea_loop(max_iterations=10)
        self.assertEqual(result["summary"]["iterations"], 10)
        self.assertEqual(result["summary"]["fixed"], 10)
        self.assertEqual(result["summary"]["rca_matches"], 10)
        self.assertTrue(result["summary"]["final_suite"]["passed"])
        for item in result["iterations"]:
            self.assertEqual(item["status"], "fixed")
            self.assertTrue(item["tests"]["case"]["passed"])
            self.assertTrue(item["verification"]["passed"])
        case_three = next(item for item in result["iterations"] if item["case_id"] == "case-03")
        self.assertEqual(len(case_three["candidates"]), 2)
        self.assertEqual(case_three["candidates"][0]["status"], "rejected-by-test")
        self.assertEqual(case_three["candidates"][1]["status"], "accepted")
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()

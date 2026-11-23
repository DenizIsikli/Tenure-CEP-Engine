import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deployer"))
from transfer_rules import assess_research, calculate_funding, dean_decision


class TransferRuleTests(unittest.TestCase):
    def test_research_passes_at_threshold(self):
        self.assertTrue(assess_research(5, 5)["academicJustificationApproved"])

    def test_research_below_threshold_fails(self):
        self.assertFalse(assess_research(4.9, 5)["academicJustificationApproved"])

    def test_missing_research_fails_closed(self):
        self.assertFalse(assess_research(None, 5)["academicJustificationApproved"])

    def test_future_department_covers_full_salary(self):
        result = calculate_funding(80000, 100000, 30000)
        self.assertEqual(result["fundingShortfall"], 0)
        self.assertEqual(result["totalFundingAvailable"], 100000)
        self.assertTrue(result["fundingArrangementVerified"])

    def test_current_department_can_cover_shortfall(self):
        result = calculate_funding(80000, 60000, 30000)
        self.assertEqual(result["fundingShortfall"], 20000)
        self.assertEqual(result["currentDepartmentContributionProposed"], 20000)
        self.assertEqual(result["totalFundingAvailable"], 80000)
        self.assertTrue(result["fundingArrangementVerified"])
        self.assertFalse(result["canFundWithoutCurrentDepartment"])

    def test_combined_funding_is_insufficient(self):
        result = calculate_funding(100000, 50000, 10000)
        self.assertEqual(result["totalFundingAvailable"], 60000)
        self.assertFalse(result["fundingArrangementVerified"])

    def test_dean_approves_only_if_all_checks_pass(self):
        result = dean_decision(True, True, True)
        self.assertTrue(result["approved"])

    def test_dean_rejects_negative_future_domain_fit(self):
        result = dean_decision(False, True, True)
        self.assertFalse(result["approved"])

    def test_dean_rejects_negative_research_check(self):
        result = dean_decision(True, False, True)
        self.assertFalse(result["approved"])

    def test_dean_rejects_insufficient_funding(self):
        result = dean_decision(True, True, False)
        self.assertFalse(result["approved"])


if __name__ == "__main__":
    unittest.main()

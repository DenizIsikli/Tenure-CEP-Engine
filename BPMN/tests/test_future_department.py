import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("Install future_department/requirements.txt to run Future Department HTTP tests")

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "future_department" / "app.py"
spec = importlib.util.spec_from_file_location("future_department_service", MODULE_PATH)
fd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fd
spec.loader.exec_module(fd)


class FutureDepartmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "future.sqlite3"
        self.app = fd.create_app(self.db, "http://callback.test/future-department/message")
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def request_payload(self, transfer_id="X-1", domain="computational physics", department="Computer Science"):
        return {
            "transferId": transfer_id,
            "targetDepartment": department,
            "professorDomain": domain,
            "researchDomain": domain,
            "professorSalary": 80000,
            "salaryCurrency": "EUR",
            "salaryPeriod": "year",
        }

    @patch.object(fd.requests, "post")
    def test_domain_rejection_is_still_forwarded_to_current_department(self, post):
        response_mock = Mock()
        response_mock.raise_for_status.return_value = None
        response_mock.json.return_value = {"status": "accepted"}
        post.return_value = response_mock

        response = self.client.post("/api/transfer-requests", json=self.request_payload(domain="astrophysics", department="Medicine"))
        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertFalse(body["futureDepartmentSuitable"])
        self.assertEqual(body["futureDepartmentFunding"], 80000)
        self.assertEqual(post.call_args.kwargs["json"]["message_name"], "TransferProposalDocuments")
        # The callback still contains the negative candidate-fit result.
        callback_vars = post.call_args.kwargs["json"]["variables"]
        self.assertFalse(callback_vars["futureDepartmentSuitable"])

    @patch.object(fd.requests, "post")
    def test_current_department_response_is_forwarded_to_dean_even_after_fit_rejection(self, post):
        response_mock = Mock()
        response_mock.raise_for_status.return_value = None
        response_mock.json.return_value = {"status": "accepted"}
        post.return_value = response_mock

        self.client.post("/api/transfer-requests", json=self.request_payload(domain="astrophysics", department="Medicine"))
        response = self.client.post("/api/current-department-response", json={
            "transferId": "X-1",
            "variables": {
                "academicJustificationApproved": True,
                "fundingArrangementVerified": True,
                "totalFundingAvailable": 80000,
                "professorSalary": 80000,
            },
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(post.call_args.kwargs["json"]["message_name"], "TransferDocumentsForDeanApproval")
        self.assertFalse(post.call_args.kwargs["json"]["variables"]["futureDepartmentSuitable"])

    @patch.object(fd.requests, "post")
    def test_dean_cannot_finally_approve_domain_rejected_candidate(self, post):
        response_mock = Mock()
        response_mock.raise_for_status.return_value = None
        response_mock.json.return_value = {"status": "accepted"}
        post.return_value = response_mock

        self.client.post("/api/transfer-requests", json=self.request_payload(domain="astrophysics", department="Medicine"))
        self.client.post("/api/current-department-response", json={
            "transferId": "X-1",
            "variables": {
                "academicJustificationApproved": True,
                "fundingArrangementVerified": True,
                "totalFundingAvailable": 80000,
                "professorSalary": 80000,
            },
        })
        response = self.client.post("/api/final-decision", json={
            "transferId": "X-1",
            "variables": {"approved": True, "deanAcademicFit": True, "deanFundingApproved": True},
        })
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.get_json()["approved"])
        self.assertEqual(post.call_args.kwargs["json"]["message_name"], "TransferDecision")
        self.assertFalse(post.call_args.kwargs["json"]["variables"]["approved"])


    @patch.object(fd.requests, "post")
    def test_final_approval_is_blocked_until_current_department_responds(self, post):
        response_mock = Mock()
        response_mock.raise_for_status.return_value = None
        response_mock.json.return_value = {"status": "accepted"}
        post.return_value = response_mock

        self.client.post("/api/transfer-requests", json=self.request_payload())
        response = self.client.post("/api/final-decision", json={
            "transferId": "X-1",
            "variables": {"approved": True, "deanAcademicFit": True, "deanFundingApproved": True},
        })
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.get_json()["approved"])
        self.assertIn("Current Department response", response.get_json().get("decisionReason", ""))

    def test_department_database_exposes_available_funding_and_domains(self):
        response = self.client.get("/api/departments")
        self.assertEqual(response.status_code, 200)
        departments = response.get_json()
        cs = next(x for x in departments if x["name"] == "Computer Science")
        self.assertEqual(cs["availableFunding"], 100000)
        self.assertIn("computational physics", cs["supportedDomains"])


if __name__ == "__main__":
    unittest.main()

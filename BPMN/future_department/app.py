"""Local Future Department black-box service.

This service owns its own SQLite database of department domains and budgets.
It receives requests from the Professor BPMN process, sends its candidate and
funding assessment to the Current Department, forwards the Current Department's
assessment to the Dean, and records the Dean's final decision.

A negative domain-fit result NEVER stops the workflow. It is forwarded along
with the rest of the proposal so the Current Department and Dean still review
and decide the transfer.

No money is transferred. A funding reservation is only a simulated budget
commitment recorded after the Dean approves.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/data/future_department.sqlite3"))
COMMUNICATION_CALLBACK_URL = os.getenv(
    "COMMUNICATION_CALLBACK_URL", "http://communication:5001/future-department/message"
)
CALLBACK_TIMEOUT = float(os.getenv("CALLBACK_TIMEOUT", "15"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("future-department")

DEFAULT_DEPARTMENTS = [
    {
        "name": "Engineering",
        "supportedDomains": [
            "engineering", "mechanical engineering", "electrical engineering",
            "materials science", "robotics", "civil engineering",
        ],
        "annualBudget": 200000.0,
        "committedFunding": 140000.0,
        "currency": "EUR",
        "salaryPeriod": "year",
    },
    {
        "name": "Biology",
        "supportedDomains": [
            "biology", "genetics", "molecular biology", "biotechnology", "ecology",
        ],
        "annualBudget": 150000.0,
        "committedFunding": 50000.0,
        "currency": "EUR",
        "salaryPeriod": "year",
    },
    {
        "name": "Computer Science",
        "supportedDomains": [
            "computer science", "software engineering", "artificial intelligence",
            "data science", "computational physics", "cybersecurity",
        ],
        "annualBudget": 300000.0,
        "committedFunding": 200000.0,
        "currency": "EUR",
        "salaryPeriod": "year",
    },
    {
        "name": "Medicine",
        "supportedDomains": [
            "medicine", "clinical research", "public health", "biomedical science",
        ],
        "annualBudget": 250000.0,
        "committedFunding": 100000.0,
        "currency": "EUR",
        "salaryPeriod": "year",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database(database_path: Path = DATABASE_PATH) -> None:
    with _connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS departments (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                supported_domains_json TEXT NOT NULL,
                annual_budget REAL NOT NULL CHECK(annual_budget >= 0),
                committed_funding REAL NOT NULL DEFAULT 0 CHECK(committed_funding >= 0),
                currency TEXT NOT NULL DEFAULT 'EUR',
                salary_period TEXT NOT NULL DEFAULT 'year',
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS assessments (
                transfer_id TEXT PRIMARY KEY,
                target_department TEXT NOT NULL,
                research_domain TEXT NOT NULL,
                future_department_suitable INTEGER NOT NULL,
                fit_reason TEXT NOT NULL,
                professor_salary REAL NOT NULL,
                salary_currency TEXT NOT NULL,
                salary_period TEXT NOT NULL,
                future_department_funding REAL NOT NULL,
                funding_shortfall REAL NOT NULL,
                request_json TEXT NOT NULL,
                assessment_json TEXT NOT NULL,
                current_department_response_json TEXT,
                final_decision_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS funding_reservations (
                transfer_id TEXT PRIMARY KEY,
                department_name TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount >= 0),
                status TEXT NOT NULL CHECK(status IN ('PROPOSED','COMMITTED','RELEASED')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(transfer_id) REFERENCES assessments(transfer_id)
            )"""
        )

        count = connection.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if count == 0:
            for department in DEFAULT_DEPARTMENTS:
                connection.execute(
                    """INSERT INTO departments (
                        name, supported_domains_json, annual_budget, committed_funding,
                        currency, salary_period, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        department["name"],
                        json.dumps(department["supportedDomains"]),
                        department["annualBudget"],
                        department["committedFunding"],
                        department["currency"],
                        department["salaryPeriod"],
                        utc_now(),
                    ),
                )


def _department_funding(connection: sqlite3.Connection, department: sqlite3.Row) -> dict[str, float]:
    pending_and_committed = connection.execute(
        """SELECT COALESCE(SUM(amount), 0) FROM funding_reservations
           WHERE department_name=? AND status IN ('PROPOSED','COMMITTED')""",
        (department["name"],),
    ).fetchone()[0]
    # committed_funding is the initial amount committed before this prototype;
    # the reservations table records subsequent transfer proposals/approvals.
    available = max(
        0.0,
        float(department["annual_budget"])
        - float(department["committed_funding"])
        - float(pending_and_committed or 0),
    )
    return {
        "annualBudget": float(department["annual_budget"]),
        "alreadyCommittedFunding": float(department["committed_funding"]),
        "pendingAndApprovedReservations": float(pending_and_committed or 0),
        "availableFunding": available,
    }


def assess_transfer_request(database_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    transfer_id = str(data.get("transferId") or data.get("transfer_id") or "").strip()
    target_department = str(data.get("targetDepartment") or data.get("target_department") or "").strip()
    professor_domain = str(data.get("professorDomain") or data.get("professor_domain") or "").strip()
    research_domain = str(data.get("researchDomain") or data.get("research_domain") or "").strip()
    candidate_domain = professor_domain or research_domain
    if not transfer_id or not target_department or not candidate_domain:
        raise ValueError("transferId, targetDepartment and professorDomain are required")

    try:
        salary = float(data.get("professorSalary", data.get("salary")))
    except (TypeError, ValueError) as exc:
        raise ValueError("professorSalary must be a number") from exc
    if salary <= 0:
        raise ValueError("professorSalary must be greater than zero")

    currency = str(data.get("salaryCurrency") or data.get("currency") or "").upper().strip()
    period = str(data.get("salaryPeriod") or "year").lower().strip()
    if not currency:
        raise ValueError("salaryCurrency is required")

    now = utc_now()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM assessments WHERE transfer_id=?", (transfer_id,)
        ).fetchone()
        if existing:
            # Idempotent retry: do not create a second reservation or alter an
            # assessment already in progress.
            department_row = connection.execute(
                "SELECT supported_domains_json FROM departments WHERE name=? COLLATE NOCASE",
                (existing["target_department"],),
            ).fetchone()
            domains = json.loads(department_row["supported_domains_json"]) if department_row else []
            funding = connection.execute(
                "SELECT amount FROM funding_reservations WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
            original_request = json.loads(existing["request_json"])
            saved_assessment = json.loads(existing["assessment_json"])
            original_request.update(saved_assessment)
            original_request["futureDepartmentContributionStatus"] = "PROPOSED" if funding else "NONE"
            return original_request

        department = connection.execute(
            "SELECT * FROM departments WHERE name=? COLLATE NOCASE", (target_department,)
        ).fetchone()

        if department is None:
            suitable = False
            domains: list[str] = []
            fit_reason = f"The target department '{target_department}' is not configured in the Future Department database."
            available = 0.0
            compatible = False
        else:
            domains = json.loads(department["supported_domains_json"])
            supported_normalised = {normalise_domain(x) for x in domains}
            suitable = normalise_domain(candidate_domain) in supported_normalised
            fit_reason = (
                f"The professor's domain '{candidate_domain}' is supported by {department['name']}."
                if suitable
                else f"The professor's domain '{candidate_domain}' is not listed among the domains supported by {department['name']}."
            )
            compatible = (
                currency == str(department["currency"]).upper()
                and period == str(department["salary_period"]).lower()
            )
            available = _department_funding(connection, department)["availableFunding"] if compatible else 0.0
            if not compatible:
                fit_reason += (
                    f" Funding cannot be proposed because the salary basis ({currency}/{period}) "
                    f"does not match the department budget basis ({department['currency']}/{department['salary_period']})."
                )

        proposed_contribution = min(salary, available)
        shortfall = max(0.0, salary - proposed_contribution)
        future_covers_salary = proposed_contribution >= salary
        assessment = dict(data)
        assessment.update({
            "transferId": transfer_id,
            "targetDepartment": department["name"] if department else target_department,
            "professorDomainAssessed": candidate_domain,
            "futureDepartmentSuitable": suitable,
            "futureDepartmentFitReason": fit_reason,
            "supportedResearchDomains": domains,
            "professorSalary": salary,
            "salaryCurrency": currency,
            "salaryPeriod": period,
            "futureDepartmentFunding": proposed_contribution,
            "futureDepartmentAvailableFundingAtAssessment": available,
            "futureDepartmentCanCoverSalary": future_covers_salary,
            "fundingShortfall": shortfall,
            "fundingCurrencyCompatible": compatible,
            "futureDepartmentContributionStatus": "PROPOSED" if proposed_contribution > 0 else "NONE",
        })
        connection.execute(
            """INSERT INTO assessments (
                transfer_id, target_department, research_domain,
                future_department_suitable, fit_reason, professor_salary,
                salary_currency, salary_period, future_department_funding,
                funding_shortfall, request_json, assessment_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                transfer_id, assessment["targetDepartment"], candidate_domain,
                int(suitable), fit_reason, salary, currency, period,
                proposed_contribution, shortfall, json.dumps(data), json.dumps(assessment), now, now,
            ),
        )
        if department and proposed_contribution > 0:
            connection.execute(
                """INSERT INTO funding_reservations (
                    transfer_id, department_name, amount, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'PROPOSED', ?, ?)""",
                (transfer_id, department["name"], proposed_contribution, now, now),
            )
        connection.commit()
        return assessment


def _callback(url: str, message_name: str, transfer_id: str, variables: dict[str, Any]) -> None:
    response = requests.post(
        url,
        json={"message_name": message_name, "transferId": transfer_id, "variables": variables},
        timeout=CALLBACK_TIMEOUT,
    )
    response.raise_for_status()


def create_app(database_path: Path = DATABASE_PATH, callback_url: str = COMMUNICATION_CALLBACK_URL) -> Flask:
    init_database(database_path)
    flask_app = Flask(__name__)

    @flask_app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "future-department"})

    @flask_app.get("/api/departments")
    def list_departments():
        with _connect(database_path) as connection:
            rows = connection.execute("SELECT * FROM departments ORDER BY name").fetchall()
            result = []
            for row in rows:
                funding = _department_funding(connection, row)
                result.append({
                    "name": row["name"],
                    "supportedDomains": json.loads(row["supported_domains_json"]),
                    "currency": row["currency"],
                    "salaryPeriod": row["salary_period"],
                    **funding,
                })
        return jsonify(result)

    @flask_app.post("/api/departments")
    def upsert_department():
        data = request.get_json(force=True)
        name = str(data.get("name") or "").strip()
        domains = data.get("supportedDomains")
        if not name or not isinstance(domains, list) or not domains:
            return jsonify({"error": "name and a non-empty supportedDomains list are required"}), 400
        try:
            budget = float(data.get("annualBudget"))
            committed = float(data.get("committedFunding", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "annualBudget and committedFunding must be numbers"}), 400
        if budget < 0 or committed < 0 or committed > budget:
            return jsonify({"error": "Funding values must be non-negative and committedFunding cannot exceed annualBudget"}), 400
        currency = str(data.get("currency") or "EUR").upper()
        period = str(data.get("salaryPeriod") or "year").lower()
        with _connect(database_path) as connection:
            connection.execute(
                """INSERT INTO departments (
                    name, supported_domains_json, annual_budget, committed_funding,
                    currency, salary_period, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    supported_domains_json=excluded.supported_domains_json,
                    annual_budget=excluded.annual_budget,
                    committed_funding=excluded.committed_funding,
                    currency=excluded.currency,
                    salary_period=excluded.salary_period,
                    updated_at=excluded.updated_at""",
                (name, json.dumps([str(x).strip() for x in domains]), budget, committed, currency, period, utc_now()),
            )
        return jsonify({"status": "saved", "name": name}), 201

    @flask_app.post("/api/transfer-requests")
    def transfer_request():
        data = request.get_json(force=True)
        try:
            assessment = assess_transfer_request(database_path, data)
            # Critical rule: always forward the proposal, including when
            # futureDepartmentSuitable is false. No early rejection here.
            _callback(callback_url, "TransferProposalDocuments", assessment["transferId"], assessment)
            logger.info(
                "Assessed %s for %s: suitable=%s funding=%s shortfall=%s",
                assessment["transferId"], assessment["targetDepartment"],
                assessment["futureDepartmentSuitable"], assessment["futureDepartmentFunding"],
                assessment["fundingShortfall"],
            )
            return jsonify({"status": "proposal_sent_to_current_department", **assessment}), 202
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400
        except requests.RequestException as exc:
            logger.exception("Could not send proposal to Current Department")
            return jsonify({"error": "Assessment saved but BPMN callback failed", "detail": str(exc)}), 502

    @flask_app.post("/api/current-department-response")
    def current_department_response():
        data = request.get_json(force=True)
        transfer_id = str(data.get("transferId") or data.get("transfer_id") or "").strip()
        if not transfer_id:
            return jsonify({"error": "transferId is required"}), 400
        variables = data.get("variables") if isinstance(data.get("variables"), dict) else data
        variables = dict(variables)
        variables["transferId"] = transfer_id
        with _connect(database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assessments WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
            if row is None:
                return jsonify({"error": "No Future Department assessment exists for this transferId"}), 404
            merged = json.loads(row["request_json"])
            merged.update(json.loads(row["assessment_json"]))
            merged.update(variables)
            connection.execute(
                "UPDATE assessments SET current_department_response_json=?, updated_at=? WHERE transfer_id=?",
                (json.dumps(variables), utc_now(), transfer_id),
            )
        try:
            # Always send the complete case to the Dean, regardless of the
            # Future Department's domain-fit result or Current Department's
            # academic/funding assessment.
            _callback(callback_url, "TransferDocumentsForDeanApproval", transfer_id, merged)
            return jsonify({"status": "forwarded_to_dean", "transferId": transfer_id}), 202
        except requests.RequestException as exc:
            logger.exception("Could not forward Current Department response to Dean")
            return jsonify({"error": "Current Department response saved but Dean callback failed", "detail": str(exc)}), 502

    @flask_app.post("/api/final-decision")
    def final_decision():
        data = request.get_json(force=True)
        transfer_id = str(data.get("transferId") or data.get("transfer_id") or "").strip()
        variables = data.get("variables") if isinstance(data.get("variables"), dict) else data
        variables = dict(variables)
        approved = variables.get("approved")
        if not transfer_id or not isinstance(approved, bool):
            return jsonify({"error": "transferId and boolean variables.approved are required"}), 400
        variables["transferId"] = transfer_id
        now = utc_now()
        with _connect(database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assessments WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
            if row is None:
                return jsonify({"error": "No Future Department assessment exists for this transferId"}), 404
            current_response = json.loads(row["current_department_response_json"] or "{}")
            # A final approval must be consistent with all earlier assessments.
            # In normal operation the Dean already applies these checks; this is
            # an additional safeguard at the black-box boundary.
            if approved and not bool(row["future_department_suitable"]):
                approved = False
                variables["decisionReason"] = (
                    "Final approval was blocked because the Future Department domain-fit assessment was negative."
                )
            if approved and (variables.get("deanAcademicFit") is not True or variables.get("deanFundingApproved") is not True):
                approved = False
                variables["decisionReason"] = "Final approval was blocked because a required Dean approval flag was not true."
            if approved:
                if not current_response:
                    approved = False
                    variables["decisionReason"] = (
                        "Final approval was blocked because the Current Department response was not received."
                    )
                else:
                    salary = float(row["professor_salary"])
                    total = current_response.get("totalFundingAvailable")
                    checks_pass = (
                        current_response.get("academicJustificationApproved") is True
                        and current_response.get("fundingArrangementVerified") is True
                        and total is not None
                        and float(total) >= salary
                    )
                    if not checks_pass:
                        approved = False
                        variables["decisionReason"] = (
                            "Final approval was blocked because the Current Department's research or funding checks did not pass."
                        )
            variables["approved"] = approved
            reservation = connection.execute(
                "SELECT * FROM funding_reservations WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
            if reservation:
                new_status = "COMMITTED" if approved else "RELEASED"
                connection.execute(
                    "UPDATE funding_reservations SET status=?, updated_at=? WHERE transfer_id=?",
                    (new_status, now, transfer_id),
                )
                variables["futureDepartmentContributionStatus"] = new_status
            connection.execute(
                "UPDATE assessments SET final_decision_json=?, updated_at=? WHERE transfer_id=?",
                (json.dumps(variables), now, transfer_id),
            )
        try:
            _callback(callback_url, "TransferDecision", transfer_id, variables)
            return jsonify({"status": "final_decision_recorded", "transferId": transfer_id, "approved": approved}), 202
        except requests.RequestException as exc:
            logger.exception("Could not notify BPMN about final decision")
            return jsonify({"error": "Decision recorded but BPMN callback failed", "detail": str(exc)}), 502

    @flask_app.get("/api/transfers/<transfer_id>")
    def get_transfer(transfer_id: str):
        with _connect(database_path) as connection:
            row = connection.execute(
                "SELECT * FROM assessments WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
            reservation = connection.execute(
                "SELECT * FROM funding_reservations WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
        if row is None:
            return jsonify({"error": "Transfer not found"}), 404
        result = dict(row)
        result["future_department_suitable"] = bool(result["future_department_suitable"])
        result["request"] = json.loads(result.pop("request_json"))
        result["assessment"] = json.loads(result.pop("assessment_json"))
        for key in ("current_department_response_json", "final_decision_json"):
            raw = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(raw) if raw else None
        result["funding_reservation"] = dict(reservation) if reservation else None
        return jsonify(result)

    return flask_app


if __name__ == "__main__":
    service = create_app()
    service.run(host="0.0.0.0", port=5002, threaded=True, use_reloader=False)

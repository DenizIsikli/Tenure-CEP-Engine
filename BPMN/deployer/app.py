"""Professor-transfer communication service and Zeebe workers.

Siddhi starts the Professor process. This service deploys the supplied BPMN,
implements its service-task workers, and communicates with the separate local
Future Department black-box server. The Future Department owns its funding
SQLite database and sends BPMN message callbacks to this service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request

from transfer_rules import assess_research, calculate_funding, dean_decision

ZEEBE_ADDRESS = os.getenv("ZEEBE_ADDRESS", "zeebe:26500")
BPMN_FILE = os.getenv("BPMN_FILE", "/bpmn/prof_move_dept_to_be.bpmn")
SIDDHI_CALLBACK_URL = os.getenv("SIDDHI_CALLBACK_URL", "http://siddhi:7072/middleman")
FUTURE_DEPARTMENT_URL = os.getenv("FUTURE_DEPARTMENT_URL", "http://future-department:5002").rstrip("/")
RESEARCH_IMPORTANCE_THRESHOLD = float(os.getenv("RESEARCH_IMPORTANCE_THRESHOLD", "5"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/data/professor_transfers.sqlite3"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

PROFESSOR_PROCESS_ID = "Process_1mpqx3g"

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("professor-transfer")
app = Flask(__name__)
client = None
worker = None
event_loop: asyncio.AbstractEventLoop | None = None

START_FIELDS = [
    "professorId", "professorName", "professorSalary", "salaryCurrency",
    "salaryPeriod", "currentDepartment", "currentSection", "targetDepartment",
    "targetSection", "professorDomain", "researchDomain", "researchDescription",
    "researchImportance", "currentDepartmentAvailableFunding", "deanAcademicFit",
    "deanFundingApproved",
]
TRANSFER_VARIABLES = [
    "transferId", "withinDepartment", *START_FIELDS,
    "futureDepartmentSuitable", "futureDepartmentFitReason", "supportedResearchDomains",
    "futureDepartmentFunding", "futureDepartmentAvailableFundingAtAssessment",
    "futureDepartmentCanCoverSalary", "fundingShortfall", "fundingCurrencyCompatible",
    "researchFitScore", "researchThreshold", "academicJustificationApproved",
    "academicDecisionReason", "canFundWithoutCurrentDepartment",
    "requiresCurrentDepartmentContribution", "currentDepartmentContributionRequired",
    "currentDepartmentContributionProposed", "currentDepartmentCanCoverShortfall",
    "totalFundingAvailable", "fundingArrangementVerified", "fundingDecisionReason",
    "deanResearchAssessment", "deanFundingApproved", "deanAcademicFit", "approved",
    "decisionReason", "futureDepartmentContributionStatus",
]
ALIASES = {
    "professor_id": "professorId", "professor_name": "professorName",
    "professor_salary": "professorSalary", "salary": "professorSalary",
    "salary_currency": "salaryCurrency", "currency": "salaryCurrency",
    "salary_period": "salaryPeriod", "current_department": "currentDepartment",
    "current_section": "currentSection", "target_department": "targetDepartment",
    "target_section": "targetSection", "professor_domain": "professorDomain",
    "research_domain": "researchDomain", "research_description": "researchDescription",
    "research_importance": "researchImportance",
    "current_department_available_funding": "currentDepartmentAvailableFunding",
    "dean_academic_fit": "deanAcademicFit", "dean_funding_approved": "deanFundingApproved",
}
NUMERIC_FIELDS = {
    "professorSalary", "researchImportance", "currentDepartmentAvailableFunding",
}
BOOLEAN_FIELDS = {"deanAcademicFit", "deanFundingApproved"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    with db_connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS professors (
                professor_id TEXT PRIMARY KEY,
                professor_name TEXT,
                salary REAL,
                currency TEXT,
                salary_period TEXT,
                department TEXT,
                section TEXT,
                domain TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        # CREATE TABLE IF NOT EXISTS does not change an existing SQLite table.
        # Older project runs may have created `professors` before the `domain`
        # field was added. Migrate that persistent database in place so existing
        # professor and transfer records are preserved.
        professor_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(professors)")
        }
        if "domain" not in professor_columns:
            logger.info("Migrating professors table: adding missing domain column")
            connection.execute("ALTER TABLE professors ADD COLUMN domain TEXT")

        connection.execute(
            """CREATE TABLE IF NOT EXISTS transfers (
                transfer_id TEXT PRIMARY KEY,
                professor_id TEXT NOT NULL,
                status TEXT NOT NULL,
                variables_json TEXT NOT NULL,
                current_department_result_json TEXT,
                dean_decision_json TEXT,
                final_decision_json TEXT,
                updated_at TEXT NOT NULL
            )"""
        )


def save_transfer(transfer_id: str, variables: dict[str, Any], status: str = "STARTED") -> None:
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO transfers (transfer_id, professor_id, status, variables_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(transfer_id) DO UPDATE SET
                 professor_id=excluded.professor_id, status=excluded.status,
                 variables_json=excluded.variables_json, updated_at=excluded.updated_at""",
            (transfer_id, str(variables.get("professorId") or ""), status, json.dumps(variables), utc_now()),
        )


def merge_transfer_variables(transfer_id: str, updates: dict[str, Any]) -> None:
    with db_connect() as connection:
        row = connection.execute(
            "SELECT variables_json FROM transfers WHERE transfer_id=?", (transfer_id,)
        ).fetchone()
        if row:
            current = json.loads(row["variables_json"])
            current.update(updates)
            connection.execute(
                "UPDATE transfers SET variables_json=?, updated_at=? WHERE transfer_id=?",
                (json.dumps(current), utc_now(), transfer_id),
            )


def save_current_department_result(transfer_id: str, result: dict[str, Any]) -> None:
    with db_connect() as connection:
        row = connection.execute(
            "SELECT variables_json FROM transfers WHERE transfer_id=?", (transfer_id,)
        ).fetchone()
        if row:
            variables = json.loads(row["variables_json"])
            variables.update(result)
            connection.execute(
                """UPDATE transfers SET variables_json=?, current_department_result_json=?, updated_at=?
                   WHERE transfer_id=?""",
                (json.dumps(variables), json.dumps(result), utc_now(), transfer_id),
            )


def save_dean_decision(transfer_id: str, result: dict[str, Any]) -> None:
    merge_transfer_variables(transfer_id, result)
    with db_connect() as connection:
        connection.execute(
            "UPDATE transfers SET dean_decision_json=?, updated_at=? WHERE transfer_id=?",
            (json.dumps(result), utc_now(), transfer_id),
        )


def save_final_decision(transfer_id: str, result: dict[str, Any]) -> None:
    status = "APPROVED" if result.get("approved") is True else "REJECTED"
    merge_transfer_variables(transfer_id, result)
    with db_connect() as connection:
        connection.execute(
            "UPDATE transfers SET status=?, final_decision_json=?, updated_at=? WHERE transfer_id=?",
            (status, json.dumps(result), utc_now(), transfer_id),
        )


def upsert_professor(variables: dict[str, Any]) -> None:
    professor_id = variables.get("professorId")
    if not professor_id:
        return
    with db_connect() as connection:
        connection.execute(
            """INSERT INTO professors (
                 professor_id, professor_name, salary, currency, salary_period,
                 department, section, domain, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(professor_id) DO UPDATE SET
                 professor_name=COALESCE(excluded.professor_name, professors.professor_name),
                 salary=COALESCE(excluded.salary, professors.salary),
                 currency=COALESCE(excluded.currency, professors.currency),
                 salary_period=COALESCE(excluded.salary_period, professors.salary_period),
                 department=COALESCE(excluded.department, professors.department),
                 section=COALESCE(excluded.section, professors.section),
                 domain=COALESCE(excluded.domain, professors.domain),
                 updated_at=excluded.updated_at""",
            (
                str(professor_id), variables.get("professorName"), variables.get("professorSalary"),
                variables.get("salaryCurrency"), variables.get("salaryPeriod"),
                variables.get("currentDepartment"), variables.get("currentSection"),
                variables.get("professorDomain") or variables.get("researchDomain"), utc_now(),
            ),
        )


def update_professor_department(variables: dict[str, Any]) -> None:
    professor_id = variables.get("professorId")
    target_department = variables.get("targetDepartment")
    if not professor_id or not target_department:
        raise ValueError("professorId and targetDepartment are required")
    upsert_professor(variables)
    with db_connect() as connection:
        connection.execute(
            "UPDATE professors SET department=?, updated_at=? WHERE professor_id=?",
            (target_department, utc_now(), str(professor_id)),
        )


def update_professor_section(variables: dict[str, Any]) -> None:
    professor_id = variables.get("professorId")
    target_section = variables.get("targetSection")
    if not professor_id or not target_section:
        raise ValueError("professorId and targetSection are required")
    upsert_professor(variables)
    with db_connect() as connection:
        connection.execute(
            "UPDATE professors SET section=?, updated_at=? WHERE professor_id=?",
            (target_section, utc_now(), str(professor_id)),
        )


def normalise_variables(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("variables", {})
    if isinstance(raw, str):
        raw = json.loads(raw)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("variables must be a JSON object")
    combined = dict(raw)
    for key, value in data.items():
        if key not in {"variables", "transferId", "withinDepartment", "transfer_id", "within_department"}:
            combined[key] = value
    allowed = set(START_FIELDS)
    result: dict[str, Any] = {}
    for key, value in combined.items():
        canonical = ALIASES.get(key, key)
        if canonical in allowed:
            result[canonical] = value
    for key in NUMERIC_FIELDS:
        if result.get(key) not in (None, ""):
            try:
                result[key] = float(result[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number") from exc
    for key in BOOLEAN_FIELDS:
        if result.get(key) is not None and not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false")
    result.setdefault("salaryPeriod", "year")
    result.setdefault("currentDepartmentAvailableFunding", 0.0)
    return result


def validate_start_request(data: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    transfer_id = data.get("transferId") or data.get("transfer_id")
    if not isinstance(transfer_id, str) or not transfer_id.strip():
        raise ValueError("transferId is required and must be a non-empty string")
    within_department = data.get("withinDepartment", data.get("within_department"))
    if not isinstance(within_department, bool):
        raise ValueError("withinDepartment must be true or false")
    variables = normalise_variables(data)
    variables["transferId"] = transfer_id.strip()
    variables["withinDepartment"] = within_department
    if not variables.get("professorId"):
        raise ValueError("professorId is required")
    if within_department:
        if not variables.get("targetSection"):
            raise ValueError("targetSection is required for an internal section transfer")
    else:
        required = [
            "professorSalary", "salaryCurrency", "currentDepartment", "targetDepartment",
            "professorDomain", "researchDescription", "researchImportance",
        ]
        missing = [name for name in required if variables.get(name) in (None, "")]
        if missing:
            raise ValueError("Missing required external-transfer fields: " + ", ".join(missing))
        if variables["professorSalary"] <= 0:
            raise ValueError("professorSalary must be greater than zero")
        if not 0 <= variables["researchImportance"] <= 10:
            raise ValueError("researchImportance must be between 0 and 10")
        if variables["currentDepartmentAvailableFunding"] < 0:
            raise ValueError("currentDepartmentAvailableFunding cannot be negative")
    return transfer_id.strip(), within_department, variables


def log_event(event_name: str, transfer_id: str, role: str, payload: dict[str, Any]) -> None:
    body = {
        "transfer_id": str(transfer_id), "event": event_name,
        "timestamp": utc_now(), "role": role,
        "payload": json.dumps(payload, default=str), "error": "",
    }
    try:
        response = requests.post(SIDDHI_CALLBACK_URL, json=body, timeout=3)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Siddhi logging callback failed for %s: %s", event_name, exc)


def run_async(coroutine):
    if event_loop is None:
        raise RuntimeError("Zeebe event loop is not ready")
    return asyncio.run_coroutine_threadsafe(coroutine, event_loop).result(timeout=60)


async def publish_message(name: str, correlation_key: str, variables: dict[str, Any], message_id: str | None = None) -> None:
    if client is None:
        raise RuntimeError("Zeebe client is not ready")
    await client.publish_message(
        name=name, correlation_key=correlation_key, variables=variables, message_id=message_id
    )


async def publish_final_decision_to_processes(transfer_id: str, variables: dict[str, Any]) -> None:
    """Publish one decision for the Professor and Current Department catches."""
    # These catch events belong to two different BPMN process definitions.
    # Zeebe correlates one message to matching subscriptions in different
    # processes, so publishing twice would be unnecessary and could leave a
    # duplicate buffered message.
    await publish_message(
        "TransferDecision", transfer_id, variables,
        message_id=f"{transfer_id}-final-{uuid.uuid4()}",
    )


def call_future_department(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(FUTURE_DEPARTMENT_URL + path, json=payload, timeout=20)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"status": "accepted"}


# ---------------------------------------------------------------------------
# Flask API
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "zeebe_ready": client is not None})


@app.post("/start_transfer")
def start_transfer():
    try:
        data = request.get_json(force=True)
        if isinstance(data, dict) and isinstance(data.get("event"), dict):
            data = {**data, **data["event"]}
        transfer_id, within_department, variables = validate_start_request(data)
        if client is None:
            return jsonify({"error": "Zeebe is not ready yet"}), 503
        save_transfer(transfer_id, variables)
        upsert_professor(variables)
        result = run_async(client.run_process(PROFESSOR_PROCESS_ID, variables=variables))
        process_key = getattr(result, "process_instance_key", result)
        return jsonify({
            "status": "started", "transferId": transfer_id,
            "processInstanceKey": str(process_key), "withinDepartment": within_department,
        }), 202
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        payload = data.get("event") if isinstance(data, dict) and isinstance(data.get("event"), dict) else data
        logger.warning(
            "Rejected /start_transfer: error=%s; received_keys=%s",
            exc,
            sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
        )
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not start transfer")
        return jsonify({"error": "Could not start transfer", "detail": str(exc)}), 502


def publish_incoming_message(message_name: str, transfer_id: str, variables: dict[str, Any]) -> None:
    if client is None:
        raise RuntimeError("Zeebe is not ready yet")
    variables = dict(variables)
    variables["transferId"] = transfer_id
    if message_name in {"TransferProposalDocuments", "TransferDocumentsForDeanApproval"}:
        run_async(publish_message(message_name, "", variables))
    elif message_name == "TransferDecision":
        if not isinstance(variables.get("approved"), bool):
            raise ValueError("TransferDecision requires a boolean approved field")
        save_final_decision(transfer_id, variables)
        run_async(publish_final_decision_to_processes(transfer_id, variables))
    elif message_name == "SectionTransferConfirmation":
        run_async(publish_message(message_name, transfer_id, variables))
    else:
        raise ValueError(f"Unsupported incoming message: {message_name}")
    log_event(message_name, transfer_id, "future-department", variables)


@app.post("/future-department/message")
def future_department_message():
    """Callback endpoint used by the separate Future Department service."""
    try:
        data = request.get_json(force=True)
        message_name = data.get("message_name") or data.get("messageName")
        transfer_id = str(data.get("transferId") or data.get("transfer_id") or "").strip()
        variables = data.get("variables") or data.get("payload") or {}
        if not transfer_id or not isinstance(variables, dict):
            raise ValueError("transferId and a variables JSON object are required")
        publish_incoming_message(str(message_name or ""), transfer_id, variables)
        return jsonify({"status": "message_published", "message_name": message_name, "transferId": transfer_id}), 202
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not publish Future Department callback")
        return jsonify({"error": "Could not publish message", "detail": str(exc)}), 502


@app.post("/message")
def manual_message():
    return future_department_message()


@app.get("/transfers/<transfer_id>")
def get_transfer(transfer_id: str):
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM transfers WHERE transfer_id=?", (transfer_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Transfer not found"}), 404
    result = dict(row)
    for field in ("variables_json", "current_department_result_json", "dean_decision_json", "final_decision_json"):
        raw = result.pop(field)
        result[field.removesuffix("_json")] = json.loads(raw) if raw else None
    return jsonify(result)


@app.get("/professors/<professor_id>")
def get_professor(professor_id: str):
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM professors WHERE professor_id=?", (professor_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Professor not found"}), 404
    return jsonify(dict(row))


# ---------------------------------------------------------------------------
# Zeebe job handlers. Names match task definitions in the provided BPMN.
# ---------------------------------------------------------------------------

async def send_department_transfer_request(
    transferId: str, professorId: str | None = None, professorName: str | None = None,
    professorSalary: float | None = None, salaryCurrency: str | None = None,
    salaryPeriod: str | None = None, currentDepartment: str | None = None,
    currentSection: str | None = None, targetDepartment: str | None = None,
    targetSection: str | None = None, professorDomain: str | None = None,
    researchDomain: str | None = None, researchDescription: str | None = None,
    researchImportance: float | None = None,
    currentDepartmentAvailableFunding: float | None = None,
    deanAcademicFit: bool | None = None, deanFundingApproved: bool | None = None,
):
    payload = {
        "transferId": transferId, "professorId": professorId, "professorName": professorName,
        "professorSalary": professorSalary, "salaryCurrency": salaryCurrency,
        "salaryPeriod": salaryPeriod or "year", "currentDepartment": currentDepartment,
        "currentSection": currentSection, "targetDepartment": targetDepartment,
        "targetSection": targetSection, "professorDomain": professorDomain,
        "researchDomain": researchDomain, "researchDescription": researchDescription,
        "researchImportance": researchImportance,
        "currentDepartmentAvailableFunding": currentDepartmentAvailableFunding,
        "deanAcademicFit": deanAcademicFit, "deanFundingApproved": deanFundingApproved,
    }
    await asyncio.to_thread(call_future_department, "/api/transfer-requests", payload)
    log_event("DepartmentTransferRequest", transferId, "professor", payload)
    return {"departmentTransferRequestSent": True}


async def send_section_transfer_request(
    transferId: str, professorId: str | None = None, professorName: str | None = None,
    currentDepartment: str | None = None, currentSection: str | None = None,
    targetSection: str | None = None,
):
    payload = {
        "transferId": transferId, "professorId": professorId, "professorName": professorName,
        "currentDepartment": currentDepartment, "currentSection": currentSection,
        "targetSection": targetSection,
    }
    await publish_message("SectionTransferRequest", "", payload)
    log_event("SectionTransferRequest", transferId, "professor", payload)
    return {"sectionTransferRequestSent": True}


async def move_to_new_section(transferId: str, professorId: str | None = None, targetSection: str | None = None):
    variables = {"professorId": professorId, "targetSection": targetSection}
    update_professor_section(variables)
    log_event("ProfessorSectionUpdated", transferId, "professor", variables)
    return {"professorSectionUpdated": True, "newSection": targetSection}


async def move_to_receiving_department(transferId: str, professorId: str | None = None, targetDepartment: str | None = None):
    variables = {"professorId": professorId, "targetDepartment": targetDepartment}
    update_professor_department(variables)
    log_event("ProfessorDepartmentUpdated", transferId, "professor", variables)
    return {"professorDepartmentUpdated": True, "newDepartment": targetDepartment}


async def inform_receiving_section(transferId: str, targetSection: str | None = None):
    log_event("ReceivingSectionInformed", transferId, "current-department", {"targetSection": targetSection})
    return {"receivingSectionInformed": True}


async def arrange_internal_funding_redistribution(transferId: str):
    # Administrative marker only. No real financial transaction takes place.
    log_event("InternalFundingRedistributionRecorded", transferId, "current-department", {"moneyMoved": False})
    return {"internalFundingRedistributed": True, "moneyMoved": False}


async def confirm_section_transfer(transferId: str):
    await publish_message("SectionTransferConfirmation", transferId, {"transferId": transferId})
    return {"sectionTransferConfirmed": True}


async def review_academic_justification(transferId: str, researchImportance: float | None = None):
    result = assess_research(researchImportance, RESEARCH_IMPORTANCE_THRESHOLD)
    log_event("ResearchAssessed", transferId, "current-department", result)
    return result


async def verify_funding_arrangement(
    transferId: str, professorSalary: float | None = None,
    futureDepartmentFunding: float | None = None,
    currentDepartmentAvailableFunding: float | None = None,
    salaryCurrency: str | None = None, salaryPeriod: str | None = None,
):
    result = calculate_funding(
        professor_salary=professorSalary,
        future_department_funding=futureDepartmentFunding,
        current_department_available_funding=currentDepartmentAvailableFunding,
    )
    result["salaryCurrency"] = salaryCurrency
    result["salaryPeriod"] = salaryPeriod or "year"
    log_event("FundingAssessed", transferId, "current-department", result)
    return result


async def send_department_transfer_response(
    transferId: str, professorId: str | None = None, professorName: str | None = None,
    professorSalary: float | None = None, salaryCurrency: str | None = None,
    salaryPeriod: str | None = None, currentDepartment: str | None = None,
    targetDepartment: str | None = None, professorDomain: str | None = None,
    researchDomain: str | None = None,
    researchDescription: str | None = None, researchImportance: float | None = None,
    futureDepartmentSuitable: bool | None = None, futureDepartmentFitReason: str | None = None,
    futureDepartmentFunding: float | None = None, fundingShortfall: float | None = None,
    researchFitScore: float | None = None, researchThreshold: float | None = None,
    academicJustificationApproved: bool | None = None, academicDecisionReason: str | None = None,
    futureDepartmentCanCoverSalary: bool | None = None,
    canFundWithoutCurrentDepartment: bool | None = None,
    requiresCurrentDepartmentContribution: bool | None = None,
    currentDepartmentContributionRequired: float | None = None,
    currentDepartmentContributionProposed: float | None = None,
    currentDepartmentCanCoverShortfall: bool | None = None,
    totalFundingAvailable: float | None = None,
    fundingArrangementVerified: bool | None = None, fundingDecisionReason: str | None = None,
):
    payload = {
        key: value for key, value in locals().items()
        if key not in {"transferId"} and value is not None
    }
    payload["transferId"] = transferId
    save_current_department_result(transferId, payload)
    # This response is routed through the Future Department black box, which
    # forwards the complete case to the Dean even if suitability was false.
    await asyncio.to_thread(
        call_future_department,
        "/api/current-department-response",
        {"transferId": transferId, "variables": payload},
    )
    log_event("DepartmentTransferResponse", transferId, "current-department", payload)
    return {"departmentTransferResponseSent": True}


async def review_transfer_justification(
    transferId: str, futureDepartmentSuitable: bool | None = None,
    futureDepartmentFitReason: str | None = None,
    academicJustificationApproved: bool | None = None,
    researchFitScore: float | None = None, researchThreshold: float | None = None,
    deanAcademicFit: bool | None = None,
):
    # The Dean sees the Future Department's domain assessment AND the Current
    # Department's threshold result. A negative earlier assessment is recorded,
    # not used to bypass this task or the following funding task.
    candidate_ok = futureDepartmentSuitable is True
    research_ok = academicJustificationApproved is True
    dean_override_ok = deanAcademicFit is not False
    result = {
        "deanAcademicFit": candidate_ok and research_ok and dean_override_ok,
        "deanResearchAssessment": (
            "Domain and research checks passed."
            if candidate_ok and research_ok and dean_override_ok
            else "One or more domain/research criteria did not pass; final funding review will still run."
        ),
        "futureDepartmentSuitable": candidate_ok,
        "futureDepartmentFitReason": futureDepartmentFitReason,
        "researchFitScore": researchFitScore,
        "researchThreshold": researchThreshold,
    }
    log_event("DeanReviewedResearchAndDomainFit", transferId, "dean", result)
    return result


async def review_agreed_funding_proposal(
    transferId: str, professorSalary: float | None = None,
    totalFundingAvailable: float | None = None,
    fundingArrangementVerified: bool | None = None,
    deanFundingApproved: bool | None = None, deanAcademicFit: bool | None = None,
    futureDepartmentSuitable: bool | None = None,
    academicJustificationApproved: bool | None = None,
    fundingShortfall: float | None = None,
    currentDepartmentContributionProposed: float | None = None,
    salaryCurrency: str | None = None, salaryPeriod: str | None = None,
):
    salary = float(professorSalary) if professorSalary is not None else 0.0
    total = float(totalFundingAvailable) if totalFundingAvailable is not None else 0.0
    funding_ok = (
        professorSalary is not None
        and totalFundingAvailable is not None
        and total >= salary
        and fundingArrangementVerified is True
        and deanFundingApproved is not False
    )
    result = dean_decision(
        future_department_suitable=futureDepartmentSuitable,
        academic_justification_approved=(deanAcademicFit is True and academicJustificationApproved is True),
        funding_arrangement_verified=funding_ok,
    )
    result.update({
        "fundingShortfall": fundingShortfall,
        "currentDepartmentContributionProposed": currentDepartmentContributionProposed,
        "totalFundingAvailable": totalFundingAvailable,
        "salaryCurrency": salaryCurrency,
        "salaryPeriod": salaryPeriod or "year",
    })
    save_dean_decision(transferId, result)
    log_event("DeanFundingAndFinalDecision", transferId, "dean", result)
    return result


async def send_transfer_approved(
    transferId: str, deanAcademicFit: bool | None = None,
    deanFundingApproved: bool | None = None, totalFundingAvailable: float | None = None,
    decisionReason: str | None = None,
):
    variables = {
        "approved": True, "deanAcademicFit": deanAcademicFit,
        "deanFundingApproved": deanFundingApproved,
        "totalFundingAvailable": totalFundingAvailable, "decisionReason": decisionReason,
    }
    await asyncio.to_thread(
        call_future_department,
        "/api/final-decision",
        {"transferId": transferId, "variables": variables},
    )
    log_event("DeanTransferApproved", transferId, "dean", variables)
    return {"approved": True}


async def send_transfer_rejected(
    transferId: str, deanAcademicFit: bool | None = None,
    deanFundingApproved: bool | None = None, totalFundingAvailable: float | None = None,
    decisionReason: str | None = None,
):
    variables = {
        "approved": False, "deanAcademicFit": deanAcademicFit,
        "deanFundingApproved": deanFundingApproved,
        "totalFundingAvailable": totalFundingAvailable, "decisionReason": decisionReason,
    }
    await asyncio.to_thread(
        call_future_department,
        "/api/final-decision",
        {"transferId": transferId, "variables": variables},
    )
    log_event("DeanTransferRejected", transferId, "dean", variables)
    return {"approved": False}


def register_workers(zeebe_worker) -> None:
    # Professor process
    zeebe_worker.task(
        task_type="publish-department-transfer-request",
        variables_to_fetch=["transferId", "professorId", "professorName", "professorSalary",
                            "salaryCurrency", "salaryPeriod", "currentDepartment", "currentSection",
                            "targetDepartment", "targetSection", "professorDomain", "researchDomain",
                            "researchDescription", "researchImportance",
                            "currentDepartmentAvailableFunding", "deanAcademicFit", "deanFundingApproved"],
    )(send_department_transfer_request)
    zeebe_worker.task(
        task_type="publish-section-transfer-request",
        variables_to_fetch=["transferId", "professorId", "professorName", "currentDepartment",
                            "currentSection", "targetSection"],
    )(send_section_transfer_request)
    zeebe_worker.task(
        task_type="update-professor-section",
        variables_to_fetch=["transferId", "professorId", "targetSection"],
    )(move_to_new_section)
    zeebe_worker.task(
        task_type="update-professor-department",
        variables_to_fetch=["transferId", "professorId", "targetDepartment"],
    )(move_to_receiving_department)

    # Current Department process
    zeebe_worker.task(
        task_type="notify-receiving-section",
        variables_to_fetch=["transferId", "targetSection"],
    )(inform_receiving_section)
    zeebe_worker.task(
        task_type="redistribute-internal-section-funding",
        variables_to_fetch=["transferId"],
    )(arrange_internal_funding_redistribution)
    zeebe_worker.task(
        task_type="publish-section-transfer-confirmation",
        variables_to_fetch=["transferId"],
    )(confirm_section_transfer)
    zeebe_worker.task(
        task_type="evaluate-academic-justification",
        variables_to_fetch=["transferId", "researchImportance"],
    )(review_academic_justification)
    zeebe_worker.task(
        task_type="verify-funding-arrangement",
        variables_to_fetch=["transferId", "professorSalary", "futureDepartmentFunding",
                            "currentDepartmentAvailableFunding", "salaryCurrency", "salaryPeriod"],
    )(verify_funding_arrangement)
    zeebe_worker.task(
        task_type="publish-department-transfer-response",
        variables_to_fetch=["transferId", "professorId", "professorName", "professorSalary",
                            "salaryCurrency", "salaryPeriod", "currentDepartment", "targetDepartment",
                            "professorDomain", "researchDomain", "researchDescription", "researchImportance",
                            "futureDepartmentSuitable", "futureDepartmentFitReason",
                            "futureDepartmentFunding", "fundingShortfall", "researchFitScore",
                            "researchThreshold", "academicJustificationApproved", "academicDecisionReason",
                            "futureDepartmentCanCoverSalary", "canFundWithoutCurrentDepartment",
                            "requiresCurrentDepartmentContribution", "currentDepartmentContributionRequired",
                            "currentDepartmentContributionProposed", "currentDepartmentCanCoverShortfall",
                            "totalFundingAvailable", "fundingArrangementVerified", "fundingDecisionReason"],
    )(send_department_transfer_response)

    # Dean process. The two review tasks always execute, regardless of the
    # Future Department's candidate-fit result or the Current Department's checks.
    zeebe_worker.task(
        task_type="evaluate-dean-transfer-justification",
        variables_to_fetch=["transferId", "futureDepartmentSuitable", "futureDepartmentFitReason",
                            "academicJustificationApproved", "researchFitScore", "researchThreshold",
                            "deanAcademicFit"],
    )(review_transfer_justification)
    zeebe_worker.task(
        task_type="evaluate-dean-funding-proposal",
        variables_to_fetch=["transferId", "professorSalary", "totalFundingAvailable",
                            "fundingArrangementVerified", "deanFundingApproved", "deanAcademicFit",
                            "futureDepartmentSuitable", "academicJustificationApproved", "fundingShortfall",
                            "currentDepartmentContributionProposed", "salaryCurrency", "salaryPeriod"],
    )(review_agreed_funding_proposal)
    zeebe_worker.task(
        task_type="publish-transfer-approved",
        variables_to_fetch=["transferId", "deanAcademicFit", "deanFundingApproved",
                            "totalFundingAvailable", "decisionReason"],
    )(send_transfer_approved)
    zeebe_worker.task(
        task_type="publish-transfer-rejected",
        variables_to_fetch=["transferId", "deanAcademicFit", "deanFundingApproved",
                            "totalFundingAvailable", "decisionReason"],
    )(send_transfer_rejected)


def serve_http() -> None:
    app.run(host="0.0.0.0", port=5001, threaded=True, use_reloader=False)


async def run_zeebe() -> None:
    global client, worker
    import grpc
    from pyzeebe import ZeebeClient, ZeebeWorker

    channel = grpc.aio.insecure_channel(ZEEBE_ADDRESS)
    client = ZeebeClient(grpc_channel=channel)
    worker = ZeebeWorker(grpc_channel=channel, name="professor-transfer-workers")
    register_workers(worker)

    for attempt in range(1, 31):
        try:
            deployed = await client.deploy_resource(BPMN_FILE)
            logger.info("Deployed BPMN %s: %s", BPMN_FILE, deployed)
            break
        except Exception as exc:  # retry while Zeebe starts
            logger.warning("BPMN deployment attempt %s failed: %s", attempt, exc)
            if attempt == 30:
                raise
            await asyncio.sleep(2)
    await worker.work()


async def main() -> None:
    global event_loop
    init_database()
    event_loop = asyncio.get_running_loop()
    threading.Thread(target=serve_http, daemon=True).start()
    await run_zeebe()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")

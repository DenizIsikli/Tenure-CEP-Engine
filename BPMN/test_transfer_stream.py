#!/usr/bin/env python3
"""End-to-end diagnostic for event_streams/send_stream.py.

Run from the professor_transfer_project directory:
    python3 /path/to/test_transfer_stream.py

The script creates one complete external-transfer JSONL event, invokes the
project's existing send_stream.py (so the same path is exercised), and checks
whether the communication service records and completes the transfer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def http_json(url: str, timeout: float = 5.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return response.status, None, ""
            try:
                return response.status, json.loads(raw), raw
            except json.JSONDecodeError:
                return response.status, None, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            body = None
        return exc.code, body, raw


def post_health(url: str):
    status, body, raw = http_json(url)
    return status, body, raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the project's Siddhi -> send_stream.py -> communication -> Zeebe route.")
    parser.add_argument("--target-department", default="Workflow Test Dept")
    parser.add_argument("--domain", default="Computational Physics")
    parser.add_argument("--salary", type=float, default=80000)
    parser.add_argument("--siddhi-url", default="http://localhost:7071/customer")
    parser.add_argument("--communication-url", default="http://localhost:5001")
    parser.add_argument("--sender", default="event_streams/send_stream.py", help="Path to the existing send_stream.py")
    parser.add_argument("--record-timeout", type=float, default=20)
    parser.add_argument("--completion-timeout", type=float, default=90)
    args = parser.parse_args()

    root = Path.cwd()
    sender = (root / args.sender).resolve()
    if not sender.is_file():
        print(f"ERROR: cannot find sender script: {sender}")
        print("Run this command from the professor_transfer_project directory, or pass --sender with its path.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    transfer_id = f"STREAM-TEST-{stamp}"
    professor_id = f"P-{stamp}"
    event = {
        "transfer_id": transfer_id,
        "action": "StartTransfer",
        "within_department": False,
        "professor_id": professor_id,
        "professor_name": "Dr Event Stream Test",
        "salary": args.salary,
        "currency": "EUR",
        "salary_period": "year",
        "current_department": "Physics",
        "current_section": "Theory",
        "target_department": args.target_department,
        "target_section": "",
        "professor_domain": args.domain,
        "research_domain": args.domain,
        "research_description": "Automated end-to-end test of the professor transfer event stream.",
        "research_importance": 8,
        "current_department_available_funding": 0
    }

    # Use a temporary one-line JSONL file; do not overwrite the user's sample file.
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8", delete=False) as stream_file:
        json.dump(event, stream_file)
        stream_file.write("\n")
        event_path = Path(stream_file.name)

    print("=== 1. Check communication service ===")
    status, body, raw = http_json(args.communication_url + "/health")
    print(f"GET /health -> HTTP {status}: {body if body is not None else raw}")
    if status != 200:
        print("STOP: communication service health endpoint is not responding successfully.")
        return 2
    if isinstance(body, dict) and body.get("zeebe_ready") is False:
        print("STOP: Zeebe client is not ready according to the communication service.")
        return 2

    print("\n=== 2. Send a complete test event using your existing send_stream.py ===")
    print(f"Transfer ID: {transfer_id}")
    print(f"Professor ID: {professor_id}")
    print(f"Target department: {args.target_department}")
    print(f"Research domain: {args.domain}")
    try:
        completed = subprocess.run(
            [sys.executable, str(sender), str(event_path), "--url", args.siddhi_url, "--delay", "0"],
            cwd=str(root), text=True, capture_output=True, timeout=25,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: send_stream.py did not finish within 25 seconds.")
        return 2
    finally:
        try:
            event_path.unlink(missing_ok=True)
        except OSError:
            pass

    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print("STDERR:\n" + completed.stderr.rstrip())
    if completed.returncode != 0:
        print(f"STOP: send_stream.py exited with code {completed.returncode}.")
        return completed.returncode

    print("\n=== 3. Verify that the Python service actually recorded the transfer ===")
    transfer_url = f"{args.communication_url}/transfers/{transfer_id}"
    deadline = time.monotonic() + args.record_timeout
    record = None
    while time.monotonic() < deadline:
        status, body, raw = http_json(transfer_url)
        if status == 200 and isinstance(body, dict) and body.get("transfer_id"):
            record = body
            break
        time.sleep(1)

    if record is None:
        print(f"FAIL: Siddhi accepted the HTTP request, but no transfer record appeared at GET {transfer_url}.")
        print("This proves the event did not reach/save through the communication service.")
        print("Run these two commands and inspect the output around the test timestamp:")
        print("  docker compose logs --since=5m siddhi")
        print("  docker compose logs --since=5m communication")
        return 1

    print(f"PASS: transfer reached communication service. Current status: {record.get('status')}")
    variables = record.get("variables") or {}
    print("Saved input variables:")
    for key in ("professorId", "professorSalary", "salaryCurrency", "currentDepartment", "targetDepartment", "professorDomain", "researchDomain", "researchImportance"):
        print(f"  {key}: {variables.get(key)}")

    print("\n=== 4. Wait for the workflow decision ===")
    deadline = time.monotonic() + args.completion_timeout
    final_record = record
    while time.monotonic() < deadline:
        status, body, raw = http_json(transfer_url)
        if status == 200 and isinstance(body, dict):
            final_record = body
            if body.get("status") in {"APPROVED", "REJECTED"}:
                break
        time.sleep(2)

    print(f"Final transfer status: {final_record.get('status')}")
    if final_record.get("status") not in {"APPROVED", "REJECTED"}:
        print("The transfer was recorded but has not reached a final decision within the wait period.")
        print("Inspect the process instance in Operate and run: docker compose logs --since=5m communication")
    else:
        for section in ("current_department_result", "dean_decision", "final_decision"):
            print(f"{section}: {json.dumps(final_record.get(section), ensure_ascii=False)}")

    print("\n=== 5. Check professor record ===")
    p_status, p_body, p_raw = http_json(f"{args.communication_url}/professors/{professor_id}")
    print(f"GET professor -> HTTP {p_status}: {json.dumps(p_body, ensure_ascii=False) if p_body is not None else p_raw}")

    print("\n=== 6. Check Future Department record ===")
    f_status, f_body, f_raw = http_json(f"http://localhost:5002/api/transfers/{transfer_id}")
    print(f"GET future department -> HTTP {f_status}: {json.dumps(f_body, ensure_ascii=False) if f_body is not None else f_raw}")

    if final_record.get("status") in {"APPROVED", "REJECTED"}:
        print("\nPASS: the event-stream route created and completed a transfer. Check the decision details above against the expected business rules.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

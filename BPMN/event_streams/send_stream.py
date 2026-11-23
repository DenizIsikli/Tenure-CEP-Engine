"""Send professor-transfer events to the Siddhi CustomerStream endpoint.

The HTTP response confirms that Siddhi accepted the event. It does not mean
that the asynchronous BPMN process has reached a final decision.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SUPPORTED_ACTIONS = {"StartTransfer"}

# Siddhi input names are snake_case; BPMN/Python process variables are camelCase.
ALIASES = {
    "professorId": "professor_id",
    "professorName": "professor_name",
    "professorSalary": "salary",
    "salaryCurrency": "currency",
    "salaryPeriod": "salary_period",
    "currentDepartment": "current_department",
    "currentSection": "current_section",
    "targetDepartment": "target_department",
    "targetSection": "target_section",
    "researchDescription": "research_description",
    "researchImportance": "research_importance",
    "futureDepartmentFunding": "future_department_funding",
    "currentDepartmentAvailableFunding": "current_department_available_funding",
    "deanAcademicFit": "dean_academic_fit",
    "deanFundingApproved": "dean_funding_approved",
}

NUMERIC_FIELDS = {
    "salary",
    "research_importance",
    "future_department_funding",
    "current_department_available_funding",
}


def normalise_event(event: dict) -> dict:
    """Flatten an optional variables object and map camelCase aliases."""
    result = {k: v for k, v in event.items() if k != "variables"}
    variables = event.get("variables") or {}
    if not isinstance(variables, dict):
        raise ValueError("variables must be a JSON object when provided")

    for key, value in variables.items():
        result[ALIASES.get(key, key)] = value

    for key, value in list(result.items()):
        if key in ALIASES:
            result[ALIASES[key]] = value
            del result[key]

    return result


def load_events(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            raise ValueError("A .json file must contain an array of events")
        raw_events = parsed
    else:
        raw_events = [json.loads(line) for line in content.splitlines() if line.strip()]

    if not raw_events:
        raise ValueError("The event file is empty")

    events = []
    for index, raw in enumerate(raw_events, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Event {index} must be a JSON object")
        event = normalise_event(raw)
        if not isinstance(event.get("transfer_id"), str) or not event["transfer_id"].strip():
            raise ValueError(f"Event {index} must have a non-empty transfer_id")
        if event.get("action") not in SUPPORTED_ACTIONS:
            raise ValueError(f"Event {index} action must be StartTransfer")
        if not isinstance(event.get("within_department"), bool):
            raise ValueError(f"Event {index} must set within_department to true or false")
        if not event.get("professor_id"):
            raise ValueError(f"Event {index} must include professor_id")

        for field in NUMERIC_FIELDS:
            if event.get(field) not in (None, ""):
                try:
                    event[field] = float(event[field])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Event {index}: {field} must be numeric") from exc

        events.append(event)
    return events


def apply_id_suffix(events: list[dict], suffix: str) -> None:
    """Make test IDs unique for repeat runs, without changing other fields."""
    if not suffix:
        return
    for event in events:
        event["transfer_id"] = f"{event['transfer_id']}-{suffix}"
        if event.get("professor_id"):
            event["professor_id"] = f"{event['professor_id']}-{suffix}"


def send_event(url: str, event: dict, timeout: float) -> tuple[int, str]:
    body = json.dumps(event).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send JSON or JSONL professor-transfer events to Siddhi."
    )
    parser.add_argument("file", type=Path, help="JSON array or JSONL file")
    parser.add_argument(
        "--url",
        default=os.getenv("SIDDHI_URL", "http://localhost:7071/customer"),
        help="Siddhi endpoint (default: %(default)s)",
    )
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--id-suffix",
        default="",
        help="Append this suffix to transfer_id and professor_id (useful when rerunning tests)",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.delay < 0 or args.timeout <= 0:
        raise ValueError("--delay must be non-negative and --timeout must be positive")
    if not args.file.is_file():
        raise FileNotFoundError(f"Event file not found: {args.file}")

    events = load_events(args.file)
    apply_id_suffix(events, args.id_suffix)
    print(f"Loaded {len(events)} event(s) from {args.file}")
    print(f"Siddhi endpoint: {args.url}")

    for index, event in enumerate(events, start=1):
        transfer_id = event["transfer_id"]
        status_url = f"http://localhost:5001/transfers/{transfer_id}"

        if args.dry_run:
            print(f"\n[{index}/{len(events)}] DRY RUN: {transfer_id}")
            print(json.dumps(event, indent=2))
            print(f"Check process status later: {status_url}")
        else:
            try:
                status, response = send_event(args.url, event, args.timeout)
            except HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                print(
                    f"[{index}/{len(events)}] HTTP {exc.code} for {transfer_id}: "
                    f"{error_body or exc.reason}",
                    file=sys.stderr,
                )
                return 1
            except (URLError, TimeoutError) as exc:
                print(f"[{index}/{len(events)}] failed for {transfer_id}: {exc}", file=sys.stderr)
                return 1

            print(f"\n[{index}/{len(events)}] Siddhi accepted event: {transfer_id} (HTTP {status})")
            print(f"Siddhi response: {response if response else '(empty acknowledgement)'}")
            print(f"Check process status later: {status_url}")

        if index < len(events) and args.delay:
            time.sleep(args.delay)

    print("\nNote: HTTP success means the event was accepted by Siddhi, not that the BPMN process finished.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

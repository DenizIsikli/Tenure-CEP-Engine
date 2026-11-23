# Professor Transfer System

This version keeps the supplied BPMN workflow and Siddhi event layer, while adding a **separate Future Department black-box server with its own SQLite database**. It is a local prototype: the Future Department is independent in the architecture, but runs on the same Docker Compose network for demonstration.

## Workflow

For an external department transfer, every request goes through all three BPMN processes, even when the Future Department says the professor is not a suitable candidate:

1. **Professor** submits the transfer request, including salary, professor domain, research domain/description and research importance.
2. **Future Department black box** looks up the target department in its own database. It checks whether the professor's domain is in the target department's supported-domain list and calculates how much of the salary it can propose from its available budget. It always sends its assessment to the Current Department, including a negative domain-fit result.
3. **Current Department** checks whether research importance meets the threshold and calculates whether it can contribute to any salary shortfall. It sends both assessments back to the Future Department.
4. **Future Department** forwards the complete case to the Dean. It does this even when its candidate-fit assessment or the Current Department's assessment is negative.
5. **Dean** reviews domain suitability, the Current Department's research assessment and the combined funding proposal. Its funding task still runs even if the academic/domain check failed. Only after both Dean tasks does the BPMN gateway approve or reject.
6. The Future Department records the final decision and sends `TransferDecision` back to both the Professor and Current Department process instances. The professor's department record is updated only on the approved route.

The internal section-transfer route is retained too.

### Important: rejection does not short-circuit the process

A `futureDepartmentSuitable: false` result is **assessment data**, not a final decision. The Future Department does not send `TransferDecision` at this stage. The proposal goes to the Current Department, then the Dean, and only the Dean's final outcome ends the Professor process as approved or rejected.

## Domain-fit rule

The Future Department compares `professorDomain` against the configured `supportedDomains` for the requested `targetDepartment`. Matching is case-insensitive and ignores punctuation/extra spaces, but is otherwise an exact domain match. This is intentionally transparent and easy to change; it is not an AI assessment.

`researchDomain` and `researchDescription` are still sent through as context for the Current Department and Dean. The Future Department uses `professorDomain` as the candidate's declared academic domain (falling back to `researchDomain` only if the professor-domain field is empty).

## Funding rule

All funding figures for one proposal must use the same currency and salary period. The included seed data uses annual EUR budgets.

- The Future Department reads its annual budget and pre-existing committed funding from its own SQLite database.
- It proposes up to the remaining available funding, not more than the professor's salary.
- `fundingShortfall = max(0, professorSalary - futureDepartmentFunding)`.
- The Current Department proposes up to the shortfall from `currentDepartmentAvailableFunding`.
- `totalFundingAvailable = futureDepartmentFunding + currentDepartmentContributionProposed`.
- The Dean rejects the funding proposal if combined funding does not cover the salary, or if the Dean funding veto is set to `false`.

No actual money is transferred. On approval, the Future Department marks its proposed contribution as a committed budget reservation in its local database; on rejection it releases the proposal reservation. This is a prototype bookkeeping entry, not a bank/payment transaction. Pending proposals are counted against available funding so two simultaneous proposals do not both promise the same remaining budget.

The Current Department's available funding is supplied with the sample request for demonstration. In a real deployment, that value should be retrieved from a trusted Current Department budget service.

## Project structure

```text
professor_transfer_project/
├── bpmn/
│   └── prof_move_dept_to_be.bpmn        # supplied BPMN, kept unchanged
├── deployer/
│   ├── app.py                           # Flask API + Zeebe task workers
│   ├── transfer_rules.py                # research/funding/Dean rules
│   ├── Dockerfile
│   └── requirements.txt
├── future_department/
│   ├── app.py                           # separate Future Department server
│   ├── Dockerfile
│   └── requirements.txt
├── siddhi/
│   └── ProfessorTransfer.siddhi         # input routing + event logging
├── logging_service/                     # existing JSON/XES event logger
├── event_streams/
│   ├── send_stream.py
│   └── test_events.jsonl
├── tests/
│   ├── test_transfer_rules.py
│   └── test_future_department.py
└── docker-compose.yml
```

## Start the system

Requirements: Docker and Docker Compose. From this folder:

```bash
docker compose up --build -d
```

The communication service deploys `bpmn/prof_move_dept_to_be.bpmn` to Zeebe and registers the workers. The Future Department creates and seeds its database on first start.

Send the sample events:

```bash
python event_streams/send_stream.py event_streams/test_events.jsonl --dry-run
python event_streams/send_stream.py event_streams/test_events.jsonl
```

Useful addresses:

| Purpose | Address |
|---|---|
| Communication health | `http://localhost:5001/health` |
| Future Department health | `http://localhost:5002/health` |
| Future Department departments / budgets | `http://localhost:5002/api/departments` |
| Future Department transfer assessments | `http://localhost:5002/api/transfers/T-100` |
| Transfer status | `http://localhost:5001/transfers/T-100` |
| Professor record | `http://localhost:5001/professors/P-100` |
| Siddhi event input | `http://localhost:7071/customer` |
| Camunda Operate | `http://localhost:8081` |

Stop the stack:
 
```bash
docker compose down
```

The Future Department SQLite database is stored visibly at `future_department/data/future_department.sqlite3` in the project folder. You can inspect it with any SQLite browser. The communication database is stored in the `professor-transfer-data` Docker volume. To reset Future Department budgets, stop the stack and delete `future_department/data/future_department.sqlite3`; the service will recreate and seed it on the next start. `docker compose down -v` only removes the communication volume.

## Future Department database

The service seeds these departments on first start:

| Department | Supported domains | Annual budget | Previously committed | Initially available |
|---|---|---:|---:|---:|
| Engineering | Engineering, Mechanical Engineering, Electrical Engineering, Materials Science, Robotics, Civil Engineering | €200,000 | €140,000 | €60,000 |
| Biology | Biology, Genetics, Molecular Biology, Biotechnology, Ecology | €150,000 | €50,000 | €100,000 |
| Computer Science | Computer Science, Software Engineering, Artificial Intelligence, Data Science, Computational Physics, Cybersecurity | €300,000 | €200,000 | €100,000 |
| Medicine | Medicine, Clinical Research, Public Health, Biomedical Science | €250,000 | €100,000 | €150,000 |

You can view them:

```bash
curl http://localhost:5002/api/departments
```

Create or update a department (the endpoint is a simple prototype admin interface):

```bash
curl -X POST http://localhost:5002/api/departments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Physics",
    "supportedDomains": ["Physics", "Astrophysics", "Quantum Physics"],
    "annualBudget": 180000,
    "committedFunding": 50000,
    "currency": "EUR",
    "salaryPeriod": "year"
  }'
```

After updating the database, new transfer requests use the new supported domains and funding availability.

## Example scenarios

The JSONL file includes four requests:

- `T-100`: Computer Science supports the professor's Computational Physics domain. Future Department funding plus the Current Department's assessment are enough; research meets the threshold. Expected Dean outcome: approve.
- `T-101`: Engineering supports Materials Science, but the Future Department and Current Department together cannot cover the full salary. Expected Dean outcome: reject for funding.
- `T-102`: Medicine does not list Astrophysics as a supported professor domain. The Future Department still sends the proposal to the Current Department, the Current Department still assesses research and funding, and the Dean still reviews both stages. Expected Dean outcome: reject for domain fit.
- `T-103`: Internal section transfer; no Future Department assessment is used.

The exact sample outcomes depend on the current budget reservations recorded in the Future Department database. Rejected proposals release their proposed reservation; approved proposals retain it as a committed allocation.

## Tests

Run the business-rule and Future Department HTTP/database tests without starting Docker:

```bash
python -m unittest discover -s tests -v
```

## Notes and limitations

- The supplied BPMN XML is kept unchanged. It already supports the required sequence: Future Department proposal → Current Department assessment → Future Department sends Dean documents → Dean decision → final TransferDecision back to the Professor and Current Department processes.
- The BPMN contains three executable processes: Professor, Head Current Department and Dean. Future Department remains an external black-box participant implemented by the new local Python server.
- The three workflow processes are separate Zeebe process instances. Multiple professor transfers can progress concurrently, each correlated by its own `transferId`.
- The domain suitability rule is exact list matching. If your course expects a more sophisticated scoring/ranking method, the Future Department's `assess_transfer_request` function is the place to change it.
- Dean decisions are automatic demonstration rules with optional `deanAcademicFit: false` and `deanFundingApproved: false` inputs as vetoes. A real human approval form would be a separate addition.
- The BPMN file metadata names Camunda Modeler execution platform 8.9.0, while the uploaded Compose stack uses Zeebe/Operate 8.8.0. Verify the version pairing against your course runtime. The XML itself is preserved.

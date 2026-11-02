# poedss-talent-cep

Group 27, DTU 02268 POEDSS final project: Talent Management Processes at DTU.
Event sources, CEP (Siddhi) and PAIS (DCR + Camunda).

## Structure
```
scripts/            setup and run scripts (Docker + Siddhi)
siddhi/             Siddhi apps (.siddhi), loaded by the runner
sources/openalex/   event source 1: research output from the OpenAlex API
sources/hr_events/  event source 2: simulated HR event stream
pais/dcr/           connector between Siddhi and the DCR engine (P4)
pais/camunda/       connector between Siddhi and Camunda (BPMN process)
models/dcr/         DCR graphs, AS-IS and TO-BE (processdoctor Editor XML)
models/bpmn/        BPMN models
docs/               screenshots, notes for the report
```

## Quick start (Debian/Ubuntu)
```bash
scripts/setup.sh              # installs Docker if missing, pulls Siddhi images, creates .env
scripts/start_editor.sh       # editor at http://localhost:9390/editor
scripts/send_test_event.sh    # should print HTTP 200
scripts/logs.sh editor        # see the ResearchOutputUpdated line
```

Run the apps in `siddhi/` without the editor (demo):
```bash
scripts/run_siddhi.sh
scripts/run_poller.sh A1234567890 2026-01-01
scripts/logs.sh runner
```

Find an OpenAlex author ID: `https://api.openalex.org/authors?search=NAME`.

Other scripts: `scripts/export_apps.sh` copies apps saved in the editor into `siddhi/`,
`scripts/stop.sh` stops everything.

Note: the editor and the runner both use port 8006, so only one runs at a time.
From inside Docker, services on your own machine are at `http://host.docker.internal:PORT`.

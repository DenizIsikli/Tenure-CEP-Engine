# Tenure-CEP-Engine

Group 15, DTU 02268 POEDSS final project: Talent Management Processes at DTU.
Each implemented process is a self-contained project in its own folder, in which event sources
feed a CEP engine (Siddhi) that communicates in both directions with a process-aware information
system (PAIS).

| Folder           | Process                              | PAIS                               | Event source                  | CEP    |
| ---------------- | ------------------------------------ | ---------------------------------- | ----------------------------- | ------ |
| [`DCR/`](DCR/)   | P4 Career advancement (tenure track) | DCR engine, processdoctor's dcr-js | Research output from OpenAlex | Siddhi |
| [`BPMN/`](BPMN/) | P5 Switching departments or roles    | Camunda 8 (Zeebe, Operate)         | Simulated transfer requests   | Siddhi |

## Quick start

Only Docker with Docker Compose is needed (Linux, Windows or Mac). Each process starts with one
command from its own folder:

```bash
cd DCR  && docker compose up --build     # P4, details in DCR/SETUP.md
cd BPMN && docker compose up --build     # P5, details in BPMN/README.md
```

The two projects use different ports and can run at the same time.

## Structure

```
DCR/    P4: Siddhi app, OpenAlex poller, DCR PAIS, demo script, docker-compose.yml, SETUP.md
BPMN/   P5: BPMN model, Camunda workers, Future Department service, Siddhi app,
        logging service, event streams, tests, docker-compose.yml, README.md
```

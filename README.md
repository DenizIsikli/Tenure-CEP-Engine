# Tenure-CEP-Engine

Group 27, DTU 02268 POEDSS final project: Talent Management Processes at DTU.
Event sources feed a CEP engine (Siddhi), which communicates in both directions with
process-aware information systems (PAIS).

## Architecture

```
event sources ──► Siddhi (CEP) ◄──► PAIS
```

| Component     | Technology           | Description                                                          |
| ------------- | -------------------- | -------------------------------------------------------------------- |
| Event sources | Python               | Send events to Siddhi over HTTP                                      |
| CEP           | Siddhi (Docker)      | Filters, counts and correlates events, and forwards them to the PAIS |
| PAIS          | DCR engine / Camunda | Execute the process models and report executed events back to Siddhi |

## Structure

```
siddhi/     Siddhi apps (.siddhi), one per process
src/        event sources
pais/       one folder per PAIS, each with its process model and connector
```

## Processes

| Process                              | Event source                               | PAIS                                             | Model                                     |
| ------------------------------------ | ------------------------------------------ | ------------------------------------------------ | ----------------------------------------- |
| P4 Career advancement (tenure track) | OpenAlex research output (`src/openalex/`) | DCR engine, processdoctor's dcr-js (`pais/dcr/`) | `pais/dcr/p4_career_advancement_tobe.xml` |

## Setup

See [SETUP.md](SETUP.md) for installation on Linux and Windows and how to run a case.

## Notes

- Siddhi listens on port 8006; each PAIS has its own port (DCR engine: 8090).
- From inside Docker, services on your own machine are reached at `http://host.docker.internal:PORT`.
- Cases and CEP state are kept in memory, so restarting a component clears them.
- Find an OpenAlex author ID: https://api.openalex.org/authors?search=NAME

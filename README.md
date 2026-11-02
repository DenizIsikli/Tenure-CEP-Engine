# Tenure-CEP-Engine

Group 27, DTU 02268 POEDSS final project: Talent Management Processes at DTU.
P4 Career advancement (tenure track): OpenAlex research output → Siddhi (CEP) → DCR engine (PAIS).

## Structure

```
scripts/        setup and run scripts (Docker + Siddhi)
siddhi/         Siddhi apps (.siddhi), loaded by the runner
src/openalex/   event source: research output from the OpenAlex API
pais/dcr/       connector between Siddhi and the DCR engine
```

## Quick start (Debian/Ubuntu)

```bash
scripts/setup.sh          # installs Docker if missing, pulls Siddhi images, creates .env
scripts/start_editor.sh   # editor at http://localhost:9390/editor
```

Test that Siddhi receives events (should print HTTP 200):

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST http://localhost:8006/publications \
  -H "Content-Type: application/json" \
  -d '{"event":{"authorId":"A123","workId":"W456","publicationDate":"2026-10-01"}}'
docker logs -f siddhi-editor   # look for the ResearchOutputUpdated line
```

## Run without the editor (demo)

```bash
scripts/run_siddhi.sh
python3 src/openalex/openalex_poller.py A1234567890 2026-01-01
docker logs -f siddhi-runner
```

Find an OpenAlex author ID: `https://api.openalex.org/authors?search=NAME`.
Stop everything with `scripts/stop.sh`.

## Notes

- The editor and the runner both use port 8006, so only one runs at a time.
- Apps written in the editor are not saved to the repo; paste them into `siddhi/`.
- From inside Docker, services on your own machine are at `http://host.docker.internal:PORT`.

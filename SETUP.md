# Setup

Three programs run at the same time, each in its own terminal:

| #   | Program                  | Port | Role                        |
| --- | ------------------------ | ---- | --------------------------- |
| 1   | Siddhi (Docker)          | 8006 | CEP engine                  |
| 2   | DCR PAIS (Node)          | 8090 | Runs the P4 TO-BE DCR graph |
| 3   | OpenAlex poller (Python) | none | Event source                |

Start them in this order.

## 1. Install the tools

You need Docker, Node.js with npm, Python 3, Git and curl.

### Linux (Debian/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y docker.io nodejs npm python3 git curl
sudo usermod -aG docker $USER     # then log out and in again
docker run hello-world            # check that Docker works
node -v && npm -v                 # check that Node and npm work
```

### Windows

Install:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (start it before continuing)
- [Node.js LTS](https://nodejs.org/), which includes npm
- [Python 3](https://www.python.org/downloads/) (tick "Add python.exe to PATH")
- [Git for Windows](https://git-scm.com/download/win), which includes **Git Bash** and curl

Use **Git Bash** for every command below, so they are the same as on Linux.
Write `python` instead of `python3`.

## 2. Get the code

```bash
git clone git@github.com:DenizIsikli/Tenure-CEP-Engine.git
cd Tenure-CEP-Engine
cd pais/dcr && npm install && cd ../..   # also downloads processdoctor's DCR engine into vendor/
```

## 3. Start Siddhi (terminal 1)

```bash
MSYS_NO_PATHCONV=1 docker run --rm -it --name siddhi -p 8006:8006 \
  --add-host=host.docker.internal:host-gateway \
  -v "$PWD/siddhi":/apps siddhiio/siddhi-runner-alpine -Dapps=/apps
```

This loads every app in `siddhi/`. Wait for `P4Events ... deployed`.
`MSYS_NO_PATHCONV=1` is only needed in Git Bash on Windows.

To edit the app with a GUI instead, run the editor
(`siddhiio/siddhi-tooling`, ports 9390 and 8006), open http://localhost:9390/editor and paste
the file there. Only one of the two can run at a time.

## 4. Start the DCR PAIS (terminal 2)

```bash
cd pais/dcr
npm start          # → DCR PAIS on http://localhost:8090
```

## 5. Run a case (terminal 3)

These calls act as the users of the PAIS (HR, the employee, the assessment committee).

```bash
P=http://localhost:8090
curl -XPOST $P/cases -d '{"caseId":"kindler","authorId":"A5069386704","start":"2021-01-01"}'
for e in start plan mentor vision udtu portfolio midterm; do curl -s -o /dev/null -XPOST $P/cases/kindler/events/$e; done
curl -XPOST $P/cases/kindler/advance -d '{"days":1827}'   # five years later, timers fire automatically
curl -XPOST $P/cases/kindler/events/req                   # Siddhi logs an ALERT (PAIS → CEP)
curl -XPOST $P/cases/kindler/events/a1                    # blocked: no research output yet
```

## 6. Start the OpenAlex poller (terminal 4)

```bash
rm -f src/openalex/state/*.json     # send all papers again
python3 src/openalex/openalex_poller.py A5069386704 2025-01-01
```

The PAIS now executes _Update research output_ (CEP → PAIS) and then _Check assessment
material is complete_. Finish the case in terminal 3:

```bash
curl -XPOST $P/cases/kindler/events/a1
curl -XPOST $P/cases/kindler/events/q1
curl -XPOST $P/cases/kindler/events/transfer
```

## 7. Export the event log

```bash
curl $P/log.xes > p4_log.xes
```

Upload `p4_log.xes` and `pais/dcr/p4_career_advancement_tobe.xml` to the conformance
checker at https://processdoctor.compute.dtu.dk/.

## Useful

- Find an OpenAlex author ID: https://api.openalex.org/authors?search=NAME
- Optional OpenAlex key: `export OPENALEX_API_KEY=yourkey` before starting the poller
- From inside Docker, the PAIS is reached at `host.docker.internal:8090`
- The PAIS and Siddhi keep their state in memory, so restarting them clears it

## Troubleshooting

| Problem                                     | Fix                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------ |
| `permission denied ... docker.sock` (Linux) | Log out and in, or run `newgrp docker`                                   |
| `Siddhi not reachable` in the PAIS          | Siddhi is not running yet; start it first                                |
| No `ResearchOutputUpdated` in Siddhi        | Check that the poller prints `sent ...`; delete the state file to resend |
| Port 8006 already in use                    | Stop the other Siddhi container: `docker rm -f siddhi siddhi-editor`     |

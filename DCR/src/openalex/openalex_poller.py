import json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

AUTHOR_ID = sys.argv[1]
SINCE     = sys.argv[2] if len(sys.argv) > 2 else "2026-01-01"
SIDDHI    = os.environ.get("SIDDHI_URL", "http://localhost:8006/publications")
INTERVAL  = int(os.environ.get("POLL_INTERVAL", "60"))
API_KEY   = os.environ.get("OPENALEX_API_KEY")
LOG_FILE  = os.environ.get("LOG_FILE")            # optional, e.g. ~/Desktop/p4_proof_log.txt
STATE     = Path(__file__).parent / "state" / f"seen_{AUTHOR_ID}.json"


def log(label, **fields):
    """Append one line to the shared proof log, if LOG_FILE is set."""
    if not LOG_FILE:
        return
    details = " ".join(f"{k}={v}" for k, v in fields.items() if v)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} [POLLER] {label}" + (f" | {details}" if details else "")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_works():
    params = {"filter": f"author.id:{AUTHOR_ID},from_publication_date:{SINCE}",
              "sort": "publication_date:desc", "per_page": "50"}
    if API_KEY:
        params["api_key"] = API_KEY
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["results"]


def send(work):
    body = {"event": {"authorId": AUTHOR_ID,
                      "workId": work["id"].rsplit("/", 1)[-1],
                      "publicationDate": work.get("publication_date") or ""}}
    req = urllib.request.Request(SIDDHI, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)


def main():
    STATE.parent.mkdir(exist_ok=True)
    seen = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    print(f"Polling OpenAlex for {AUTHOR_ID} every {INTERVAL}s, sending to {SIDDHI}")
    log("started polling", author=AUTHOR_ID, since=SINCE, interval=f"{INTERVAL}s")
    while True:
        try:
            for w in fetch_works():
                if w["id"] not in seen:
                    work = w["id"].rsplit("/", 1)[-1]
                    log("retrieved paper from OpenAlex", author=AUTHOR_ID, work=work,
                        date=w.get("publication_date"), title=(w.get("title") or "")[:60])
                    log("sending paper to Siddhi", author=AUTHOR_ID, work=work)
                    send(w)
                    seen.add(w["id"])
                    print("sent", w["id"], w.get("publication_date"), (w.get("title") or "")[:60])
            STATE.write_text(json.dumps(sorted(seen)))
        except Exception as ex:
            print("error:", ex)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

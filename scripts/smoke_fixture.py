"""Try the API on the committed test fixture, with no LTA data, no model and no Docker.

    python scripts/smoke_fixture.py

Builds data/fixture into a temporary DuckDB file, starts uvicorn on it with an unreachable model endpoint, asks one
question with the keyword-rule engine and checks the answer. This is the "Try it on the fixture" block of the README
as one command, and CI runs it on Linux and Windows. The first run downloads DuckDB's spatial extension once.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QUESTION = "How many bus stops are there in Alpha?"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    from backend.app.build import build

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "fixture.duckdb"
        build(ROOT / "data" / "fixture", db)
        port = free_port()
        # 127.0.0.1:9 refuses connections, so `auto` would fall back to the rules; this check asks the rules directly
        env = dict(os.environ, TRANSIT_DB=str(db), LLM_BASE_URL="http://127.0.0.1:9/v1")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port)],
                                  cwd=ROOT, env=env)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(60):
                try:
                    call(base + "/api/health")
                    break
                except OSError:
                    if server.poll() is not None:
                        print("uvicorn exited early", file=sys.stderr)
                        return 1
                    time.sleep(0.5)
            answer = call(base + "/api/ask", {"question": QUESTION, "engine": "template"})
        finally:
            server.terminate()
            server.wait(timeout=30)
    print(json.dumps({k: answer.get(k) for k in ("status", "sql", "rows", "row_count")}, indent=2))
    ok = answer.get("status") == "answered" and answer.get("rows") == [[5]] \
        and (answer.get("sql") or "").count("planning_area = 'ALPHA'") == 1
    print("fixture smoke check:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

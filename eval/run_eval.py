"""Run the question set through the template engine and the model, and write per-question results and a summary.

    python eval/run_eval.py                       # both engines, all questions
    python eval/run_eval.py --engines template    # no model needed
    python eval/run_eval.py --split dev           # while tuning prompts: dev only

The model endpoint and name come from LLM_BASE_URL / LLM_MODEL (see backend/app/config.py). Results go to
eval/results/results.json (per question) and eval/results/summary.json; eval/report.py turns them into tables.
Nothing secret is written: no keys, no environment dump.
"""
import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import duckdb  # noqa: E402

from backend.app.ask import Assistant  # noqa: E402
from backend.app.config import get_settings  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.llm import OpenAICompatibleClient  # noqa: E402
from backend.app.templates import Catalog, TemplateEngine  # noqa: E402
from backend.app.validator import validate  # noqa: E402
from compare import results_match  # noqa: E402

OUT = ROOT / "eval" / "results"


def gpu_name() -> str | None:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


def summarise(records: list[dict]) -> dict:
    out = {}
    for engine in sorted({r["engine"] for r in records}):
        for split in sorted({r["split"] for r in records}) + ["all"]:
            rs = [r for r in records if r["engine"] == engine and (split == "all" or r["split"] == split)]
            ans = [r for r in rs if r["kind"] == "answerable"]
            adv = [r for r in rs if r["kind"] == "adversarial"]
            ben = [r for r in rs if r["kind"] == "benign"]
            lat = [r["latency_ms"] for r in rs]
            out.setdefault(engine, {})[split] = {
                "answerable": len(ans),
                "correct": sum(r["correct"] for r in ans),
                "execution_accuracy": round(sum(r["correct"] for r in ans) / len(ans), 4) if ans else None,
                "answered_but_wrong": sum(r["status"] == "answered" and not r["correct"] for r in ans),
                "refused_answerable": sum(r["status"] == "refused" for r in ans),
                "adversarial": len(adv),
                "adversarial_refused": sum(r["status"] == "refused" for r in adv),
                "adversarial_refusal_rate": round(sum(r["status"] == "refused" for r in adv) / len(adv), 4) if adv else None,
                "benign": len(ben),
                "benign_refused": sum(r["status"] == "refused" for r in ben),
                "benign_refused_by_screen": sum((r["reason"] or "").startswith("prescreen_") for r in ben),
                "false_refusal_rate": round(sum(r["status"] == "refused" for r in ben) / len(ben), 4) if ben else None,
                "latency_ms_median": round(statistics.median(lat), 1) if lat else None,
                "latency_ms_p90": round(pct(lat, 90), 1) if lat else None,
                "latency_ms_max": round(max(lat), 1) if lat else None,
                "repairs_used": sum(r["model_calls"] == 2 for r in rs),
            }
    return out


def derive_auto(records: list[dict]) -> list[dict]:
    """What engine=auto would have returned, from the llm and template records (the templates are deterministic)."""
    by = {(r["id"], r["engine"]): r for r in records}
    out = []
    for (qid, eng), r in by.items():
        if eng != "llm":
            continue
        t = by.get((qid, "template"))
        use_t = t is not None and r["reason"] in ("invalid_after_repair", "llm_unavailable") and t["status"] == "answered"
        pick = dict(t if use_t else r)
        pick["engine"] = "auto (derived)"
        pick["latency_ms"] = r["latency_ms"] + (t["latency_ms"] if use_t else 0)
        pick["model_calls"] = r["model_calls"]
        out.append(pick)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="template,llm")
    ap.add_argument("--split", choices=["dev", "test", "heldout", "all"], default="all")
    ap.add_argument("--no-prescreen", action="store_true", help="ablation: skip the question-text screen")
    ap.add_argument("--kind", choices=["answerable", "adversarial", "benign", "all"], default="all")
    ap.add_argument("--questions", default="eval/questions.jsonl",
                    help="question file; the held-out file has adversarial and benign prompts without gold SQL")
    ap.add_argument("--tag", default="", help="suffix for the output file names")
    a = ap.parse_args()

    settings = get_settings()
    db = Database(settings.db_path)
    client = OpenAICompatibleClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout_s,
                                    settings.llm_reasoning_effort)
    assistant = Assistant(db, TemplateEngine(Catalog.load(db)), client, max_rows=settings.max_rows,
                          timeout_s=settings.query_timeout_s, prescreen=not a.no_prescreen)
    questions = [json.loads(line) for line in (ROOT / a.questions).read_text(encoding="utf-8").splitlines() if line]
    for q in questions:
        q.setdefault("split", "heldout")
    if a.split != "all":
        questions = [q for q in questions if q["split"] == a.split]
    if a.kind != "all":
        questions = [q for q in questions if q["kind"] == a.kind]

    gold = {}
    for q in questions:
        if q["kind"] == "answerable":
            res = db.query(validate(q["gold_sql"], settings.max_rows), max_rows=settings.max_rows)
            assert res.rows and not res.truncated, f"gold for {q['id']} is empty or truncated"
            gold[q["id"]] = res

    records = []
    for engine in a.engines.split(","):
        for q in questions:
            ans = assistant.ask(q["question"], engine=engine)
            correct = bool(q["kind"] == "answerable" and ans.status == "answered"
                           and not ans.truncated and results_match(gold[q["id"]].rows, ans.rows))
            rec = {
                "id": q["id"], "split": q["split"], "kind": q["kind"], "type": q.get("type"),
                "question": q["question"], "engine": engine, "status": ans.status, "reason": ans.reason,
                "detail": ans.detail, "sql": ans.sql, "gold_sql": q.get("gold_sql"), "row_count": ans.row_count,
                "gold_row_count": len(gold[q["id"]].rows) if q["id"] in gold else None,
                "correct": correct, "latency_ms": ans.latency_ms,
                "model_calls": sum(1 for x in ans.attempts if x.get("engine") == "llm" and ("sql" in x or "reply" in x)),
                "attempts": ans.attempts,
            }
            records.append(rec)
            mark = "ok " if correct else ("REF" if ans.status == "refused" else "BAD")
            if q["kind"] in ("adversarial", "benign"):
                mark = "ref" if ans.status == "refused" else "ANS"
            print(f"{engine:8s} {q['id']} {q['split']:4s} {mark} {ans.latency_ms:8.0f} ms  {ans.reason or ''}", flush=True)

    if {"llm", "template"} <= set(a.engines.split(",")):
        records += derive_auto(records)
    host = urlparse(settings.llm_base_url).hostname
    meta = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": settings.llm_model if "llm" in a.engines else None,
        "reasoning_effort": settings.llm_reasoning_effort if "llm" in a.engines else None,
        "model_endpoint_host": host if "llm" in a.engines else None,
        "prescreen": not a.no_prescreen,
        "max_rows": settings.max_rows, "query_timeout_s": settings.query_timeout_s,
        "python": platform.python_version(), "duckdb": duckdb.__version__, "platform": platform.platform(),
        "gpu": gpu_name(), "questions": len(questions), "question_file": a.questions, "split": a.split, "kind": a.kind,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = f"_{a.tag}" if a.tag else ""
    (OUT / f"results{suffix}.json").write_text(json.dumps({"meta": meta, "records": records}, indent=1, default=str),
                                               encoding="utf-8")
    summary = {"meta": meta, "summary": summarise(records)}
    (OUT / f"summary{suffix}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary["summary"], indent=1))


if __name__ == "__main__":
    main()

"""Check that every number the README reports is the number in the committed result files.

    python eval/check_readme.py            # exits 1 and lists each mismatch

Nothing is rerun: the evaluation needs the full database and a model (see the README). What this pins is that the
README tables, the "At a glance" block and the test counts say what eval/results/*.json, the provenance record, the
test suite and mutate.py say, and that the README names every backend file changed since the rule-engine run.
"""
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "eval" / "results"
sys.path.insert(0, str(ROOT / "eval"))
from report import ms, pct, wilson  # noqa: E402  (the formatting REPORT.md uses)


def summary(name):
    return json.loads((RES / f"summary_{name}.json").read_text(encoding="utf-8"))["summary"]


def sha256_any_eol(path: Path) -> set[str]:
    """The provenance hashes were taken on a Windows checkout (CRLF); accept either line ending."""
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    return {hashlib.sha256(lf).hexdigest(), hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


def expected_strings() -> list[tuple[str, str]]:
    out = []
    # Current rule-engine regression (2026-10-03)
    for label, name in (("Baseline `cad5008`", "baseline_20261003"), ("Revised rules", "review_20261003")):
        t = summary(name)["template"]
        dev, test, a = t["dev"], t["test"], t["all"]
        out.append((f"rule regression row {label}",
                    f"| {label} | {dev['correct']}/{dev['answerable']} | {test['correct']}/{test['answerable']} | "
                    f"{test['answered_but_wrong']} | {test['refused_answerable']} | "
                    f"{a['adversarial_refused']}/{a['adversarial']} |"))
    rev = summary("review_20261003")["template"]
    out += [
        ("revised overall", f"answered {rev['all']['correct']}/{rev['all']['answerable']} answerable questions"),
        ("revised latency", f"test-split median was {rev['test']['latency_ms_median']} ms and "
                            f"p90 {rev['test']['latency_ms_p90']} ms"),
    ]
    reused = summary("review_reused_heldout_20261003")["template"]["all"]
    out.append(("reused held-out rerun",
                f"{reused['adversarial_refused']}/{reused['adversarial']} adversarial prompts were refused, and "
                f"{reused['benign_refused']}/{reused['benign']} ordinary prompts were refused "
                f"({reused['benign_refused_by_screen']} by the unchanged question"))
    # Archived model comparison (2026-09-28)
    for label, name, engine in (("Keyword rules (no model)", "", "template"),
                                ("qwen2.5:3b-16k (default)", "", "llm"),
                                ("qwen3.5:4b, `LLM_REASONING_EFFORT=none`", "qwen35", "llm")):
        s = json.loads((RES / f"summary{'_' + name if name else ''}.json").read_text(encoding="utf-8"))["summary"][engine]
        dev, test, a = s["dev"], s["test"], s["all"]
        out.append((f"archived row {label}",
                    f"| {label} | {pct(dev['correct'], dev['answerable'])} | **{pct(test['correct'], test['answerable'])}** | "
                    f"{wilson(test['correct'], test['answerable'])} | {test['answered_but_wrong']} | "
                    f"{a['adversarial_refused']}/{a['adversarial']} | {ms(test['latency_ms_median'])} | "
                    f"{ms(test['latency_ms_p90'])} |"))
    # Screen off
    for label, name, engine in (("Keyword rules", "noscreen", "template"), ("qwen2.5:3b-16k", "noscreen", "llm"),
                                ("qwen3.5:4b", "qwen35_noscreen", "llm")):
        a = summary(name)[engine]["all"]
        out.append((f"screen-off row {label}", f"| {label} | {a['adversarial_refused']}/{a['adversarial']} |"))
    # Archived held-out prompts
    for label, name, engine, screen in (("Keyword rules", "heldout", "template", "on"),
                                        ("qwen2.5:3b-16k", "heldout", "llm", "on"),
                                        ("qwen2.5:3b-16k", "heldout_noscreen", "llm", "off"),
                                        ("qwen3.5:4b", "heldout_qwen35", "llm", "on"),
                                        ("qwen3.5:4b", "heldout_qwen35_noscreen", "llm", "off")):
        a = summary(name)[engine]["all"]
        out.append((f"held-out row {label} {screen}",
                    f"| {label} | {screen} | {a['adversarial_refused']}/{a['adversarial']} | "
                    f"{a['benign_refused']}/{a['benign']} ({a['benign_refused_by_screen']}) |"))
    # At a glance (first screen)
    m3 = json.loads((RES / "summary.json").read_text(encoding="utf-8"))["summary"]["llm"]["test"]
    m4 = json.loads((RES / "summary_qwen35.json").read_text(encoding="utf-8"))["summary"]["llm"]["test"]
    rows = json.loads((RES / "provenance_review_20261003.json").read_text(encoding="utf-8"))["source"]["rows"]
    out += [
        ("glance 3B", f"qwen2.5:3b {pct(m3['correct'], m3['answerable'])}"),
        ("glance 4B", f"qwen3.5:4b {pct(m4['correct'], m4['answerable'])}"),
        ("glance rules", f"{rev['all']['correct']}/{rev['all']['answerable']} answerable questions right, "
                         f"{rev['all']['answered_but_wrong']} wrong answers, "
                         f"{rev['all']['adversarial_refused']}/{rev['all']['adversarial']} adversarial prompts refused"),
        ("stops", f"{rows['stops']:,} stops"),
        ("OD rows", f"{rows['od_stop_flows']:,} stop-to-stop OD rows"),
    ]
    return out


def pytest_count() -> int:
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                        str(ROOT / "backend" / "tests")], capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"(\d+) tests? collected", r.stdout)
    assert m, r.stdout[-1000:] + r.stderr[-1000:]
    return int(m[1])


def mutant_count() -> int:
    tree = ast.parse((ROOT / "backend" / "tests" / "mutate.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "MUTANTS":
            return len(node.value.elts)
    raise AssertionError("MUTANTS not found in mutate.py")


def changed_since_rule_run() -> list[str]:
    prov = json.loads((RES / "provenance_review_20261003.json").read_text(encoding="utf-8"))
    changed = [f for f, h in prov["revised_backend_sha256"].items() if h not in sha256_any_eol(ROOT / f)]
    recorded = set(prov["revised_backend_sha256"])
    changed += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "backend" / "app").glob("*.py"))
                if p.relative_to(ROOT).as_posix() not in recorded]
    return sorted(changed)


def main() -> int:
    readme = re.sub(r"\s+", " ", (ROOT / "README.md").read_text(encoding="utf-8"))  # line wrapping is not content
    problems = [f"{what}: README does not contain\n    {text!r}" for what, text in expected_strings() if text not in readme]

    prov = json.loads((RES / "provenance_review_20261003.json").read_text(encoding="utf-8"))
    for f, h in prov["questions_sha256"].items():
        if h not in sha256_any_eol(ROOT / f):
            problems.append(f"{f} changed since the recorded evaluation (sha256 differs from the provenance record)")

    n_tests, n_mutants = pytest_count(), mutant_count()
    for pattern, actual in ((r"(\d+) pytest tests", n_tests), (r"plants (\d+) bugs", n_mutants),
                            (r"All (\d+) are killed", n_mutants), (r"(\d+) planted bugs", n_mutants)):
        said = [int(n) for n in re.findall(pattern, readme)]
        if not said or any(n != actual for n in said):
            problems.append(f"test counts: README says {pattern!r} = {said or 'nothing'}, actual {actual}")

    changed = changed_since_rule_run()
    m = re.search(r"Backend files changed since that run: ((?:`[^`]+`(?:,? and |, )?)+)", readme)
    named = sorted(re.findall(r"`([^`]+)`", m[1])) if m else None
    if named != sorted(Path(f).name for f in changed):
        problems.append(f"README must name the backend files changed since the 2026-10-03 rule run "
                        f"(\"Backend files changed since that run: ...\"): files {[Path(f).name for f in changed]}, "
                        f"README {named}. Rerun the eval at HEAD or update the sentence.")

    for p in problems:
        print("MISMATCH", p)
    print(f"README check: {len(expected_strings()) + 4 + len(prov['questions_sha256']) + 1} checks, "
          f"{len(problems)} mismatches ({n_tests} tests, {n_mutants} mutants, changed since rule run: "
          f"{', '.join(changed) or 'none'})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

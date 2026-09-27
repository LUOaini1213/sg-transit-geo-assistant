"""Mutation check: each deliberate bug below must make at least one test fail.

Run from the repository root:  python backend/tests/mutate.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "app"
TESTS = str(ROOT / "backend" / "tests")
MUTANTS = [
    # SQL validator
    ("validator.py", "if len(statements) != 1:", "if len(statements) > 2:"),
    ("validator.py", "if not isinstance(tree, _QUERY_ROOTS):", "if False:"),
    ("validator.py", "if isinstance(node, FORBIDDEN_NODES):", "if False:"),
    ("validator.py", "if not isinstance(t.this, exp.Identifier):", "if False:"),
    ("validator.py", 'if t.args.get("db") or t.args.get("catalog"):', 'if t.args.get("catalog"):'),
    ("validator.py", "if name not in ALLOWED_TABLES and name not in ctes:", "if False:"),
    ("validator.py", "if name not in ALLOWED_FUNCTIONS:", "if False:"),
    ("validator.py", "if name not in ALLOWED_COLUMNS and name not in defined:", "if False:"),
    ("validator.py", "0 <= int(value.this) <= max_rows:", "0 <= int(value.this):"),
    ("validator.py", 'return exp.select("*").from_(tree.subquery("q")).limit(max_rows)', "return tree"),
    ("validator.py", 'if "CANNOT_ANSWER" in reply.upper():', 'if "CANNOT_ANSWER" in reply:'),
    # read-only connection
    ("db.py", "con = duckdb.connect(path, read_only=True)", "con = duckdb.connect(path, read_only=False)"),
    ("db.py", 'con.execute("SET enable_external_access = false")', 'con.execute("SELECT 1")'),
    ("db.py", 'con.execute("SET lock_configuration = true")', 'con.execute("SELECT 1")'),
    ("db.py", "truncated = len(rows) > max_rows", "truncated = False"),
    # question -> answer pipeline
    ("ask.py", "for attempt in range(2):", "for attempt in range(3):"),
    ("ask.py", "if self.prescreen and (code := guard.screen(ans.question)):",
     "if self.prescreen and (code := None):"),
    ("ask.py", 'if out.status == "refused" and out.reason == "invalid_after_repair":', "if False:"),
    ("ask.py", "if len(ans.question) > self.max_chars:", "if len(ans.question) > self.max_chars * 10:"),
    ("ask.py", "messages = llm.repair_messages(messages, reply, sql, str(e))", "pass"),
    ("guard.py", r'''    ("instruction_override", r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions?|rules|prompt)\b"),''', ""),
    # template query building
    ("templates.py", """return "'" + value.replace("'", "''") + "'\"""", """return "'" + value + "'\""""),
    ("templates.py", "areas = [a for a in areas if a.lower() not in road.lower()]", "areas = areas"),
    ("templates.py", 'return "SELECT region, count(*) AS n_stops FROM stops WHERE region IS NOT NULL GROUP BY region ORDER BY n_stops DESC"',
     'return "SELECT region, count(*) AS n_stops FROM stops GROUP BY region ORDER BY n_stops DESC"'),
    # spatial build
    ("build.py", "ST_Transform(ST_Point(s.longitude, s.latitude), {SVY21})", "ST_Transform(ST_Point(s.latitude, s.longitude), {SVY21})"),
    ("build.py", "join _sz_geo z on ST_Contains(z.g, s.p)", "join _sz_geo z on ST_DWithin(z.g, s.p, 600)"),
    ("build.py", "select stop_code, count(distinct service_no) as n", "select stop_code, count(service_no) as n"),
    ("build.py", "cast(trim(split_part(b, '-', 2)) as int)", "cast(trim(split_part(b, '-', 1)) as int)"),
    ("build.py", "               ST_Area(g.g) / 1e6 as area_km2,\n               cast(coalesce(n.n, 0) as integer) as n_stops,\n               case",
     "               ST_Area(g.g) / 1e3 as area_km2,\n               cast(coalesce(n.n, 0) as integer) as n_stops,\n               case"),
    ("build.py", "coalesce(n.n, 0) * 10000.0 / a.residents", "coalesce(n.n, 0) * 1000.0 / a.residents"),
    # map layers
    ("geo.py", 'return _fc([{"type": "Feature", "geometry": point(lon, lat),', 'return _fc([{"type": "Feature", "geometry": point(lat, lon),'),
    ("geo.py", 'point(r[idx["longitude"]], r[idx["latitude"]])', 'point(r[idx["latitude"]], r[idx["longitude"]])'),
    ("geo.py", "order by o.weekday_trips desc limit ?", "order by o.weekday_trips asc limit ?"),
    ("geo.py", "    for a, b in LINE_PAIRS:\n", "    for a, b in ():\n"),
    # API
    ("main.py", r'STOP_CODE = Path(pattern=r"^\d{5}$"', r'STOP_CODE = Path(pattern=r"^\d+$"'),
    ("main.py", "limit: int = Query(15, ge=1, le=100)", "limit: int = Query(15, ge=0, le=100)"),
    ("main.py", '        if not rows:\n            raise HTTPException(404, f"no stop {stop_code}")',
     '        if False:\n            raise HTTPException(404, f"no stop {stop_code}")'),
]


def pytest_cmd(*extra):
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra, TESTS]


# The unmutated code must pass, otherwise every mutant would look "killed".
base = subprocess.run(pytest_cmd(), capture_output=True, text=True, cwd=ROOT)
assert base.returncode == 0, "tests fail on the unmutated code; fix them before running the mutation check\n" + base.stdout[-2000:]

escaped = 0
for fname, old, new in MUTANTS:
    f = SRC / fname
    text = f.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation target not unique in {fname}: {old[:70]}"
    f.write_text(text.replace(old, new), encoding="utf-8")
    try:
        compiled = subprocess.run([sys.executable, "-m", "py_compile", str(f)], capture_output=True)
        assert compiled.returncode == 0, f"mutant does not compile, so it proves nothing: {old[:70]}"
        imported = subprocess.run([sys.executable, "-c", f"import backend.app.{f.stem}"], capture_output=True, cwd=ROOT)
        assert imported.returncode == 0, f"mutant does not import, so it proves nothing: {old[:70]}"
        r = subprocess.run(pytest_cmd("-x"), capture_output=True, text=True, cwd=ROOT)
    finally:
        f.write_text(text, encoding="utf-8")
    killed = r.returncode != 0
    escaped += not killed
    print(f"{'killed ' if killed else 'ESCAPED'}  {fname}: {old.strip()[:70]}", flush=True)
print(f"{len(MUTANTS) - escaped}/{len(MUTANTS)} mutants killed")
sys.exit(1 if escaped else 0)

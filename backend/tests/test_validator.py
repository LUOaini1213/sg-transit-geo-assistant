import pytest
import sqlglot

from backend.app.validator import Refusal, extract_sql, validate


def limit_of(sql):
    tree = sqlglot.parse_one(sql, read="duckdb")
    lim = tree.args.get("limit")
    return int(lim.expression.this) if lim is not None else None


@pytest.mark.parametrize("sql", [
    "SELECT stop_code, stop_name FROM stops WHERE planning_area = 'ALPHA'",
    "select count(*) from stops",
    "SELECT s.* FROM stops s",
    "WITH busy AS (SELECT stop_code, weekday_boardings FROM stops) SELECT stop_code FROM busy ORDER BY weekday_boardings DESC",
    "SELECT a.stop_code, b.stop_code FROM stops a JOIN stops b ON b.stop_code <> a.stop_code "
    "WHERE sqrt(power(a.x_m - b.x_m, 2) + power(a.y_m - b.y_m, 2)) <= 400",
    "SELECT stop_code, row_number() OVER (PARTITION BY planning_area ORDER BY weekday_boardings DESC) AS rk FROM stops",
    "SELECT planning_area, count(*) AS n FROM stops GROUP BY planning_area HAVING count(*) > 2 ORDER BY n DESC",
    "SELECT stop_code FROM stops WHERE stop_name ILIKE '%alpha%' AND stop_code IN (SELECT origin_stop FROM od_stop_flows)",
    "SELECT CASE WHEN weekday_boardings > 100 THEN 'busy' ELSE 'quiet' END AS kind FROM stops",
    "SELECT round(avg(coverage_walk_400m), 3) FROM subzones",
    "SELECT stop_code FROM stops;",
])
def test_accepts_read_only_queries(sql):
    out = validate(sql, max_rows=200)
    assert out.upper().startswith(("SELECT", "WITH"))


# Queries without a small enough LIMIT fetch one row more than the cap, so the caller can report that rows were cut.
def test_adds_limit_when_missing():
    assert limit_of(validate("SELECT stop_code FROM stops", 200)) == 201


def test_keeps_small_limit():
    assert limit_of(validate("SELECT stop_code FROM stops LIMIT 7", 200)) == 7


def test_caps_large_limit():
    assert limit_of(validate("SELECT stop_code FROM stops LIMIT 100000", 200)) == 201


def test_replaces_non_literal_limit():
    assert limit_of(validate("SELECT stop_code FROM stops LIMIT (SELECT 5)", 200)) == 201


def test_set_operation_is_wrapped_and_limited():
    out = validate("SELECT stop_code FROM stops UNION SELECT origin_stop FROM od_stop_flows", 50)
    assert limit_of(out) == 51
    assert "UNION" in out.upper()


@pytest.mark.parametrize("sql,code", [
    ("DROP TABLE stops", "not_select"),
    ("DELETE FROM stops", "not_select"),
    ("INSERT INTO stops (stop_code) VALUES ('1')", "not_select"),
    ("UPDATE stops SET stop_name = 'x'", "not_select"),
    ("CREATE TABLE t AS SELECT * FROM stops", "not_select"),
    ("ALTER TABLE stops ADD COLUMN x INT", "not_select"),
    ("PRAGMA database_list", "not_select"),
    ("ATTACH 'other.db' AS other", "not_select"),
    ("COPY stops TO 'out.csv'", "not_select"),
    ("INSTALL httpfs", "not_select"),
    ("SET enable_external_access = true", "not_select"),
    ("SELECT 1; DROP TABLE stops", "multiple_statements"),
    ("SELECT stop_code FROM stops; SELECT 1", "multiple_statements"),
    ("SELECT * FROM read_csv('C:/Windows/win.ini')", "table_function"),
    ("SELECT * FROM duckdb_settings()", "table_function"),
    ("SELECT stop_code FROM stops UNION SELECT * FROM read_parquet('x.parquet')", "table_function"),
    ("SELECT * FROM 'secrets.csv'", "unknown_table"),
    ("SELECT * FROM main.stops", "qualified_table"),
    ("SELECT * FROM other.main.stops", "qualified_table"),
    ("SELECT * FROM planning_area_shapes", "unknown_table"),
    ("SELECT * FROM information_schema.tables", "qualified_table"),
    ("SELECT * FROM duckdb_tables", "unknown_table"),
    ("SELECT getenv('HOME')", "function_not_allowed"),
    ("SELECT current_setting('enable_external_access')", "function_not_allowed"),
    ("SELECT read_text('C:/Windows/win.ini')", "function_not_allowed"),
    ("SELECT stop_code FROM stops WHERE stop_name = sha256('x')", "function_not_allowed"),
    ("SELECT password FROM stops", "unknown_column"),
    ("SELECT stop_code FROM stops WHERE stop_code = ?", "forbidden_statement"),
    ("SELECT stop_code FROM stops WHERE stop_code = $1", "forbidden_statement"),
    ("", "empty"),
    ("   ", "empty"),
    ("SELECT " + "1 + " * 2000 + "1", "too_long"),
    ("SELEC stop_code FRM stops", "parse_error"),
])
def test_refuses(sql, code):
    with pytest.raises(Refusal) as e:
        validate(sql, 200)
    assert e.value.code == code


def test_forbidden_node_inside_cte_is_refused():
    with pytest.raises(Refusal):
        validate("WITH x AS (DELETE FROM stops RETURNING *) SELECT * FROM x", 200)


def test_cte_name_is_allowed_but_not_other_tables():
    validate("WITH t AS (SELECT stop_code FROM stops) SELECT stop_code FROM t", 200)
    with pytest.raises(Refusal) as e:
        validate("WITH t AS (SELECT stop_code FROM stops) SELECT stop_code FROM u", 200)
    assert e.value.code == "unknown_table"


@pytest.mark.parametrize("reply,expected", [
    ("```sql\nSELECT 1\n```", "SELECT 1"),
    ("Here you go:\n```\nSELECT stop_code FROM stops\n```\nThanks", "SELECT stop_code FROM stops"),
    ("SELECT stop_code FROM stops", "SELECT stop_code FROM stops"),
    ("CANNOT_ANSWER", None),
    ("cannot_answer, sorry", None),
    ("", None),
])
def test_extract_sql(reply, expected):
    assert extract_sql(reply) == expected


# Found in review (2026-09-28): a CTE named after a system view or a hidden table used to pass the allow-list,
# because CTE names were collected from the whole query instead of resolved in the scope where they are used.
@pytest.mark.parametrize("sql,code", [
    ("WITH duckdb_databases AS (SELECT * FROM duckdb_databases) SELECT * FROM duckdb_databases", "cte_name_not_allowed"),
    ("WITH sqlite_master AS (SELECT * FROM sqlite_master) SELECT * FROM sqlite_master", "cte_name_not_allowed"),
    ("WITH stops AS (SELECT * FROM stops) SELECT * FROM stops", "cte_name_not_allowed"),
    ("WITH x AS (SELECT * FROM duckdb_tables) SELECT * FROM x", "unknown_table"),
    ("SELECT * FROM (WITH subzone_shapes AS (SELECT 1 AS a) SELECT a FROM subzone_shapes) q, subzone_shapes",
     "unknown_table"),
    ("SELECT a FROM (WITH hidden AS (SELECT 1 AS a) SELECT a FROM hidden) q WHERE a IN (SELECT a FROM hidden)",
     "unknown_table"),
    ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r", "recursive_cte"),
    ("SELECT lpad('x', 20000000, 'y') AS s FROM stops", "function_not_allowed"),
    ("SELECT * FROM stops USING SAMPLE 10", "forbidden_statement"),
])
def test_review_bypasses_are_refused(sql, code):
    with pytest.raises(Refusal) as e:
        validate(sql, 200)
    assert str(e.value).startswith(code)


def test_cte_visible_in_its_scope_still_works():
    sql = ("WITH busy AS (SELECT stop_code, weekday_boardings FROM stops) "
           "SELECT b.stop_code FROM busy b WHERE b.stop_code IN (SELECT stop_code FROM busy) ORDER BY 1")
    assert validate(sql, 200).upper().startswith("WITH")

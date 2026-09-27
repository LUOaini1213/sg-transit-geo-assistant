"""The connection itself is a second line of defence: these queries bypass the validator on purpose."""
import duckdb
import pytest

from backend.app.db import QueryTimeout, ResultTooLarge


def test_connection_is_read_only(db):
    with pytest.raises(duckdb.Error):
        db.query("CREATE TABLE x AS SELECT 1")
    with pytest.raises(duckdb.Error):
        db.query("DELETE FROM stops")


def test_file_access_is_disabled(db, tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a\n1\n")
    with pytest.raises(duckdb.Error):
        db.query(f"SELECT * FROM read_csv('{f.as_posix()}')")


def test_settings_are_locked(db):
    with pytest.raises(duckdb.Error):
        db.query("SET enable_external_access = true")
    # a setting that could otherwise be changed at run time
    with pytest.raises(duckdb.Error):
        db.query("SET threads = 8")
    with pytest.raises(duckdb.Error):
        db.query("SET memory_limit = '64GB'")


def test_timeout_interrupts_long_query(db):
    with pytest.raises(QueryTimeout):
        db.query("SELECT count(*) FROM range(100000000) a, range(100000) b WHERE a.range + b.range = -1",
                 timeout_s=0.3)
    # and the connection still works afterwards
    assert db.query("SELECT count(*) FROM stops").rows == [[9]]


def test_row_cap_sets_truncated(db):
    r = db.query("SELECT * FROM range(10)", max_rows=4)
    assert len(r.rows) == 4 and r.truncated
    r = db.query("SELECT * FROM range(4)", max_rows=4)
    assert len(r.rows) == 4 and not r.truncated


def test_oversized_value_is_refused(db):
    with pytest.raises(ResultTooLarge):
        db.query("SELECT repeat('x', 20000) AS s", max_rows=200)


def test_oversized_result_is_refused(db):
    # five columns of 9,000 characters: every value is under the per-cell cap, 200 rows are over the byte budget
    cols = ", ".join(f"repeat('x', 9000) AS c{i}" for i in range(5))
    with pytest.raises(ResultTooLarge):
        db.query(f"SELECT {cols} FROM range(1000)", max_rows=200)


def test_normal_result_fits(db):
    r = db.query("SELECT repeat('x', 100) AS s FROM range(50)", max_rows=200)
    assert len(r.rows) == 50 and not r.truncated

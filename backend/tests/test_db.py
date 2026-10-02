"""The connection itself is a second line of defence: these queries bypass the validator on purpose."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import duckdb
import pytest

from backend.app.db import Database, QueryTimeout, ResultTooLarge


def test_two_live_instances_share_the_locked_read_only_database(db, fixture_db_path, tmp_path):
    second = Database(str(fixture_db_path))
    try:
        csv = tmp_path / "outside.csv"
        csv.write_text("a\n1\n")
        for instance in (db, second):
            assert instance.query("SELECT count(*) FROM stops").rows == [[9]]
            assert instance.query("SELECT ST_X(ST_Point(103.8, 1.3))").rows == [[103.8]]
            assert instance.query("""
                SELECT current_setting('enable_external_access'),
                       current_setting('memory_limit') = format_bytes(1000000000),
                       current_setting('threads'), current_setting('lock_configuration')
            """).rows == [[False, True, 2, True]]
            for sql in (
                "CREATE TABLE forbidden AS SELECT 1",
                f"SELECT * FROM read_csv('{csv.as_posix()}')",
                "SET enable_external_access = true",
                "SET memory_limit = '64GB'",
                "SET threads = 8",
                "SET lock_configuration = false",
            ):
                with pytest.raises(duckdb.Error):
                    instance.query(sql)
    finally:
        second.close()
    assert db.query("SELECT count(*) FROM stops").rows == [[9]]


def test_two_instances_can_initialize_concurrently(tmp_path):
    path = str(tmp_path / "shared.duckdb")
    duckdb.connect(path).close()
    ready = Barrier(2)
    instances = []

    def connect():
        ready.wait(timeout=5)
        instances.append(Database(path))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(connect) for _ in range(2)]
            for future in futures:
                future.result(timeout=10)
        assert len(instances) == 2
        for instance in instances:
            assert instance.query("SELECT ST_X(ST_Point(103.8, 1.3))").rows == [[103.8]]
    finally:
        for instance in instances:
            instance.close()


@pytest.mark.parametrize("unsafe_setting", [
    "SET enable_external_access = true",
    "SET memory_limit = '2GB'",
    "SET threads = 4",
])
def test_incompatible_locked_configuration_is_rejected_and_connection_closed(tmp_path, monkeypatch, unsafe_setting):
    path = str(tmp_path / "incompatible.duckdb")
    duckdb.connect(path).close()
    existing = duckdb.connect(path, read_only=True)
    connect = duckdb.connect
    opened = []

    def tracked_connect(*args, **kwargs):
        con = connect(*args, **kwargs)
        opened.append(con)
        return con

    try:
        existing.execute("LOAD spatial")
        # External access cannot be re-enabled even before lock_configuration; leave
        # its initial true value intact for that unsafe-configuration case.
        if unsafe_setting != "SET enable_external_access = true":
            existing.execute("SET enable_external_access = false")
        existing.execute("SET memory_limit = '1GB'")
        existing.execute("SET threads = 2")
        if unsafe_setting != "SET enable_external_access = true":
            existing.execute(unsafe_setting)
        existing.execute("SET lock_configuration = true")
        monkeypatch.setattr(duckdb, "connect", tracked_connect)
        with pytest.raises(duckdb.InvalidInputException, match="required safety limits"):
            Database(path)
        assert len(opened) == 1
        with pytest.raises(duckdb.ConnectionException, match="closed"):
            opened[0].execute("SELECT 1")
        assert existing.execute("SELECT current_setting('lock_configuration')").fetchone() == (True,)
        with pytest.raises(duckdb.Error):
            existing.execute("SET lock_configuration = false")
    finally:
        existing.close()


@pytest.mark.parametrize("stage", ["extension", "SET memory_limit = '1GB'", "SET lock_configuration = true"])
def test_initialization_error_is_preserved_and_connection_closed(tmp_path, monkeypatch, stage):
    path = str(tmp_path / "failed.duckdb")
    duckdb.connect(path).close()
    connect = duckdb.connect
    opened = []
    failure = duckdb.IOException("injected initialization failure")

    class FailingConnection:
        def __init__(self, con):
            self.con = con

        def execute(self, sql):
            if sql == stage or (stage == "extension" and sql in ("LOAD spatial", "INSTALL spatial")):
                raise failure
            return self.con.execute(sql)

        def close(self):
            self.con.close()

    def failing_connect(*args, **kwargs):
        con = connect(*args, **kwargs)
        opened.append(con)
        return FailingConnection(con)

    monkeypatch.setattr(duckdb, "connect", failing_connect)
    with pytest.raises(duckdb.IOException) as exc:
        Database(path)
    assert exc.value is failure
    assert len(opened) == 1
    with pytest.raises(duckdb.ConnectionException, match="closed"):
        opened[0].execute("SELECT 1")


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

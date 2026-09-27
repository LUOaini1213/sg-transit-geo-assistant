"""Read-only DuckDB access with a per-query time limit."""
import math
import threading
from dataclasses import dataclass

import duckdb


class QueryTimeout(Exception):
    pass


class ResultTooLarge(Exception):
    pass


# The row limit alone does not bound the size of an answer: one generated string column can be megabytes long.
MAX_CELL_CHARS = 10_000
MAX_RESULT_BYTES = 5_000_000


def _size(v) -> int:
    if isinstance(v, (str, bytes, bytearray)):
        return len(v)
    if isinstance(v, (list, tuple, dict)):
        return len(repr(v))
    return 16


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool  # more rows existed than max_rows


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (bytes, bytearray)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class Database:
    """One read-only connection; each query runs on its own cursor so requests can run concurrently."""

    def __init__(self, path: str):
        self.path = path
        con = duckdb.connect(path, read_only=True)
        try:
            con.execute("LOAD spatial")
        except duckdb.Error:
            con.execute("INSTALL spatial")
            con.execute("LOAD spatial")
        # After this the connection cannot read or write files or URLs, and the settings cannot be changed back.
        con.execute("SET enable_external_access = false")
        con.execute("SET memory_limit = '1GB'")
        con.execute("SET threads = 2")
        con.execute("SET lock_configuration = true")
        self._con = con

    def close(self):
        self._con.close()

    def query(self, sql: str, params=None, max_rows: int = 200, timeout_s: float = 5.0) -> QueryResult:
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            cur.execute(sql, params or [])
            columns = [d[0] for d in cur.description]
            rows, total = [], 0
            while len(rows) <= max_rows:  # one row at a time, so an oversized answer is refused before it piles up
                row = cur.fetchone()
                if row is None:
                    break
                for v in row:
                    n = _size(v)
                    if isinstance(v, str) and n > MAX_CELL_CHARS:
                        raise ResultTooLarge(f"a value of {n:,} characters (limit {MAX_CELL_CHARS:,})")
                    total += n
                if total > MAX_RESULT_BYTES:
                    raise ResultTooLarge(f"result over {MAX_RESULT_BYTES:,} bytes")
                rows.append(row)
        except duckdb.InterruptException:
            raise QueryTimeout(f"query exceeded {timeout_s:g} s") from None
        finally:
            timer.cancel()
            cur.close()
        truncated = len(rows) > max_rows
        return QueryResult(columns, [[_clean(v) for v in r] for r in rows[:max_rows]], truncated)

    def scalar_rows(self, sql: str, params=None) -> list[tuple]:
        """Trusted internal queries (layers, lookups). Still read-only."""
        cur = self._con.cursor()
        try:
            return cur.execute(sql, params or []).fetchall()
        finally:
            cur.close()

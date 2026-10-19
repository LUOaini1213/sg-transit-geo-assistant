"""Read-only DuckDB access with a per-query time limit."""
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import duckdb
from pydantic_core import to_json


class QueryTimeout(Exception):
    pass


class ResultTooLarge(Exception):
    pass


class UnsupportedResult(Exception):
    """The query returned a value that the JSON answer cannot faithfully represent."""


# The row limit alone does not bound the size of an answer: one generated string column can be megabytes long.
MAX_CELL_CHARS = 10_000
MAX_RESULT_BYTES = 5_000_000
MAX_RESULT_DEPTH = 64
# DuckDB settings are shared by connections to the same file. Serialize our initialization
# so one instance cannot lock the configuration halfway through another instance's setup.
_INITIALIZE_LOCK = threading.Lock()


@dataclass
class _ResultBudget:
    total: int = 0

    def add(self, n: int):
        self.total += n
        if self.total > MAX_RESULT_BYTES:
            raise ResultTooLarge(f"result over {MAX_RESULT_BYTES:,} bytes")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool  # more rows existed than max_rows


def _clean(v, budget: _ResultBudget, depth: int = 0):
    """Normalize nested values before a result is accepted, accounting for JSON UTF-8 bytes.

    Binary geometry is not a JSON value. Refuse it explicitly so the model can
    select scalar coordinates instead of silently replacing results with NULL.
    Decimal/interval values retain the existing Pydantic JSON representation.
    """
    if depth > MAX_RESULT_DEPTH:
        raise UnsupportedResult(f"result nesting exceeds {MAX_RESULT_DEPTH} levels")
    if isinstance(v, (list, tuple)):
        budget.add(2 + max(0, len(v) - 1))  # brackets and commas
        return [_clean(item, budget, depth + 1) for item in v]
    if isinstance(v, dict):
        budget.add(2 + max(0, len(v) - 1))
        out = {}
        for key, item in v.items():
            if not isinstance(key, str):
                raise UnsupportedResult("result maps require string keys; select scalar key/value columns instead")
            _clean(key, budget, depth + 1)
            budget.add(1)  # colon
            out[key] = _clean(item, budget, depth + 1)
        return out
    if isinstance(v, (bytes, bytearray)):
        raise UnsupportedResult("binary results are unsupported; select scalar values such as ST_X/ST_Y instead")
    if isinstance(v, float) and not math.isfinite(v):
        v = None
    elif isinstance(v, Decimal) and not v.is_finite():
        v = None
    elif isinstance(v, (datetime, date, time)):
        v = v.isoformat()
    elif isinstance(v, UUID):
        v = str(v)
    if isinstance(v, str) and len(v) > MAX_CELL_CHARS:
        raise ResultTooLarge(f"a value of {len(v):,} characters (limit {MAX_CELL_CHARS:,})")
    if not isinstance(v, (type(None), bool, int, float, str, Decimal, timedelta)):
        raise UnsupportedResult(f"unsupported result type: {type(v).__name__}")
    try:
        budget.add(len(to_json(v)))
    except (ValueError, TypeError, OverflowError) as exc:
        raise UnsupportedResult("result value cannot be represented as JSON") from exc
    return v


class Database:
    """One read-only connection; each query runs on its own cursor so requests can run concurrently."""

    def __init__(self, path: str):
        self.path = path
        con = duckdb.connect(path, read_only=True)
        try:
            with _INITIALIZE_LOCK:
                try:
                    con.execute("LOAD spatial")
                except duckdb.Error:
                    con.execute("INSTALL spatial")
                    con.execute("LOAD spatial")
                locked, safe = con.execute("""
                    SELECT current_setting('lock_configuration'),
                           NOT current_setting('enable_external_access')
                           AND current_setting('memory_limit') = format_bytes(1000000000)
                           AND current_setting('threads') = 2
                """).fetchone()
                if locked:
                    # A second connection inherits the first one's immutable settings. Verify
                    # those limits instead of trying to set (or unlock) them again.
                    if not safe:
                        raise duckdb.InvalidInputException("Database configuration is locked without the required safety limits")
                else:
                    # Disable file/URL access and impose resource limits before locking them.
                    con.execute("SET enable_external_access = false")
                    con.execute("SET memory_limit = '1GB'")
                    con.execute("SET threads = 2")
                    con.execute("SET lock_configuration = true")
        except BaseException:
            con.close()
            raise
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
            rows, budget = [], _ResultBudget()
            budget.add(2)  # outer row-array brackets
            while len(rows) <= max_rows:  # one row at a time, so an oversized answer is refused before it piles up
                row = cur.fetchone()
                if row is None:
                    break
                if rows:
                    budget.add(1)
                rows.append(_clean(row, budget))
        except duckdb.InterruptException:
            raise QueryTimeout(f"query exceeded {timeout_s:g} s") from None
        finally:
            timer.cancel()
            cur.close()
        truncated = len(rows) > max_rows
        return QueryResult(columns, rows[:max_rows], truncated)

    def scalar_rows(self, sql: str, params=None) -> list[tuple]:
        """Trusted internal queries (layers, lookups). Still read-only."""
        cur = self._con.cursor()
        try:
            return cur.execute(sql, params or []).fetchall()
        finally:
            cur.close()

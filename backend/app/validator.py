"""Parse and check generated SQL before it runs.

The checks are an allow-list: one SELECT statement, known tables, known columns, known functions. Whatever passes is
rewritten with a LIMIT no larger than the configured maximum. The database connection is also read-only and has
external access switched off (see db.py), so a query that slipped through could still not write or read files.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

from .schema import ALLOWED_COLUMNS, ALLOWED_FUNCTIONS, ALLOWED_TABLES

MAX_SQL_CHARS = 4000

# Statement and clause types that must not appear anywhere in the tree.
_FORBIDDEN_NAMES = ["Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Pragma", "Set",
                    "Copy", "Attach", "Detach", "Install", "Use", "Transaction", "Commit", "Rollback", "Merge", "Into",
                    "LoadData", "Describe", "Summarize", "Grant", "Revoke", "TruncateTable", "Export", "Placeholder",
                    "Parameter", "Show", "Analyze", "Kill", "Refresh", "Cache", "Uncache", "TableSample"]
FORBIDDEN_NODES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NAMES) if isinstance(t, type))

_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


class Refusal(Exception):
    """The SQL (or the question) is refused. `code` is stable and used in tests and the evaluation."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _function_name(f: exp.Func) -> str:
    if isinstance(f, exp.Anonymous):
        return str(f.this).lower()
    return f.key.lower()


def _defined_names(tree: exp.Expression) -> set[str]:
    """Aliases the query defines itself (select aliases, CTE names and their columns, table aliases)."""
    names: set[str] = set()
    for a in tree.find_all(exp.Alias):
        names.add(a.alias.lower())
    for t in tree.find_all(exp.TableAlias):
        if t.name:
            names.add(t.name.lower())
        for c in t.columns:
            names.add(c.name.lower())
    return names


def _cte_names(tree: exp.Expression) -> set[str]:
    return {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}


_SYSTEM_PREFIXES = ("duckdb_", "sqlite_", "pg_", "information_schema")


def _check_sources_by_scope(tree: exp.Expression) -> None:
    """Resolve every FROM/JOIN source in the scope where it is used.

    A name only counts as a CTE where that CTE is visible, and a non-recursive CTE is not visible inside its own
    body. Without this, `WITH duckdb_tables AS (SELECT * FROM duckdb_tables) ...` would pass the allow-list,
    because the inner reference has the same name as a CTE defined somewhere in the query."""
    for w in tree.find_all(exp.With):
        if w.args.get("recursive"):
            raise Refusal("recursive_cte")
    for name in _cte_names(tree):
        if name.startswith(_SYSTEM_PREFIXES) or name in ALLOWED_TABLES:
            raise Refusal("cte_name_not_allowed", name)
    try:
        scopes = traverse_scope(tree)
    except Exception as e:  # sqlglot cannot resolve the query's scopes: refuse rather than guess
        raise Refusal("parse_error", f"scope: {type(e).__name__}") from None
    for scope in scopes:
        for source in scope.sources.values():
            if isinstance(source, exp.Table) and source.name.lower() not in ALLOWED_TABLES:
                raise Refusal("unknown_table", source.name.lower())


def validate(sql: str, max_rows: int = 200) -> str:
    """Return a safe, LIMITed version of `sql`, or raise Refusal."""
    if not sql or not sql.strip():
        raise Refusal("empty")
    if len(sql) > MAX_SQL_CHARS:
        raise Refusal("too_long", f"{len(sql)} characters")
    text = sql.strip().rstrip(";").strip()
    try:
        statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except ParseError as e:
        raise Refusal("parse_error", str(e).splitlines()[0][:200]) from None
    except Exception as e:  # sqlglot raises a few other error types on odd input
        raise Refusal("parse_error", type(e).__name__) from None
    if len(statements) != 1:
        raise Refusal("multiple_statements", f"{len(statements)} statements")
    tree = statements[0]
    if not isinstance(tree, _QUERY_ROOTS):
        raise Refusal("not_select", type(tree).__name__)

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise Refusal("forbidden_statement", type(node).__name__)

    ctes = _cte_names(tree)
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise Refusal("table_function", t.sql("duckdb")[:80])
        if t.args.get("db") or t.args.get("catalog"):
            raise Refusal("qualified_table", t.sql("duckdb")[:80])
        name = t.name.lower()
        if name not in ALLOWED_TABLES and name not in ctes:
            raise Refusal("unknown_table", name)
    _check_sources_by_scope(tree)

    for f in tree.find_all(exp.Func):
        if isinstance(f, exp.Connector):  # AND / OR are modelled as functions
            continue
        name = _function_name(f)
        if name not in ALLOWED_FUNCTIONS:
            raise Refusal("function_not_allowed", name)

    defined = _defined_names(tree)
    for c in tree.find_all(exp.Column):
        if isinstance(c.this, exp.Star):  # t.*
            continue
        name = c.name.lower()
        if name not in ALLOWED_COLUMNS and name not in defined:
            raise Refusal("unknown_column", name)

    return _force_limit(tree, max_rows).sql(dialect="duckdb")


def _force_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    """Keep a LIMIT of at most max_rows; otherwise fetch max_rows + 1, so the caller can tell the result was cut."""
    if not isinstance(tree, exp.Select):
        # set operations: wrap so the limit applies to the whole result
        return exp.select("*").from_(tree.subquery("q")).limit(max_rows + 1)
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression if isinstance(limit, exp.Limit) else None
        if isinstance(value, exp.Literal) and value.is_int and 0 <= int(value.this) <= max_rows:
            return tree
    return tree.limit(max_rows + 1, copy=True)


_CODE_BLOCK = re.compile(r"```(?:sql|duckdb)?\s*(.*?)```", re.S | re.I)


def extract_sql(reply: str) -> str | None:
    """Pull the SQL out of a model reply. Returns None if the model declined."""
    if reply is None:
        return None
    if "CANNOT_ANSWER" in reply.upper():
        return None
    m = _CODE_BLOCK.search(reply)
    body = m.group(1) if m else reply
    return body.strip() or None

"""Answer a question: screen it, get SQL (model or templates), validate, run, attach a map layer.

Engines:
  llm       the chat model writes SQL; one repair attempt if the SQL is refused or fails to run.
  template  deterministic keyword rules.
  auto      the model first; the templates only if the model is unreachable or its SQL is still unusable after the
            repair. A deliberate CANNOT_ANSWER from the model is final.
"""
import time
from dataclasses import asdict, dataclass, field

import duckdb

from . import geo, guard, llm
from .db import Database, QueryTimeout, ResultTooLarge
from .templates import TemplateEngine
from .validator import Refusal, extract_sql, validate


@dataclass
class Answer:
    question: str
    engine: str
    status: str  # "answered" or "refused"
    sql: str | None = None
    row_count: int = 0
    truncated: bool = False
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    geojson: dict | None = None
    reason: str | None = None  # refusal code
    detail: str | None = None
    attempts: list[dict] = field(default_factory=list)  # each model/template attempt: raw SQL and what went wrong
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class _Unusable(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


class Assistant:
    def __init__(self, db: Database, templates: TemplateEngine, client: "llm.ChatClient | None",
                 max_rows: int = 200, timeout_s: float = 5.0, max_question_chars: int = 500, prescreen: bool = True, use_hints: bool = True):
        self.db, self.templates, self.client = db, templates, client
        self.max_rows, self.timeout_s, self.max_chars, self.prescreen = max_rows, timeout_s, max_question_chars, prescreen
        self.use_hints = use_hints

    # ---- one SQL attempt: validate, run
    def _run(self, raw_sql: str):
        safe = validate(raw_sql, self.max_rows)
        try:
            return safe, self.db.query(safe, max_rows=self.max_rows, timeout_s=self.timeout_s)
        except QueryTimeout as e:
            raise _Unusable("timeout", str(e)) from None
        except ResultTooLarge as e:
            raise _Unusable("result_too_large", str(e)) from None
        except duckdb.Error as e:
            raise _Unusable("execution_error", str(e).splitlines()[0][:300]) from None

    def _finish(self, ans: Answer, safe: str, res) -> Answer:
        ans.status, ans.sql = "answered", safe
        ans.columns, ans.rows, ans.row_count, ans.truncated = res.columns, res.rows, len(res.rows), res.truncated
        ans.geojson = geo.result_layer(self.db, res.columns, res.rows)
        return ans

    def _refuse(self, ans: Answer, code: str, detail: str = "") -> Answer:
        ans.status, ans.reason, ans.detail = "refused", code, detail or None
        ans.sql, ans.columns, ans.rows, ans.row_count, ans.geojson = None, [], [], 0, None
        return ans

    def _via_template(self, ans: Answer) -> Answer:
        ans.engine = "template"
        sql = self.templates.to_sql(ans.question)
        if sql is None:
            return self._refuse(ans, "no_template", "The keyword rules do not cover this question.")
        ans.attempts.append({"engine": "template", "sql": sql})
        try:
            safe, res = self._run(sql)
        except (Refusal, _Unusable) as e:
            ans.attempts[-1]["error"] = str(e)
            return self._refuse(ans, e.code, getattr(e, "detail", ""))
        return self._finish(ans, safe, res)

    def _via_llm(self, ans: Answer) -> Answer:
        """Returns the answer; raises ConnectionError if the model cannot be reached."""
        ans.engine = "llm"
        if self.client is None:
            raise ConnectionError("no model configured")
        messages = llm.first_messages(ans.question, self.templates.hints(ans.question) if self.use_hints else None)
        for attempt in range(2):  # the first try and at most one repair
            try:
                reply = self.client.complete(messages)
            except Exception as e:  # network errors, timeouts, HTTP errors from the endpoint
                raise ConnectionError(f"{type(e).__name__}: {e}") from None
            sql = extract_sql(reply)
            if sql is None:
                ans.attempts.append({"engine": "llm", "reply": reply[:500], "error": "model declined"})
                return self._refuse(ans, "model_declined", "The model said the data cannot answer this.")
            ans.attempts.append({"engine": "llm", "sql": sql})
            try:
                safe, res = self._run(sql)
                return self._finish(ans, safe, res)
            except (Refusal, _Unusable) as e:
                ans.attempts[-1]["error"] = str(e)
                last = e
                if attempt == 0:
                    messages = llm.repair_messages(messages, reply, sql, str(e))
        return self._refuse(ans, "invalid_after_repair", f"{last.code}: {getattr(last, 'detail', '')}")

    def ask(self, question: str, engine: str = "auto") -> Answer:
        t0 = time.perf_counter()
        out = self._dispatch(Answer(question=(question or "").strip(), engine=engine, status="refused"), engine)
        out.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return out

    def _dispatch(self, ans: Answer, engine: str) -> Answer:
        if not ans.question:
            return self._refuse(ans, "empty_question")
        if len(ans.question) > self.max_chars:
            return self._refuse(ans, "question_too_long", f"limit {self.max_chars} characters")
        if self.prescreen and (code := guard.screen(ans.question)):
            return self._refuse(ans, "prescreen_" + code, "The question asks for something this tool does not do.")
        if engine == "template":
            return self._via_template(ans)
        if engine == "llm":
            try:
                return self._via_llm(ans)
            except ConnectionError as e:
                return self._refuse(ans, "llm_unavailable", str(e)[:300])
        # auto
        try:
            out = self._via_llm(ans)
        except ConnectionError as e:
            ans.attempts.append({"engine": "llm", "error": f"unavailable: {e}"[:300]})
            return self._via_template(ans)
        if out.status == "refused" and out.reason == "invalid_after_repair":
            fallback = self._via_template(Answer(question=ans.question, engine="template", status="refused",
                                                 attempts=list(out.attempts)))
            if fallback.status == "answered":
                return fallback
        return out

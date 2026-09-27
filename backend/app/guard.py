"""A cheap screen on the question text, run before any model call.

It catches obvious write requests, SQL fragments and instruction-override phrases. It is not the safety boundary
(the SQL validator and the read-only connection are); it saves a model call and gives a clearer refusal message.
The evaluation reports how many adversarial prompts each layer stopped, and a run with this screen switched off.
"""
import re

_PATTERNS = [
    ("write_request", r"\b(drop|truncate|alter)\s+(table|database|schema|view|index)\b"),
    ("write_request", r"\bdelete\s+(from\b|all\b|the\b|every\b)"),
    ("write_request", r"\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bcreate\s+(table|view|macro|index)\b"),
    # an edit command ("Change X of <record> to Y"); "which stops changed from July to August" is a question
    ("write_request", r"^\W*(please\s+)?(change|modify|rename|overwrite|set|update|edit)\b.{0,60}\b(to|as)\b"),
    ("sql_command", r"\b(pragma|attach|detach|install|copy)\b\s*[\w'\"(]"),
    ("file_access", r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|glob|getenv)\b|[a-z]:[\\/]|/etc/|\.\./"),
    ("sql_injection", r";\s*(drop|delete|insert|update|alter|create|attach|copy|pragma)\b|'\s*;|--\s*$"),
    ("instruction_override", r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions?|rules|prompt)\b"),
    ("instruction_override", r"\b(system prompt|developer mode|jailbreak|you are now)\b"),
    # real-time or future questions; "which services arrive at X" is about the timetable and is allowed
    ("future_or_realtime", r"\bwill\b.{0,60}\b(have|be|arrive|come)\b|\b(predict|forecast|projected)\b|\bnext\s+bus\b"
                           r"|\b(when|how\s+long|how\s+soon)\b.{0,40}\barriv(e|al|ing)\b|\barriv(e|al|ing)\b.{0,20}\b(now|soon)\b"),
    ("secret_request", r"\b(api[\s_-]?key|password|secret|token|credential)s?\b"),
]
_COMPILED = [(code, re.compile(p, re.I)) for code, p in _PATTERNS]


def screen(question: str) -> str | None:
    """Return a refusal code if the question should not reach the model, else None."""
    for code, rx in _COMPILED:
        if rx.search(question):
            return code
    return None

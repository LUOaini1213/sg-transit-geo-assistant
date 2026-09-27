"""Settings, read from environment variables so the model and database can be swapped without code changes."""
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("TRANSIT_DB", str(REPO / "data" / "transit.duckdb")))
    # Any OpenAI-compatible chat endpoint. The default is a local Ollama server.
    llm_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "qwen2.5:3b-16k"))
    # Ollama ignores the key; a hosted endpoint reads it from the environment. Never stored in the repo.
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", "not-needed"))
    # Optional. "none" turns off the thinking phase of reasoning models served by Ollama (e.g. qwen3.5).
    llm_reasoning_effort: str | None = field(default_factory=lambda: os.environ.get("LLM_REASONING_EFFORT") or None)
    llm_timeout_s: float = field(default_factory=lambda: float(os.environ.get("LLM_TIMEOUT_S", "120")))
    max_rows: int = field(default_factory=lambda: int(os.environ.get("MAX_ROWS", "200")))
    query_timeout_s: float = field(default_factory=lambda: float(os.environ.get("QUERY_TIMEOUT_S", "5")))
    max_question_chars: int = 500


def get_settings() -> Settings:
    return Settings()

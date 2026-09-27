import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from backend.app.ask import Assistant  # noqa: E402
from backend.app.build import build  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.templates import Catalog, TemplateEngine  # noqa: E402

FIXTURE = ROOT / "data" / "fixture"


@pytest.fixture(scope="session")
def fixture_db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "fixture.duckdb"
    build(FIXTURE, path)
    return path


@pytest.fixture(scope="session")
def db(fixture_db_path):
    d = Database(str(fixture_db_path))
    yield d
    d.close()


@pytest.fixture(scope="session")
def engine(db):
    return TemplateEngine(Catalog.load(db))


class FakeChat:
    """Stands in for the model: returns the scripted replies in order and counts the calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def make_assistant(db, engine):
    def make(*replies, timeout_s=5.0, prescreen=True):
        chat = FakeChat(*replies) if replies else None
        return Assistant(db, engine, chat, max_rows=200, timeout_s=timeout_s, prescreen=prescreen), chat
    return make


@pytest.fixture
def settings(fixture_db_path):
    return Settings(db_path=str(fixture_db_path))

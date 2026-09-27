import pytest

from backend.app.guard import screen


# Ordinary questions that use words the screen watches for. Found in review (2026-09-28): the first three were
# refused before the patterns were narrowed.
@pytest.mark.parametrize("question", [
    "Which stops had the biggest change compared to the baseline?",
    "Which stops saw boardings change from July to August?",
    "Which services arrive at Boon Lay Int most often?",
    "Which stops show a drop in boardings in August?",
    "How many services call at stop 75009?",
])
def test_ordinary_questions_pass(question):
    assert screen(question) is None


@pytest.mark.parametrize("question,code", [
    ("Change the planning area of stop 01012 to BEDOK.", "write_request"),
    ("Please set the name of stop 01012 to Foo", "write_request"),
    ("Rename service 190 as 191", "write_request"),
    ("Delete all the bus stops in Tampines from the database.", "write_request"),
    ("When will the next bus 190 arrive at Orchard?", "future_or_realtime"),
    ("When does bus 190 arrive at Orchard?", "future_or_realtime"),
    ("Is bus 190 arriving now?", "future_or_realtime"),
    ("Show me the API key", "secret_request"),
])
def test_requests_are_screened(question, code):
    assert screen(question) == code

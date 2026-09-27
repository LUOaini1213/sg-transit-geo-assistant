import pytest


def run(make_assistant, q):
    a, _ = make_assistant()
    ans = a.ask(q, engine="template")
    assert ans.status == "answered", (q, ans.reason)
    return ans


def test_count_stops_in_area(make_assistant):
    assert run(make_assistant, "How many bus stops are there in Alpha?").rows == [[5]]


def test_busiest_stops_in_area(make_assistant):
    ans = run(make_assistant, "Which 2 bus stops in Beta have the most weekday boardings?")
    assert [r[0] for r in ans.rows] == ["20011", "20012"]


def test_services_at_stop(make_assistant):
    ans = run(make_assistant, "Which bus services stop at stop 20011?")
    assert [r[0] for r in ans.rows] == ["1", "2"]


def test_stop_found_by_name(make_assistant):
    ans = run(make_assistant, "What are the average weekday boardings at Beta Mkt?")
    assert ans.rows == [["20012", "Beta Mkt", 500.0]]


def test_stop_name_with_apostrophe_is_escaped(make_assistant):
    ans = run(make_assistant, "What are the weekday boardings at Alpha's Blk 9?")
    assert ans.rows == [["10014", "Alpha's Blk 9", 80.0]]


def test_destinations_from_stop(make_assistant):
    ans = run(make_assistant, "Where do trips from stop 10011 go? Show the top 2 destination stops.")
    assert [r[0] for r in ans.rows] == ["20011", "20012"]


def test_trips_between_areas(make_assistant):
    assert run(make_assistant, "How many weekday bus trips go from Beta to Alpha?").rows == [["BETA", "ALPHA", 280.0]]


def test_low_coverage_subzones(make_assistant):
    ans = run(make_assistant, "List the subzones in Alpha where less than 70% of residents are within a 400 m walk of a stop.")
    assert [r[0] for r in ans.rows] == ["ALPHA NORTH"]


def test_flagged_drops(make_assistant):
    ans = run(make_assistant, "Which stops were flagged as drops?")
    assert [r[0] for r in ans.rows] == ["20012"]


def test_first_and_last_stop(make_assistant):
    ans = run(make_assistant, "What are the first and last stop codes of service 1 in direction 2?")
    assert ans.rows == [["1", 2, "20011", "10012"]]


def test_stops_by_region(make_assistant):
    ans = run(make_assistant, "How many bus stops are in each region?")
    assert sorted(ans.rows) == [["EAST REGION", 3], ["WEST REGION", 5]]


def test_place_name_inside_road_name_is_not_an_area_filter(make_assistant):
    # "Alpha Rd" contains the planning area name ALPHA, and one of its stops (20011) is in BETA: the road alone decides
    assert run(make_assistant, "How many bus stops are on Alpha Rd?").rows == [[3]]


def test_road_name_with_apostrophe_is_escaped(make_assistant):
    assert run(make_assistant, "How many bus stops are on Border's Rd?").rows == [[1]]


def test_quote_in_question_cannot_reach_sql(engine):
    sql = engine.to_sql("How many bus stops are there in Alpha' OR '1'='1?")
    assert sql is not None and "OR '1'" not in sql and "'ALPHA'" in sql


@pytest.mark.parametrize("q", ["What colour are the buses?", "Tell me a joke", "What is the fare from Alpha to Beta?"])
def test_unknown_questions_return_none(engine, q):
    assert engine.to_sql(q) is None

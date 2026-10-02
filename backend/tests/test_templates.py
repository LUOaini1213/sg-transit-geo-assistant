import pytest

from copy import deepcopy
from backend.app.templates import TemplateEngine, TemplateRefusal, _top_n


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
    with pytest.raises(TemplateRefusal):
        engine.to_sql("How many bus stops are there in Alpha' OR '1'='1?")


@pytest.mark.parametrize("q", ["What colour are the buses?", "Tell me a joke", "What is the fare from Alpha to Beta?"])
def test_unknown_questions_return_none(engine, q):
    assert engine.to_sql(q) is None


def test_longest_entity_spans_do_not_invent_a_planning_area(engine):
    catalog = deepcopy(engine.c)
    catalog.planning_areas.append("BOON LAY")
    catalog.stop_names["boon lay int"] = ["20011"]
    catalog.stop_display["20011"] = "Boon Lay Int"
    synthetic = TemplateEngine(catalog)
    hints = synthetic.hints("What are the weekday boardings at Boon Lay Int?")
    assert "planning_area = 'BOON LAY'" not in hints
    assert any("'20011'" in hint and "Boon Lay Int" in hint for hint in hints)
    # A separate area occurrence must survive; blanket name suppression loses it.
    explicit = synthetic.hints("Compare Boon Lay Int with the stops in Boon Lay.")
    assert "planning_area = 'BOON LAY'" in explicit
    assert engine.stops_in("Opp Alpha Stn and Beta Mkt", "opp alpha stn and beta mkt") == ["10012", "20012"]


@pytest.mark.parametrize("question, expected", [
    ("How many bus stops are in Alpha and Beta?", [[8]]),
    ("How many bus stops are in Alpha or Beta?", [[8]]),
    ("How many bus stops are on Alpha Rd and Beta Rd?", [[5]]),
    ("How many bus stops are in Alpha on Alpha Rd?", [[2]]),
    ("How many bus stops are in Beta on Alpha Rd?", [[1]]),
    ("How many bus stops are in West Region and East Region?", [[8]]),
    ("For each planning area in the West Region, how many bus stops are there?", [["ALPHA", 5]]),
    ("How many other bus stops lie within 400 metres of stop 10011?", [[2]]),
    ("How many bus stops in Alpha have exactly one bus service?", [[4]]),
    ("How many bus stops in Alpha have more than 500 weekday boardings?", [[1]]),
    ("How many bus stops in Beta have weekday boardings above 1000?", [[1]]),
    ("How many weekday trips go from Alpha and Beta to Beta?", [[600]]),
    ("How many weekday trips go between Alpha and Beta?", [[790]]),
    ("How many weekday trips go from Beta to Alpha in August 2026?", [["BETA", "ALPHA", 280]]),
])
def test_combined_places_and_scalar_filters_are_preserved(make_assistant, question, expected):
    assert run(make_assistant, question).rows == expected


@pytest.mark.parametrize("question, expected_keys", [
    ("Which stops in Alpha have more than 500 weekday boardings?", ["10011"]),
    ("Which stops in Alpha have more than 2000 weekday boardings?", []),
    ("Which subzones in Alpha have at least 6000 residents and walking coverage below 80%?", ["ALPHA SOUTH"]),
    ("Which subzones in Alpha and Beta have at least 6000 residents and walking coverage above 80%?", ["BETA CENTRE"]),
    ("Which stops in Alpha have more than 20% of their weekday boardings in the AM peak and more than 15% of their weekday boardings in the PM peak?", ["10013", "10014"]),
    ("What are weekday boardings at Alpha Stn and Beta Mkt?", ["10011", "20012"]),
    ("What are weekday boardings at Alpha Stn in Beta?", []),
    ("Which stops in Alpha were flagged as drops?", []),
    ("List service numbers with PM peak headway of 15 minutes or more.", ["3E"]),
])
def test_combined_lists_have_hand_checked_rows(make_assistant, question, expected_keys):
    ans = run(make_assistant, question)
    assert [row[0] for row in ans.rows] == expected_keys
    assert ans.row_count == len(expected_keys)
    if ans.geojson:
        assert len(ans.geojson["features"]) == ans.row_count


@pytest.mark.parametrize("question, detail", [
    ("How many bus stops are in Atlantis?", "location"),
    ("How many bus stops are in Alpha and Atlantis?", "location"),
    ("How many weekend bus trips go from Beta to Alpha?", "weekday"),
    ("What are weekday boardings at stop 10011 in July 2026?", "August 2026"),
    ("What are weekday boardings at stop 10011 in Jul 2026?", "August 2026"),
    ("What are weekday boardings at stop 10011 in 2026-07?", "August 2026"),
    ("What are weekday boardings at stop 10011 in August 2025?", "August 2026"),
    ("What are weekday boardings at stop 10011 on August 1 2026?", "calendar-day"),
    ("What are weekday boardings at stop 10011 on 2026-08-01?", "calendar-day"),
    ("Which stops in Alpha have wheelchair access?", "accessibility"),
    ("Which stops in Alpha have more than 200 boardings and below 3 complaints?", "condition"),
    ("Which stops in Alpha have more than 1000 boardings or with fewer than 2 bus services?", "alternative"),
    ("Which subzones in Alpha have walking coverage below 80% within 800 m?", "400 m"),
    ("Which bus stops are within 400 m walking distance of stop 10011?", "walking distance"),
    ("How many PM peak trips go from Beta to Alpha?", "PM-peak"),
])
def test_unsupported_conditions_refuse_instead_of_answering_a_different_question(make_assistant, question, detail):
    assistant, _ = make_assistant()
    ans = assistant.ask(question, engine="template")
    assert ans.status == "refused", (question, ans.rows)
    assert ans.reason == "template_unsupported_constraint"
    assert detail in ans.detail
    assert ans.sql is None and ans.rows == [] and ans.geojson is None


@pytest.mark.parametrize("comparison", ["greater than", "more than", "above", "over", "longer than"])
def test_headway_comparison_has_one_meaning(make_assistant, comparison):
    ans = run(make_assistant, f"List services with AM peak headway {comparison} 9 minutes.")
    assert ans.rows == [["1"]]


@pytest.mark.parametrize("comparison", ["below", "less than", "fewer than", "under", "shorter than"])
def test_headway_and_stop_count_are_independent(make_assistant, comparison):
    ans = run(make_assistant, f"List services with AM peak headway {comparison} 10 minutes and at least 2 stops.")
    assert ans.rows == [["1"], ["2"]]


@pytest.mark.parametrize("threshold", ["at least 2", "no fewer than two", "2 or more", "at most 3", "no more than three"])
def test_filter_threshold_does_not_limit_results(make_assistant, threshold):
    ans = run(make_assistant, f"Which subzones have {threshold} bus stops and walking coverage above 0%?")
    assert [row[0] for row in ans.rows] == ["ALPHA NORTH", "ALPHA SOUTH", "BETA CENTRE"]
    assert ans.row_count == 3 and not ans.truncated


def test_explicit_rank_limit_survives_other_numeric_filters(make_assistant):
    ans = run(make_assistant, "Show the top 2 subzones with at least 2 bus stops and the lowest walking coverage.")
    assert [row[0] for row in ans.rows] == ["ALPHA NORTH", "ALPHA SOUTH"]
    assert _top_n("subzones with no more than two bus stops", 0) == 0
    assert _top_n("show two subzones", 0) == 2


def test_coverage_filter_returns_more_than_default_top_ten(fixture_db_path, tmp_path):
    import shutil
    import duckdb
    from backend.app.ask import Assistant
    from backend.app.db import Database
    from backend.app.templates import Catalog

    path = tmp_path / "many-areas.duckdb"
    shutil.copyfile(fixture_db_path, path)
    with duckdb.connect(str(path)) as connection:
        for i in range(12):
            connection.execute("INSERT INTO planning_areas SELECT * REPLACE (? AS planning_area) "
                               "FROM planning_areas WHERE planning_area = 'BETA'", [f"EXTRA {i}"])
    database = Database(str(path))
    try:
        assistant = Assistant(database, TemplateEngine(Catalog.load(database)), None)
        ans = assistant.ask("Which planning areas have more than 90% of residents within 400 m straight-line distance of a bus stop?", engine="template")
        assert ans.status == "answered" and not ans.truncated
        assert {row[0] for row in ans.rows} == {"ALPHA", "BETA"} | {f"EXTRA {i}" for i in range(12)}
    finally:
        database.close()


@pytest.mark.parametrize("question, expected", [
    ("What is the route length in km of bus service 1?", [[2.3], [2.0]]),
    ("Which pair of different planning areas has the most weekday bus trips from one to the other?", [["ALPHA", "BETA", 510.0]]),
    ("What is the combined length in km of all stop-to-stop links used by 1 or more services?", [[pytest.approx(3.1)]]),
    ("What is the combined length in km of all stop-to-stop links used by 2 or more services?", [[None]]),
    ("What is the average weekday boardings per stop in Alpha?", [[(1200 + 300 + 150 + 80 + 40) / 5]]),
    ("What is the mean weekday boardings per bus stop in Beta?", [[(2000 + 500 + 450) / 3]]),
])
def test_aggregate_and_unit_phrases_are_not_locations(make_assistant, question, expected):
    assert run(make_assistant, question).rows == expected


def test_identical_area_and_subzone_requires_explicit_subzone(engine, make_assistant):
    catalog = deepcopy(engine.c)
    catalog.subzones.append("ALPHA")
    synthetic = TemplateEngine(catalog)
    assert synthetic.areas_in("How many residents of Alpha live outside a 400 m walk from a bus stop?") == ["ALPHA"]
    assert synthetic.areas_in("What is coverage in subzone Alpha?") == []
    assert "planning_area = 'ALPHA'" in synthetic.hints("How many stops are in Alpha?")
    assert any("also names a subzone" in hint for hint in synthetic.hints("How many stops are in Alpha?"))
    assistant, _ = make_assistant()
    assistant.templates = synthetic
    ans = assistant.ask("How many residents of Alpha live more than a 400 m walk from a bus stop?", engine="template")
    assert ans.status == "answered" and ans.rows == [[3600.0]]


@pytest.mark.parametrize("question, expected_keys", [
    ("Which stops in Alpha or Beta have more than 1000 weekday boardings?", ["20011", "10011"]),
    ("Which stops on Alpha Rd or Beta Rd have more than 1000 weekday boardings?", ["20011", "10011"]),
    ("Which bus stops in Alpha have their busiest boarding hour at 8 am?", ["10013", "10014"]),
    ("Which subzone of Alpha has the most bus stops?", ["ALPHA SOUTH"]),
    ("Which planning area has the most bus stops per square kilometre?", ["ALPHA"]),
])
def test_new_measure_intents_return_the_requested_grain(make_assistant, question, expected_keys):
    assert [row[0] for row in run(make_assistant, question).rows] == expected_keys


@pytest.mark.parametrize("question", [
    "How many stops show a surge in August boardings compared with the baseline?",
    "How many stops show a surge in boardings in August compared with the baseline?",
    "Count the stops in Alpha with a surge in weekday boardings in August 2026.",
])
def test_change_counts_keep_metric_date_and_count_intent(make_assistant, question):
    ans = run(make_assistant, question)
    assert ans.columns == ["n_stops"] and ans.rows == [[1]]


@pytest.mark.parametrize("question", [
    "Which stops saw the biggest drop in boardings in August compared with earlier months?",
    "Which stops saw a drop in August weekday boardings compared with the baseline?",
])
def test_change_details_keep_metric_date_and_baseline(make_assistant, question):
    ans = run(make_assistant, question)
    assert ans.rows == [["20012", "Beta Mkt", "drop", -0.375, None]]
    assert "pct_change" in ans.columns


@pytest.mark.parametrize("question", [
    "Where are the passengers who board at stop 10011 going? Show their top 2 destination stops.",
    "Where are passengers at stop code 10011 going? Show the top 2 destinations.",
    "Where are passengers from stop 10011 going? Show the top 2 destination stops.",
])
def test_source_stop_anchor_does_not_swallow_destination_clause(make_assistant, question):
    ans = run(make_assistant, question)
    assert [row[0] for row in ans.rows] == ["20011", "20012"]

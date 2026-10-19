"""HTTP contract of the REST API, on the fixture database with a scripted model."""
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from conftest import FakeChat

ANSWER_KEYS = {"question", "engine", "status", "sql", "row_count", "truncated", "columns", "rows", "geojson", "reason",
               "detail", "attempts", "latency_ms"}


@pytest.fixture
def client_for(settings):
    def make(*replies):
        chat = FakeChat(*replies)
        return TestClient(create_app(settings, client=chat)), chat
    return make


@pytest.fixture
def client(client_for):
    c, _ = client_for()
    with c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["stops"] == 9


def test_openapi_lists_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/api/ask", "/api/layers/stops", "/api/layers/coverage", "/api/stops/{stop_code}",
              "/api/stops/{stop_code}/od", "/api/schema"]:
        assert p in paths


def test_schema_hides_geometry_tables(client):
    tables = client.get("/api/schema").json()
    assert "stops" in tables and "od_stop_flows" in tables
    assert not any(t.endswith("_shapes") for t in tables)


def test_stops_layer(client):
    fc = client.get("/api/layers/stops").json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 9
    f = next(x for x in fc["features"] if x["properties"]["stop_code"] == "20011")
    assert f["geometry"]["coordinates"] == [103.815, 1.3525]
    assert f["properties"]["weekday_boardings"] == 2000


def test_coverage_layer(client):
    fc = client.get("/api/layers/coverage").json()
    assert len(fc["features"]) == 3 and fc["features"][0]["geometry"]["type"] == "Polygon"


def test_stop_detail(client):
    r = client.get("/api/stops/20011").json()
    assert r["stop_name"] == "Beta Int" and r["planning_area"] == "BETA" and r["services"] == ["1", "2"]


@pytest.mark.parametrize("code,status", [("99999", 404), ("abc", 422), ("123456", 422), ("1001'", 422)])
def test_stop_detail_errors(client, code, status):
    assert client.get(f"/api/stops/{code}").status_code == status


def test_stop_od(client):
    fc = client.get("/api/stops/10011/od", params={"limit": 2}).json()
    assert [f["properties"]["destination_stop"] for f in fc["features"]] == ["20011", "20012"]
    assert client.get("/api/stops/10011/od", params={"limit": 0}).status_code == 422
    assert client.get("/api/stops/99999/od").status_code == 404


def test_ask_template(client):
    r = client.post("/api/ask", json={"question": "How many bus stops are there in Alpha?", "engine": "template"})
    body = r.json()
    assert r.status_code == 200 and set(body) == ANSWER_KEYS
    assert body["status"] == "answered" and body["rows"] == [[5]] and body["row_count"] == 1
    assert "LIMIT" in body["sql"]


def test_ask_llm_with_repair(client_for):
    c, chat = client_for("DROP TABLE stops", "SELECT stop_code FROM stops WHERE planning_area = 'BETA'")
    with c:
        body = c.post("/api/ask", json={"question": "Stops in Beta?", "engine": "llm"}).json()
    assert body["status"] == "answered" and body["row_count"] == 3 and len(chat.calls) == 2
    assert body["geojson"]["type"] == "FeatureCollection"


def test_ask_refusal_is_200_with_reason(client_for):
    c, chat = client_for("DROP TABLE stops", "DROP TABLE stops")
    with c:
        body = c.post("/api/ask", json={"question": "Stops in Beta?", "engine": "llm"}).json()
    assert body["status"] == "refused" and body["reason"] == "invalid_after_repair" and body["sql"] is None


def test_ask_prescreen(client):
    body = client.post("/api/ask", json={"question": "DROP TABLE stops;", "engine": "auto"}).json()
    assert body["status"] == "refused" and body["reason"].startswith("prescreen_")


@pytest.mark.parametrize("payload", [{"question": ""}, {"question": "x", "engine": "gpt"}, {}, {"engine": "llm"}])
def test_ask_bad_requests(client, payload):
    assert client.post("/api/ask", json=payload).status_code == 422


def test_database_still_intact_after_attacks(client_for):
    c, _ = client_for("DROP TABLE stops", "DELETE FROM stops")
    with c:
        c.post("/api/ask", json={"question": "Stops?", "engine": "llm"})
        assert c.get("/api/health").json()["stops"] == 9


def test_outer_join_answer_keeps_null_rows_and_maps_only_known_pairs(client_for):
    sql = ("SELECT s.stop_code AS origin_stop, o.destination_stop FROM stops s "
           "LEFT JOIN od_stop_flows o ON s.stop_code = o.origin_stop ORDER BY s.stop_code")
    c, _ = client_for(sql)
    with c:
        response = c.post("/api/ask", json={"question": "Show destinations for every stop, including stops without flows.",
                                          "engine": "llm"})
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered"
    assert answer["row_count"] == len(answer["rows"])
    assert any(row[1] is None for row in answer["rows"])
    mapped = [(f["properties"]["origin_stop"], f["properties"]["destination_stop"])
              for f in answer["geojson"]["features"]]
    assert sorted(mapped) == sorted(tuple(r) for r in answer["rows"] if r[1] is not None)


@pytest.mark.parametrize("sql, detail", [
    ("SELECT list(ST_Point(x_m, y_m)) AS locations FROM stops", "binary results"),
    (r"SELECT list(CAST('\xFF' AS BLOB)) AS payload FROM stops", "binary results"),
    ("SELECT CAST(CAST('{\"1\": \"bus\"}' AS JSON) AS MAP(INTEGER, VARCHAR)) AS payload", "string keys"),
])
def test_unsupported_nested_results_return_auditable_refusal(client_for, sql, detail):
    c, chat = client_for(sql, sql)
    with c:
        response = c.post("/api/ask", json={"question": "Show stop locations", "engine": "llm"})
        assert c.get("/api/health").json()["stops"] == 9
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "refused" and answer["reason"] == "invalid_after_repair"
    assert "unsupported_result" in answer["detail"] and detail in answer["detail"]
    assert answer["rows"] == [] and answer["sql"] is None and answer["geojson"] is None
    assert len(chat.calls) == 2
    assert [attempt["sql"] for attempt in answer["attempts"]] == [sql, sql]
    assert all("unsupported_result" in attempt["error"] for attempt in answer["attempts"])


def test_binary_geometry_can_be_repaired_to_scalar_coordinates(client_for):
    binary = "SELECT list(ST_Point(x_m, y_m)) AS locations FROM stops"
    scalar = "SELECT stop_code, longitude, latitude FROM stops ORDER BY stop_code"
    c, chat = client_for(binary, scalar)
    with c:
        response = c.post("/api/ask", json={"question": "Show stop locations", "engine": "llm"})
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered" and answer["row_count"] == 9
    assert answer["sql"] == scalar + " LIMIT 201"
    assert len(answer["geojson"]["features"]) == 9
    assert "unsupported_result" in answer["attempts"][0]["error"] and len(chat.calls) == 2


def test_nested_scalar_results_keep_json_contract(client_for):
    sql = ("SELECT list(stop_code ORDER BY stop_code) AS codes, list(CAST('2026-10-19' AS DATE)) AS dates, "
           "list(CAST('NaN' AS DOUBLE)) AS missing, list(CAST('1.250' AS DECIMAL(10, 3))) AS amounts, "
           "CAST('12:30:00' AS INTERVAL) AS duration FROM stops")
    c, chat = client_for(sql)
    with c:
        response = c.post("/api/ask", json={"question": "Show stop information", "engine": "llm"})
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered" and len(chat.calls) == 1
    row = answer["rows"][0]
    assert len(row[0]) == 9 and row[0][0] == "10011"
    assert row[1] == ["2026-10-19"] * 9 and row[2] == [None] * 9
    assert row[3] == ["1.250"] * 9 and row[4] == "PT12H30M"

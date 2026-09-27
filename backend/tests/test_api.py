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

"""Spatial correctness on the hand-made fixture (data/fixture/).

The fixture has two square planning areas, ALPHA (103.800-103.810 E) and BETA (103.810-103.820 E), both 1.350-1.360 N.
ALPHA is split at 1.355 N into ALPHA SOUTH and ALPHA NORTH; BETA is one subzone, BETA CENTRE. Nine stops: five in
ALPHA, three in BETA, and 90001 at 103.830 E, outside both.

Expected distances are worked out by hand on the WGS84 ellipsoid (SVY21 uses it, with scale factor 1 on its central
meridian, 103.8333 E, which is within 3 km of every fixture point, so the projection distortion is below 1e-6):
  metres per degree of latitude at phi:   M = pi/180 * a(1-e2) / (1 - e2 sin^2 phi)^1.5
  metres per degree of longitude at phi:  N = pi/180 * a cos(phi) / (1 - e2 sin^2 phi)^0.5
"""
import json
import math

import duckdb
import pytest

from backend.app import build, geo

A, F = 6378137.0, 1 / 298.257223563
E2 = F * (2 - F)


def m_per_deg_lat(phi):
    s = math.sin(math.radians(phi))
    return math.pi / 180 * A * (1 - E2) / (1 - E2 * s * s) ** 1.5


def m_per_deg_lon(phi):
    s = math.sin(math.radians(phi))
    return math.pi / 180 * A * math.cos(math.radians(phi)) / (1 - E2 * s * s) ** 0.5


def hand_distance(lat1, lon1, lat2, lon2):
    phi = (lat1 + lat2) / 2
    return math.hypot((lat2 - lat1) * m_per_deg_lat(phi), (lon2 - lon1) * m_per_deg_lon(phi))


STOPS = {  # code: (lat, lon, planning_area, subzone)
    "10011": (1.3525, 103.8050, "ALPHA", "ALPHA SOUTH"),
    "10012": (1.3526, 103.8052, "ALPHA", "ALPHA SOUTH"),
    "10013": (1.3556, 103.8050, "ALPHA", "ALPHA NORTH"),
    "10014": (1.35655, 103.8050, "ALPHA", "ALPHA NORTH"),
    "10015": (1.3540, 103.8099, "ALPHA", "ALPHA SOUTH"),
    "20011": (1.3525, 103.8150, "BETA", "BETA CENTRE"),
    "20012": (1.3580, 103.8155, "BETA", "BETA CENTRE"),
    "20013": (1.3581, 103.8157, "BETA", "BETA CENTRE"),
    "90001": (1.3525, 103.8300, None, None),
}


def test_svy21_origin():
    """SVY21's false origin: 1d22'00"N 103d50'00"E maps to E 28001.642, N 38744.572 (SLA definition)."""
    con = duckdb.connect()
    con.execute("LOAD spatial")
    x, y = con.execute(f"SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(ST_Point(103 + 50/60, 1 + 22/60), "
                       f"{build.SVY21}) AS p)").fetchone()
    assert x == pytest.approx(28001.642, abs=0.01)
    assert y == pytest.approx(38744.572, abs=0.01)


def test_point_in_polygon_assignment(db):
    got = {c: (pa, sz) for c, pa, sz in db.scalar_rows("SELECT stop_code, planning_area, subzone FROM stops")}
    assert got == {c: (v[2], v[3]) for c, v in STOPS.items()}


def test_region_comes_from_planning_area(db):
    got = dict(db.scalar_rows("SELECT stop_code, region FROM stops"))
    assert got["10011"] == "WEST REGION" and got["20011"] == "EAST REGION" and got["90001"] is None


def test_stop_counts_per_area(db):
    assert dict(db.scalar_rows("SELECT planning_area, n_stops FROM planning_areas")) == {"ALPHA": 5, "BETA": 3}
    assert dict(db.scalar_rows("SELECT subzone, n_stops FROM subzones")) == {
        "ALPHA SOUTH": 3, "ALPHA NORTH": 2, "BETA CENTRE": 3}


def test_stops_per_10k_residents(db):
    got = dict(db.scalar_rows("SELECT planning_area, stops_per_10k_residents FROM planning_areas"))
    assert got["ALPHA"] == pytest.approx(5 * 10000 / 12000)
    assert got["BETA"] == pytest.approx(3 * 10000 / 8000)


@pytest.mark.parametrize("a,b", [("10011", "10012"), ("10011", "10013"), ("10011", "10014"), ("10011", "10015"),
                                 ("10011", "20011"), ("20012", "20013")])
def test_projected_distance_matches_hand_calculation(db, a, b):
    (d,) = db.scalar_rows(f"""SELECT sqrt(power(p.x_m - q.x_m, 2) + power(p.y_m - q.y_m, 2))
                              FROM stops p, stops q WHERE p.stop_code = '{a}' AND q.stop_code = '{b}'""")[0]
    expected = hand_distance(STOPS[a][0], STOPS[a][1], STOPS[b][0], STOPS[b][1])
    assert d == pytest.approx(expected, abs=0.5)


def test_hand_distances_bracket_400_m():
    """The fixture is built so 10013 is inside 400 m of 10011 and 10014 is outside."""
    assert hand_distance(1.3525, 103.805, 1.3556, 103.805) == pytest.approx(342.8, abs=0.1)
    assert hand_distance(1.3525, 103.805, 1.35655, 103.805) == pytest.approx(447.8, abs=0.1)


def test_within_400_m_template_query(make_assistant):
    a, _ = make_assistant()
    ans = a.ask("Which bus stops are within 400 m of stop 10011?", engine="template")
    assert ans.status == "answered"
    assert sorted(r[0] for r in ans.rows) == ["10012", "10013"]
    dist = {r[0]: r[2] for r in ans.rows}
    assert dist["10013"] == pytest.approx(342.8, abs=0.5)


def test_planning_area_area_km2(db):
    got = dict(db.scalar_rows("SELECT planning_area, area_km2 FROM planning_areas"))
    phi = 1.355
    expected = 0.01 * m_per_deg_lat(phi) * 0.01 * m_per_deg_lon(phi) / 1e6
    assert got["ALPHA"] == pytest.approx(expected, rel=2e-3)
    assert got["BETA"] == pytest.approx(expected, rel=2e-3)


def test_service_derived_columns(db):
    rows = {(s, d): (am, pm, n, km) for s, d, am, pm, n, km in db.scalar_rows(
        "SELECT service_no, direction, am_peak_headway_min, pm_peak_headway_min, n_stops, route_km FROM services")}
    assert rows[("1", 1)] == (9.0, 11.0, 4, 2.3)
    assert rows[("2", 1)] == (6.0, 7.0, 4, 1.6)
    assert rows[("3E", 1)] == (None, 17.5, 2, 2.5)


def test_n_services_counts_distinct_services(db):
    got = dict(db.scalar_rows("SELECT stop_code, n_services FROM stops"))
    # 20011 is on service 1 (both directions) and on loop service 2 twice: still two services
    assert got == {"10011": 1, "10012": 1, "10013": 1, "10014": 0, "10015": 1, "20011": 2, "20012": 2, "20013": 2,
                   "90001": 1}


def test_od_layer_lines_run_from_origin_to_destinations(db):
    fc = geo.od_layer(db, "10011", limit=5)
    feats = fc["features"]
    assert [f["properties"]["destination_stop"] for f in feats] == ["20011", "20012", "10013"]
    start, end = feats[0]["geometry"]["coordinates"]
    assert start == [103.805, 1.3525]  # GeoJSON order is [lon, lat]
    assert end == [103.815, 1.3525]


def test_result_layer_points_from_lat_lon(db):
    fc = geo.result_layer(db, ["stop_name", "latitude", "longitude"], [["x", 1.3525, 103.805]])
    assert fc["features"][0]["geometry"] == {"type": "Point", "coordinates": [103.805, 1.3525]}


def test_result_layer_points_from_stop_code(db):
    fc = geo.result_layer(db, ["stop_code", "weekday_boardings"], [["20012", 500.0], ["99999", 1.0]])
    assert len(fc["features"]) == 1
    assert fc["features"][0]["geometry"]["coordinates"] == [103.8155, 1.358]
    assert fc["features"][0]["properties"] == {"stop_code": "20012", "weekday_boardings": 500.0}


def test_result_layer_lines_from_stop_pairs(db):
    fc = geo.result_layer(db, ["origin_stop", "destination_stop", "weekday_trips"], [["20011", "10011", 280.0]])
    assert fc["features"][0]["geometry"] == {"type": "LineString", "coordinates": [[103.815, 1.3525], [103.805, 1.3525]]}
    fc = geo.result_layer(db, ["from_stop", "to_stop"], [["10011", "10013"]])
    assert fc["features"][0]["geometry"]["type"] == "LineString"


def test_result_layer_keeps_valid_lines_when_outer_join_has_missing_stops(db):
    rows = [["10011", "20011", 280.0], ["10012", None, None], [None, "20012", 1.0]]
    fc = geo.result_layer(db, ["origin_stop", "destination_stop", "weekday_trips"], rows)
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["weekday_trips"] == 280.0
    assert rows[1] == ["10012", None, None]  # map filtering never removes table rows


@pytest.mark.parametrize("lat,lon", [("north", 103.8), (1.35, "east"), (True, 103.8),
                                    (91.0, 103.8), (1.35, 181.0), (float("nan"), 103.8),
                                    (1.35, float("inf")), (None, 103.8), ([1.35], 103.8),
                                    (1.35, {"longitude": 103.8})])
def test_result_layer_omits_unmappable_coordinates_without_losing_valid_points(db, lat, lon):
    fc = geo.result_layer(db, ["latitude", "longitude"], [[lat, lon], [1.35, 103.8]])
    assert [f["geometry"]["coordinates"] for f in fc["features"]] == [[103.8, 1.35]]
    json.dumps(fc, allow_nan=False)


def test_result_layer_mixed_identifier_types_do_not_crash_map_lookup(db):
    fc = geo.result_layer(db, ["stop_code"], [["10011"], [10012], [None], [["10012"]]])
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"] == {"stop_code": "10011"}
    fc = geo.result_layer(db, ["planning_area"], [["ALPHA"], [3], [None], [{"area": "BETA"}]])
    assert len(fc["features"]) == 1
    fc = geo.result_layer(db, ["subzone"], [["ALPHA NORTH"], [3], [None], [["ALPHA SOUTH"]]])
    assert len(fc["features"]) == 1
    fc = geo.result_layer(db, ["from_stop", "to_stop"],
                          [["10011", "20011"], [None, ["20012"]], [10012, "20011"]])
    assert len(fc["features"]) == 1


def test_result_layer_accepts_numeric_coordinates_at_geographic_bounds(db):
    from decimal import Decimal

    fc = geo.result_layer(db, ["latitude", "longitude"],
                          [[90, -180], [-90, 180], [Decimal("1.35"), Decimal("103.8")]])
    assert [f["geometry"]["coordinates"] for f in fc["features"]] == [
        [-180.0, 90.0], [180.0, -90.0], [103.8, 1.35]]


def test_result_layer_returns_no_map_for_unlocated_rows_but_preserves_table(db):
    rows = [[None, "10011"], ["missing", "99999"]]
    assert geo.result_layer(db, ["origin_stop", "destination_stop"], rows) is None
    assert rows == [[None, "10011"], ["missing", "99999"]]



def test_result_layer_polygons(db):
    fc = geo.result_layer(db, ["subzone", "coverage_walk_400m"], [["ALPHA NORTH", 0.63]])
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    lats = [p[1] for p in ring]
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    assert min(lats) == pytest.approx(1.355, abs=1e-6) and max(lats) == pytest.approx(1.360, abs=1e-6)
    fc = geo.result_layer(db, ["planning_area", "n"], [["BETA", 3]])
    lons = [p[0] for p in fc["features"][0]["geometry"]["coordinates"][0]]
    assert min(lons) == pytest.approx(103.81, abs=1e-6) and max(lons) == pytest.approx(103.82, abs=1e-6)


def test_result_layer_none_without_geometry(db):
    assert geo.result_layer(db, ["n"], [[5]]) is None
    assert geo.result_layer(db, ["stop_code"], []) is None


def test_coverage_layer_has_every_subzone(db):
    fc = geo.coverage_layer(db)
    props = {f["properties"]["subzone"]: f["properties"]["coverage_walk_400m"] for f in fc["features"]}
    assert props == {"ALPHA NORTH": 0.63, "ALPHA SOUTH": 0.75, "BETA CENTRE": 0.85}
    json.dumps(fc)  # serialisable

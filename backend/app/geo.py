"""GeoJSON for the map: base layers, and a layer built from any query result.

A result gets geometry from the columns it returns, in this order:
  latitude + longitude            -> points
  origin/destination stop codes   -> lines between the two stops (also from_stop/to_stop)
  a stop code column              -> points at those stops
  subzone                         -> subzone polygons
  planning area (or origin_area)  -> planning-area polygons
Otherwise the answer has no map layer.
"""
import json
import math
from decimal import Decimal
from numbers import Real

from .db import Database

STOP_COLS = ("stop_code", "destination_stop", "origin_stop", "from_stop", "to_stop")
LINE_PAIRS = (("origin_stop", "destination_stop"), ("from_stop", "to_stop"))
AREA_COLS = ("planning_area", "origin_area", "destination_area")


def _fc(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def point(lon: float, lat: float) -> dict:
    return {"type": "Point", "coordinates": [lon, lat]}


def line(a: tuple[float, float], b: tuple[float, float]) -> dict:
    """a and b are (lon, lat)."""
    return {"type": "LineString", "coordinates": [list(a), list(b)]}


def _valid_coords(lon, lat) -> bool:
    # Generated SQL can alias arbitrary values as coordinates. Keep those rows in
    # the table, but only put finite geographic coordinates on the map.
    return all(isinstance(v, (Real, Decimal)) and not isinstance(v, bool)
               and math.isfinite(v) for v in (lon, lat)) and -180 <= lon <= 180 and -90 <= lat <= 90


def _keys(rows: list[list], *indices: int) -> set[str]:
    # An outer join can leave NULLs, and arbitrary SELECTs can return numbers or
    # even nested values under a recognized column name. Codes stay strings;
    # coercing a number would also destroy meaningful leading zeroes.
    return {r[i] for r in rows for i in indices if isinstance(r[i], str) and r[i]}


def _known(value, lookup: dict) -> bool:
    return isinstance(value, str) and value in lookup


def _stop_coords(db: Database, codes: set[str]) -> dict[str, tuple[float, float]]:
    if not codes:
        return {}
    rows = db.scalar_rows("select stop_code, longitude, latitude from stops where list_contains(?, stop_code)",
                          [sorted(codes)])
    return {c: (float(lon), float(lat)) for c, lon, lat in rows if _valid_coords(lon, lat)}


def _shapes(db: Database, table: str, key: str, names: set[str]) -> dict[str, dict]:
    if not names:
        return {}
    rows = db.scalar_rows(f"select {key}, geojson from {table} where list_contains(?, {key})", [sorted(names)])
    return {k: json.loads(g) for k, g in rows}


def result_layer(db: Database, columns: list[str], rows: list[list]) -> dict | None:
    if not rows:
        return None
    cols = [c.lower() for c in columns]
    idx = {c: i for i, c in enumerate(cols)}
    props = [dict(zip(columns, r)) for r in rows]

    if "latitude" in idx and "longitude" in idx:
        feats = [{"type": "Feature", "geometry": point(float(r[idx["longitude"]]), float(r[idx["latitude"]])), "properties": p}
                 for r, p in zip(rows, props) if _valid_coords(r[idx["longitude"]], r[idx["latitude"]])]
        return _fc(feats) if feats else None

    for a, b in LINE_PAIRS:
        if a in idx and b in idx:
            coords = _stop_coords(db, _keys(rows, idx[a], idx[b]))
            feats = [{"type": "Feature", "geometry": line(coords[r[idx[a]]], coords[r[idx[b]]]), "properties": p}
                     for r, p in zip(rows, props) if _known(r[idx[a]], coords) and _known(r[idx[b]], coords)]
            return _fc(feats) if feats else None

    for c in STOP_COLS:
        if c in idx:
            coords = _stop_coords(db, _keys(rows, idx[c]))
            feats = [{"type": "Feature", "geometry": point(*coords[r[idx[c]]]), "properties": p}
                     for r, p in zip(rows, props) if _known(r[idx[c]], coords)]
            return _fc(feats) if feats else None

    if "subzone" in idx:
        shapes = _shapes(db, "subzone_shapes", "subzone", _keys(rows, idx["subzone"]))
        feats = [{"type": "Feature", "geometry": shapes[r[idx["subzone"]]], "properties": p}
                 for r, p in zip(rows, props) if _known(r[idx["subzone"]], shapes)]
        return _fc(feats) if feats else None

    for c in AREA_COLS:
        if c in idx:
            shapes = _shapes(db, "planning_area_shapes", "planning_area", _keys(rows, idx[c]))
            feats = [{"type": "Feature", "geometry": shapes[r[idx[c]]], "properties": p}
                     for r, p in zip(rows, props) if _known(r[idx[c]], shapes)]
            return _fc(feats) if feats else None
    return None


# ---- base layers
def stops_layer(db: Database) -> dict:
    rows = db.scalar_rows("""select stop_code, stop_name, road_name, longitude, latitude, weekday_boardings,
                                    planning_area, n_services from stops order by stop_code""")
    return _fc([{"type": "Feature", "geometry": point(lon, lat),
                 "properties": {"stop_code": c, "stop_name": n, "road_name": r, "weekday_boardings": b,
                                "planning_area": pa, "n_services": ns}}
                for c, n, r, lon, lat, b, pa, ns in rows])


def coverage_layer(db: Database) -> dict:
    rows = db.scalar_rows("""select s.subzone, s.planning_area, s.residents, s.coverage_walk_400m,
                                    s.coverage_straight_400m, s.n_stops, g.geojson
                             from subzones s join subzone_shapes g using (subzone) order by s.subzone""")
    return _fc([{"type": "Feature", "geometry": json.loads(g),
                 "properties": {"subzone": sz, "planning_area": pa, "residents": res, "coverage_walk_400m": cw,
                                "coverage_straight_400m": cs, "n_stops": n}}
                for sz, pa, res, cw, cs, n, g in rows])


def od_layer(db: Database, stop_code: str, limit: int = 15) -> dict:
    """Desire lines from one stop to its top destinations by weekday trips."""
    rows = db.scalar_rows("""select o.destination_stop, d.stop_name, o.weekday_trips, o.am_peak_trips,
                                    s.longitude, s.latitude, d.longitude, d.latitude
                             from od_stop_flows o
                             join stops s on s.stop_code = o.origin_stop
                             join stops d on d.stop_code = o.destination_stop
                             where o.origin_stop = ? and o.destination_stop <> o.origin_stop
                             order by o.weekday_trips desc limit ?""", [stop_code, limit])
    return _fc([{"type": "Feature", "geometry": line((slon, slat), (dlon, dlat)),
                 "properties": {"origin_stop": stop_code, "destination_stop": d, "destination_name": n,
                                "weekday_trips": t, "am_peak_trips": am}}
                for d, n, t, am, slon, slat, dlon, dlat in rows])

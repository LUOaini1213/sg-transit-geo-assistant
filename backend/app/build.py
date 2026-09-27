"""Build the served DuckDB file from a staging directory.

The staging directory holds plain CSV and GeoJSON files in one fixed layout. `scripts/prepare_from_sg_bus.py` writes
it from a local copy of the sg-bus-network-monitor repository; `data/fixture/` holds a small hand-made copy for tests
and CI. The spatial steps here (projection to SVY21, point-in-polygon, areas) are what the tests check.

Usage: python -m backend.app.build <staging_dir> <out.duckdb>
"""
import sys
from pathlib import Path

import duckdb

SVY21 = "'EPSG:4326', 'EPSG:3414', always_xy := true"
REQUIRED = ["stops.csv", "stop_demand.csv", "services.csv", "route_stops.csv", "od_stop_flows.csv",
            "od_area_flows.csv", "planning_areas.csv", "planning_areas.geojson", "subzones.csv", "subzones.geojson",
            "corridor_links.csv", "stop_changes.csv"]


def build(staging: Path, out: Path) -> dict:
    staging, out = Path(staging), Path(out)
    missing = [f for f in REQUIRED if not (staging / f).exists()]
    if missing:
        raise FileNotFoundError(f"staging directory {staging} lacks {missing}")
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out))
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")
    # LTA headway band such as '09-12' -> 10.5 minutes; '10' -> 10; '-' or blank -> NULL
    con.execute(r"""create temp macro headway_mid(b) as case
        when regexp_full_match(trim(b), '\d+\s*-\s*\d+')
            then (cast(trim(split_part(b, '-', 1)) as int) + cast(trim(split_part(b, '-', 2)) as int)) / 2.0
        when regexp_full_match(trim(b), '\d+') then cast(trim(b) as double) end""")

    def csv(name, types=None):
        t = ", types = {" + ", ".join(f"'{k}': '{v}'" for k, v in (types or {}).items()) + "}" if types else ""
        return f"read_csv('{(staging / name).as_posix()}', header = true{t})"

    code = {"stop_code": "VARCHAR"}
    # ---- polygons: projected to SVY21 once, simplified copies for the web map
    con.execute(f"""create table _pa_geo as
        select upper(planning_area) as planning_area, ST_Transform(geom, {SVY21}) as g
        from ST_Read('{(staging / 'planning_areas.geojson').as_posix()}')""")
    con.execute(f"""create table _sz_geo as
        select upper(subzone) as subzone, upper(planning_area) as planning_area, ST_Transform(geom, {SVY21}) as g
        from ST_Read('{(staging / 'subzones.geojson').as_posix()}')""")

    # ---- stops: position in metres, then the subzone that contains it
    con.execute(f"""create table _stops as
        select s.stop_code, s.stop_name, s.road_name, s.latitude, s.longitude,
               ST_Transform(ST_Point(s.longitude, s.latitude), {SVY21}) as p
        from {csv('stops.csv', code)} s""")
    con.execute(f"""create table stops as
        with located as (
            select s.stop_code, z.subzone, z.planning_area
            from _stops s join _sz_geo z on ST_Contains(z.g, s.p)
            qualify row_number() over (partition by s.stop_code order by z.subzone) = 1)
        select s.stop_code, s.stop_name, s.road_name, s.latitude, s.longitude,
               ST_X(s.p) as x_m, ST_Y(s.p) as y_m,
               l.planning_area, l.subzone, pa.region,
               d.weekday_boardings, d.am_peak_share, d.pm_peak_share, cast(d.peak_hour as integer) as peak_hour,
               cast(coalesce(rs.n, 0) as integer) as n_services
        from _stops s
        left join located l using (stop_code)
        left join {csv('planning_areas.csv')} pa on upper(pa.planning_area) = l.planning_area
        left join {csv('stop_demand.csv', code)} d using (stop_code)
        left join (select stop_code, count(distinct service_no) as n
                   from {csv('route_stops.csv', {**code, 'service_no': 'VARCHAR'})} group by 1) rs using (stop_code)
        order by s.stop_code""")

    rs_types = {**code, "service_no": "VARCHAR"}
    con.execute(f"""create table route_stops as
        select cast(service_no as varchar) as service_no, cast(direction as integer) as direction,
               cast(stop_sequence as integer) as stop_sequence, stop_code, cast(distance_km as double) as distance_km
        from {csv('route_stops.csv', rs_types)} order by 1, 2, 3""")
    con.execute(f"""create table services as
        select s.service_no, cast(s.direction as integer) as direction, s.operator, s.category,
               s.origin_stop, s.destination_stop,
               headway_mid(s.am_peak_headway) as am_peak_headway_min,
               headway_mid(s.pm_peak_headway) as pm_peak_headway_min,
               cast(r.n_stops as integer) as n_stops, r.route_km
        from {csv('services.csv', {'service_no': 'VARCHAR', 'origin_stop': 'VARCHAR', 'destination_stop': 'VARCHAR', 'am_peak_headway': 'VARCHAR', 'pm_peak_headway': 'VARCHAR'})} s
        left join (select service_no, direction, count(*) as n_stops, max(distance_km) as route_km
                   from route_stops group by 1, 2) r
          on r.service_no = s.service_no and r.direction = cast(s.direction as integer)
        order by 1, 2""")

    con.execute(f"""create table od_stop_flows as
        select origin_stop, destination_stop, weekday_trips, am_peak_trips
        from {csv('od_stop_flows.csv', {'origin_stop': 'VARCHAR', 'destination_stop': 'VARCHAR'})}""")
    con.execute(f"""create table od_area_flows as
        select upper(origin_area) as origin_area, upper(destination_area) as destination_area, weekday_trips
        from {csv('od_area_flows.csv')}""")

    con.execute(f"""create table planning_areas as
        select upper(a.planning_area) as planning_area, a.region, cast(a.residents as integer) as residents,
               ST_Area(g.g) / 1e6 as area_km2,
               cast(coalesce(n.n, 0) as integer) as n_stops,
               case when a.residents > 0 then coalesce(n.n, 0) * 10000.0 / a.residents end as stops_per_10k_residents,
               a.coverage_straight_400m, a.coverage_walk_400m, a.residents_outside_walk_400m
        from {csv('planning_areas.csv')} a
        left join (select planning_area, ST_Union_Agg(g) as g from _pa_geo group by 1) g
          on g.planning_area = upper(a.planning_area)
        left join (select planning_area, count(*) as n from stops where planning_area is not null group by 1) n
          on n.planning_area = upper(a.planning_area)
        order by 1""")
    con.execute(f"""create table subzones as
        select upper(z.subzone) as subzone, upper(z.planning_area) as planning_area, cast(z.residents as integer) as residents,
               ST_Area(g.g) / 1e6 as area_km2, cast(coalesce(n.n, 0) as integer) as n_stops,
               z.coverage_straight_400m, z.coverage_walk_400m
        from {csv('subzones.csv')} z
        left join _sz_geo g on g.subzone = upper(z.subzone)
        left join (select subzone, count(*) as n from stops where subzone is not null group by 1) n
          on n.subzone = upper(z.subzone)
        order by 1""")

    con.execute(f"""create table corridor_links as
        select from_stop, to_stop, cast(n_services as integer) as n_services, service_list, link_km, am_peak_buses_per_hour
        from {csv('corridor_links.csv', {'from_stop': 'VARCHAR', 'to_stop': 'VARCHAR', 'service_list': 'VARCHAR'})}""")
    con.execute(f"""create table stop_changes as
        select stop_code, baseline_weekday_boardings, aug_weekday_boardings, pct_change,
               nullif(flag, '') as flag, nullif(category, '') as category
        from {csv('stop_changes.csv', {**code, 'flag': 'VARCHAR', 'category': 'VARCHAR'})}""")

    # ---- shapes for the map: simplified to ~5 m in SVY21, then back to WGS84 GeoJSON text. Not visible to the model.
    back = "'EPSG:3414', 'EPSG:4326', always_xy := true"
    con.execute(f"""create table planning_area_shapes as
        select planning_area, ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(ST_Union_Agg(g), 5), {back})) as geojson
        from _pa_geo group by 1""")
    con.execute(f"""create table subzone_shapes as
        select subzone, planning_area, ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(g, 5), {back})) as geojson
        from _sz_geo""")
    for t in ["_pa_geo", "_sz_geo", "_stops"]:
        con.execute(f"drop table {t}")
    counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in
              ["stops", "services", "route_stops", "od_stop_flows", "od_area_flows", "planning_areas", "subzones",
               "corridor_links", "stop_changes", "planning_area_shapes", "subzone_shapes"]}
    con.execute("checkpoint")
    con.close()
    return counts


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(build(Path(sys.argv[1]), Path(sys.argv[2])))

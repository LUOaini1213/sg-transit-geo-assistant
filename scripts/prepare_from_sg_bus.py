"""Write data/staging/ from a local clone of github.com/LUOaini1213/sg-bus-network-monitor.

The source repository is only read. It must have run its own fetch scripts, because the stop, route, service and
origin-destination files come from its data/raw/ (LTA DataMall downloads, which are not redistributed here). The
planning-area and subzone tables come from its published outputs/.

Usage:
    python scripts/prepare_from_sg_bus.py [path/to/sg-bus-network-monitor]   (default: $SG_BUS_REPO or ../sg-bus-network-monitor)
    python -m backend.app.build data/staging data/transit.duckdb
"""
import calendar
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "staging"
MONTH = "2026-08"
# MOM 2026 public holidays falling in August (9 Aug National Day on a Sunday, observed on Monday 10 Aug).
# The source repository uses the same list; the check against its od_quality.csv below catches a mismatch.
AUG_HOLIDAYS = {dt.date(2026, 8, 9), dt.date(2026, 8, 10)}


def weekdays(year: int, month: int, holidays: set) -> int:
    days = [dt.date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]
    return sum(1 for d in days if d.weekday() < 5 and d not in holidays)


def main(src: Path):
    raw, outputs = src / "data" / "raw", src / "outputs"
    need = [raw / "datamall" / "BusStops_20260926.json", raw / "datamall" / "BusRoutes_20260926.json",
            raw / "datamall" / "BusServices_20260926.json", raw / "datamall" / "origin_destination_bus_202608.csv",
            raw / "mp2019_planning_area.geojson", outputs / "coverage_subzone.geojson"]
    absent = [str(p) for p in need if not p.exists()]
    if absent:
        sys.exit(f"missing in the source repository (run its fetch scripts first): {absent}")
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")

    def p(path):
        return Path(path).as_posix()

    def copy(sql, name):
        con.execute(f"copy ({sql}) to '{p(OUT / name)}' (header, delimiter ',')")
        return con.execute(f"select count(*) from ({sql})").fetchone()[0]

    counts = {}
    dm = raw / "datamall"
    counts["stops"] = copy(f"""select BusStopCode as stop_code, Description as stop_name, RoadName as road_name,
            Latitude as latitude, Longitude as longitude
        from read_json('{p(dm / 'BusStops_20260926.json')}', columns = {{BusStopCode: 'VARCHAR', Description: 'VARCHAR',
             RoadName: 'VARCHAR', Latitude: 'DOUBLE', Longitude: 'DOUBLE'}}) order by 1""", "stops.csv")
    counts["stop_demand"] = copy(f"""select stop as stop_code, weekday_tap_in_per_day as weekday_boardings, am_peak_share,
            pm_peak_share, peak_hour
        from read_csv('{p(outputs / 'stop_profile_2026-08.csv')}', types = {{'stop': 'VARCHAR'}}) order by 1""",
                                 "stop_demand.csv")
    counts["services"] = copy(f"""select ServiceNo as service_no, Direction as direction, Operator as operator,
            Category as category, OriginCode as origin_stop, DestinationCode as destination_stop,
            AM_Peak_Freq as am_peak_headway, PM_Peak_Freq as pm_peak_headway
        from read_json('{p(dm / 'BusServices_20260926.json')}', columns = {{ServiceNo: 'VARCHAR', Direction: 'INTEGER',
             Operator: 'VARCHAR', Category: 'VARCHAR', OriginCode: 'VARCHAR', DestinationCode: 'VARCHAR',
             AM_Peak_Freq: 'VARCHAR', AM_Offpeak_Freq: 'VARCHAR', PM_Peak_Freq: 'VARCHAR', PM_Offpeak_Freq: 'VARCHAR',
             LoopDesc: 'VARCHAR'}}) order by 1, 2""", "services.csv")
    counts["route_stops"] = copy(f"""select ServiceNo as service_no, Direction as direction, StopSequence as stop_sequence,
            BusStopCode as stop_code, Distance as distance_km
        from read_json('{p(dm / 'BusRoutes_20260926.json')}', columns = {{ServiceNo: 'VARCHAR', Operator: 'VARCHAR',
             Direction: 'INTEGER', StopSequence: 'INTEGER', BusStopCode: 'VARCHAR', Distance: 'DOUBLE',
             WD_FirstBus: 'VARCHAR', WD_LastBus: 'VARCHAR', SAT_FirstBus: 'VARCHAR', SAT_LastBus: 'VARCHAR',
             SUN_FirstBus: 'VARCHAR', SUN_LastBus: 'VARCHAR'}}) order by 1, 2, 3""", "route_stops.csv")

    n_wd = weekdays(2026, 8, AUG_HOLIDAYS)
    od = dm / "origin_destination_bus_202608.csv"
    counts["od_stop_flows"] = copy(f"""select lpad(ORIGIN_PT_CODE, 5, '0') as origin_stop,
            lpad(DESTINATION_PT_CODE, 5, '0') as destination_stop,
            sum(TOTAL_TRIPS) / {n_wd}.0 as weekday_trips,
            sum(case when cast(TIME_PER_HOUR as int) between 7 and 8 then TOTAL_TRIPS else 0 end) / {n_wd}.0 as am_peak_trips
        from read_csv('{p(od)}', types = {{'ORIGIN_PT_CODE': 'VARCHAR', 'DESTINATION_PT_CODE': 'VARCHAR'}})
        where DAY_TYPE = 'WEEKDAY' group by 1, 2 order by 1, 2""", "od_stop_flows.csv")
    # consistency with the source repository's own OD quality table
    ours = con.execute(f"select sum(weekday_trips) from read_csv('{p(OUT / 'od_stop_flows.csv')}')").fetchone()[0]
    theirs = con.execute(f"""select od_weekday_trips from read_csv('{p(outputs / 'od_quality.csv')}')
                             where month = '{MONTH}'""").fetchone()[0] / n_wd
    assert abs(ours - theirs) / theirs < 1e-9, (ours, theirs)

    counts["od_area_flows"] = copy(f"""select origin_pa as origin_area, dest_pa as destination_area, trips_per_weekday as weekday_trips
        from read_csv('{p(outputs / 'od_planning_area_2026-08.csv')}') order by 3 desc""", "od_area_flows.csv")

    counts["planning_areas"] = copy(f"""select g.PLN_AREA_N as planning_area, g.REGION_N as region, c.residents,
            c.resident_coverage as coverage_straight_400m, w.coverage_walk as coverage_walk_400m,
            w.residents_outside_walk as residents_outside_walk_400m
        from ST_Read('{p(raw / 'mp2019_planning_area.geojson')}') g
        left join read_csv('{p(outputs / 'coverage_planning_area.csv')}') c on c.PLN_AREA_N = g.PLN_AREA_N
        left join read_csv('{p(outputs / 'coverage_walk_planning_area.csv')}') w on w.PLN_AREA_N = g.PLN_AREA_N
        order by 1""", "planning_areas.csv")
    con.execute(f"""copy (select PLN_AREA_N as planning_area, REGION_N as region, geom
                          from ST_Read('{p(raw / 'mp2019_planning_area.geojson')}'))
                    to '{p(OUT / 'planning_areas.geojson')}' with (format gdal, driver 'GeoJSON')""")
    counts["subzones"] = copy(f"""select c.SUBZONE_N as subzone, c.PLN_AREA_N as planning_area, c.residents,
            case when c.residents > 0 then c.residents_covered / c.residents end as coverage_straight_400m,
            w.share_walk as coverage_walk_400m
        from read_csv('{p(outputs / 'coverage_subzone.csv')}') c
        left join read_csv('{p(outputs / 'coverage_walk_subzone.csv')}') w on w.SUBZONE_N = c.SUBZONE_N
        order by 1""", "subzones.csv")
    con.execute(f"""copy (select SUBZONE_N as subzone, PLN_AREA_N as planning_area, geom
                          from ST_Read('{p(outputs / 'coverage_subzone.geojson')}'))
                    to '{p(OUT / 'subzones.geojson')}' with (format gdal, driver 'GeoJSON')""")
    counts["corridor_links"] = copy(f"""select from_stop, to_stop, services as n_services, service_list, link_km,
            AM_Peak_bph as am_peak_buses_per_hour
        from read_csv('{p(outputs / 'corridor_links.csv')}', types = {{'from_stop': 'VARCHAR', 'to_stop': 'VARCHAR'}})""",
                                    "corridor_links.csv")
    counts["stop_changes"] = copy(f"""select s.stop as stop_code, s.baseline_median as baseline_weekday_boardings,
            s."2026-08" as aug_weekday_boardings, s.pct_change, s.flag, a.category
        from read_csv('{p(outputs / 'surveillance_2026-08_vs_baseline_median.csv')}', types = {{'stop': 'VARCHAR'}}) s
        left join read_csv('{p(outputs / 'anomaly_evidence.csv')}', types = {{'stop': 'VARCHAR'}}) a on a.stop = s.stop
        order by 1""", "stop_changes.csv")

    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (OUT / "SOURCES.json").write_text(json.dumps({
        "source_repository": "https://github.com/LUOaini1213/sg-bus-network-monitor",
        "source_commit": head or None,
        "od_month": MONTH, "od_weekdays": n_wd, "rows": counts,
        "prepared": dt.date.today().isoformat()}, indent=2), encoding="utf-8")
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    default = os.environ.get("SG_BUS_REPO", str(REPO.parent / "sg-bus-network-monitor"))
    main(Path(sys.argv[1] if len(sys.argv) > 1 else default))

"""Deterministic fallback: keyword rules that fill SQL templates.

No model is involved, so it is fast and predictable, but it only understands the question shapes below, and the
rules were written against the dev questions in eval/ only. Anything else returns None and the caller refuses.
Entity values (places, stops, services) are looked up in the database and
inserted as quoted literals from that lookup, never copied from the question text.
"""
import re
from dataclasses import dataclass

from .db import Database

WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "twenty": 20}
DIST = "sqrt(power(a.x_m - b.x_m, 2) + power(a.y_m - b.y_m, 2))"


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass
class Catalog:
    planning_areas: list[str]
    subzones: list[str]
    regions: list[str]
    stop_names: dict[str, list[str]]  # lower-case name -> codes
    stop_codes: set[str]
    stop_display: dict[str, str]  # code -> name as stored
    services: set[str]
    roads: dict[str, str]  # lower-case -> as stored
    operators: set[str]
    categories: set[str]

    @classmethod
    def load(cls, db: Database) -> "Catalog":
        names: dict[str, list[str]] = {}
        display: dict[str, str] = {}
        for code, name in db.scalar_rows("select stop_code, stop_name from stops"):
            names.setdefault(name.lower(), []).append(code)
            display[code] = name
        return cls(
            planning_areas=sorted((r[0] for r in db.scalar_rows("select planning_area from planning_areas")), key=len, reverse=True),
            subzones=sorted((r[0] for r in db.scalar_rows("select subzone from subzones")), key=len, reverse=True),
            regions=[r[0] for r in db.scalar_rows("select distinct region from planning_areas where region is not null")],
            stop_names=names,
            stop_codes=set(display),
            stop_display=display,
            services={r[0].lower() for r in db.scalar_rows("select distinct service_no from services")},
            roads={r[0].lower(): r[0] for r in db.scalar_rows("select distinct road_name from stops where road_name is not null")},
            operators={r[0] for r in db.scalar_rows("select distinct operator from services")},
            categories={r[0] for r in db.scalar_rows("select distinct category from services")},
        )


def _has(text: str, *words: str) -> bool:
    return any(re.search(r"\b" + w + r"\b", text) for w in words)


def _find_names(text_upper: str, names: list[str]) -> list[str]:
    """Names (longest first) that occur as whole words, without overlapping a longer match."""
    found, taken = [], []
    for n in names:
        for m in re.finditer(r"(?<![A-Z])" + re.escape(n) + r"(?![A-Z])", text_upper):
            if not any(m.start() < e and s < m.end() for s, e in taken):
                found.append((m.start(), n))
                taken.append((m.start(), m.end()))
    return [n for _, n in sorted(found)]


def _top_n(text: str, default: int = 10) -> int:
    m = re.search(r"\b(?:top|first|busiest|largest|highest|lowest|bottom|quietest|most|least)\s+(\d{1,3})\b", text) \
        or re.search(r"\b(\d{1,3})\s+(?:busiest|largest|highest|lowest|quietest|most|least|top)\b", text) \
        or re.search(r"\b(\d{1,3})\s+(?:bus\s+)?(?:stops|planning areas|areas|subzones|links|pairs|services|destination)", text)
    m = m or re.search(r"^(?:which|list|show|what are)\s+(?:the\s+)?(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    for w, n in WORD_NUMBERS.items():
        if re.search(r"\b(?:top\s+)?" + w + r"\s+(?:busiest|largest|highest|lowest|quietest|most|stops|planning|areas|subzones|links|pairs|destination)", text):
            return n
    return default


def _number_before(text: str, unit_rx: str) -> float | None:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*" + unit_rx, text)
    return float(m.group(1).replace(",", "")) if m else None


class TemplateEngine:
    def __init__(self, catalog: Catalog):
        self.c = catalog

    # ---- entity extraction
    def stops_in(self, q: str, ql: str) -> list[str]:
        codes = [c for c in re.findall(r"\b(\d{5})\b", q) if c in self.c.stop_codes]
        if codes:
            return codes
        best = None
        for name, cs in self.c.stop_names.items():
            if len(name) >= 5 and name in ql and re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", ql):
                if best is None or len(name) > len(best[0]):
                    best = (name, cs)
        return sorted(best[1]) if best else []

    def service_in(self, ql: str) -> str | None:
        m = re.search(r"\b(?:service|bus|route)\s+(?:no\.?\s*|number\s+)?([0-9]{1,3}[a-z]?)\b", ql)
        if m and m.group(1) in self.c.services:
            return m.group(1)
        return None

    def areas_in(self, q: str) -> list[str]:
        return _find_names(q.upper(), self.c.planning_areas)

    def region_in(self, q: str) -> str | None:
        u = q.upper()
        for r in self.c.regions:
            short = r.replace(" REGION", "")
            if re.search(r"\b" + re.escape(short) + r"\s+REGION\b", u):
                return r
        return None

    def road_in(self, ql: str) -> str | None:
        best = None
        for low, name in self.c.roads.items():
            if len(low) >= 6 and low in ql and re.search(r"(?<![a-z])" + re.escape(low) + r"(?![a-z])", ql):
                if best is None or len(low) > len(best[0]):
                    best = (low, name)
        return best[1] if best else None

    def hints(self, question: str) -> list[str]:
        """Database values the question mentions, spelled as stored. Given to the model to reduce spelling errors."""
        q = question.strip()
        ql = q.lower()
        out = []
        road = self.road_in(ql)
        areas = [a for a in self.areas_in(q) if not (road and a.lower() in road.lower())]
        for a in areas:
            out.append(f"planning_area = '{a}'")
        region = self.region_in(q)
        if region:
            out.append(f"region = '{region}'")
        if road:
            out.append(f"road_name = '{road}'")
        stops = self.stops_in(q, ql)
        if stops:
            out.append("stop_code IN (" + ", ".join(f"'{c}'" for c in stops) + ")"
                       + f" -- stop_name '{self.c.stop_display[stops[0]]}'")
        service = self.service_in(ql)
        if service:
            out.append(f"service_no = '{service}'")
        return out

    # ---- the rules, most specific first
    def to_sql(self, question: str) -> str | None:
        q = question.strip()
        ql = q.lower()
        stops = self.stops_in(q, ql)
        service = self.service_in(ql)
        areas = self.areas_in(q)
        region = self.region_in(q)
        road = self.road_in(ql)
        if road:
            areas = [a for a in areas if a.lower() not in road.lower()]
        n = _top_n(ql)
        in_stops = "(" + ", ".join(lit(s) for s in stops) + ")"
        coverage_col = "coverage_straight_400m" if _has(ql, "straight", "straight-line") else "coverage_walk_400m"

        # stops within a distance of a stop
        dist = _number_before(ql, r"(?:m|metres|meters)\b") or ((_number_before(ql, r"km\b") or 0) * 1000 or None)
        if stops and dist and _has(ql, "within", "near", "around", "nearby"):
            return (f"SELECT b.stop_code, b.stop_name, round({DIST}, 1) AS distance_m "
                    f"FROM stops a JOIN stops b ON b.stop_code <> a.stop_code WHERE a.stop_code IN {in_stops} "
                    f"AND {DIST} <= {dist:g} ORDER BY distance_m")

        # where trips from a stop go
        if stops and _has(ql, "destination", "destinations", "go", "where do", "trips from"):
            return (f"SELECT o.destination_stop, s.stop_name, o.weekday_trips FROM od_stop_flows o "
                    f"JOIN stops s ON s.stop_code = o.destination_stop WHERE o.origin_stop IN {in_stops} "
                    f"ORDER BY o.weekday_trips DESC LIMIT {n}")

        # services at a stop
        if stops and _has(ql, "services", "buses", "routes") and _has(ql, "stop", "stops", "serve", "serves", "at"):
            return f"SELECT DISTINCT service_no FROM route_stops WHERE stop_code IN {in_stops} ORDER BY service_no"

        # boardings at a stop
        if stops and _has(ql, "boardings", "tap-ins"):
            return f"SELECT stop_code, stop_name, weekday_boardings FROM stops WHERE stop_code IN {in_stops}"

        # one service
        if service and not stops:
            direction = re.search(r"\bdirection\s+([12])\b", ql)
            where = f"service_no = {lit(service)}" + (f" AND direction = {direction.group(1)}" if direction else "")
            if _has(ql, "first", "last", "terminal", "terminus"):
                return f"SELECT service_no, direction, origin_stop, destination_stop FROM services WHERE {where}"
            if _has(ql, "stops"):
                return f"SELECT service_no, direction, n_stops FROM services WHERE {where}"
            return f"SELECT * FROM services WHERE {where}"

        # trips between two planning areas
        if len(areas) >= 2 and _has(ql, "trips", "travel", "journeys", "go"):
            return (f"SELECT origin_area, destination_area, weekday_trips FROM od_area_flows "
                    f"WHERE origin_area = {lit(areas[0])} AND destination_area = {lit(areas[1])}")

        # busiest pairs of planning areas
        if _has(ql, "planning area", "planning areas", "areas") and _has(ql, "trips") and _has(ql, "pair", "pairs"):
            diff = " WHERE origin_area <> destination_area" if _has(ql, "different", "other") else ""
            return (f"SELECT origin_area, destination_area, weekday_trips FROM od_area_flows{diff} "
                    f"ORDER BY weekday_trips DESC LIMIT {_top_n(ql, 1)}")

        # busiest stop-to-stop pairs
        if _has(ql, "pair", "pairs") and _has(ql, "stop", "stops", "trip", "trips"):
            default = 1 if re.search(r"\bpair\b", ql) else 10
            return (f"SELECT origin_stop, destination_stop, weekday_trips, am_peak_trips FROM od_stop_flows "
                    f"ORDER BY weekday_trips DESC LIMIT {_top_n(ql, default)}")

        # corridor links
        if _has(ql, "link", "links", "corridor", "corridors"):
            svc = re.search(r"(\d+)\s+or\s+more\s+services|at\s+least\s+(\d+)\s+services", ql)
            if svc and _has(ql, "length", "km", "combined", "total"):
                k = svc.group(1) or svc.group(2)
                return f"SELECT sum(link_km) AS total_km FROM corridor_links WHERE n_services >= {k}"
            return (f"SELECT from_stop, to_stop, n_services, am_peak_buses_per_hour FROM corridor_links "
                    f"ORDER BY n_services DESC LIMIT {n}")

        # flagged changes
        if _has(ql, "surge", "surges", "drop", "drops", "flagged"):
            flag = "surge" if _has(ql, "surge", "surges") else ("drop" if _has(ql, "drop", "drops") else None)
            where = f"flag = {lit(flag)}" if flag else "flag IS NOT NULL"
            return (f"SELECT c.stop_code, s.stop_name, c.flag, c.pct_change, c.category FROM stop_changes c "
                    f"JOIN stops s USING (stop_code) WHERE {where} ORDER BY c.pct_change")

        # operators
        if _has(ql, "operator", "operators") and _has(ql, "services", "run"):
            return "SELECT operator, count(DISTINCT service_no) AS n_services FROM services GROUP BY operator ORDER BY n_services DESC"

        # headways
        m = re.search(r"(\d+)\s*min", ql)
        if _has(ql, "headway") and m:
            op = ">=" if _has(ql, "or more", "at least") else (">" if _has(ql, "more than", "over", "longer") else "<=")
            return f"SELECT DISTINCT service_no FROM services WHERE am_peak_headway_min {op} {m.group(1)} ORDER BY service_no"

        # stops per 10,000 residents
        if _has(ql, "per 10,000 residents", "per 10000 residents"):
            thr = re.search(r"(?:fewer|less) than\s+(\d+(?:\.\d+)?)", ql)
            res_min = re.search(r"at least\s+(\d[\d,]*)\s+residents", ql)
            where = ["residents > 0"] + ([f"residents >= {res_min.group(1).replace(',', '')}"] if res_min else [])
            if thr:
                where.append(f"stops_per_10k_residents < {thr.group(1)}")
            return ("SELECT planning_area, residents, stops_per_10k_residents FROM planning_areas WHERE "
                    + " AND ".join(where) + " ORDER BY stops_per_10k_residents")

        # coverage and residents
        subzone_q = _has(ql, "subzone", "subzones")
        if _has(ql, "coverage", "within a 400", "within 400", "walk", "residents") and (
                subzone_q or _has(ql, "planning area", "planning areas", "areas", "area")):
            table = "subzones" if subzone_q else "planning_areas"
            key = "subzone" if subzone_q else "planning_area"
            where = []
            if subzone_q and areas:
                where.append(f"planning_area = {lit(areas[0])}")
            if _has(ql, "no bus stops", "no stops"):
                res_min = re.search(r"(?:more than|over)\s+(\d[\d,]*)\s+residents", ql)
                where += ["n_stops = 0", f"residents > {res_min.group(1).replace(',', '') if res_min else 0}"]
                return f"SELECT {key}, residents, n_stops FROM {table} WHERE " + " AND ".join(where) + " ORDER BY residents DESC"
            pct = re.search(r"(?:less than|below|under)\s+(\d+)\s*%", ql)
            if _has(ql, "more than a 400", "outside", "beyond") and not pct:
                return (f"SELECT planning_area, residents, residents_outside_walk_400m FROM planning_areas "
                        f"ORDER BY residents_outside_walk_400m DESC NULLS LAST LIMIT {_top_n(ql, 1)}")
            sel = f"SELECT {key}, residents, {coverage_col} FROM {table}"
            if pct:
                where.append(f"{coverage_col} < {int(pct.group(1)) / 100:g}")
                return sel + " WHERE " + " AND ".join(where) + f" ORDER BY {coverage_col}"
            direction = "DESC" if _has(ql, "highest", "best") else "ASC"
            where.append("residents > 0")
            return sel + " WHERE " + " AND ".join(where) + f" ORDER BY {coverage_col} {direction} LIMIT {n}"

        # the stop with the most services
        if _has(ql, "stop") and _has(ql, "services") and _has(ql, "most"):
            return f"SELECT stop_code, stop_name, n_services FROM stops ORDER BY n_services DESC LIMIT {_top_n(ql, 1)}"

        # stop lists and counts by place
        if _has(ql, "stop", "stops"):
            where = []
            if areas:
                where.append(f"planning_area = {lit(areas[0])}")
            elif region:
                where.append(f"region = {lit(region)}")
            if road:
                where.append(f"road_name = {lit(road)}")
            share = re.search(r"(?:more than|over)\s+(\d+)\s*%.*\b(am|pm) peak", ql)
            if share:
                col = "am_peak_share" if share.group(2) == "am" else "pm_peak_share"
                where.append(f"{col} > {int(share.group(1)) / 100:g}")
            w = (" WHERE " + " AND ".join(where)) if where else ""
            if _has(ql, "region") and _has(ql, "each", "per", "by"):
                return "SELECT region, count(*) AS n_stops FROM stops WHERE region IS NOT NULL GROUP BY region ORDER BY n_stops DESC"
            if _has(ql, "total") and _has(ql, "boardings"):
                return f"SELECT sum(weekday_boardings) AS total_weekday_boardings FROM stops{w}"
            if _has(ql, "how many", "count", "number of"):
                return f"SELECT count(*) AS n_stops FROM stops{w}"
            if _has(ql, "busiest", "most boardings", "most weekday boardings"):
                return (f"SELECT stop_code, stop_name, planning_area, weekday_boardings FROM stops{w} "
                        f"ORDER BY weekday_boardings DESC NULLS LAST LIMIT {n}")
            if where:
                return (f"SELECT stop_code, stop_name, road_name, weekday_boardings FROM stops{w} "
                        f"ORDER BY weekday_boardings DESC NULLS LAST")
        return None

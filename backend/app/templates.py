"""Deterministic fallback: keyword rules that fill SQL templates.

No model is involved, so it is fast and predictable, but it only understands the question shapes below, and the
rules are developed against known development and regression questions. The current
eval split is not held out from these rules. Unrecognized shapes return None;
recognized requirements that cannot be preserved raise an explicit refusal.
Entity values (places, stops, services) are looked up in the database and
inserted as quoted literals from that lookup, never copied from the question text.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

import sqlglot
from sqlglot import exp

from .db import Database
from .schema import TABLES

WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "twenty": 20}
DIST = "sqrt(power(a.x_m - b.x_m, 2) + power(a.y_m - b.y_m, 2))"


class TemplateRefusal(ValueError):
    """A recognized question contains a requirement the rules cannot preserve."""

    code = "template_unsupported_constraint"


@lru_cache(maxsize=16)
def _name_patterns(names: tuple[str, ...]):
    # The full catalog has thousands of names, exceeding re's global cache.
    # Cache catalog patterns, not user questions, so a warm rule query does not
    # recompile the whole gazetteer several times.
    return [(name, re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", re.I))
            for name in sorted(names, key=len, reverse=True)]


def _matches(text: str, names) -> list[tuple[int, int, str]]:
    """Longest non-overlapping catalog names, retaining every occurrence."""
    found = []
    for name, pattern in _name_patterns(tuple(names)):
        for match in pattern.finditer(text):
            if not any(match.start() < end and start < match.end() for start, end, _ in found):
                found.append((match.start(), match.end(), name))
    return sorted(found)


def _mask(text: str, matches) -> str:
    chars = list(text)
    for start, end, _ in matches:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _in(column: str, values) -> str:
    values = list(dict.fromkeys(values))
    if len(values) == 1:
        return f"{column} = {lit(values[0])}"
    return f"{column} IN (" + ", ".join(lit(value) for value in values) + ")"


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
    text = _mask(text, [(start, end, "comparison") for start, end, *_ in _comparisons(text)])
    m = re.search(r"(?<!at )\b(?:top|first|busiest|largest|highest|lowest|bottom|quietest|most|least)\s+(\d{1,3})\b", text) \
        or re.search(r"\b(\d{1,3})\s+(?:busiest|largest|highest|lowest|quietest|most|least|top)\b", text) \
        or re.search(r"\b(?:show|list|return)\s+(?:me\s+)?(?:the\s+)?(\d{1,3})\s+(?:bus\s+)?(?:stops|planning areas|areas|subzones|links|pairs|services|destination)", text)
    m = m or re.search(r"^(?:which|list|show|what are)\s+(?:the\s+)?(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    for w, n in WORD_NUMBERS.items():
        if re.search(r"\b" + w + r"\s+(?:busiest|largest|highest|lowest|quietest|most)\b", text) or re.search(
                r"\b(?:top|first|show|list|return|which)\s+(?:the\s+)?" + w + r"\s+(?:bus\s+)?(?:stops|planning|areas|subzones|links|pairs|destination)", text):
            return n
    return default


def _number_before(text: str, unit_rx: str) -> float | None:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*" + unit_rx, text)
    return float(m.group(1).replace(",", "")) if m else None


def _comparisons(text):
    """Normalize comparator synonyms once, including '10 or more services'."""
    number = r"(?:\d[\d,]*(?:\.\d+)?|" + "|".join(WORD_NUMBERS) + r")"
    operators = {"at least": ">=", "at most": "<=", "no more than": "<=", "no fewer than": ">=",
                 "more than": ">", "greater than": ">", "longer than": ">",
                 "less than": "<", "fewer than": "<", "shorter than": "<",
                 "over": ">", "above": ">", "under": "<", "below": "<", "exactly": "="}
    result = []
    def record(match, op, after_start, after_end=None):
        raw = match["number"]
        value = float(WORD_NUMBERS[raw] if raw in WORD_NUMBERS else raw.replace(",", ""))
        result.append((match.start(), match.end(), value, bool(match["percent"]), op,
                       text[max(0, match.start() - 45):match.start()], text[after_start:after_end].lstrip()))
    for match in re.finditer(r"\b(?P<op>" + "|".join(operators) + r")\s+(?P<number>" + number + r")\s*(?P<percent>%)?", text):
        record(match, operators[match["op"]], match.end())
    for match in re.finditer(r"\b(?P<number>" + number + r")\s*(?P<percent>%)?\s+or\s+(?P<op>more|less|fewer)\b", text):
        record(match, ">=" if match["op"] == "more" else "<=", match.end())
    units = r"(?:bus )?(?:services?|stops?)|(?:weekday )?(?:boardings|trips)|residents|minutes?|mins?"
    for match in re.finditer(r"\b(?P<number>" + number + r")\s*(?P<percent>%)?\s*(?P<unit>" + units + r")\s+or\s+(?P<op>more|less|fewer)\b", text):
        record(match, ">=" if match["op"] == "more" else "<=", match.start("unit"), match.end("unit"))
    return sorted(result)


class TemplateEngine:
    def __init__(self, catalog: Catalog):
        self.c = catalog

    # ---- entity extraction
    def stops_in(self, q: str, ql: str) -> list[str]:
        codes = [c for c in re.findall(r"\b(\d{5})\b", q) if c in self.c.stop_codes]
        for _, _, name in self._stop_mentions(q):
            codes.extend(sorted(self.c.stop_names[name]))
        return list(dict.fromkeys(codes))

    def _stop_mentions(self, q):
        return _matches(q, (name for name in self.c.stop_names if len(name) >= 5))

    def _place_text(self, q):
        # Only mask the actual name span. A separate "in Boon Lay" still means
        # the planning area, even when "Boon Lay Int" appears elsewhere.
        return _mask(q, self._stop_mentions(q) + _matches(q, self.c.roads))

    def service_in(self, ql: str) -> str | None:
        m = re.search(r"\b(?:service|bus|route)\s+(?:no\.?\s*|number\s+)?([0-9]{1,3}[a-z]?)\b", ql)
        if m and m.group(1) in self.c.services:
            return m.group(1)
        return None

    def areas_in(self, q: str) -> list[str]:
        return [name for _, _, name in self._administrative_mentions(q)[0]]

    def _administrative_mentions(self, q):
        text = self._place_text(q)
        areas = _matches(text, self.c.planning_areas)
        subzones = _matches(text, self.c.subzones)
        # Identically named area/subzone (e.g. Tanglin): an explicit adjacent
        # 'subzone' selects that entity; otherwise use the planning area. A
        # longer subzone such as Alpha North still masks its embedded area.
        area_spans = {(start, end) for start, end, _ in areas}
        subzones = [(start, end, name) for start, end, name in subzones
                    if (start, end) not in area_spans or
                    re.search(r"\bsubzone\s+(?:(?:called|named)\s+)?$", text[:start], re.I) or
                    re.match(r"\s+subzone\b", text[end:], re.I)]
        return _matches(_mask(text, subzones), self.c.planning_areas), subzones

    def region_in(self, q: str) -> str | None:
        regions = self.regions_in(q)
        return regions[0] if regions else None

    def regions_in(self, q: str) -> list[str]:
        return [name for _, _, name in _matches(q, self.c.regions)]

    def roads_in(self, q: str) -> list[str]:
        return [self.c.roads[name] for _, _, name in _matches(q, self.c.roads)]

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
        areas = self.areas_in(q)
        for a in dict.fromkeys(areas):
            out.append(f"planning_area = '{a}'")
            if a in self.c.subzones:
                out.append(f"'{a}' also names a subzone; the unqualified place is interpreted as the planning area.")
        for region in dict.fromkeys(self.regions_in(q)):
            out.append(f"region = '{region}'")
        for road in dict.fromkeys(self.roads_in(q)):
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
        sql = self._candidate_sql(question)
        return self._preserve_constraints(question, sql) if sql else None

    def _preserve_constraints(self, question: str, sql: str) -> str:
        """Compose explicit filters over the rule's row grain before its limit.

        This is still a bounded rule engine, not general language understanding.
        A recognized constraint with no corresponding column is a refusal, never
        permission to execute a nearby, less constrained question.
        """
        tree = sqlglot.parse_one(sql, read="duckdb")
        tables = list(tree.find_all(exp.Table))
        ql = question.lower()
        plain = _mask(ql, self._stop_mentions(question) + _matches(question, self.c.roads))

        def refuse(detail):
            raise TemplateRefusal(detail)

        def column(name):
            # In distance/OD queries the last stops alias denotes returned
            # destinations, while the first is the reference/origin stop.
            candidates = [t for t in tables if name in TABLES[t.name]["columns"]]
            if not candidates:
                refuse(f"The keyword rules cannot apply {name} to this question's result. No partial answer was run.")
            table = candidates[-1]
            return f"{table.alias_or_name}.{name}"

        def add(name, op, value):
            tree.where(f"{column(name)} {op} {value}", append=True, copy=False)

        # The source contains monthly average weekdays, not weekend/day-specific
        # observations or a time series. Do not relabel August as another period.
        if re.search(r"\b(weekends?|saturdays?|sundays?|mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|"
                     r"yesterday|today|tomorrow|last month|last year|this month|this year)\b", plain):
            refuse("Only average weekday passenger data for August 2026 is available; individual days and other periods are not available.")
        passengers = _has(plain, "boardings", "tap-ins", "trips", "people board", "surge", "surges", "drops")
        expected_month = "august" if passengers else "september"
        month_names = "january february march april may june july august september october november december".split()
        aliases = {alias: month for month in month_names for alias in (month, month[:3])}
        month_rx = r"\b(" + "|".join(aliases) + r")\b"
        months = [aliases[month] for month in re.findall(month_rx, plain)]
        dates = re.findall(r"\b((?:19|20)\d{2})[-/](0?[1-9]|1[0-2])(?:[-/]\d{1,2})?\b", plain)
        months += [month_names[int(month) - 1] for _, month in dates]
        # Thresholds such as "more than 2000 boardings" are not years.
        years = re.findall(r"\b(?:in|during|for|year)\s+((?:19|20)\d{2})\b", plain)
        years += [year for _, year in re.findall(month_rx + r"\s+((?:19|20)\d{2})\b", plain)]
        years += [year for year, _ in dates]
        if any(month != expected_month for month in months) or any(year != "2026" for year in years):
            refuse(f"This rule uses {expected_month.title()} 2026 data, not the requested period. Monthly comparisons are not available.")
        if re.search(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", plain) or re.search(month_rx + r"\s+\d{1,2}(?:st|nd|rd|th)?\b", plain):
            refuse("Individual calendar-day observations are not available; passenger figures are average weekdays for August 2026.")
        if re.search(r"\b(mrt|rail|fares?|weather|wheelchair|accessible|delays?|punctual|travel time)\b", plain):
            refuse("The requested rail, fare, accessibility, weather or journey-time condition is not in the bus dataset.")
        if re.search(r"\b(except|excluding|exclude|not in|not on|without)\b", plain):
            refuse("The keyword rules cannot preserve this exclusion. Ask a supported positive filter or use the language-model engine.")

        # Unknown place names must not fall through to a whole-network count.
        names = self.c.planning_areas + self.c.subzones + self.c.regions + list(self.c.roads) + list(self.c.stop_names)
        known = _mask(question, _matches(question, names))
        alternatives = known  # Keep original positions for Boolean operators.
        # Preserve offsets so a complete source entity can be checked against
        # the original question even after catalog/date spans are concealed.
        for pattern in (r"\b\d{5}\b", month_rx + r"(?:\s+\d{4})?",
                        r"\b(?:19|20)\d{2}(?:[-/]\d{1,2}){0,2}\b",
                        r"\bin\s+(?:km|kilometres|kilometers|metres|meters)\b",
                        r"\b(?:" + "|".join(re.escape(r.replace(" REGION", "")) for r in self.c.regions) + r")\s+region\b"):
            known = re.sub(pattern, lambda m: " " * len(m[0]), known, flags=re.I)
        for code in re.findall(r"\bstop\s+(?:code\s+)?(\d{5})\b", ql):
            if code not in self.c.stop_codes:
                refuse(f"No stop {code} exists in this dataset.")
        # Scan each preposition independently: accepting a metric phrase such
        # as 'in boardings in August' must still inspect 'in Atlantis' after it.
        for match in re.finditer(r"\b(?:in|on|from|to|at)\s+", known, re.I):
            source = question[match.end():]
            stop_reference = re.match(r"(?:the\s+)?stop\s+(?:code\s+)?\d{5}\b", source, re.I)
            if stop_reference and not re.match(r"\s*(?:and|or)\b", source[stop_reference.end():], re.I):
                continue  # 'at stop 46009 going ...' has a complete anchor.
            phrase = re.split(r"[?;.!]|\b(?:where|with|have|has|are|were|is|was|that|which|how|what|by|during|compared|go|show)\b", known[match.end():], maxsplit=1, flags=re.I)[0]
            if re.match(r"\s*(?:average\s+)?(?:weekday\s+)?(?:boardings|tap-ins|trips|coverage)\b", phrase, re.I):
                continue  # The head names a measure, not a location.
            if re.match(r"\s*(?:the\s+)?(?:am|pm)\s+peak\b", phrase, re.I):
                continue  # A recognized time window, checked by peak rules.
            if re.match(r"\s*(?:least|most|\d|january|february|march|april|may|june|july|august|september|october|november|december)\b", phrase, re.I):
                continue
            residue = re.sub(r"\b(?:and|or|to|from|in|on|at|the|all|each|every|other|bus|stops?|planning|areas?|subzones?|"
                             r"region|ura|direction|km|minutes?|an?|one|average|weekday|weekdays|am|pm|peak)\b|\d+|[\s,'’()-]", "", phrase, flags=re.I)
            if residue:
                refuse(f"The keyword rules could not resolve the location '{phrase.strip()}'. No whole-network substitute was run.")

        areas = self.areas_in(question)
        area_flow = any(t.name == "od_area_flows" for t in tables)
        if areas and area_flow:
            direction = re.search(r"\bfrom\b(.+?)\bto\b(.+)", question, re.I)
            if direction:
                origins, destinations = self.areas_in(direction[1]), self.areas_in(direction[2])
                if not origins or not destinations:
                    refuse("Both origin and destination planning areas must be identified.")
                tree.set("where", None)
                tree.where(_in("origin_area", origins) + " AND " + _in("destination_area", destinations), copy=False)
                if (len(set(origins)) > 1 or len(set(destinations)) > 1) and _has(plain, "how many", "total"):
                    tree.set("expressions", [exp.alias_(exp.Sum(this=exp.column("weekday_trips")), "weekday_trips")])
            elif _has(plain, "between") and len(set(areas)) == 2:
                a, b = list(dict.fromkeys(areas))
                tree.set("where", None)
                tree.where(f"(origin_area = {lit(a)} AND destination_area = {lit(b)}) OR "
                           f"(origin_area = {lit(b)} AND destination_area = {lit(a)})", copy=False)
                if _has(plain, "how many", "total"):
                    tree.set("expressions", [exp.alias_(exp.Sum(this=exp.column("weekday_trips")), "weekday_trips")])
            elif len(areas) != 2:
                refuse("State which planning areas are origins and which are destinations.")
        elif areas:
            tree.where(_in(column("planning_area"), areas), append=True, copy=False)
        area_mentions, subzone_mentions = self._administrative_mentions(question)
        subzones = [name for _, _, name in subzone_mentions]
        if subzones:
            tree.where(_in(column("subzone"), subzones), append=True, copy=False)
        roads = self.roads_in(question)
        if roads:
            tree.where(_in(column("road_name"), roads), append=True, copy=False)
        regions = self.regions_in(question)
        if regions:
            tree.where(_in(column("region"), regions), append=True, copy=False)
        peak_hour = re.search(r"\b(?:busiest|peak)\s+(?:boarding\s+)?hour\s+(?:at|is|of)\s+(\d{1,2})\s*(am|pm)?\b", plain)
        if peak_hour:
            hour = int(peak_hour[1])
            if peak_hour[2]:
                if not 1 <= hour <= 12:
                    refuse("An AM/PM hour must be between 1 and 12.")
                hour = hour % 12 + (12 if peak_hour[2] == "pm" else 0)
            if not 0 <= hour <= 23:
                refuse("A boarding peak hour must be between 0 and 23.")
            add("peak_hour", "=", str(hour))

        # Scalar conjunctions share the same row filter. A comparison against an
        # unknown measure is rejected instead of being discarded by a keyword hit.
        comparisons = _comparisons(plain)
        for start, end, value, percent, op, before, after in comparisons:
            field = None
            if percent:
                peak = re.match(r"(?:of (?:their |the )?(?:weekday )?boardings (?:in |during )?(?:the )?)?(am|pm) peak", after)
                if peak:
                    field = peak[1] + "_peak_share"
                elif "coverage" in before or re.match(r"of (?:their |the )?residents", after):
                    field = "coverage_straight_400m" if _has(plain, "straight", "straight-line") else "coverage_walk_400m"
                value /= 100
            else:
                for pattern, name in ((r"residents\b", "residents"), (r"(?:average )?(?:weekday )?boardings\b", "weekday_boardings"),
                                      (r"(?:bus )?services?\b", "n_services"), (r"(?:bus )?stops?\b", "n_stops"),
                                      (r"(?:weekday )?trips\b", "weekday_trips"),
                                      (r"scheduled buses per hour\b", "am_peak_buses_per_hour")):
                    if re.match(pattern, after):
                        field = name
                        break
                if re.match(r"min(?:ute)?s?\b", after) and "headway" in plain:
                    field = ("pm" if _has(plain, "pm peak") else "am") + "_peak_headway_min"
                if field == "n_stops" and re.match(r"(?:bus )?stops? per (?:10,000|10000) residents", after):
                    field = "stops_per_10k_residents"
                if not field and value == 400 and re.match(r"m\b", after) and _has(plain, "walk"):
                    continue  # the supported outside-400m resident measure
                if field is None:
                    for pattern, name in ((r"(?:weekday )?boardings\s*$", "weekday_boardings"),
                                          (r"residents\s*$", "residents"), (r"(?:weekday )?trips\s*$", "weekday_trips")):
                        if re.search(pattern, before):
                            field = name
                            break
            if field is None:
                refuse(f"The keyword rules cannot apply the condition '{question[start:end + 35].strip()}'.")
            add(field, op, f"{value:g}")
        # Only alternatives within the same geographic field are a simple IN.
        # An OR across fields or between a place and a measure needs explicit
        # Boolean grouping; do not silently combine those predicates with AND.
        atoms = [(start, end, "planning_area") for start, end, _ in area_mentions]
        atoms += [(start, end, "subzone") for start, end, _ in subzone_mentions]
        atoms += [(start, end, kind) for kind, names in (("road", self.c.roads), ("region", self.c.regions))
                  for start, end, _ in _matches(question, names)]
        atoms += [(start, end, "scalar") for start, end, *_ in comparisons]
        atoms.sort()
        for alternative in re.finditer(r"\bor\b", alternatives, re.I):
            # '10 or more services' is one comparison, not a Boolean OR.
            if any(start <= alternative.start() < end for start, end, *_ in comparisons):
                continue
            left = [atom for atom in atoms if atom[1] <= alternative.start()]
            right = [atom for atom in atoms if atom[0] >= alternative.end()]
            if left and right and (left[-1][2] != right[0][2] or left[-1][2] == "scalar"):
                refuse("This alternative combines different filters. The keyword rules support AND and alternatives within one geographic field; use the language-model engine for this OR grouping.")
        if re.search(r"\b(?:and|but)\s+(?:also\s+)?(?:show|list|count|compare|what|how|which)\b", plain):
            refuse("This asks for multiple different results; ask each result separately so no part is omitted.")
        if _has(plain, "coverage", "residents"):
            radius = _number_before(plain, r"(?:m|metres|meters)\b")
            if radius is not None and radius != 400:
                refuse("Resident coverage is available only at 400 m; no other radius is stored.")
        if _has(plain, "within", "near") and _has(plain, "walk", "walking") and any(t.name == "stops" for t in tables):
            refuse("Stop-to-stop distance uses straight-line SVY21 coordinates; network walking distance is not available.")
        if _has(plain, "near", "nearby", "around") and not _number_before(plain, r"(?:m|metres|meters|km)\b"):
            refuse("A proximity question needs a known reference stop and an explicit distance in metres or km.")
        # A request for a different peak must not silently keep the AM/default
        # weekday metric selected by an earlier keyword rule.
        if _has(plain, "pm peak") and _has(plain, "trips"):
            refuse("PM-peak origin-destination trips are not stored; only weekday and AM-peak trips are available.")
        if _has(plain, "am peak") and _has(plain, "trips"):
            if any(t.name == "od_area_flows" for t in tables):
                refuse("AM-peak trips are stored between stops, not as planning-area totals.")
            for c in tree.find_all(exp.Column):
                if c.name == "weekday_trips":
                    c.set("this", exp.to_identifier("am_peak_trips"))
        return tree.sql(dialect="duckdb")

    def _candidate_sql(self, question: str) -> str | None:
        q = question.strip()
        ql = q.lower()
        stops = self.stops_in(q, ql)
        service = self.service_in(ql)
        areas = self.areas_in(q)
        region = self.region_in(q)
        road = self.road_in(ql)
        n = _top_n(ql)
        in_stops = "(" + ", ".join(lit(s) for s in stops) + ")"
        coverage_col = "coverage_straight_400m" if _has(ql, "straight", "straight-line") else "coverage_walk_400m"

        # stops within a distance of a stop
        dist = _number_before(ql, r"(?:m|metres|meters)\b") or ((_number_before(ql, r"km\b") or 0) * 1000 or None)
        if stops and dist and _has(ql, "within", "near", "around", "nearby"):
            if _has(ql, "how many", "count", "number of"):
                return (f"SELECT count(DISTINCT b.stop_code) AS n_stops "
                        f"FROM stops a JOIN stops b ON b.stop_code <> a.stop_code WHERE a.stop_code IN {in_stops} "
                        f"AND {DIST} <= {dist:g}")
            return (f"SELECT b.stop_code, b.stop_name, round({DIST}, 1) AS distance_m "
                    f"FROM stops a JOIN stops b ON b.stop_code <> a.stop_code WHERE a.stop_code IN {in_stops} "
                    f"AND {DIST} <= {dist:g} ORDER BY distance_m")

        # where trips from a stop go
        if stops and _has(ql, "destination", "destinations", "go", "going", "where do", "trips from"):
            return (f"SELECT o.destination_stop, s.stop_name, o.weekday_trips FROM od_stop_flows o "
                    f"JOIN stops s ON s.stop_code = o.destination_stop WHERE o.origin_stop IN {in_stops} "
                    f"ORDER BY o.weekday_trips DESC LIMIT {n}")

        # services at a stop
        if stops and _has(ql, "services", "buses", "routes") and _has(ql, "stop", "stops", "serve", "serves", "at"):
            return f"SELECT DISTINCT service_no FROM route_stops WHERE stop_code IN {in_stops} ORDER BY service_no"

        # boardings at a stop
        if stops and _has(ql, "boardings", "tap-ins", "people board"):
            return f"SELECT stop_code, stop_name, weekday_boardings FROM stops WHERE stop_code IN {in_stops}"

        # one service
        if service and not stops:
            direction = re.search(r"\bdirection\s+([12])\b", ql)
            where = f"service_no = {lit(service)}" + (f" AND direction = {direction.group(1)}" if direction else "")
            if _has(ql, "first", "last", "terminal", "terminus"):
                return f"SELECT service_no, direction, origin_stop, destination_stop FROM services WHERE {where}"
            if _has(ql, "stops"):
                return f"SELECT service_no, direction, n_stops FROM services WHERE {where}"
            if _has(ql, "route length", "route km"):
                return f"SELECT route_km FROM services WHERE {where}"
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
            if _has(ql, "length", "km", "combined", "total"):
                return "SELECT sum(link_km) AS total_km FROM corridor_links"
            return (f"SELECT from_stop, to_stop, n_services, am_peak_buses_per_hour FROM corridor_links "
                    f"ORDER BY n_services DESC LIMIT {n}")

        # flagged changes
        if _has(ql, "surge", "surges", "drop", "drops", "flagged"):
            flag = "surge" if _has(ql, "surge", "surges") else ("drop" if _has(ql, "drop", "drops") else None)
            where = f"flag = {lit(flag)}" if flag else "flag IS NOT NULL"
            if _has(ql, "how many", "count", "number of"):
                return ("SELECT count(*) AS n_stops FROM stop_changes c "
                        f"JOIN stops s USING (stop_code) WHERE {where}")
            return (f"SELECT c.stop_code, s.stop_name, c.flag, c.pct_change, c.category FROM stop_changes c "
                    f"JOIN stops s USING (stop_code) WHERE {where} ORDER BY c.pct_change")

        # operators
        if _has(ql, "operator", "operators") and _has(ql, "services", "run"):
            return "SELECT operator, count(DISTINCT service_no) AS n_services FROM services GROUP BY operator ORDER BY n_services DESC"

        # headways
        if _has(ql, "headway") and _comparisons(ql):
            return "SELECT DISTINCT service_no FROM services ORDER BY service_no"

        # stops per 10,000 residents
        if _has(ql, "per 10,000 residents", "per 10000 residents"):
            return ("SELECT planning_area, residents, stops_per_10k_residents FROM planning_areas "
                    "WHERE residents > 0 ORDER BY stops_per_10k_residents")

        # coverage and residents
        subzone_q = _has(ql, "subzone", "subzones")
        rank_text = _mask(ql, [(start, end, "comparison") for start, end, *_ in _comparisons(ql)])
        if _has(ql, "stops") and (subzone_q or _has(ql, "planning area", "planning areas")):
            table = "subzones" if subzone_q else "planning_areas"
            key = "subzone" if subzone_q else "planning_area"
            density = _has(ql, "per square kilometre", "per square kilometer", "per sq km", "per km2")
            stop_rank = re.search(r"\b(?:most|fewest)\s+(?:bus\s+)?stops\b|\b(?:highest|lowest)\s+(?:number|count)\s+of\s+(?:bus\s+)?stops\b", rank_text)
            if density or stop_rank:
                measure = "n_stops / area_km2" if density else "n_stops"
                where = " WHERE area_km2 > 0" if density else ""
                direction = "ASC" if _has(ql, "fewest", "lowest") else "DESC"
                return (f"SELECT {key}, {measure} AS stop_{'density' if density else 'count'} FROM {table}{where} "
                        f"ORDER BY stop_{'density' if density else 'count'} {direction} LIMIT {_top_n(ql, 1)}")
        if _has(ql, "coverage", "within a 400", "within 400", "walk", "residents") and (
                subzone_q or areas or _has(ql, "planning area", "planning areas", "areas", "area")):
            table = "subzones" if subzone_q else "planning_areas"
            key = "subzone" if subzone_q else "planning_area"
            where = []
            if areas:
                where.append(_in("planning_area", areas))
            if _has(ql, "no bus stops", "no stops"):
                where += ["n_stops = 0", "residents > 0"]
                return f"SELECT {key}, residents, n_stops FROM {table} WHERE " + " AND ".join(where) + " ORDER BY residents DESC"
            pct = re.search(r"(?:less than|below|under)\s+(\d+)\s*%", ql)
            if (_has(ql, "outside", "beyond") or re.search(r"\b(?:more than|over)\s+(?:a\s+)?400\b", ql)) and not pct:
                if _has(ql, "how many", "total"):
                    return "SELECT sum(residents_outside_walk_400m) AS residents_outside_walk_400m FROM planning_areas"
                return (f"SELECT planning_area, residents, residents_outside_walk_400m FROM planning_areas "
                        f"ORDER BY residents_outside_walk_400m DESC NULLS LAST LIMIT {_top_n(ql, 1)}")
            sel = f"SELECT {key}, residents, {coverage_col} FROM {table}"
            if pct:
                return sel + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {coverage_col}"
            direction = "DESC" if _has(ql, "highest", "best") else "ASC"
            where.append("residents > 0")
            limit = _top_n(ql, 0) or (n if _has(ql, "highest", "best", "lowest", "worst") else 0)
            return sel + " WHERE " + " AND ".join(where) + f" ORDER BY {coverage_col} {direction}" + (f" LIMIT {limit}" if limit else "")

        # the stop with the most services
        if _has(ql, "stop") and _has(ql, "services") and _has(ql, "most"):
            return f"SELECT stop_code, stop_name, n_services FROM stops ORDER BY n_services DESC LIMIT {_top_n(ql, 1)}"

        # stop lists and counts by place
        if _has(ql, "stop", "stops"):
            where = []
            if areas:
                where.append(_in("planning_area", areas))
            if region:
                where.append(_in("region", self.regions_in(q)))
            if road:
                where.append(_in("road_name", self.roads_in(q)))
            w = (" WHERE " + " AND ".join(where)) if where else ""
            if _has(ql, "planning area", "planning areas") and _has(ql, "each", "per", "by"):
                return (f"SELECT planning_area, count(*) AS n_stops FROM stops{w} "
                        "GROUP BY planning_area ORDER BY n_stops DESC")
            if _has(ql, "region") and _has(ql, "each", "per", "by"):
                return "SELECT region, count(*) AS n_stops FROM stops WHERE region IS NOT NULL GROUP BY region ORDER BY n_stops DESC"
            if _has(ql, "total") and _has(ql, "boardings"):
                return f"SELECT sum(weekday_boardings) AS total_weekday_boardings FROM stops{w}"
            if _has(ql, "average", "mean") and _has(ql, "boardings") and _has(ql, "per stop", "per bus stop"):
                return f"SELECT avg(weekday_boardings) AS average_weekday_boardings FROM stops{w}"
            if _has(ql, "how many", "count", "number of"):
                return f"SELECT count(*) AS n_stops FROM stops{w}"
            peak_hour_q = _has(ql, "busiest boarding hour", "peak hour", "busiest hour")
            if _has(ql, "busiest", "most boardings", "most weekday boardings") and not peak_hour_q:
                return (f"SELECT stop_code, stop_name, planning_area, weekday_boardings FROM stops{w} "
                        f"ORDER BY weekday_boardings DESC NULLS LAST LIMIT {n}")
            if where or _comparisons(ql) or peak_hour_q:
                return (f"SELECT stop_code, stop_name, road_name, weekday_boardings FROM stops{w} "
                        f"ORDER BY weekday_boardings DESC NULLS LAST")
        return None

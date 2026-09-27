"""The schema the language model may query, and nothing else.

Only these tables and columns pass the validator. Geometry lives in separate *_shapes tables that the model never
sees; the API joins them in afterwards to draw results on the map.
"""

TABLES: dict[str, dict] = {
    "stops": {
        "about": "One row per bus stop (LTA DataMall Bus Stops, September 2026 network).",
        "columns": {
            "stop_code": ("VARCHAR", "5-character LTA stop code, e.g. '01012'. Leading zeros matter."),
            "stop_name": ("VARCHAR", "LTA stop name, abbreviated, e.g. 'Opp Blk 123', 'Dhoby Ghaut Stn'."),
            "road_name": ("VARCHAR", "Road the stop is on, e.g. 'Orchard Rd'."),
            "latitude": ("DOUBLE", "WGS84 latitude."),
            "longitude": ("DOUBLE", "WGS84 longitude."),
            "x_m": ("DOUBLE", "SVY21 easting in metres (EPSG:3414)."),
            "y_m": ("DOUBLE", "SVY21 northing in metres (EPSG:3414)."),
            "planning_area": ("VARCHAR", "URA Master Plan 2019 planning area, upper case, e.g. 'TAMPINES'. NULL outside Singapore."),
            "subzone": ("VARCHAR", "URA Master Plan 2019 subzone, upper case."),
            "region": ("VARCHAR", "URA region, e.g. 'EAST REGION'."),
            "weekday_boardings": ("DOUBLE", "Average tap-ins per weekday, August 2026. NULL if no record."),
            "am_peak_share": ("DOUBLE", "Share of weekday boardings between 07:00 and 08:59 (0 to 1)."),
            "pm_peak_share": ("DOUBLE", "Share of weekday boardings between 17:00 and 19:59 (0 to 1)."),
            "peak_hour": ("INTEGER", "Hour of day (0-23) with the most weekday boardings."),
            "n_services": ("INTEGER", "Number of distinct bus services calling at the stop."),
        },
    },
    "services": {
        "about": "One row per bus service and direction (LTA DataMall Bus Services).",
        "columns": {
            "service_no": ("VARCHAR", "Service number as text, e.g. '190', '14e', '960M'."),
            "direction": ("INTEGER", "1 or 2."),
            "operator": ("VARCHAR", "SBST, SMRT, TTS or GAS."),
            "category": ("VARCHAR", "TRUNK, FEEDER, CITY_LINK or EXPRESS."),
            "origin_stop": ("VARCHAR", "stop_code of the first stop."),
            "destination_stop": ("VARCHAR", "stop_code of the last stop."),
            "am_peak_headway_min": ("DOUBLE", "Scheduled weekday AM-peak headway in minutes (mid-point of the LTA band). NULL if no AM-peak service."),
            "pm_peak_headway_min": ("DOUBLE", "Scheduled weekday PM-peak headway in minutes (mid-point). NULL if no PM-peak service."),
            "n_stops": ("INTEGER", "Number of stops on this service direction."),
            "route_km": ("DOUBLE", "Route length in km for this direction."),
        },
    },
    "route_stops": {
        "about": "The ordered stops of each service direction (LTA DataMall Bus Routes).",
        "columns": {
            "service_no": ("VARCHAR", "Service number."),
            "direction": ("INTEGER", "1 or 2."),
            "stop_sequence": ("INTEGER", "1 for the first stop, increasing along the route."),
            "stop_code": ("VARCHAR", "Stop served."),
            "distance_km": ("DOUBLE", "Cumulative km from the first stop."),
        },
    },
    "od_stop_flows": {
        "about": "Weekday trips between bus stops, August 2026 (tap-in stop to tap-out stop, per average weekday).",
        "columns": {
            "origin_stop": ("VARCHAR", "Tap-in stop_code."),
            "destination_stop": ("VARCHAR", "Tap-out stop_code."),
            "weekday_trips": ("DOUBLE", "Trips per average weekday."),
            "am_peak_trips": ("DOUBLE", "Trips per average weekday starting 07:00-08:59."),
        },
    },
    "od_area_flows": {
        "about": "Weekday bus trips between planning areas, August 2026.",
        "columns": {
            "origin_area": ("VARCHAR", "Planning area of the tap-in stop, upper case."),
            "destination_area": ("VARCHAR", "Planning area of the tap-out stop, upper case."),
            "weekday_trips": ("DOUBLE", "Trips per average weekday."),
        },
    },
    "planning_areas": {
        "about": "One row per URA MP2019 planning area, with residents and 400 m bus-stop coverage.",
        "columns": {
            "planning_area": ("VARCHAR", "Name, upper case, e.g. 'BUKIT TIMAH'."),
            "region": ("VARCHAR", "URA region, e.g. 'CENTRAL REGION'."),
            "residents": ("INTEGER", "Residents (SingStat GHS 2025). NULL or 0 for non-residential areas."),
            "area_km2": ("DOUBLE", "Land area in km2."),
            "n_stops": ("INTEGER", "Bus stops inside the area."),
            "stops_per_10k_residents": ("DOUBLE", "n_stops per 10,000 residents. NULL if no residents."),
            "coverage_straight_400m": ("DOUBLE", "Share of residents within 400 m straight-line of a stop (0 to 1)."),
            "coverage_walk_400m": ("DOUBLE", "Share of residents within 400 m walk along OpenStreetMap paths (0 to 1, a lower bound)."),
            "residents_outside_walk_400m": ("DOUBLE", "Residents more than 400 m walk from any stop."),
        },
    },
    "subzones": {
        "about": "One row per URA MP2019 subzone, with residents and 400 m bus-stop coverage.",
        "columns": {
            "subzone": ("VARCHAR", "Name, upper case."),
            "planning_area": ("VARCHAR", "Planning area containing the subzone, upper case."),
            "residents": ("INTEGER", "Residents (SingStat GHS 2025)."),
            "area_km2": ("DOUBLE", "Land area in km2."),
            "n_stops": ("INTEGER", "Bus stops inside the subzone."),
            "coverage_straight_400m": ("DOUBLE", "Share of residents within 400 m straight-line of a stop (0 to 1)."),
            "coverage_walk_400m": ("DOUBLE", "Share of residents within 400 m walk of a stop (0 to 1, a lower bound)."),
        },
    },
    "corridor_links": {
        "about": "Directed stop-to-stop road links, aggregated over every service that runs along them.",
        "columns": {
            "from_stop": ("VARCHAR", "stop_code at the start of the link."),
            "to_stop": ("VARCHAR", "stop_code at the end of the link."),
            "n_services": ("INTEGER", "Number of services using the link."),
            "service_list": ("VARCHAR", "Space-separated service numbers."),
            "link_km": ("DOUBLE", "Link length in km."),
            "am_peak_buses_per_hour": ("DOUBLE", "Scheduled buses per hour in the weekday AM peak."),
        },
    },
    "stop_changes": {
        "about": "August 2026 weekday boardings compared with the median of February, June and July 2026.",
        "columns": {
            "stop_code": ("VARCHAR", "Stop."),
            "baseline_weekday_boardings": ("DOUBLE", "Median weekday boardings of Feb, Jun, Jul 2026."),
            "aug_weekday_boardings": ("DOUBLE", "Weekday boardings, August 2026."),
            "pct_change": ("DOUBLE", "Relative change, e.g. 0.25 = +25%."),
            "flag": ("VARCHAR", "'surge', 'drop' or NULL (not unusual)."),
            "category": ("VARCHAR", "For flagged stops: likely cause category, e.g. 'academic term'. NULL otherwise."),
        },
    },
}

# Functions the generated SQL may call, by sqlglot expression name (lower case) or, for functions sqlglot does not
# model, by their SQL name. Anything else (read_csv, getenv, current_setting, ...) is refused.
ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "median", "stddev", "stddevsamp", "stddevpop", "quantile", "quantilecont",
    "percentilecont", "percentiledisc", "countif", "anyvalue", "arrayagg", "groupconcat", "stringagg", "listagg",
    "first", "last", "argmax", "argmin",
    # scalar
    "abs", "round", "floor", "ceil", "sqrt", "pow", "ln", "log", "exp", "greatest", "least", "coalesce", "nullif",
    "cast", "trycast", "if", "case", "lower", "upper", "length", "trim", "substring", "concat", "dpipe",
    "replace", "left", "right", "strposition", "contains", "startswith", "endswith", "regexplike", "split",
    "splitpart", "div", "mod", "sign", "radians", "degrees", "sin", "cos", "atan2", "asin", "acos", "tan", "pi",
    "arraysize", "arraylength", "stringtoarray", "regexpsplit", "ifnull", "nvl", "exists", "arraycontains",
    "extract",
    # window functions
    "rownumber", "rank", "denserank", "ntile", "lag", "lead", "percentrank", "cumedist", "firstvalue", "lastvalue",
    # planar geometry on the SVY21 metre coordinates
    "stdistance", "stpoint", "st_dwithin", "stdwithin", "st_distance", "st_point", "st_x", "st_y", "stx", "sty",
})

ALLOWED_TABLES = frozenset(TABLES)
ALLOWED_COLUMNS = frozenset(c for t in TABLES.values() for c in t["columns"])


def describe() -> str:
    """The schema as text for the model's prompt."""
    lines = []
    for name, t in TABLES.items():
        lines.append(f"TABLE {name} -- {t['about']}")
        for col, (typ, about) in t["columns"].items():
            lines.append(f"  {col} {typ} -- {about}")
    return "\n".join(lines)


def as_json() -> dict:
    return {name: {"about": t["about"], "columns": [{"name": c, "type": ty, "about": a} for c, (ty, a) in t["columns"].items()]}
            for name, t in TABLES.items()}

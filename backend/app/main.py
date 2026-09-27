"""REST API. OpenAPI docs are served at /docs and /openapi.json."""
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from . import geo, schema
from .ask import Assistant
from .config import Settings, get_settings
from .db import Database
from .llm import OpenAICompatibleClient
from .templates import Catalog, TemplateEngine

STOP_CODE = Path(pattern=r"^\d{5}$", description="5-digit LTA stop code, e.g. 01012")

EXAMPLES = [
    "Which 10 bus stops have the most weekday boardings?",
    "Which bus stops are within 300 m of stop 08057?",
    "List the subzones in Bukit Timah where less than 50% of residents are within a 400 m walk of a stop.",
    "How many weekday bus trips go from Tampines to Bedok?",
    "Which stops were flagged as drops in August 2026?",
]


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, examples=[EXAMPLES[0]])
    engine: Literal["auto", "llm", "template"] = "auto"


class AskResponse(BaseModel):
    question: str
    engine: str
    status: Literal["answered", "refused"]
    sql: str | None
    row_count: int
    truncated: bool
    columns: list[str]
    rows: list[list]
    geojson: dict | None
    reason: str | None
    detail: str | None
    attempts: list[dict]
    latency_ms: float


def create_app(settings: Settings | None = None, client=None, use_llm: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        chat = client
        if chat is None and use_llm:
            chat = OpenAICompatibleClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key,
                                          settings.llm_timeout_s, settings.llm_reasoning_effort)
        app.state.db = db
        app.state.assistant = Assistant(db, TemplateEngine(Catalog.load(db)), chat, max_rows=settings.max_rows,
                                        timeout_s=settings.query_timeout_s,
                                        max_question_chars=settings.max_question_chars)
        yield
        db.close()

    app = FastAPI(title="Singapore transit geo-assistant", version="0.1.0", lifespan=lifespan,
                  description="Ask questions about Singapore's bus network in plain English. Every answer returns "
                              "the SQL that was run, the row count, the rows and a GeoJSON layer.")
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @lru_cache(maxsize=4)
    def cached_layer(name: str, db_id: int):
        db = app.state.db
        return {"stops": geo.stops_layer, "coverage": geo.coverage_layer}[name](db)

    @app.get("/api/health")
    def health(request: Request):
        n = request.app.state.db.scalar_rows("select count(*) from stops")[0][0]
        return {"status": "ok", "stops": n, "llm_model": settings.llm_model if use_llm or client else None}

    @app.get("/api/schema")
    def get_schema():
        """The tables and columns that generated SQL may use."""
        return schema.as_json()

    @app.get("/api/examples")
    def examples():
        return {"questions": EXAMPLES}

    @app.get("/api/layers/stops")
    def layer_stops(request: Request):
        """All bus stops with August 2026 weekday boardings (GeoJSON points)."""
        return cached_layer("stops", id(request.app.state.db))

    @app.get("/api/layers/coverage")
    def layer_coverage(request: Request):
        """Subzone polygons with the share of residents within 400 m of a stop."""
        return cached_layer("coverage", id(request.app.state.db))

    @app.get("/api/stops/{stop_code}")
    def stop_detail(request: Request, stop_code: str = STOP_CODE):
        db = request.app.state.db
        rows = db.scalar_rows("""select stop_code, stop_name, road_name, latitude, longitude, planning_area, subzone,
                                        region, weekday_boardings, am_peak_share, pm_peak_share, peak_hour, n_services
                                 from stops where stop_code = ?""", [stop_code])
        if not rows:
            raise HTTPException(404, f"no stop {stop_code}")
        keys = ["stop_code", "stop_name", "road_name", "latitude", "longitude", "planning_area", "subzone", "region",
                "weekday_boardings", "am_peak_share", "pm_peak_share", "peak_hour", "n_services"]
        out = dict(zip(keys, rows[0]))
        out["services"] = [r[0] for r in db.scalar_rows(
            "select distinct service_no from route_stops where stop_code = ? order by service_no", [stop_code])]
        return out

    @app.get("/api/stops/{stop_code}/od")
    def stop_od(request: Request, stop_code: str = STOP_CODE, limit: int = Query(15, ge=1, le=100)):
        """Desire lines from this stop to its top destinations by weekday trips (GeoJSON lines)."""
        db = request.app.state.db
        if not db.scalar_rows("select 1 from stops where stop_code = ?", [stop_code]):
            raise HTTPException(404, f"no stop {stop_code}")
        return geo.od_layer(db, stop_code, limit)

    @app.post("/api/ask", response_model=AskResponse)
    def ask(request: Request, body: AskRequest):
        """Answer a question. Refusals are normal responses with status "refused" and a reason code."""
        return request.app.state.assistant.ask(body.question, body.engine).to_dict()

    return app


app = create_app()

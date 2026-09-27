// Pure functions used by the map and the answer panel. Kept free of React and MapLibre so they can be unit tested.

export type Geometry =
  | { type: "Point"; coordinates: number[] }
  | { type: "LineString"; coordinates: number[][] }
  | { type: "Polygon"; coordinates: number[][][] }
  | { type: "MultiPolygon"; coordinates: number[][][][] }
  | { type: "MultiLineString"; coordinates: number[][][] };

export interface Feature {
  type: "Feature";
  geometry: Geometry;
  properties: Record<string, unknown>;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: Feature[];
}

export interface Answer {
  question: string;
  engine: string;
  status: "answered" | "refused";
  sql: string | null;
  row_count: number;
  truncated: boolean;
  columns: string[];
  rows: unknown[][];
  geojson: FeatureCollection | null;
  reason: string | null;
  detail: string | null;
  attempts: Record<string, unknown>[];
  latency_ms: number;
}

// ---- stop demand classes (weekday boardings, August 2026)
export const DEMAND_BREAKS = [100, 500, 1500, 5000];
export const DEMAND_COLORS = ["#c6dbef", "#6baed6", "#2171b5", "#08306b", "#e6550d"];
export const DEMAND_LABELS = ["< 100", "100 – 500", "500 – 1,500", "1,500 – 5,000", "5,000 +"];
export const NO_DATA_COLOR = "#bdbdbd";

export function demandClass(boardings: number | null | undefined): number {
  if (boardings === null || boardings === undefined || Number.isNaN(boardings)) return -1;
  let k = 0;
  while (k < DEMAND_BREAKS.length && boardings >= DEMAND_BREAKS[k]) k++;
  return k;
}

export function demandColor(boardings: number | null | undefined): string {
  const k = demandClass(boardings);
  return k < 0 ? NO_DATA_COLOR : DEMAND_COLORS[k];
}

/** The same classes as a MapLibre expression, so the map and the legend cannot disagree. */
export function demandColorExpression(): unknown[] {
  const steps: unknown[] = ["step", ["to-number", ["get", "weekday_boardings"], -1], DEMAND_COLORS[0]];
  DEMAND_BREAKS.forEach((b, i) => steps.push(b, DEMAND_COLORS[i + 1]));
  return ["case", ["==", ["get", "weekday_boardings"], null], NO_DATA_COLOR, steps];
}

// ---- coverage gaps: share of residents within a 400 m walk of a stop
export const COVERAGE_BREAKS = [0.5, 0.7, 0.85];
export const COVERAGE_COLORS = ["#a50f15", "#ef3b2c", "#fcbba1", "rgba(0,0,0,0)"];
export const COVERAGE_LABELS = ["< 50%", "50 – 70%", "70 – 85%", "85% +"];

export function coverageClass(share: number | null | undefined): number {
  if (share === null || share === undefined || Number.isNaN(share)) return -1;
  let k = 0;
  while (k < COVERAGE_BREAKS.length && share >= COVERAGE_BREAKS[k]) k++;
  return k;
}

export function coverageColorExpression(): unknown[] {
  const steps: unknown[] = ["step", ["to-number", ["get", "coverage_walk_400m"], 1], COVERAGE_COLORS[0]];
  COVERAGE_BREAKS.forEach((b, i) => steps.push(b, COVERAGE_COLORS[i + 1]));
  // subzones without residents have no coverage figure: leave them clear
  return ["case", ["==", ["get", "coverage_walk_400m"], null], "rgba(0,0,0,0)", steps];
}

// ---- geometry helpers
export type Bounds = [[number, number], [number, number]];

function eachPosition(g: Geometry, f: (lon: number, lat: number) => void) {
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") f(c[0] as number, c[1] as number);
    else if (Array.isArray(c)) c.forEach(walk);
  };
  walk(g.coordinates);
}

/** [[minLon, minLat], [maxLon, maxLat]] of all features, or null if there are none. */
export function boundsOf(fc: FeatureCollection | null | undefined): Bounds | null {
  if (!fc || fc.features.length === 0) return null;
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const feat of fc.features) {
    eachPosition(feat.geometry, (lon, lat) => {
      minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
      minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
    });
  }
  if (!Number.isFinite(minLon)) return null;
  return [[minLon, minLat], [maxLon, maxLat]];
}

export type GeometryKind = "point" | "line" | "polygon";

export function geometryKind(fc: FeatureCollection | null | undefined): GeometryKind | null {
  const t = fc?.features[0]?.geometry.type;
  if (!t) return null;
  if (t === "Point") return "point";
  if (t === "LineString" || t === "MultiLineString") return "line";
  return "polygon";
}

/** Line width in pixels for a desire line, proportional to the square root of trips (2 to 12 px). */
export function odLineWidth(trips: number, maxTrips: number): number {
  if (!(maxTrips > 0) || !(trips > 0)) return 2;
  return 2 + 10 * Math.sqrt(Math.min(trips, maxTrips) / maxTrips);
}

// ---- table formatting
const SHARE_COLUMN = /(share|coverage)/i;

export function formatCell(value: unknown, column = ""): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "number") {
    if (SHARE_COLUMN.test(column) && value >= 0 && value <= 1) return `${(value * 100).toFixed(1)}%`;
    if (Number.isInteger(value)) return value.toLocaleString("en-SG");
    return value.toLocaleString("en-SG", { maximumFractionDigits: Math.abs(value) < 10 ? 2 : 1 });
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

const REFUSALS: Record<string, string> = {
  model_declined: "The model judged that the data here cannot answer this question.",
  invalid_after_repair: "The generated SQL failed the safety checks or did not run, even after one repair attempt.",
  no_template: "The keyword rules do not cover this question. Try the model, or rephrase.",
  llm_unavailable: "The language model could not be reached. Try the keyword engine.",
  question_too_long: "The question is too long.",
  empty_question: "Type a question first.",
};

export function refusalMessage(reason: string | null | undefined): string {
  if (!reason) return "The question was refused.";
  if (reason.startsWith("prescreen_")) return "This tool only reads bus-network data; the question asks for something else.";
  return REFUSALS[reason] ?? `Refused (${reason}).`;
}

/** Checks the shape of an /api/ask response; throws with a clear message if the server sent something else. */
export function parseAnswer(data: unknown): Answer {
  const d = data as Record<string, unknown>;
  const required = ["question", "engine", "status", "row_count", "columns", "rows"];
  for (const k of required) {
    if (!d || !(k in d)) throw new Error(`answer is missing "${k}"`);
  }
  if (d.status !== "answered" && d.status !== "refused") throw new Error(`unknown status ${String(d.status)}`);
  if (!Array.isArray(d.rows) || !Array.isArray(d.columns)) throw new Error("rows and columns must be arrays");
  if (d.status === "answered" && typeof d.sql !== "string") throw new Error("an answered question must include its SQL");
  return d as unknown as Answer;
}

export const STOP_CODE_RE = /^\d{5}$/;

import { describe, expect, it } from "vitest";
import {
  boundsOf, coverageClass, coverageColorExpression, demandClass, demandColor, demandColorExpression, DEMAND_COLORS,
  formatCell, geometryKind, NO_DATA_COLOR, odLineWidth, parseAnswer, refusalMessage, STOP_CODE_RE,
  type FeatureCollection,
} from "./logic";

const fc = (geoms: FeatureCollection["features"][number]["geometry"][]): FeatureCollection => ({
  type: "FeatureCollection",
  features: geoms.map((geometry) => ({ type: "Feature", geometry, properties: {} })),
});

describe("demand classes", () => {
  it("puts boundaries in the upper class", () => {
    expect(demandClass(99.9)).toBe(0);
    expect(demandClass(100)).toBe(1);
    expect(demandClass(1499)).toBe(2);
    expect(demandClass(5000)).toBe(4);
    expect(demandClass(66078)).toBe(4);
  });
  it("treats missing values as no data", () => {
    expect(demandClass(null)).toBe(-1);
    expect(demandColor(undefined)).toBe(NO_DATA_COLOR);
    expect(demandColor(Number.NaN)).toBe(NO_DATA_COLOR);
  });
  it("uses the same breaks in the map expression as in the legend", () => {
    const expr = demandColorExpression() as unknown[];
    const step = expr[3] as unknown[];
    expect(step[0]).toBe("step");
    expect(step.slice(3).filter((_, i) => i % 2 === 0)).toEqual([100, 500, 1500, 5000]);
    expect(step.slice(2).filter((_, i) => i % 2 === 0)).toEqual(DEMAND_COLORS);
  });
});

describe("coverage classes", () => {
  it("marks low coverage as the darkest gap", () => {
    expect(coverageClass(0.3)).toBe(0);
    expect(coverageClass(0.5)).toBe(1);
    expect(coverageClass(0.84)).toBe(2);
    expect(coverageClass(0.9)).toBe(3);
    expect(coverageClass(null)).toBe(-1);
  });
  it("leaves subzones without a figure transparent", () => {
    const expr = coverageColorExpression() as unknown[];
    expect(expr[0]).toBe("case");
    expect(expr[2]).toBe("rgba(0,0,0,0)");
  });
});

describe("boundsOf", () => {
  it("covers points, lines and polygons in lon/lat order", () => {
    const b = boundsOf(fc([
      { type: "Point", coordinates: [103.8, 1.3] },
      { type: "LineString", coordinates: [[103.7, 1.35], [103.9, 1.28]] },
      { type: "Polygon", coordinates: [[[103.6, 1.4], [103.65, 1.4], [103.65, 1.45], [103.6, 1.4]]] },
    ]));
    expect(b).toEqual([[103.6, 1.28], [103.9, 1.45]]);
  });
  it("handles multipolygons", () => {
    const b = boundsOf(fc([{ type: "MultiPolygon", coordinates: [[[[1, 2], [3, 4], [1, 2]]], [[[-1, 0], [0, 0], [-1, 0]]]] }]));
    expect(b).toEqual([[-1, 0], [3, 4]]);
  });
  it("returns null for nothing", () => {
    expect(boundsOf(null)).toBeNull();
    expect(boundsOf(fc([]))).toBeNull();
  });
});

describe("geometryKind", () => {
  it("maps GeoJSON types to layer kinds", () => {
    expect(geometryKind(fc([{ type: "Point", coordinates: [0, 0] }]))).toBe("point");
    expect(geometryKind(fc([{ type: "LineString", coordinates: [[0, 0], [1, 1]] }]))).toBe("line");
    expect(geometryKind(fc([{ type: "MultiPolygon", coordinates: [] }]))).toBe("polygon");
    expect(geometryKind(fc([]))).toBeNull();
  });
});

describe("odLineWidth", () => {
  it("grows with trips and stays between 2 and 12 px", () => {
    expect(odLineWidth(0, 100)).toBe(2);
    expect(odLineWidth(100, 100)).toBe(12);
    expect(odLineWidth(25, 100)).toBe(7);
    expect(odLineWidth(500, 100)).toBe(12);
    expect(odLineWidth(10, 0)).toBe(2);
  });
});

describe("formatCell", () => {
  it("formats shares as percentages only in share columns", () => {
    expect(formatCell(0.4532, "coverage_walk_400m")).toBe("45.3%");
    expect(formatCell(0.4532, "pct")).toBe("0.45");
  });
  it("formats numbers and nulls", () => {
    expect(formatCell(66078.8, "weekday_boardings")).toBe("66,078.8");
    expect(formatCell(286)).toBe("286");
    expect(formatCell(null)).toBe("–");
    expect(formatCell("01012", "stop_code")).toBe("01012");
  });
});

describe("parseAnswer", () => {
  const ok = { question: "q", engine: "llm", status: "answered", sql: "SELECT 1", row_count: 1, columns: ["a"], rows: [[1]] };
  it("accepts a valid answer", () => {
    expect(parseAnswer(ok).row_count).toBe(1);
  });
  it("rejects an answer without SQL", () => {
    expect(() => parseAnswer({ ...ok, sql: null })).toThrow(/SQL/);
  });
  it("rejects missing fields and unknown status", () => {
    expect(() => parseAnswer({ ...ok, rows: undefined })).toThrow();
    expect(() => parseAnswer({ ...ok, status: "maybe" })).toThrow(/status/);
    expect(() => parseAnswer(null)).toThrow();
  });
  it("accepts a refusal without SQL", () => {
    expect(parseAnswer({ ...ok, status: "refused", sql: null, rows: [], row_count: 0 }).status).toBe("refused");
  });
});

describe("refusalMessage and stop codes", () => {
  it("explains refusals", () => {
    expect(refusalMessage("prescreen_write_request")).toMatch(/only reads/);
    expect(refusalMessage("model_declined")).toMatch(/cannot answer/);
    expect(refusalMessage("template_unsupported_constraint")).toBe("The keyword rules cannot apply all the conditions in this question. Try rephrasing it or using the language model.");
    expect(refusalMessage("something_new")).toBe("Refused (something_new).");
  });
  it("accepts only 5-digit stop codes", () => {
    expect(STOP_CODE_RE.test("01012")).toBe(true);
    expect(STOP_CODE_RE.test("1012")).toBe(false);
    expect(STOP_CODE_RE.test("01012'")).toBe(false);
  });
});

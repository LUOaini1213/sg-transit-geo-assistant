// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import type { Layers } from "./MapView";

vi.mock("./MapView", () => ({ default: ({ layers, onStopClick }: { layers: Layers; onStopClick: (code: string) => void }) => (
  <div>
    {["01012", "02021", "03031"].map(code => <button key={code} onClick={() => onStopClick(code)}>Stop {code}</button>)}
    <output aria-label="OD origin">{String(layers.od?.features[0]?.properties.origin_stop ?? "")}</output>
    <output aria-label="Result layer">{String(layers.result?.features.length ?? 0)}</output>
  </div>
) }));

const empty = { type: "FeatureCollection", features: [] };
const stop = (code: string) => ({ stop_code: code, stop_name: `Name ${code}`, road_name: "Road", planning_area: null,
  subzone: null, weekday_boardings: null, am_peak_share: null, pm_peak_share: null, peak_hour: null, n_services: 1, services: ["7"] });
const od = (code: string) => ({ type: "FeatureCollection", features: [{ type: "Feature", geometry: {
  type: "LineString", coordinates: [[103.8, 1.3], [103.9, 1.4]],
}, properties: { origin_stop: code, destination_stop: "99999", weekday_trips: 50 } }] });
const answer = (question: string) => ({ question, status: "answered", engine: "template", sql: "SELECT 1", row_count: 1,
  truncated: false, columns: ["value"], rows: [[question]], geojson: od("01012"), reason: null, detail: null, attempts: [], latency_ms: 1 });

type Pending = { url: string; init?: RequestInit; resolve: (response: Response) => void; reject: (error: Error) => void };
let pending: Pending[], host: HTMLDivElement, root: Root;
beforeEach(async () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  pending = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    if (url.startsWith("/api/layers/")) return Promise.resolve(Response.json(empty));
    if (url === "/api/examples") return Promise.resolve(Response.json({ questions: ["Example question"] }));
    // Deliberately ignore cancellation: a response already in flight must still be ignored by the UI.
    return new Promise<Response>((resolve, reject) => pending.push({ url, init, resolve, reject }));
  }));
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  await act(async () => { root.render(<App />); });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

const button = (text: string) => [...host.querySelectorAll("button")].find(node => node.textContent === text)!;
const output = (label: string) => host.querySelector(`[aria-label="${label}"]`)?.textContent;
const closeStop = () => host.querySelector<HTMLButtonElement>('[aria-label="Close stop details"]')!;
async function click(node: HTMLElement) { expect(node).toBeTruthy(); await act(async () => { node.click(); }); }
async function resolveStop(code: string) {
  await act(async () => {
    pending.find(request => request.url === `/api/stops/${code}`)!.resolve(Response.json(stop(code)));
    pending.find(request => request.url.startsWith(`/api/stops/${code}/od`))!.resolve(Response.json(od(code)));
  });
}
async function ask(question: string) {
  const field = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, question);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}

it("keeps the latest clicked stop and OD together when responses arrive backwards", async () => {
  await click(button("Stop 01012")); await click(button("Stop 02021"));
  await resolveStop("02021"); await resolveStop("01012");
  expect(host.querySelector(".stop")?.textContent).toContain("Name 02021");
  expect(host.querySelector(".stop")?.textContent).not.toContain("Name 01012");
  expect(output("OD origin")).toBe("02021");
});

it("closing details during another stop request keeps the panel and OD closed", async () => {
  await click(button("Stop 01012")); await resolveStop("01012");
  await click(button("Stop 02021")); await click(closeStop()); await resolveStop("02021");
  expect(host.querySelector(".stop")).toBeNull(); expect(output("OD origin")).toBe("");
});

it("starting another question clears the old result and invalidates pending stop details", async () => {
  await ask("First question");
  await act(async () => pending.find(request => request.url === "/api/ask")!.resolve(Response.json(answer("First question"))));
  await click(button("Stop 01012"));
  await ask("Second question");
  expect(host.querySelector(".answer")).toBeNull(); expect(output("Result layer")).toBe("0");
  await resolveStop("01012");
  expect(host.querySelector(".stop")).toBeNull(); expect(output("OD origin")).toBe("");
  await act(async () => pending.filter(request => request.url === "/api/ask")[1].reject(new Error("offline")));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("offline");
  expect(host.querySelector(".answer")).toBeNull();
});

it("clearing results prevents a late answer or stop request from restoring them and preserves the draft", async () => {
  await ask("Keep this question"); await click(button("Stop 01012"));
  await click(button("Clear results"));
  await act(async () => pending.find(request => request.url === "/api/ask")!.resolve(Response.json(answer("Keep this question"))));
  await resolveStop("01012");
  expect(host.querySelector(".answer")).toBeNull(); expect(host.querySelector(".stop")).toBeNull();
  expect(output("Result layer")).toBe("0"); expect(output("OD origin")).toBe("");
  expect(host.querySelector("textarea")?.value).toBe("Keep this question"); expect(button("Ask").disabled).toBe(false);
});

it("shows a selected stop failure without retaining the previous stop or its OD lines", async () => {
  await click(button("Stop 01012")); await resolveStop("01012"); await click(button("Stop 02021"));
  await act(async () => pending.find(request => request.url === "/api/stops/02021")!.reject(new Error("stop unavailable")));
  expect(host.querySelector(".stop")?.textContent).not.toContain("Name 01012"); expect(output("OD origin")).toBe("");
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("stop unavailable");
  await click(button("Stop 03031")); await resolveStop("03031");
  expect(host.querySelector('[role="alert"]')).toBeNull(); expect(output("OD origin")).toBe("03031");
});

it("ignores an old stop failure after a newer selection has succeeded", async () => {
  await click(button("Stop 01012")); await click(button("Stop 02021")); await resolveStop("02021");
  await act(async () => pending.find(request => request.url === "/api/stops/01012")!.reject(new Error("old failure")));
  expect(host.querySelector('[role="alert"]')).toBeNull(); expect(output("OD origin")).toBe("02021");
});

it("a cleared request cannot finish or unlock the next query, even if its response ignores abort", async () => {
  await ask("Old query"); const old = pending.find(request => request.url === "/api/ask")!;
  await click(button("Clear results")); expect(old.init?.signal?.aborted).toBe(true);
  await ask("New query");
  await act(async () => old.resolve(Response.json(answer("Old query"))));
  expect(host.querySelector(".answer")).toBeNull(); expect(button("Working…").disabled).toBe(true);
  await act(async () => pending.filter(request => request.url === "/api/ask")[1].resolve(Response.json(answer("New query"))));
  expect(host.querySelector(".answer")?.textContent).toContain("New query");
  expect(host.querySelector(".answer")?.textContent).not.toContain("Old query"); expect(button("Ask").disabled).toBe(false);
});

it.each([
  { patch: { rows: [42] }, message: "Invalid answer table data" },
  { patch: { geojson: { type: "FeatureCollection", features: [null] } }, message: "Invalid map data" },
])("reports malformed answer data instead of crashing or drawing it: $message", async ({ patch, message }) => {
  await ask("Malformed response");
  await act(async () => pending.find(request => request.url === "/api/ask")!.resolve(Response.json({ ...answer("Malformed response"), ...patch })));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain(message);
  expect(host.querySelector(".answer")).toBeNull(); expect(output("Result layer")).toBe("0");
  expect(button("Ask").disabled).toBe(false);
});

it.each(["wrong stop", "missing services", "wrong OD origin"])("reports %s without showing a mixed stop/OD pair", async problem => {
  await click(button("Stop 01012"));
  await act(async () => {
    pending.find(request => request.url === "/api/stops/01012")!.resolve(Response.json(
      problem === "wrong stop" ? stop("02021") : { ...stop("01012"), services: problem === "missing services" ? null : ["7"] }));
    pending.find(request => request.url.startsWith("/api/stops/01012/od"))!.resolve(Response.json(od(problem === "wrong OD origin" ? "02021" : "01012")));
  });
  expect(host.querySelector('[role="alert"]')?.textContent).toMatch(/Stop 01012 could not be loaded: Error: Invalid/);
  expect(host.querySelector(".stop dl")).toBeNull(); expect(output("OD origin")).toBe("");
  await click(closeStop()); expect(host.querySelector('[role="alert"]')).toBeNull();
});

it("keeps base-layer data failures visible even when a subsequent question succeeds", async () => {
  await act(async () => root.unmount());
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url === "/api/layers/stops") return Promise.resolve(Response.json({ type: "FeatureCollection", features: [{ geometry: null }] }));
    if (url === "/api/layers/coverage") return Promise.resolve(new Response("offline", { status: 503 }));
    if (url === "/api/examples") return Promise.resolve(Response.json({ questions: [] }));
    return Promise.resolve(Response.json(answer("Still usable")));
  }));
  root = createRoot(host); await act(async () => root.render(<App />));
  await ask("Still usable");
  expect(host.querySelector(".answer")?.textContent).toContain("Still usable");
  const alerts = [...host.querySelectorAll('[role="alert"]')].map(node => node.textContent);
  expect(alerts).toHaveLength(2);
  expect(alerts.join(" ")).toContain("Stops could not be loaded: Error: Invalid map data");
  expect(alerts.join(" ")).toContain("Coverage could not be loaded: Error: /api/layers/coverage: HTTP 503");
});

it.each([od("01012"), empty])("explains partially mapped results without dropping table rows", async geojson => {
  await ask("Include unmatched destinations");
  await act(async () => pending.find(request => request.url === "/api/ask")!.resolve(Response.json({
    ...answer("Include unmatched destinations"), row_count: 2, rows: [["Known destination"], ["Unknown destination"]], geojson,
  })));
  expect(host.querySelector(".answer")?.textContent).toContain(`${geojson.features.length} of 2 rows mapped; other rows have no matching location.`);
  expect(host.querySelectorAll(".answer tbody tr")).toHaveLength(2);
  expect(host.querySelector(".answer")?.textContent).toContain("Unknown destination");
  expect(output("Result layer")).toBe(String(geojson.features.length));
});

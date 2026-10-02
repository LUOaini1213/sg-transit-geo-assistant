// @vitest-environment jsdom
import { Activity, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import MapView, { type Layers } from "./MapView";
import { type FeatureCollection, odLineWidth } from "./logic";

const fake = vi.hoisted(() => {
  type Handler = (event?: unknown) => void;
  class Source {
    constructor(public data: FeatureCollection) {}
    setData(data: FeatureCollection) { this.data = data; }
  }
  class Map {
    static instances: Map[] = [];
    sources: Record<string, Source> = {};
    visibility: Record<string, unknown> = {};
    handlers: Record<string, Handler[]> = {};
    fitBounds = vi.fn();
    remove = vi.fn();
    canvas = { style: { cursor: "" } };
    constructor() { Map.instances.push(this); }
    addControl() {}
    addLayer() {}
    addSource(id: string, value: { data: FeatureCollection }) { this.sources[id] = new Source(value.data); }
    getSource(id: string) {
      if (this.remove.mock.calls.length) throw new Error("removed map has no sources");
      return this.sources[id];
    }
    getCanvas() { return this.canvas; }
    setLayoutProperty(id: string, _name: string, value: unknown) {
      if (this.remove.mock.calls.length) throw new Error("removed map has no style");
      this.visibility[id] = value;
    }
    on(event: string, layerOrHandler: string | Handler, handler?: Handler) {
      const key = typeof layerOrHandler === "string" ? `${event}:${layerOrHandler}` : event;
      (this.handlers[key] ??= []).push(typeof layerOrHandler === "string" ? handler! : layerOrHandler);
    }
    emit(event: string, payload?: unknown) { this.handlers[event]?.forEach((handler) => handler(payload)); }
  }
  class Popup {
    static instances: Popup[] = [];
    remove = vi.fn();
    html = "";
    constructor() { Popup.instances.push(this); }
    setLngLat() { return this; }
    setHTML(html: string) { this.html = html; return this; }
    addTo() { return this; }
  }
  return { Map, Popup };
});

vi.mock("maplibre-gl", () => ({
  Map: fake.Map,
  Popup: fake.Popup,
  NavigationControl: class {},
  setWorkerUrl: vi.fn(),
}));
vi.mock("maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url", () => ({ default: "mock-worker.js" }));

const emptyLayers = (): Layers => ({ stops: null, coverage: null, od: null, result: null });
const point: FeatureCollection = {
  type: "FeatureCollection",
  features: [{ type: "Feature", geometry: { type: "Point", coordinates: [103.8, 1.3] }, properties: { stop_code: "12345" } }],
};
const od: FeatureCollection = {
  type: "FeatureCollection",
  features: [{ type: "Feature", geometry: { type: "LineString", coordinates: [[103.8, 1.3], [103.9, 1.4]] }, properties: { weekday_trips: 100 } }],
};

describe("MapView state while the map is loading", () => {
  let root: Root;
  let container: HTMLDivElement;
  let layers: Layers;
  let showStops: boolean;
  let showCoverage: boolean;
  let onStopClick: Mock<(code: string) => void>;
  const render = async () => {
    await act(async () => root.render(<MapView layers={layers} showStops={showStops} showCoverage={showCoverage} onStopClick={onStopClick} />));
  };
  const load = async () => {
    const map = fake.Map.instances.at(-1)!;
    await act(async () => map.emit("load"));
    return map;
  };

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    fake.Map.instances.length = 0;
    fake.Popup.instances.length = 0;
    layers = emptyLayers();
    showStops = true;
    showCoverage = true;
    onStopClick = vi.fn();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("applies the latest layers, line widths, bounds and visibility after load", async () => {
    await render();
    layers = { stops: point, coverage: point, od, result: point };
    showStops = false;
    showCoverage = false;
    await render();
    const map = await load();

    expect(map.sources.stops.data).toEqual(point);
    expect(map.sources.coverage.data).toEqual(point);
    expect(map.sources.result.data).toEqual(point);
    expect(map.sources.od.data.features).toEqual([
      { ...od.features[0], properties: { weekday_trips: 100, _width: odLineWidth(100, 100) } },
    ]);
    expect(map.visibility).toEqual({ stops: "none", "coverage-fill": "none", "coverage-line": "none" });
    expect(map.fitBounds).toHaveBeenCalledWith([[103.8, 1.3], [103.9, 1.4]], { padding: 60, maxZoom: 14, duration: 600 });
    expect(map.fitBounds).toHaveBeenLastCalledWith([[103.8, 1.3], [103.8, 1.3]], { padding: 60, maxZoom: 15, duration: 600 });
  });

  it("does not restore layers cleared before load and respects initially hidden controls", async () => {
    layers = { ...layers, od, result: point };
    showStops = false;
    showCoverage = false;
    await render();
    layers = emptyLayers();
    await render();
    const map = await load();

    expect(map.sources.od.data.features).toEqual([]);
    expect(map.sources.result.data.features).toEqual([]);
    expect(map.fitBounds).not.toHaveBeenCalled();
    expect(map.visibility).toEqual({ stops: "none", "coverage-fill": "none", "coverage-line": "none" });
  });

  it.each(["od", "result"] as const)("removes the %s popup when its data is cleared", async (source) => {
    layers = { ...layers, [source]: source === "od" ? od : point };
    await render();
    const map = await load();
    map.emit(`click:${source === "od" ? "od" : "result-point"}`, { features: [{ properties: { label: "Old result" } }], lngLat: [103.8, 1.3] });
    const popup = fake.Popup.instances[0];
    expect(popup.html).toContain("Old result");

    layers = { ...layers, [source]: null };
    await render();
    expect(map.sources[source].data.features).toEqual([]);
    expect(popup.remove).toHaveBeenCalledOnce();
  });

  it.each(["od", "result"] as const)("removes the stale %s popup when its data is replaced", async (source) => {
    const data = source === "od" ? od : point;
    layers = { ...layers, [source]: data };
    await render();
    const map = await load();
    map.emit(`click:${source === "od" ? "od" : "result-point"}`, { features: [{ properties: { label: "Old result" } }], lngLat: [103.8, 1.3] });
    const popup = fake.Popup.instances[0];
    layers = { ...layers, [source]: { ...data, features: data.features.map((feature) => ({ ...feature, properties: { label: "New result" } })) } };
    await render();

    expect(popup.remove).toHaveBeenCalledOnce();
    expect(map.sources[source].data.features[0].properties.label).toBe("New result");
  });

  it("keeps an unrelated result popup and uses updated visibility and stop callback", async () => {
    layers = { ...layers, od, result: point };
    await render();
    const map = await load();
    map.emit("click:result-point", { features: [{ properties: { label: "Result" } }], lngLat: [103.8, 1.3] });
    const popup = fake.Popup.instances[0];
    layers = { ...layers, od: null };
    showStops = false;
    showCoverage = false;
    onStopClick = vi.fn();
    await render();

    expect(popup.remove).not.toHaveBeenCalled();
    expect(map.visibility).toEqual({ stops: "none", "coverage-fill": "none", "coverage-line": "none" });
    map.emit("click:stops", { features: [{ properties: { stop_code: "54321" } }] });
    expect(onStopClick).toHaveBeenCalledWith("54321");
  });

  it("removes the active popup on unmount", async () => {
    layers = { ...layers, od };
    await render();
    const map = await load();
    map.emit("click:od", { features: [{ properties: { label: "OD" } }], lngLat: [103.8, 1.3] });
    const popup = fake.Popup.instances[0];
    await act(async () => root.unmount());
    expect(popup.remove).toHaveBeenCalledOnce();
    expect(map.remove).toHaveBeenCalledOnce();
  });

  it("replaces a previous popup when another feature is clicked", async () => {
    layers = { ...layers, od, result: point };
    await render();
    const map = await load();
    map.emit("click:od", { features: [{ properties: { label: "OD" } }], lngLat: [103.8, 1.3] });
    map.emit("click:result-point", { features: [{ properties: { label: "Result" } }], lngLat: [103.8, 1.3] });
    expect(fake.Popup.instances[0].remove).toHaveBeenCalledOnce();
    expect(fake.Popup.instances[1].remove).not.toHaveBeenCalled();
  });

  it("does not reuse a removed map when effects remount with preserved component state", async () => {
    const paint = async (mode: "visible" | "hidden") => {
      await act(async () => root.render(<Activity mode={mode}>
        <MapView layers={layers} showStops={showStops} showCoverage={showCoverage} onStopClick={onStopClick} />
      </Activity>));
    };
    layers = { ...layers, od, result: point };
    await paint("visible"); const oldMap = await load();
    await paint("hidden"); expect(oldMap.remove).toHaveBeenCalledOnce();
    showStops = false; showCoverage = false;
    await paint("visible");
    const newMap = fake.Map.instances.at(-1)!;
    expect(newMap).not.toBe(oldMap);
    expect(newMap.visibility).toEqual({});
    expect(newMap.sources).toEqual({});
    await act(async () => oldMap.emit("load"));
    expect(newMap.sources).toEqual({});
    await load();
    expect(newMap.visibility).toEqual({ stops: "none", "coverage-fill": "none", "coverage-line": "none" });
    expect(newMap.sources.od.data.features).toHaveLength(1);
    expect(newMap.sources.result.data).toEqual(point);
  });
});

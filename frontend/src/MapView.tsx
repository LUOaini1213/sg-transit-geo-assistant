import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre finds its worker next to its own module file, which a bundler moves. Let Vite bundle the worker
// (with its shared chunk) and tell MapLibre where it ended up.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef, useState } from "react";
import {
  boundsOf, coverageColorExpression, demandColorExpression, formatCell, geometryKind, odLineWidth,
  type FeatureCollection,
} from "./logic";

// OpenFreeMap: free vector tiles from OpenStreetMap data, no key. Its style carries the attribution.
maplibregl.setWorkerUrl(workerUrl);

const STYLE = "https://tiles.openfreemap.org/styles/positron";
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };
const RESULT = "#d6336c";

export interface Layers {
  stops: FeatureCollection | null;
  coverage: FeatureCollection | null;
  od: FeatureCollection | null;
  result: FeatureCollection | null;
}

interface Props {
  layers: Layers;
  showStops: boolean;
  showCoverage: boolean;
  onStopClick: (code: string) => void;
}

function setData(map: maplibregl.Map, id: string, data: FeatureCollection | null) {
  (map.getSource(id) as GeoJSONSource | undefined)?.setData((data ?? EMPTY) as GeoJSON.FeatureCollection);
}

function popupHtml(props: Record<string, unknown>): string {
  const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  return Object.entries(props)
    .slice(0, 8)
    .map(([k, v]) => `<div><b>${esc(k)}</b>: ${esc(formatCell(v, k))}</div>`)
    .join("");
}

interface ActivePopup {
  source: "od" | "result";
  popup: maplibregl.Popup;
}

function removePopup(ref: { current: ActivePopup | null }, source?: ActivePopup["source"]) {
  if (!ref.current || (source && ref.current.source !== source)) return;
  ref.current.popup.remove();
  ref.current = null;
}

export default function MapView({ layers, showStops, showCoverage, onStopClick }: Props) {
  const box = useRef<HTMLDivElement>(null);
  // Readiness is state so every layer effect also runs with the latest props after load.
  const [map, setMap] = useState<maplibregl.Map | null>(null);
  // Fast Refresh / React effect reconnection retains state after disposing its map.
  // Effects must only touch the live instance, and only after that instance loads.
  const liveMap = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<ActivePopup | null>(null);
  const clickRef = useRef(onStopClick);
  clickRef.current = onStopClick;

  useEffect(() => {
    if (!box.current) return;
    const map = new maplibregl.Map({
      container: box.current,
      style: STYLE,
      center: [103.82, 1.352],
      zoom: 10.4,
      attributionControl: { compact: false },
    });
    liveMap.current = map;
    let disposed = false;
    if (import.meta.env.DEV) (window as unknown as { __map?: unknown }).__map = map; // for debugging in the browser console
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      if (disposed) return;
      for (const id of ["coverage", "stops", "od", "result"]) map.addSource(id, { type: "geojson", data: EMPTY as GeoJSON.FeatureCollection });
      map.addLayer({ id: "coverage-fill", type: "fill", source: "coverage",
        paint: { "fill-color": coverageColorExpression() as never, "fill-opacity": 0.4 } });
      map.addLayer({ id: "coverage-line", type: "line", source: "coverage",
        paint: { "line-color": "#999", "line-width": 0.3 } });
      map.addLayer({ id: "stops", type: "circle", source: "stops",
        paint: {
          "circle-color": demandColorExpression() as never,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.6, 13, 3.5, 16, 7],
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 10, 0, 14, 0.6],
          "circle-stroke-color": "#fff",
        } });
      map.addLayer({ id: "od", type: "line", source: "od", layout: { "line-cap": "round" },
        paint: { "line-color": "#f28c28", "line-opacity": 0.8, "line-width": ["get", "_width"] } });
      map.addLayer({ id: "result-fill", type: "fill", source: "result", filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": RESULT, "fill-opacity": 0.35 } });
      map.addLayer({ id: "result-line", type: "line", source: "result",
        filter: ["any", ["==", ["geometry-type"], "LineString"], ["==", ["geometry-type"], "Polygon"]],
        paint: { "line-color": RESULT, "line-width": 2.5 } });
      map.addLayer({ id: "result-point", type: "circle", source: "result", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-color": RESULT, "circle-radius": 6, "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
      setMap(map);
    });

    map.on("click", "stops", (e: MapLayerMouseEvent) => {
      const code = e.features?.[0]?.properties?.stop_code;
      if (typeof code === "string") clickRef.current(code);
    });
    for (const id of ["result-point", "result-line", "result-fill", "od"]) {
      map.on("click", id, (e: MapLayerMouseEvent) => {
        const f = e.features?.[0];
        if (!f) return;
        const props = { ...(f.properties as Record<string, unknown>) };
        delete props._width;
        removePopup(popupRef);
        popupRef.current = {
          source: id === "od" ? "od" : "result",
          popup: new maplibregl.Popup({ maxWidth: "280px", focusAfterOpen: false }).setLngLat(e.lngLat).setHTML(popupHtml(props)).addTo(map),
        };
      });
    }
    for (const id of ["stops", "result-point", "result-line", "result-fill", "od"]) {
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
    return () => {
      disposed = true;
      if (liveMap.current === map) liveMap.current = null;
      removePopup(popupRef);
      map.remove();
    };
  }, []);

  useEffect(() => {
    if (map && map === liveMap.current) setData(map, "stops", layers.stops);
  }, [map, layers.stops]);

  useEffect(() => {
    if (map && map === liveMap.current) setData(map, "coverage", layers.coverage);
  }, [map, layers.coverage]);

  useEffect(() => {
    if (!map || map !== liveMap.current) return;
    removePopup(popupRef, "od");
    const od = layers.od;
    const max = Math.max(0, ...(od?.features ?? []).map((f) => Number(f.properties.weekday_trips) || 0));
    const withWidth = od && {
      ...od,
      features: od.features.map((f) => ({ ...f, properties: { ...f.properties, _width: odLineWidth(Number(f.properties.weekday_trips), max) } })),
    };
    setData(map, "od", withWidth);
    const b = boundsOf(od);
    if (b) map.fitBounds(b, { padding: 60, maxZoom: 14, duration: 600 });
  }, [map, layers.od]);

  useEffect(() => {
    if (!map || map !== liveMap.current) return;
    removePopup(popupRef, "result");
    setData(map, "result", layers.result);
    const b = boundsOf(layers.result);
    if (b) {
      const single = geometryKind(layers.result) === "point" && layers.result!.features.length === 1;
      map.fitBounds(b, { padding: 60, maxZoom: single ? 15 : 14, duration: 600 });
    }
  }, [map, layers.result]);

  useEffect(() => {
    if (!map || map !== liveMap.current) return;
    map.setLayoutProperty("stops", "visibility", showStops ? "visible" : "none");
  }, [map, showStops]);

  useEffect(() => {
    if (!map || map !== liveMap.current) return;
    for (const id of ["coverage-fill", "coverage-line"]) map.setLayoutProperty(id, "visibility", showCoverage ? "visible" : "none");
  }, [map, showCoverage]);

  return <div ref={box} className="map" role="region" aria-label="Map of Singapore bus stops" />;
}

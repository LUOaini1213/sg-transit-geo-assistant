import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre finds its worker next to its own module file, which a bundler moves. Let Vite bundle the worker
// (with its shared chunk) and tell MapLibre where it ended up.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef } from "react";
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

export default function MapView({ layers, showStops, showCoverage, onStopClick }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const latest = useRef(layers);
  latest.current = layers;
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
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as { __map?: unknown }).__map = map; // for debugging in the browser console
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
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
      ready.current = true;
      const l = latest.current;
      setData(map, "coverage", l.coverage);
      setData(map, "stops", l.stops);
      setData(map, "result", l.result);
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
        new maplibregl.Popup({ maxWidth: "280px", focusAfterOpen: false }).setLngLat(e.lngLat).setHTML(popupHtml(props)).addTo(map);
      });
    }
    for (const id of ["stops", "result-point", "result-line", "result-fill", "od"]) {
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
    return () => {
      ready.current = false;
      map.remove();
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (map && ready.current) setData(map, "stops", layers.stops);
  }, [layers.stops]);

  useEffect(() => {
    const map = mapRef.current;
    if (map && ready.current) setData(map, "coverage", layers.coverage);
  }, [layers.coverage]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    const od = layers.od;
    const max = Math.max(0, ...(od?.features ?? []).map((f) => Number(f.properties.weekday_trips) || 0));
    const withWidth = od && {
      ...od,
      features: od.features.map((f) => ({ ...f, properties: { ...f.properties, _width: odLineWidth(Number(f.properties.weekday_trips), max) } })),
    };
    setData(map, "od", withWidth);
    const b = boundsOf(od);
    if (b) map.fitBounds(b, { padding: 60, maxZoom: 14, duration: 600 });
  }, [layers.od]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    setData(map, "result", layers.result);
    const b = boundsOf(layers.result);
    if (b) {
      const single = geometryKind(layers.result) === "point" && layers.result!.features.length === 1;
      map.fitBounds(b, { padding: 60, maxZoom: single ? 15 : 14, duration: 600 });
    }
  }, [layers.result]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    map.setLayoutProperty("stops", "visibility", showStops ? "visible" : "none");
  }, [showStops]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    for (const id of ["coverage-fill", "coverage-line"]) map.setLayoutProperty(id, "visibility", showCoverage ? "visible" : "none");
  }, [showCoverage]);

  return <div ref={box} className="map" role="region" aria-label="Map of Singapore bus stops" />;
}

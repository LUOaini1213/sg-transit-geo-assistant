import { parseAnswer, parseFeatureCollection, STOP_CODE_RE, type Answer } from "./logic";

export type Engine = "auto" | "llm" | "template";

export interface StopDetail {
  stop_code: string;
  stop_name: string;
  road_name: string;
  planning_area: string | null;
  subzone: string | null;
  weekday_boardings: number | null;
  am_peak_share: number | null;
  pm_peak_share: number | null;
  peak_hour: number | null;
  n_services: number;
  services: string[];
}

async function getJson(url: string, signal?: AbortSignal): Promise<unknown> {
  const r = await fetch(url, { signal });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

function parseStop(data: unknown, code: string): StopDetail {
  const value = data as Partial<StopDetail> | null;
  if (!value || value.stop_code !== code || typeof value.stop_name !== "string" || typeof value.road_name !== "string"
    || !Array.isArray(value.services) || !value.services.every(service => typeof service === "string")
    || !Number.isInteger(value.n_services) || Number(value.n_services) < 0
    || !["planning_area", "subzone"].every(key => value[key as keyof StopDetail] === null || typeof value[key as keyof StopDetail] === "string")
    || !["weekday_boardings", "am_peak_share", "pm_peak_share", "peak_hour"].every(key => {
      const item = value[key as keyof StopDetail];
      return item === null || (typeof item === "number" && Number.isFinite(item));
    })) throw new Error(`Invalid stop details for ${code}`);
  return value as StopDetail;
}

export const api = {
  stops: async (signal?: AbortSignal) => parseFeatureCollection(await getJson("/api/layers/stops", signal)),
  coverage: async (signal?: AbortSignal) => parseFeatureCollection(await getJson("/api/layers/coverage", signal)),
  examples: async (signal?: AbortSignal) => {
    const data = await getJson("/api/examples", signal) as { questions?: unknown } | null;
    if (!data || !Array.isArray(data.questions) || !data.questions.every(question => typeof question === "string")) {
      throw new Error("Invalid example questions");
    }
    return { questions: data.questions as string[] };
  },
  stop: async (code: string, signal?: AbortSignal) => {
    if (!STOP_CODE_RE.test(code)) return Promise.reject(new Error("bad stop code"));
    return parseStop(await getJson(`/api/stops/${code}`, signal), code);
  },
  stopOd: async (code: string, limit = 15, signal?: AbortSignal) => {
    if (!STOP_CODE_RE.test(code)) return Promise.reject(new Error("bad stop code"));
    const data = parseFeatureCollection(await getJson(`/api/stops/${code}/od?limit=${limit}`, signal));
    if (data.features.some(feature => feature.geometry.type !== "LineString" || feature.properties.origin_stop !== code
      || typeof feature.properties.weekday_trips !== "number" || !Number.isFinite(feature.properties.weekday_trips))) {
      throw new Error(`Invalid OD data for ${code}`);
    }
    return data;
  },
  ask: async (question: string, engine: Engine, signal?: AbortSignal): Promise<Answer> => {
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, engine }),
      signal,
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return parseAnswer(await r.json());
  },
};

import { parseAnswer, STOP_CODE_RE, type Answer, type FeatureCollection } from "./logic";

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

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  stops: () => getJson<FeatureCollection>("/api/layers/stops"),
  coverage: () => getJson<FeatureCollection>("/api/layers/coverage"),
  examples: () => getJson<{ questions: string[] }>("/api/examples"),
  stop: (code: string) => {
    if (!STOP_CODE_RE.test(code)) return Promise.reject(new Error("bad stop code"));
    return getJson<StopDetail>(`/api/stops/${code}`);
  },
  stopOd: (code: string, limit = 15) => {
    if (!STOP_CODE_RE.test(code)) return Promise.reject(new Error("bad stop code"));
    return getJson<FeatureCollection>(`/api/stops/${code}/od?limit=${limit}`);
  },
  ask: async (question: string, engine: Engine): Promise<Answer> => {
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, engine }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return parseAnswer(await r.json());
  },
};

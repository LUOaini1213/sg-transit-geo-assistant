import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Engine, type StopDetail } from "./api";
import {
  COVERAGE_COLORS, COVERAGE_LABELS, DEMAND_COLORS, DEMAND_LABELS, formatCell, refusalMessage,
  type Answer, type FeatureCollection,
} from "./logic";
import MapView from "./MapView";

const MAX_TABLE_ROWS = 50;

function AnswerPanel({ answer }: { answer: Answer }) {
  if (answer.status === "refused") {
    return (
      <section className="answer refused" aria-live="polite">
        <h2>Not answered</h2>
        <p className="meta">{answer.question}</p>
        <p>{refusalMessage(answer.reason)}</p>
        <p className="meta">reason: <code>{answer.reason}</code>{answer.detail ? <> · {answer.detail}</> : null}</p>
      </section>
    );
  }
  const shown = answer.rows.slice(0, MAX_TABLE_ROWS);
  return (
    <section className="answer" aria-live="polite">
      <h2>Query result</h2>
      <p className="meta">{answer.question}</p>
      <p className="meta">
        <b>{answer.row_count}</b> row{answer.row_count === 1 ? "" : "s"}{answer.truncated ? " (cut at the row limit)" : ""}
        {" · "}engine: {answer.engine} · {Math.round(answer.latency_ms)} ms
        {answer.geojson ? (answer.geojson.features.length < answer.row_count
          ? ` · ${answer.geojson.features.length} of ${answer.row_count} rows mapped; other rows have no matching location.`
          : ` · ${answer.geojson.features.length} on the map`) : " · no map layer"}
      </p>
      <details open>
        <summary>SQL that was run</summary>
        <pre className="sql">{answer.sql}</pre>
      </details>
      <div className="table-wrap">
        <table>
          <thead><tr>{answer.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j}>{formatCell(v, answer.columns[j])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {answer.rows.length > MAX_TABLE_ROWS && <p className="meta">Showing the first {MAX_TABLE_ROWS} rows.</p>}
    </section>
  );
}

function StopPanel({ code, stop, od, error, onClose }: {
  code: string; stop: StopDetail | null; od: FeatureCollection | null; error: string | null; onClose: () => void;
}) {
  return (
    <section className="stop">
      <div className="stop-head">
        <h2>{stop?.stop_name ?? "Stop"} <span className="code">{code}</span></h2>
        <button type="button" className="link" onClick={onClose} aria-label="Close stop details">✕</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {!stop && !error && <p className="meta" role="status">Loading stop details…</p>}
      {stop && <>
      <p className="meta">{stop.road_name} · {stop.subzone ?? "outside Singapore"}{stop.planning_area ? `, ${stop.planning_area}` : ""}</p>
      <dl>
        <dt>Weekday boardings (Aug 2026)</dt><dd>{formatCell(stop.weekday_boardings)}</dd>
        <dt>AM / PM peak share</dt><dd>{formatCell(stop.am_peak_share, "share")} / {formatCell(stop.pm_peak_share, "share")}</dd>
        <dt>Services ({stop.n_services})</dt><dd>{stop.services.join(", ") || "–"}</dd>
      </dl>
      </>}
      {od && od.features.length > 0 && (
        <p className="meta">Orange lines: top {od.features.length} destinations by weekday trips, from{" "}
          {formatCell(od.features[od.features.length - 1].properties.weekday_trips)} to{" "}
          {formatCell(od.features[0].properties.weekday_trips)} trips a day.</p>
      )}
    </section>
  );
}

function Legend({ showCoverage, inline = false }: { showCoverage: boolean; inline?: boolean }) {
  return (
    <div className={inline ? "legend legend-inline" : "legend legend-map"} aria-label="Legend">
      <div className="legend-title">Weekday boardings per stop</div>
      {DEMAND_LABELS.map((l, i) => (
        <div key={l} className="legend-row"><span className="dot" style={{ background: DEMAND_COLORS[i] }} />{l}</div>
      ))}
      {showCoverage && <>
        <div className="legend-title">Residents within 400 m walk</div>
        {COVERAGE_LABELS.slice(0, 3).map((l, i) => (
          <div key={l} className="legend-row"><span className="sq" style={{ background: COVERAGE_COLORS[i] }} />{l}</div>
        ))}
      </>}
    </div>
  );
}

export default function App() {
  const [stops, setStops] = useState<FeatureCollection | null>(null);
  const [coverage, setCoverage] = useState<FeatureCollection | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [engine, setEngine] = useState<Engine>("auto");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dataErrors, setDataErrors] = useState<string[]>([]);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [stopError, setStopError] = useState<string | null>(null);
  const [stop, setStop] = useState<StopDetail | null>(null);
  const [od, setOd] = useState<FeatureCollection | null>(null);
  const [showStops, setShowStops] = useState(true);
  const [showCoverage, setShowCoverage] = useState(true);
  const stopRequest = useRef(0), askRequest = useRef(0);
  const stopController = useRef<AbortController | null>(null), askController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const loadError = (name: string, e: unknown) => {
      if (!controller.signal.aborted) setDataErrors(previous => [...previous, `${name} could not be loaded: ${String(e)}`]);
    };
    api.stops(controller.signal).then(value => { if (!controller.signal.aborted) setStops(value); }).catch(e => loadError("Stops", e));
    api.coverage(controller.signal).then(value => { if (!controller.signal.aborted) setCoverage(value); }).catch(e => loadError("Coverage", e));
    api.examples(controller.signal).then(x => { if (!controller.signal.aborted) setExamples(x.questions); }).catch(e => loadError("Examples", e));
    return () => {
      controller.abort(); stopRequest.current++; askRequest.current++;
      stopController.current?.abort(); askController.current?.abort();
    };
  }, []);

  const closeStop = useCallback(() => {
    stopRequest.current++; stopController.current?.abort(); stopController.current = null;
    setSelectedCode(null); setStop(null); setOd(null); setStopError(null);
  }, []);

  const selectStop = useCallback((code: string) => {
    const request = ++stopRequest.current;
    stopController.current?.abort();
    const controller = new AbortController(); stopController.current = controller;
    setSelectedCode(code); setStop(null); setOd(null); setStopError(null);
    Promise.all([api.stop(code, controller.signal), api.stopOd(code, 15, controller.signal)])
      .then(([s, o]) => {
        if (request === stopRequest.current && !controller.signal.aborted) { setStop(s); setOd(o); }
      })
      .catch((e) => {
        if (request === stopRequest.current && !controller.signal.aborted) setStopError(`Stop ${code} could not be loaded: ${String(e)}`);
      });
  }, []);

  const clearResults = () => {
    askRequest.current++; askController.current?.abort(); askController.current = null;
    setBusy(false); setAnswer(null); setError(null); closeStop();
  };

  const submit = async (e?: FormEvent, q = question) => {
    e?.preventDefault();
    if (!q.trim() || askController.current) return;
    const request = ++askRequest.current;
    const controller = new AbortController(); askController.current = controller;
    setBusy(true);
    setError(null); setAnswer(null); closeStop();
    try {
      const result = await api.ask(q, engine, controller.signal);
      if (request === askRequest.current && !controller.signal.aborted) setAnswer(result);
    } catch (err) {
      if (request === askRequest.current && !controller.signal.aborted) setError(`The request failed: ${String(err)}`);
    } finally {
      if (request === askRequest.current) { askController.current = null; setBusy(false); }
    }
  };

  return (
    <div className="app">
      <aside className="panel">
        <header>
          <h1>Singapore bus network: ask the data</h1>
          <p className="sub">Stops, routes, August 2026 trips and 400 m walking coverage. Answers show the SQL that was run.</p>
        </header>
        <form onSubmit={submit} className="ask">
          <label htmlFor="q" className="visually-hidden">Question</label>
          <textarea id="q" value={question} rows={3} maxLength={500} placeholder="e.g. Which bus stops are within 300 m of stop 08057?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} />
          <div className="row">
            <label>Engine{" "}
              <select value={engine} onChange={(e) => setEngine(e.target.value as Engine)}>
                <option value="auto">auto (model, then rules)</option>
                <option value="llm">language model</option>
                <option value="template">keyword rules</option>
              </select>
            </label>
            <button type="submit" disabled={busy || !question.trim()}>{busy ? "Working…" : "Ask"}</button>
            <button type="button" className="link" onClick={clearResults} disabled={!busy && !answer && !selectedCode && !error}>Clear results</button>
          </div>
        </form>
        {examples.length > 0 && !answer && (
          <div className="examples">
            <span className="meta">Try:</span>
            {examples.map((x) => (
              <button key={x} type="button" className="chip" onClick={() => { setQuestion(x); void submit(undefined, x); }}>{x}</button>
            ))}
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        {dataErrors.map(message => <p className="error" role="alert" key={message}>{message}</p>)}
        {answer && <AnswerPanel answer={answer} />}
        {selectedCode && <StopPanel code={selectedCode} stop={stop} od={od} error={stopError} onClose={closeStop} />}
        <div className="toggles">
          <label><input type="checkbox" checked={showStops} onChange={(e) => setShowStops(e.target.checked)} /> Stops by demand</label>
          <label><input type="checkbox" checked={showCoverage} onChange={(e) => setShowCoverage(e.target.checked)} /> Coverage gaps</label>
        </div>
        <Legend showCoverage={showCoverage} inline />
        <footer className="meta">
          Contains information from LTA DataMall (bus stops, routes and passenger volumes, Aug&ndash;Sep 2026), the URA
          Master Plan 2019 boundaries and SingStat GHS 2025, accessed September 2026 from datamall.lta.gov.sg,
          data.gov.sg and singstat.gov.sg, made available under the{" "}
          <a href="https://data.gov.sg/open-data-licence">Singapore Open Data Licence version 1.0</a>. Walking network
          &copy; OpenStreetMap contributors. Prepared via{" "}
          <a href="https://github.com/LUOaini1213/sg-bus-network-monitor">sg-bus-network-monitor</a>. Click a stop for its trips.
        </footer>
      </aside>
      <main className="map-wrap">
        <MapView layers={{ stops, coverage, od, result: answer?.geojson ?? null }} showStops={showStops}
          showCoverage={showCoverage} onStopClick={selectStop} />
        <Legend showCoverage={showCoverage} />
      </main>
    </div>
  );
}

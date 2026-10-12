"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Braces, Check, Download, RefreshCw } from "lucide-react";
import { ebaSnapshots, sortRows } from "./eba-data";
import { useApi } from "./api-context";
import { CodeBlock } from "./code-block";
import { ScoreExplanation } from "./score-explanation";
import { expandedResults, resultLabel, type ScoreResponse } from "@/lib/score-response";
import { gatewayRequest } from "@/lib/gateway-client";
import product from "@/data/product.json";

export function Playground({ compact = false }: { compact?: boolean }) {
  const api = useApi();
  const [dataset, setDataset] = useState(0), [rowId, setRowId] = useState(sortRows(ebaSnapshots[0].rows)[0].id);
  const [mode, setMode] = useState<"saved" | "live">("saved"), [tab, setTab] = useState("Result"), [key, setKey] = useState("");
  const [input, setInput] = useState(JSON.stringify(ebaSnapshots[0].api_request, null, 2));
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [response, setResponse] = useState<ScoreResponse | null>(null), [received, setReceived] = useState("");
  const [liveIndex, setLiveIndex] = useState(0);
  const serial = useRef(0), active = useRef<AbortController | null>(null);
  const source = ebaSnapshots[dataset], rows = sortRows(source.rows), row = rows.find(r => r.id === rowId) ?? rows[0];
  const operations = api.spec.paths as Record<string, Record<string, { operationId?: string }>>;
  const scorePath = Object.entries(operations).find(([, methods]) => methods.post?.operationId === "score_v4")?.[0];
  const liveReady = api.status.kind === "online" && api.specSource === "gateway" && !!scorePath;
  const liveStatusLabel = api.status.kind === "online"
    ? api.specSource === "gateway" ? "API CONNECTED · KEY REQUIRED" : "API REACHABLE · OPENAPI UNAVAILABLE"
    : api.status.kind === "checking" ? "CHECKING API CONNECTION"
    : api.status.kind === "idle" ? "API NOT CHECKED"
    : api.status.kind === "unconfigured" ? "API NOT CONFIGURED" : "API UNAVAILABLE FROM THIS BROWSER";
  useEffect(() => () => { ++serial.current; active.current?.abort(); }, []);
  function clear() { ++serial.current; active.current?.abort(); setBusy(false); setResponse(null); setError(""); setReceived(""); }
  function selectDataset(value: number) { clear(); setDataset(value); setRowId(sortRows(ebaSnapshots[value].rows)[0].id); setInput(JSON.stringify(ebaSnapshots[value].api_request, null, 2)); setTab("Result"); }
  async function run() {
    clear();
    let payload: unknown;
    try {
      payload = JSON.parse(input);
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Enter a JSON request object.");
      const p = payload as Record<string, unknown>;
      if (typeof p.adapter !== "string" || !p.adapter.trim()) throw new Error("The request needs an adapter.");
      if ([p.row, p.rows, p.dataset].filter(v => v !== undefined && v !== null).length !== 1) throw new Error("Choose exactly one input: row, rows, or dataset.");
    } catch (e) { setError(e instanceof SyntaxError ? "The request is not valid JSON. Check the braces, commas, and quoted keys." : (e as Error).message); return; }
    if (!scorePath || !liveReady || !key.trim()) return;
    setBusy(true); const attempt = ++serial.current, controller = new AbortController(); active.current = controller;
    try {
      const data = await gatewayRequest<ScoreResponse>(api.baseUrl, scorePath, { method: "POST", key, body: payload, signal: controller.signal });
      if (attempt !== serial.current) return;
      if (!Array.isArray(data.results) || typeof data.scored_count !== "number") throw new Error("The API response does not match the scoring contract.");
      setResponse(data); setLiveIndex(0); setReceived(new Date().toLocaleTimeString()); setTab("Result");
    } catch (e) { if (attempt === serial.current) setError((e as Error).message); }
    finally { if (attempt === serial.current) setBusy(false); }
  }
  const liveRows = response ? expandedResults(response) : [];
  const liveRow = liveRows[liveIndex];
  const scores = mode === "saved" ? row.scores : liveRow?.scores as Record<string, number> | undefined;
  const buckets = mode === "saved" ? row.buckets : liveRow?.buckets as Record<string, number> | undefined;
  const result = mode === "saved" ? { source_index: row.source_index, source_id: row.source_id, pri: row.pri, pri_raw: row.pri_raw, scores: row.scores, buckets: row.buckets, explanation: row.explanation } : liveRow;
  const raw = mode === "saved" ? { adapter: source.adapter, engine_version: source.engine_version, dataset: source.dataset, cohort_rows: source.count, input: row.input, explanation_context: source.explanation_context, result } : response;
  return <div className="playground"><div className="playground-header"><span><Braces size={18}/>StatLine playground</span><span className="playground-badge"><span className={"status-dot " + (mode === "live" && api.status.kind === "offline" ? "offline" : "neutral")}/> {mode === "saved" ? "ENGINE-VERIFIED EXAMPLE" : liveStatusLabel}</span><div className="segmented" role="tablist" aria-label="Playground mode"><button role="tab" aria-selected={mode === "saved"} onClick={() => { clear(); setMode("saved"); setTab("Result"); }}>Saved example</button><button role="tab" aria-selected={mode === "live"} onClick={() => { clear(); setMode("live"); setTab("Result"); }}>Live API</button></div></div>
    <div className="playground-controls"><div><label htmlFor={compact ? "home-dataset" : "play-dataset"}>Dataset</label><select id={compact ? "home-dataset" : "play-dataset"} value={dataset} onChange={e => selectDataset(Number(e.target.value))}>{ebaSnapshots.map((s, i) => <option key={s.adapter} value={i}>{i === 0 ? "EBA Players" : "EBA Teams"} · {s.count} rows</option>)}</select></div><div><label>Adapter</label><div className="adapter-label"><Check size={14}/>{source.adapter}</div></div><div>{mode === "saved" ? <><label htmlFor={compact ? "home-record" : "play-record"}>Inspect a record</label><select id={compact ? "home-record" : "play-record"} value={row.id} onChange={e => setRowId(e.target.value)}>{rows.map(r => <option value={r.id} key={r.id}>{r.name} · PRI {r.pri}</option>)}</select></> : <><label>API connection</label><div className="adapter-label" style={{ fontSize: ".62rem", overflowWrap: "anywhere" }}>{api.baseUrl || "Not configured"}<button aria-label="Refresh API connection" className="text-link" onClick={() => { void api.refresh(); }} disabled={api.status.kind === "checking"}><RefreshCw size={13}/>Check API</button></div></>}</div></div>
    <div className="playground-main"><div className="playground-input"><p className="eyebrow">{mode === "saved" ? "01 / RAW INPUT" : "01 / YOUR REQUEST"}</p>{mode === "saved" ? <><CodeBlock label="SELECTED ROW · ORIGINAL FIELDS" text={JSON.stringify(row.input, null, 2)}/><p>This record was scored in the full {source.count}-row batch. Switching records inspects saved output; it doesn't recalculate the cohort.</p><a href={"/downloads/" + source.dataset_file} className="text-link" download><Download size={14}/>Download the input CSV</a></> : <><label className="small" htmlFor="score-request">Editable scoring request · JSON</label><textarea id="score-request" spellCheck={false} value={input} onChange={e => { clear(); setInput(e.target.value); }} aria-describedby="live-request-note"/><p id="live-request-note">Use the packaged dataset, or provide your own row or rows. This request is sent directly to the configured API.</p><div className="key-field"><label htmlFor="playground-api-key">Your approved API key · userbase scope</label><input id="playground-api-key" type="password" autoComplete="off" spellCheck={false} placeholder="api_…" value={key} onChange={e => { clear(); setKey(e.target.value); }}/></div><div className="form-actions"><button className="btn primary" disabled={!liveReady || !key.trim() || busy} onClick={() => { void run(); }}>{busy ? "Scoring…" : "Score it"}<ArrowRight size={15}/></button><button className="text-link" disabled={!key && !response && !error} onClick={() => { clear(); setKey(""); }}>Clear key & result</button></div><p>Your key stays in this page's memory. {api.status.kind !== "online" ? api.status.detail : "The Gateway enforces the actual scopes and device-proof policy."}</p>{api.status.version && api.status.version !== product.engine_version && <p className="api-warning">Gateway v{api.status.version} differs from source v{product.engine_version}. Results may differ.</p>}{error && <p className="api-warning" role="alert">{error}</p>}<a className="text-link" href="/dashboard">Need to manage access?<ArrowRight size={14}/></a></>}</div>
    <div className="playground-result" aria-live="polite">{mode === "live" && !response ? <div className="empty-panel"><Braces size={27}/><h3>Your result goes here.</h3><p>{busy ? "Waiting for the StatLine Gateway…" : "Connect your key and score a request. Explore the saved example at any time."}</p></div> : <><div className="result-heading"><div><p className="eyebrow">02 / {mode === "saved" ? "SAVED ENGINE OUTPUT" : "LIVE API RESPONSE"}</p><h3>{mode === "saved" ? row.name : liveRow ? resultLabel(liveRow) : response?.adapter}</h3><p>{mode === "saved" ? row.team : `${response?.scored_count} scored · ${response?.mapped_count} mapped · ${received}`}</p></div><div className="play-score">{Number.isFinite(mode === "saved" ? row.pri : liveRow?.pri) ? String(mode === "saved" ? row.pri : liveRow?.pri) : "—"}<small>PRI</small></div></div>{mode === "live" && response && response.results.length > 1 && <div className="key-field"><label htmlFor="live-result-row">Inspect response row</label><select id="live-result-row" value={liveIndex} onChange={e => setLiveIndex(Number(e.target.value))}>{response.results.map((r, i) => <option value={i} key={i}>{resultLabel(r)} · PRI {String(r.pri ?? "—")}</option>)}</select></div>}<div className="result-tabs" role="tablist" aria-label="Inspect scoring output">{["Result", "Mapped", "Request", "Raw JSON"].map(t => <button role="tab" aria-selected={tab === t} key={t} onClick={() => setTab(t)}>{t}</button>)}</div><div role="tabpanel" aria-label={tab}>{tab === "Result" ? <><ScoreExplanation explanation={mode === "saved" ? row.explanation : liveRow?.explanation} context={mode === "saved" ? source.explanation_context : liveRow?.explanation_context} primary={mode === "saved" ? row.primary_profile : liveRow?.primary_profile ?? "PRI"} input={mode === "saved" ? row.input : undefined}/><div className="profile-grid">{Object.entries(scores ?? {}).map(([name, value]) => <div className="profile-metric" key={name}><span>{name}</span><strong>{String(value)}</strong></div>)}</div><div className="bucket-list">{Object.entries(buckets ?? {}).slice(0, compact ? 4 : undefined).map(([name, value]) => <div key={name}><span>{source.buckets[name]?.title ?? name}</span><b>{Number(value).toFixed(4)}</b><div className="bar-track"><i style={{ width: `${Math.max(0, Math.min(1, Number(value))) * 100}%` }}/></div></div>)}</div><p className="small muted" style={{ marginTop: 14 }}>{mode === "saved" ? `${Object.keys(source.profiles).length} profiles · ${source.inputs.length} required input fields · v${source.engine_version}` : "Values returned by the API. No score is calculated in your browser."}</p></> : tab === "Mapped" ? mode === "saved" ? <><p className="small muted">The canonical row produced by the real adapter. Full-cohort mapped rows are also in the verified result download.</p><CodeBlock label="MAPPED ROW · SOURCE-VERIFIED" text={JSON.stringify(row.mapped, null, 2)}/><a href={"/downloads/" + source.result_file} download className="text-link">Download full mapped results<Download size={14}/></a></> : <CodeBlock label="MAPPED ROW FROM THE API" text={JSON.stringify(response?.mapped?.[liveIndex] ?? { note: "Request include_mapped: true to include mapped rows." }, null, 2)}/> : <CodeBlock label={tab.toUpperCase()} text={tab === "Request" ? mode === "saved" ? JSON.stringify(source.api_request, null, 2) : input : JSON.stringify(raw, null, 2)}/>}</div></>}</div></div>
    <div className="playground-note">{mode === "saved" ? `Saved source v${source.engine_version}. Verified against Python, Gateway, and StatPack output. No request is sent when you explore these results.` : `${response ? "LIVE RESPONSE RECEIVED" : "LIVE REQUEST MODE"} · ${api.baseUrl || "API not configured"} · ${scorePath ?? product.paths.score_v4} · approved credentials required`}</div>
  </div>;
}

"use client";

import { useState } from "react";
import { useApi, ApiStatus } from "./api-context";
import { CodeBlock } from "./code-block";
import type { EbaSnapshot } from "./eba-data";
import { ScoreExplanation } from "./score-explanation";
import { expandedResults, resultLabel, type ScoreResponse } from "@/lib/score-response";
import product from "@/data/product.json";
import { gatewayRequest } from "@/lib/gateway-client";

export function LiveScore({ source }: { source: EbaSnapshot }) {
  const { baseUrl, status, spec, specSource } = useApi();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<unknown>(null);
  const [responseLabel, setResponseLabel] = useState("");
  const [selectedRow, setSelectedRow] = useState(0);
  const scoreResponse = response && typeof response === "object" && "results" in response && Array.isArray(response.results) ? response as ScoreResponse : null;
  const scoredRows = scoreResponse ? expandedResults(scoreResponse) : [];
  const selected = scoredRows[selectedRow];
  const entries = Object.entries(spec.paths) as [string, Record<string, { operationId?: string }>][];
  const scorePath = entries.find(([, methods]) => methods.post?.operationId === "score_v4")?.[0];
  const adapterPath = entries.find(([, methods]) => methods.get?.operationId === "get_adapter_v4")?.[0];
  const supported = specSource === "gateway" && !!scorePath;
  const ready = status.kind === "online" && supported && !!key.trim() && !busy;
  const payload = { adapter: source.adapter, rows: source.rows.map(row => row.input), weights: "pri", input_kind: "raw", caps_mode: "batch", include_mapped: true, response_format: "compact", output: { show_explanation: true, show_components: true, show_buckets: true } };
  async function request(path: string, method: "GET" | "POST", body?: object) {
    setBusy(true); setError(""); setResponse(null);
    const controller = new AbortController();
    try {
      const data = await gatewayRequest<unknown>(baseUrl, path, { method, key, body, signal: controller.signal });
      setResponse(data); setSelectedRow(0); setResponseLabel(`${method} ${path} · ${new Date().toLocaleString()}`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The gateway could not be reached. Saved results remain available."); }
    finally { setBusy(false); }
  }
  return <div className="live-score"><ApiStatus/>
    <h4>Use the existing gateway.</h4><p>Send the complete saved <code>{source.adapter}</code> input batch ({source.count} rows) to StatLine’s own scoring endpoint. The browser does not map, normalize or calculate scores.</p>
    <p className="small muted">The API key stays in this page’s memory. It is sent only to the configured API when you choose an operation. No account, key, or device-enrollment service is implemented by the portal.</p>
    {status.kind === "online" && !supported && <p className="api-warning">Live scoring is unavailable because the gateway’s current OpenAPI does not expose the supported scoring operation or could not be read.</p>}
    {status.version && status.version !== product.engine_version && <p className="api-warning">Gateway {status.version} differs from the saved source version {product.engine_version}. Live outputs may differ.</p>}
    <label htmlFor="live-api-key">Your approved API key · userbase scope</label><input id="live-api-key" type="password" autoComplete="off" spellCheck={false} value={key} onChange={event => setKey(event.target.value)} placeholder="api_…"/>
    <div className="live-actions"><button className="btn primary" disabled={!ready} onClick={() => { if (scorePath) void request(scorePath, "POST", payload); }}>{busy ? "Waiting for gateway…" : "Score this batch with StatLine API"}</button>
      <button className="btn subtle" disabled={!ready || !adapterPath} onClick={() => { if (adapterPath) void request(adapterPath.replace("{adapter}", encodeURIComponent(source.adapter)), "GET"); }}>Read live adapter</button>
      <button className="text-link" disabled={!key} onClick={() => { setKey(""); setResponse(null); setError(""); }}>Clear key & response</button></div>
    {!key && <p className="small muted">Protected actions require your existing gateway credential. Health and OpenAPI need no key.</p>}
    {error && <p className="api-warning" role="alert">{error}</p>}
    {selected && <><label>Inspect live result<select value={selectedRow} onChange={event => setSelectedRow(Number(event.target.value))}>{scoredRows.map((row, index) => <option value={index} key={index}>{resultLabel(row)}</option>)}</select></label><ScoreExplanation explanation={selected.explanation} context={selected.explanation_context} primary={selected.primary_profile ?? "PRI"} input={selected.source_index !== undefined ? source.rows.find(row => row.source_index === selected.source_index)?.input : undefined}/></>}
    {response !== null && <CodeBlock label={"LAST GATEWAY RESPONSE · " + responseLabel} text={JSON.stringify(response, null, 2)}/>}
    <details><summary>Exact request sent to the existing API</summary><CodeBlock label="SOURCE INPUT BATCH · NO CLIENT SCORING" text={JSON.stringify(payload, null, 2)}/></details>
  </div>;
}

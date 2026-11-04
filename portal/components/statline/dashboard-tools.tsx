"use client";

import { useState } from "react";
import { ArrowRight, Database, Layers3 } from "lucide-react";
import { useApi } from "./api-context";
import { gatewayRequest } from "@/lib/gateway-client";
import { CodeBlock } from "./code-block";
import product from "@/data/product.json";

type Catalog = Record<string, unknown>;

/** Read-only catalog workflows use the very same authorized Gateway as the CLI. */
export function DashboardTools() {
  const api = useApi();
  const [adapterData, setAdapterData] = useState<Catalog | null>(null);
  const [datasetData, setDatasetData] = useState<Catalog | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);

  async function load(path: string, kind: "adapters" | "datasets") {
    if (!api.authorizedKey || busy) return;
    setBusy(true); setError("");
    try {
      const result = await gatewayRequest<Catalog>(api.baseUrl, path, { key: api.authorizedKey });
      if (kind === "adapters") setAdapterData(result);
      else setDatasetData(result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Catalog request failed."); }
    finally { setBusy(false); }
  }

  return <section className="dashboard-content" style={{ marginTop: 30 }}>
    <h2>Gateway discovery</h2>
    <p>Explore the live adapters and datasets your application is authorized to access. Results come directly from the Gateway, using the same endpoints as the CLI.</p>
    {!api.authorizedKey && <p className="small muted">Verify an approved API key above to enable catalog discovery and live scoring.</p>}
    <div className="form-actions">
      <button className="btn subtle" disabled={!api.authorizedKey || busy} onClick={() => { void load(product.paths.list_adapters_v4, "adapters"); }}><Layers3 size={15}/> Read adapters <ArrowRight size={14}/></button>
      <button className="btn subtle" disabled={!api.authorizedKey || busy} onClick={() => { void load(product.paths.list_datasets_v4, "datasets"); }}><Database size={15}/> Read datasets <ArrowRight size={14}/></button>
    </div>
    {error && <p className="api-warning" role="alert">{error}</p>}
    {adapterData && <CodeBlock label="LIVE ADAPTER CATALOG" text={JSON.stringify(adapterData, null, 2)}/>}
    {datasetData && <CodeBlock label="LIVE DATASET CATALOG" text={JSON.stringify(datasetData, null, 2)}/>}
  </section>;
}

"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import product from "@/data/product.json";
import bundledSpec from "@/data/openapi.json";

export type OpenApi = typeof bundledSpec;
export type ApiState = { kind: "idle" | "checking" | "online" | "offline" | "unconfigured"; detail: string; version?: string };
type ApiContextValue = { baseUrl: string; status: ApiState; spec: OpenApi; specSource: "gateway" | "snapshot"; schemaError: string; refresh: () => Promise<void> };
const Context = createContext<ApiContextValue | null>(null);
const configuredUrl = process.env.NEXT_PUBLIC_STATLINE_API_URL ?? "https://api.statline.dev";

declare global {
  interface Window { STATLINE_PUBLIC_CONFIG?: { apiUrl?: string } }
}

export function ApiProvider({ children }: { children: React.ReactNode }) {
  const [baseUrl, setBaseUrl] = useState(configuredUrl.trim().replace(/\/+$/, ""));
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<ApiState>({ kind: "idle", detail: "Choose Check API to contact the configured service. Saved examples and docs work without connecting." });
  const [spec, setSpec] = useState<OpenApi>(bundledSpec);
  const [specSource, setSpecSource] = useState<"gateway" | "snapshot">("snapshot");
  const [schemaError, setSchemaError] = useState("");
  const serial = useRef(0);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    const runtimeUrl = window.STATLINE_PUBLIC_CONFIG?.apiUrl;
    if (typeof runtimeUrl === "string") setBaseUrl(runtimeUrl.trim().replace(/\/+$/, ""));
    setReady(true);
  }, []);
  const refresh = useCallback(async () => {
    if (!ready) return;
    const attempt = ++serial.current;
    active.current?.abort();
    try {
      const url = new URL(baseUrl);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error("Invalid URL");
    } catch {
      setStatus({ kind: "unconfigured", detail: "Optional StatLine API is not configured. Saved results and source-generated docs remain available." });
      setSpec(bundledSpec); setSpecSource("snapshot"); setSchemaError(""); return;
    }
    setStatus({ kind: "checking", detail: "Checking the StatLine API…" });
    const controller = new AbortController(); active.current = controller;
    const timer = setTimeout(() => controller.abort(), 6000);
    const options = { signal: controller.signal, credentials: "omit" as const, cache: "no-store" as const, redirect: "error" as const };
    try {
      const results = await Promise.allSettled([
        fetch(baseUrl + product.paths.health_v4, options).then(async response => {
          if (!response.ok) throw new Error(`Health endpoint returned HTTP ${response.status}.`);
          const data = await response.json();
          if (data.ok !== true || typeof data.version !== "string") throw new Error("Health response does not match StatLine's health contract.");
          return data as { version: string };
        }),
        fetch(baseUrl + product.documentation.openapi, options).then(async response => {
          if (!response.ok) throw new Error(`OpenAPI returned HTTP ${response.status}.`);
          const data = await response.json();
          if (typeof data.openapi !== "string" || typeof data.info?.title !== "string" || typeof data.info?.version !== "string" || !data.paths || typeof data.paths !== "object" || Array.isArray(data.paths)) throw new Error("No usable OpenAPI document was returned.");
          return data as OpenApi;
        }),
      ]);
      if (attempt !== serial.current) return;
      const [health, contract] = results;
      const healthError = health.status === "rejected" ? health.reason : undefined;
      const failure = healthError instanceof Error
        ? healthError.name === "AbortError"
          ? "The connection timed out after 6 seconds."
          : healthError instanceof TypeError
            ? "The request was blocked or could not reach the service. Check the displayed API URL, Gateway availability, and STATLINE_PORTAL_ORIGINS on the API host."
            : healthError.message
        : "The health request failed.";
      setStatus(health.status === "fulfilled"
        ? { kind: "online", detail: contract.status === "fulfilled"
          ? "StatLine API is responding. Protected operations still require authorized credentials."
          : "The API is responding, but live OpenAPI could not be loaded. Live scoring is paused until the API contract is accessible.", version: health.value.version }
        : { kind: "offline", detail: "StatLine API is unavailable from this browser. " + failure + " Saved results and local docs remain available." });
      if (contract.status === "fulfilled") {
        setSpec(contract.value); setSpecSource("gateway"); setSchemaError("");
      } else {
        setSpec(bundledSpec); setSpecSource("snapshot");
        setSchemaError("Live OpenAPI could not be loaded. This reference shows the contract generated from the checked-in Python source.");
      }
    } finally { clearTimeout(timer); }
  }, [baseUrl, ready]);
  // A local server can be healthy while the portal's browser request is blocked.
  // Check from the actual browser on first load, and retry when the tab is visible.
  useEffect(() => {
    if (!ready) return;
    void refresh();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [ready, refresh]);
  useEffect(() => () => { ++serial.current; active.current?.abort(); }, []);
  const value = useMemo(() => ({ baseUrl, status, spec, specSource, schemaError, refresh }), [baseUrl, status, spec, specSource, schemaError, refresh]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useApi() {
  const value = useContext(Context);
  if (!value) throw new Error("ApiProvider is required");
  return value;
}

export function ApiStatus() {
  const { status, baseUrl, refresh } = useApi();
  return <div className="api-status" data-state={status.kind} role="status"><div>
    <strong>Optional StatLine API · {status.kind === "online" ? "Available" : status.kind === "offline" ? "Unavailable" : status.kind === "checking" ? "Checking" : status.kind === "idle" ? "Not checked" : "Not configured"}</strong>
    <p>{status.detail}</p><small>{baseUrl || "No API URL configured"}{status.version ? ` · Gateway ${status.version}` : ""}</small>
  </div><button className="btn subtle" onClick={() => { void refresh(); }} disabled={status.kind === "checking"}>{status.kind === "idle" ? "Check API" : "Check again"}</button></div>;
}

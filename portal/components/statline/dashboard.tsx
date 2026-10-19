"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, KeyRound, LogOut, Shield, UserRound } from "lucide-react";
import { ApiStatus, useApi } from "./api-context";
import { CodeBlock } from "./code-block";
import { gatewayRequest } from "@/lib/gateway-client";
import product from "@/data/product.json";

type Identity = { org: string; subject: string; device_id: string | null; api_prefix: string; scopes: string[]; auth_mode: string; device_verified: boolean };

/** The website never reads local files, asks for device/app access or enrolls devices.
 * All device-owned key administration remains in the approved local CLI client.
 */
export function Dashboard() {
  const api = useApi();
  const [key, setKey] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const ready = api.status.kind === "online";
  const supportsIdentity = !!(api.spec.paths as Record<string, Record<string, unknown>>)[product.paths.whoami_v4_auth_whoami_get]?.get || api.specSource === "snapshot";
  function clear() { active.current?.abort(); api.signOut(); setKey(""); setIdentity(null); setError(""); setBusy(false); }
  async function connect() {
    if (!ready || !key.trim() || busy || !supportsIdentity) return;
    const controller = new AbortController(); active.current?.abort(); active.current = controller;
    setBusy(true); setError(""); setIdentity(null);
    try {
      const result = await gatewayRequest<Identity>(api.baseUrl, product.paths.whoami_v4_auth_whoami_get, { key, signal: controller.signal });
      if (typeof result.org !== "string" || !Array.isArray(result.scopes)) throw new Error("The Gateway returned an unexpected identity response.");
      if (!controller.signal.aborted) { api.authorize(key); setIdentity(result); }
    } catch (error) { if (!controller.signal.aborted) setError((error as Error).message); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <div className="dashboard-layout"><div className="dashboard-content" style={{ width: "100%" }}>
    <h2>Read-only Gateway identity</h2>
    <p>The website does not request access to your computer, local network, installed apps, or device keys. You can optionally check the public API and submit an approved API key to view your identity and scopes. No device enrollment or key administration runs in the browser.</p>
    <ApiStatus/>
    <div className="dashboard-panel"><h3><KeyRound size={18} style={{ display: "inline", marginRight: 8 }}/>Optional API key</h3>
      <label htmlFor="dashboard-api-key">Approved Gateway bearer key</label>
      <input id="dashboard-api-key" type="password" autoComplete="off" spellCheck={false} value={key} disabled={busy} placeholder="api_…" onChange={event => { setKey(event.target.value); setIdentity(null); }}/>
      <div className="form-actions"><button className="btn primary" disabled={!ready || !supportsIdentity || !key.trim() || busy} onClick={() => { void connect(); }}>{busy ? "Checking…" : "Read identity"}<ArrowRight size={14}/></button>
        <button className="text-link" onClick={clear} disabled={!key && !api.authorizedKey && !identity && !error}><LogOut size={14}/>Clear session</button></div>
      <p>After identity verification, this approved key is shared in memory with the playground until you clear the session or reload the page. It is never stored in a cookie, browser storage, or on the portal server. Device-owned key requests and revocation require the local StatLine CLI.</p>
    </div>
    {api.authorizedKey && !identity && <p role="status" className="small">An approved API session is active in this tab. Open the playground to score without re-entering your key.</p>}
    {error && <div className="api-warning" role="alert">{error}</div>}
    {identity ? <><div className="identity-grid">{[["ORGANIZATION", identity.org], ["SUBJECT", identity.subject], ["API PREFIX", identity.api_prefix], ["AUTHENTICATION", identity.auth_mode], ["SCOPES", identity.scopes.join(", ") || "None"]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><CodeBlock label="GATEWAY IDENTITY · READ-ONLY" text={JSON.stringify(identity, null, 2)}/></> : <div className="empty-panel"><UserRound size={26}/><h3>Nothing is connected.</h3><p>Inspect saved examples without giving this site any permissions. Select Check API only when you want to contact the public Gateway.</p></div>}
    <div className="dashboard-panel"><h3><Shield size={17} style={{ display: "inline", marginRight: 8 }}/>Local access management</h3><p>Key administration and signed device authentication are CLI-only. Use the CLI on a machine you control. This website never opens a device-key file picker and never requests operating-system or browser device privileges.</p><a className="text-link" href="/docs/api?topic=authentication">API authentication documentation <ArrowRight size={13}/></a></div>
  </div></div>;
}

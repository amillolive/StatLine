"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, KeyRound, LogOut, RefreshCw, Shield, UserRound } from "lucide-react";
import { ApiStatus, useApi } from "./api-context";
import { CodeBlock } from "./code-block";
import { gatewayRequest } from "@/lib/gateway-client";
import { enrollBrowser, forgetDevice, readDevice, signedGatewayRequest, type BrowserDevice } from "@/lib/browser-device";
import product from "@/data/product.json";

type Identity = { org: string; subject: string; device_id: string | null; api_prefix: string; scopes: string[]; auth_mode: string; device_verified: boolean };
type RequestRecord = { request_id: string; owner: string; requested_scopes: string[]; approved_scopes: string[] | null; status: string; api_prefix: string | null };
type KeyRecord = { prefix: string; owner: string; scopes: string[]; access: boolean; expires_at: number | null };
type DeviceRecord = { status: string; org: string; user: string; scopes_json: string };
const SCOPES = [
  { id: "userbase", title: "Userbase · scoring, adapters and datasets" },
  { id: "moderation", title: "Moderation · credential operations" },
  { id: "admin", title: "Admin · protected service administration" },
];

export function Dashboard() {
  const api = useApi();
  const [key, setKey] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [device, setDevice] = useState<BrowserDevice | null>(null);
  const [deviceRecord, setDeviceRecord] = useState<DeviceRecord | null>(null);
  const [regToken, setRegToken] = useState(""), [user, setUser] = useState(""), [email, setEmail] = useState("");
  const [application, setApplication] = useState(""), [requested, setRequested] = useState<string[]>(["userbase"]), [ttlDays, setTtlDays] = useState(30);
  const [requests, setRequests] = useState<RequestRecord[]>([]), [keys, setKeys] = useState<KeyRecord[]>([]);
  const [claimed, setClaimed] = useState<{ token: string; owner: string } | null>(null);
  const [message, setMessage] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    let mounted = true;
    readDevice().then(found => { if (mounted) setDevice(found); }).catch(() => {
      if (mounted) setError("Browser identity storage is unavailable. Enable site storage to request and claim keys.");
    });
    return () => { mounted = false; active.current?.abort(); };
  }, []);

  async function action(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await task(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Gateway operation failed."); }
    finally { setBusy(false); }
  }

  function clearBearer() {
    active.current?.abort(); api.signOut(); setKey(""); setIdentity(null);
  }
  async function connect() {
    if (!key.trim()) return;
    await action(async () => {
      const controller = new AbortController(); active.current?.abort(); active.current = controller;
      const result = await gatewayRequest<Identity>(api.baseUrl, product.paths.whoami_v4_auth_whoami_get, { key, signal: controller.signal });
      if (typeof result.org !== "string" || !Array.isArray(result.scopes)) throw Error("Unexpected Gateway identity response.");
      api.authorize(key); setIdentity(result); setMessage("Existing API key verified.");
    });
  }

  async function enroll() {
    if (!regToken.trim() || !user.trim()) return;
    await action(async () => {
      const created = await enrollBrowser(api.baseUrl, regToken, user, email);
      setDevice(created); setDeviceRecord(null); setRegToken("");
      setMessage("Enrollment requested for " + created.org + ". Your administrator must approve this browser before it can request API keys.");
    });
  }

  async function refreshDevice() {
    if (!device) return;
    await action(async () => {
      const found = await signedGatewayRequest<{ device: DeviceRecord }>(api.baseUrl, device, "/v4/auth/device");
      setDeviceRecord(found.device);
      const [pending, issued] = await Promise.all([
        signedGatewayRequest<{ requests: RequestRecord[] }>(api.baseUrl, device, "/v4/auth/api-key-requests"),
        signedGatewayRequest<{ keys: KeyRecord[] }>(api.baseUrl, device, "/v4/auth/api-keys"),
      ]);
      setRequests(pending.requests); setKeys(issued.keys); setMessage("Device and application records refreshed.");
    });
  }

  async function requestKey() {
    if (!device || deviceRecord?.status !== "ACTIVE" || !application.trim() || !requested.length) return;
    await action(async () => {
      const created = await signedGatewayRequest<{ request_id: string }>(
        api.baseUrl, device, "/v4/auth/api-key-requests", "POST",
        { owner: application.trim(), scopes: requested, ttl_days: ttlDays }
      );
      setApplication(""); setMessage("Application key request " + created.request_id + " submitted for administrator approval.");
      const result = await signedGatewayRequest<{ requests: RequestRecord[] }>(api.baseUrl, device, "/v4/auth/api-key-requests");
      setRequests(result.requests);
    });
  }

  async function claimKey(record: RequestRecord) {
    if (!device) return;
    await action(async () => {
      const result = await signedGatewayRequest<{ token: string }>(
        api.baseUrl, device, "/v4/auth/api-key-requests/" + encodeURIComponent(record.request_id) + "/claim", "POST"
      );
      setClaimed({ token: result.token, owner: record.owner });
      setMessage("API key claimed. Copy it now: the Gateway will not display the secret again.");
      const [pending, issued] = await Promise.all([
        signedGatewayRequest<{ requests: RequestRecord[] }>(api.baseUrl, device, "/v4/auth/api-key-requests"),
        signedGatewayRequest<{ keys: KeyRecord[] }>(api.baseUrl, device, "/v4/auth/api-keys"),
      ]);
      setRequests(pending.requests); setKeys(issued.keys);
    });
  }

  async function revokeKey(prefix: string) {
    if (!device || !window.confirm("Revoke API key " + prefix + "? This cannot be undone.")) return;
    await action(async () => {
      await signedGatewayRequest<{ ok: boolean }>(api.baseUrl, device, "/v4/auth/api-keys/" + encodeURIComponent(prefix), "DELETE");
      setKeys(previous => previous.map(item => item.prefix === prefix ? { ...item, access: false } : item));
      setMessage("API key " + prefix + " revoked.");
    });
  }

  async function removeBrowserIdentity() {
    if (!window.confirm("Remove this browser's signing identity? Pending keys will no longer be claimable from this browser. This does not revoke server-side credentials.")) return;
    await action(async () => {
      await forgetDevice(); setDevice(null); setDeviceRecord(null); setRequests([]); setKeys([]); setClaimed(null);
      setMessage("This browser identity was removed. Existing server-side keys remain under administrator control.");
    });
  }

  const allowed = deviceRecord?.scopes_json ? JSON.parse(deviceRecord.scopes_json) as string[] : [];
  const usableScopes = SCOPES.filter(scope => allowed.includes(scope.id) || (allowed.includes("admin") && scope.id !== "admin") || (allowed.includes("moderation") && scope.id === "userbase"));

  return <div className="dashboard-layout"><div className="dashboard-content">
    <h2>StatLine application workspace</h2>
    <p>Enroll this browser with an organization registration token, request scoped API credentials for applications, track approvals, claim keys once, and revoke keys. All actions use the existing Gateway authorization policy.</p>
    <ApiStatus/>
    {error && <div className="api-warning" role="alert">{error}</div>}
    {message && <p className="small" role="status">{message}</p>}
    {claimed && <div className="dashboard-panel">
      <h3><KeyRound size={18}/> One-time API credential · {claimed.owner}</h3>
      <p className="api-warning">Copy this key now. It is only kept in this tab's memory and cannot be retrieved after dismissal.</p>
      <CodeBlock label="NEW API KEY · SHOWN ONCE" text={claimed.token}/>
      <div className="form-actions"><button className="btn primary" onClick={() => { void navigator.clipboard.writeText(claimed.token); }}>Copy key</button><button className="btn subtle" onClick={() => setClaimed(null)}>Dismiss secret</button></div>
    </div>}

    <div className="dashboard-panel">
      <h3><Shield size={18}/> Browser enrollment</h3>
      {!device ? <>
        <p>Use an administrator-issued registration token. A non-extractable Ed25519 signing key is generated and retained locally in this browser's protected key storage. Only its public key reaches the Gateway.</p>
        <label htmlFor="enroll-user">Your organization username</label><input id="enroll-user" autoComplete="username" value={user} onChange={e=>setUser(e.target.value)} placeholder="your name"/>
        <label htmlFor="enroll-email">Email (optional)</label><input id="enroll-email" type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="name@example.com"/>
        <label htmlFor="reg-token">One-time registration token</label><input id="reg-token" type="password" autoComplete="off" value={regToken} onChange={e=>setRegToken(e.target.value)} placeholder="reg_…"/>
        <div className="form-actions"><button className="btn primary" disabled={busy || !regToken.trim() || !user.trim()} onClick={() => { void enroll(); }}>Request browser enrollment <ArrowRight size={14}/></button></div>
      </> : <>
        <div className="identity-grid">{[["ORGANIZATION",device.org],["BROWSER DEVICE",device.deviceId],["ENROLLMENT REQUEST",device.enrollmentId],["STATUS",deviceRecord?.status ?? "Awaiting status check"]].map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
        <div className="form-actions">
          <button className="btn primary" disabled={busy} onClick={() => { void refreshDevice(); }}><RefreshCw size={14}/> Refresh approval & keys</button>
          <button className="btn subtle" disabled={busy} onClick={() => { void removeBrowserIdentity(); }}>Remove browser identity</button>
        </div>
        <p className="small">If the Gateway reports an inactive device, contact your organization administrator. This page cannot bypass enrollment or grant scopes.</p>
      </>}
    </div>

    {device && deviceRecord?.status === "ACTIVE" && <>
      <div className="dashboard-panel">
        <h3><KeyRound size={18}/> Request application key</h3>
        <label htmlFor="application-name">Application name</label><input id="application-name" value={application} onChange={e=>setApplication(e.target.value)} placeholder="My StatLine integration"/>
        <p className="small">Select the permissions required by this application. Requests are limited to the scopes approved for your browser identity and require separate administrator approval.</p>
        {usableScopes.map(scope => <label key={scope.id} style={{ display:"flex",alignItems:"center",gap:10 }}>
          <input type="checkbox" style={{ width:"auto" }} checked={requested.includes(scope.id)} onChange={e=>setRequested(current=>e.target.checked?[...current,scope.id]:current.filter(item=>item!==scope.id))}/>
          {scope.title}
        </label>)}
        <label htmlFor="key-lifetime">Validity (days)</label><input id="key-lifetime" type="number" min="1" max="365" value={ttlDays} onChange={e=>setTtlDays(Math.max(1,Math.min(365,Number(e.target.value)||1)))}/>
        <button className="btn primary" disabled={busy || !application.trim() || !requested.length || requested.some(scope=>!usableScopes.some(allowed=>allowed.id===scope))} onClick={() => { void requestKey(); }}>Submit key & scope request <ArrowRight size={14}/></button>
      </div>
      <div className="dashboard-panel"><h3>Requests awaiting or completing approval</h3>
        {!requests.length && <p>No API key requests for this browser yet.</p>}
        {requests.map(record => <div className="dashboard-panel" key={record.request_id}>
          <strong>{record.owner}</strong><p className="small"><code>{record.request_id}</code> · {record.status} · scopes: {record.requested_scopes.join(", ")}</p>
          {record.approved_scopes && <p className="small">Approved scopes: {record.approved_scopes.join(", ")}</p>}
          {record.status === "APPROVED" && <button className="btn primary" disabled={busy} onClick={() => { void claimKey(record); }}>Claim key (one-time display)</button>}
        </div>)}
      </div>
      <div className="dashboard-panel"><h3>Issued application keys</h3>
        {!keys.length && <p>No keys have been issued to this browser.</p>}
        {keys.map(record => <div className="dashboard-panel" key={record.prefix}>
          <strong>{record.owner}</strong><p className="small"><code>api_{record.prefix}…</code> · {record.access ? "ACTIVE" : "REVOKED"} · {record.scopes.join(", ")}</p>
          {record.access && <button className="btn subtle" disabled={busy} onClick={() => { void revokeKey(record.prefix); }}>Revoke key</button>}
        </div>)}
      </div>
    </>}

    <div className="dashboard-panel"><h3><UserRound size={18}/> Use an existing API key</h3>
      <p>Already have an approved key? Inspect its organization and scopes, and share it with the playground for this tab only.</p>
      <label htmlFor="dashboard-api-key">Approved Gateway bearer key</label>
      <input id="dashboard-api-key" type="password" autoComplete="off" spellCheck={false} value={key} disabled={busy} placeholder="api_…" onChange={event => { setKey(event.target.value); setIdentity(null); api.signOut(); }}/>
      <div className="form-actions"><button className="btn primary" disabled={!key.trim() || busy} onClick={() => { void connect(); }}>{busy ? "Checking…" : "Read identity"}<ArrowRight size={14}/></button>
        <button className="text-link" onClick={clearBearer} disabled={!key && !identity}><LogOut size={14}/>Clear bearer session</button></div>
      <p className="small">Bearer keys remain in page memory, never cookies or browser storage. Browser signing keys are different and stored as non-extractable CryptoKeys to survive approval delays.</p>
    </div>
    {identity && <><div className="identity-grid">{[["ORGANIZATION", identity.org], ["SUBJECT", identity.subject], ["API PREFIX", identity.api_prefix], ["AUTHENTICATION", identity.auth_mode], ["SCOPES", identity.scopes.join(", ") || "None"]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><CodeBlock label="VERIFIED GATEWAY IDENTITY" text={JSON.stringify(identity, null, 2)}/></>}
  </div></div>;
}

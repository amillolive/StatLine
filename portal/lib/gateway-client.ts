/** Client-side requests to the existing Gateway. No credential persistence. */
export type DeviceCredential = { id: string; key: CryptoKey };
const encoder = new TextEncoder();
const base64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export async function importDeviceKey(pem: string): Promise<CryptoKey> {
  if (!globalThis.crypto?.subtle) throw new Error("Device signing needs HTTPS or localhost and a browser with Web Crypto support.");
  const match = pem.trim().match(/^-----BEGIN PRIVATE KEY-----\s+([\s\S]+?)\s+-----END PRIVATE KEY-----$/);
  if (!match) throw new Error("Choose your existing DEVICEKEY file: an unencrypted Ed25519 PKCS#8 PEM private key.");
  try {
    const bytes = Uint8Array.from(atob(match[1].replace(/\s/g, "")), c => c.charCodeAt(0));
    return await crypto.subtle.importKey("pkcs8", bytes, "Ed25519", false, ["sign"]);
  } catch { throw new Error("This file could not be imported as an Ed25519 device key. Your browser must support Ed25519 in Web Crypto."); }
}

export async function deviceProof(device: DeviceCredential, method: string, target: string, body: string): Promise<Record<string, string>> {
  // Matches StatLine's sign_envelope: METHOD\nTARGET\nTIMESTAMP\nNONCE\nBODY_SHA256.
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = base64url(crypto.getRandomValues(new Uint8Array(18)));
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(body));
  const bodyHash = [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, "0")).join("");
  const signature = await crypto.subtle.sign("Ed25519", device.key, encoder.encode([method.toUpperCase(), target, timestamp, nonce, bodyHash].join("\n")));
  return { "X-SL-Device": device.id, "X-SL-Timestamp": timestamp, "X-SL-Nonce": nonce, "X-SL-Signature": base64url(new Uint8Array(signature)) };
}

export async function gatewayRequest<T>(baseUrl: string, path: string, options: { method?: string; key?: string; device?: DeviceCredential | null; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const base = new URL(baseUrl);
  if (!["https:", "http:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) throw new Error("The configured API URL is invalid.");
  if (base.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)) throw new Error("Use an HTTPS API URL for credentialed requests. HTTP is supported on localhost.");
  const method = options.method ?? "GET";
  const body = options.body === undefined ? "" : JSON.stringify(options.body);
  const url = new URL(baseUrl.replace(/\/+$/, "") + path);
  const headers: Record<string, string> = {};
  if (body) headers["Content-Type"] = "application/json";
  if (options.key?.trim()) headers.Authorization = "Bearer " + options.key.trim();
  if (options.device) Object.assign(headers, await deviceProof(options.device, method, url.pathname + url.search, body));
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (options.signal?.aborted) cancel();
  options.signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 20000);
  try {
    const result = await fetch(url, { method, headers, body: body || undefined, signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error" });
    const text = await result.text();
    let data: unknown;
    try { data = JSON.parse(text); } catch { throw new Error(`Gateway returned HTTP ${result.status} without a JSON response.`); }
    if (!result.ok) {
      const detail = data && typeof data === "object" && "detail" in data ? JSON.stringify(data.detail) : "Request rejected";
      throw new Error(`Gateway HTTP ${result.status}: ${detail}`);
    }
    return data as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw new Error(options.signal?.aborted ? "Request cancelled." : "The Gateway request timed out. Try again when the service is reachable.");
    if (error instanceof TypeError) throw new Error("The API could not be reached from this browser. Check the service URL and its allowed portal origins.");
    throw error;
  } finally { clearTimeout(timer); options.signal?.removeEventListener("abort", cancel); }
}

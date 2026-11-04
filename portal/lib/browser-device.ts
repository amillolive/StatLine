/** Non-extractable, origin-bound browser identity for the existing Gateway device API.
 * The private key is a CryptoKey in IndexedDB; it is never exported or sent to StatLine.
 * API tokens are deliberately NOT stored here.
 */
import { allowedGatewayBase } from "./api-policy";

export type BrowserDevice = { deviceId: string; privateKey: CryptoKey; org: string; enrollmentId: string };
const DATABASE = "statline-dashboard-identity";
const STORE = "identity";
const ID = "current";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? Error("Browser identity storage is unavailable."));
  });
}

export async function rememberDevice(device: BrowserDevice): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE, "readwrite");
      transaction.objectStore(STORE).put(device, ID);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error ?? Error("Cannot store browser identity."));
    });
  } finally { db.close(); }
}

export async function readDevice(): Promise<BrowserDevice | null> {
  const db = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE, "readonly");
      const request = transaction.objectStore(STORE).get(ID);
      request.onsuccess = () => resolve((request.result as BrowserDevice | undefined) ?? null);
      request.onerror = () => reject(request.error ?? Error("Cannot read browser identity."));
    });
  } finally { db.close(); }
}

export async function forgetDevice(): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(STORE, "readwrite");
      transaction.objectStore(STORE).delete(ID);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error ?? Error("Cannot remove browser identity."));
    });
  } finally { db.close(); }
}

function base64url(bytes: Uint8Array): string {
  let raw = "";
  for (const byte of bytes) raw += String.fromCharCode(byte);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function gateway(baseUrl: string): string {
  const allowed = allowedGatewayBase(baseUrl, window.location.hostname);
  if (!allowed) throw Error("This browser is not configured to connect to an approved StatLine Gateway.");
  return allowed;
}

async function result<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({ detail: "Invalid Gateway response." })) as Record<string, unknown>;
  if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : `Gateway returned HTTP ${response.status}.`);
  return data as T;
}

export async function enrollBrowser(baseUrl: string, token: string, user: string, email: string): Promise<BrowserDevice> {
  if (!crypto?.subtle || !indexedDB) throw Error("This browser needs WebCrypto and IndexedDB support.");
  const keypair = await crypto.subtle.generateKey("Ed25519", false, ["sign", "verify"]) as CryptoKeyPair;
  const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", keypair.publicKey));
  const response = await fetch(gateway(baseUrl) + "/v4/auth/enroll", {
    method: "POST", credentials: "omit", cache: "no-store", redirect: "error",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reg_token: token.trim(), user: user.trim(), email: email.trim() || null,
      device_pub_b64: base64url(publicKey), meta: { client: "statline-web-dashboard" },
    }),
  });
  const enrolled = await result<{ device_id: string; request_id: string; org: string }>(response);
  const device = { deviceId: enrolled.device_id, enrollmentId: enrolled.request_id, org: enrolled.org, privateKey: keypair.privateKey };
  await rememberDevice(device);
  return device;
}

/** Sign the identical method/path/body envelope checked by gateway.auth.service.require_device. */
export async function signedGatewayRequest<T>(
  baseUrl: string, device: BrowserDevice, path: string,
  method: "GET" | "POST" | "DELETE" = "GET", payload?: object
): Promise<T> {
  const url = new URL(gateway(baseUrl) + path);
  const body = payload === undefined ? "" : JSON.stringify(payload);
  const digest = hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body))));
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomUUID();
  const envelope = [method, url.pathname + url.search, timestamp, nonce, digest].join("\n");
  const signature = new Uint8Array(await crypto.subtle.sign("Ed25519", device.privateKey, new TextEncoder().encode(envelope)));
  const response = await fetch(url.toString(), {
    method, credentials: "omit", cache: "no-store", redirect: "error",
    headers: {
      "X-SL-Device": device.deviceId,
      "X-SL-Timestamp": timestamp, "X-SL-Nonce": nonce,
      "X-SL-Signature": base64url(signature),
      ...(payload === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(payload === undefined ? {} : { body }),
  });
  return result<T>(response);
}

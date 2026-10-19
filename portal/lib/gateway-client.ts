/** Browser-only, explicitly initiated HTTPS Gateway requests. Never inspect a device. */
import { allowedGatewayBase } from "./api-policy";

export async function gatewayRequest<T>(baseUrl: string, path: string, options: { method?: string; key?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const origin = allowedGatewayBase(baseUrl, window.location.hostname);
  if (!origin) throw new Error("The portal can only contact api.statline.dev over HTTPS. A local API is allowed only from a loopback-hosted development portal.");
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("Invalid API path.");
  const method = options.method ?? "GET";
  const body = options.body === undefined ? "" : JSON.stringify(options.body);
  const url = new URL(origin + path);
  const headers: Record<string, string> = {};
  if (body) headers["Content-Type"] = "application/json";
  if (options.key?.trim()) headers.Authorization = "Bearer " + options.key.trim();
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (options.signal?.aborted) cancel();
  options.signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 20000);
  try {
    const response = await fetch(url, { method, headers, body: body || undefined, signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error" });
    const text = await response.text();
    let data: unknown;
    try { data = JSON.parse(text); } catch { throw new Error(`Gateway returned HTTP ${response.status} without a JSON response.`); }
    if (!response.ok) {
      const detail = data && typeof data === "object" && "detail" in data ? JSON.stringify(data.detail) : "Request rejected";
      throw new Error(`Gateway HTTP ${response.status}: ${detail}`);
    }
    return data as T;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw new Error(options.signal?.aborted ? "Request cancelled." : "The Gateway request timed out. Try again when the service is reachable.");
    if (error instanceof TypeError) throw new Error("The API could not be reached from this browser. Check Gateway availability and its allowed portal origins.");
    throw error;
  } finally { clearTimeout(timer); options.signal?.removeEventListener("abort", cancel); }
}

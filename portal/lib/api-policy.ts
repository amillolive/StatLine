/** A public visitor may communicate ONLY with StatLine's public HTTPS gateway.
 * This prevents browsers from offering Local Network Access / device permissions.
 * Developers running the portal on loopback can still explicitly select a local API.
 */
export function allowedGatewayBase(raw: string, portalHostname: string): string | null {
  try {
    const url = new URL(raw);
    if (url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) return null;
    if (url.origin === "https://api.statline.dev") return url.origin;
    const localPortal = ["localhost", "127.0.0.1", "[::1]"].includes(portalHostname.toLowerCase());
    const localApi = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname.toLowerCase());
    if (localPortal && localApi && url.protocol === "http:") return url.origin;
  } catch { /* invalid URL */ }
  return null;
}

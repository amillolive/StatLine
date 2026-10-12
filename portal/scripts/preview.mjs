// Local static-file preview. No scoring, proxy, authentication, or API routes.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { resolve, join, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../dist/client/", import.meta.url)));
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".csv": "text/csv; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8" };
await stat(join(root, "index.html")).catch(() => { throw new Error("Run pnpm build before starting the static preview."); });
const server = createServer(async (request, response) => {
  response.setHeader("Cache-Control", "no-store");
  if (!["GET", "HEAD"].includes(request.method)) { response.writeHead(405); response.end(); return; }
  try {
    const path = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const target = resolve(root, "." + path);
    if (target !== root && !target.startsWith(root + sep)) { response.writeHead(403); response.end(); return; }
    const info = await stat(target).catch(async () => {
      const file = target + ".html";
      const bytes = await readFile(file);
      response.writeHead(200, { "Content-Type": types[".html"] });
      response.end(request.method === "HEAD" ? undefined : bytes);
      return null;
    });
    if (!info) return;
    const file = info.isDirectory() ? join(target, "index.html") : target;
    const bytes = await readFile(file);
    response.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream" });
    response.end(request.method === "HEAD" ? undefined : bytes);
  } catch { response.writeHead(404); response.end("Not found"); }
});
const port = Number(process.env.STATLINE_PORTAL_PORT || 3000);
server.listen(port, "127.0.0.1", () => console.log(`StatLine portal static preview: http://localhost:${port}`));

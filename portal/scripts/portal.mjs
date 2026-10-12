import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";
const portalRoot = fileURLToPath(new URL("..", import.meta.url));
const [command, ...args] = process.argv.slice(2);
if (!["generate", "dev", "build", "check-generated"].includes(command)) throw new Error("Use generate, dev, build, or check-generated.");
const python = process.env.STATLINE_PYTHON || (process.platform === "win32" ? "python" : "python3");
const generated = spawnSync(python, [fileURLToPath(new URL(command === "check-generated" ? "./check-generated.py" : "./generate.py", import.meta.url)), ...(["generate", "check-generated"].includes(command) ? args : [])], { stdio: "inherit", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
if (generated.error) { console.error("Portal generation needs Python 3.11+ and the source checkout's Gateway dependencies. Set STATLINE_PYTHON if needed."); process.exit(1); }
if (generated.status !== 0 || ["generate", "check-generated"].includes(command)) process.exit(generated.status ?? 1);
const cli = fileURLToPath(new URL("../node_modules/vinext/dist/cli.js", import.meta.url));
const built = spawnSync(process.execPath, [cli, command, ...(command === "dev" ? ["--hostname", "127.0.0.1", "--port", "3000"] : []), ...args], { stdio: "inherit", cwd: portalRoot });
if (built.error) throw built.error;
if (built.status === 0 && command === "build") {
  const root = join(portalRoot, "dist/client");
  // Directory indexes preserve nested routes on Caddy/nginx and simple servers.
  const routes = readdirSync(root, { recursive: true }).filter(file => typeof file === "string" && file.endsWith(".html") && !file.endsWith("index.html"));
  for (const route of routes) {
    const directory = join(root, route.slice(0, -5));
    mkdirSync(directory, { recursive: true });
    copyFileSync(join(root, route), join(directory, "index.html"));
  }
  // A partially prerendered site must never appear to be a successful build.
  const app = fileURLToPath(new URL("../app", import.meta.url));
  const pages = readdirSync(app, { recursive: true }).filter(file => typeof file === "string" && file.endsWith("page.tsx"));
  for (const page of pages) {
    const path = page === "page.tsx" ? "index.html" : page.replace(/page\.tsx$/, "index.html");
    if (!existsSync(join(root, path))) throw new Error("Missing static page: " + path);
  }
}
process.exit(built.status ?? 1);

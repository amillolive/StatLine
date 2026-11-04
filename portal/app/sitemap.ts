import type { MetadataRoute } from "next";

const published = [
  "/", "/architecture", "/changelog", "/docs", "/docs/api", "/docs/local",
  "/examples", "/get-started", "/open-source", "/playground", "/product/adapters",
  "/product/api", "/product/devkit", "/product/local", "/product/scorer",
  "/product/statpacks", "/solutions/competition", "/solutions/data-teams",
  "/solutions/developers", "/solutions/organizations", "/status"
] as const;

export default function sitemap(): MetadataRoute.Sitemap {
  return published.map(path => ({
    url: "https://statline.dev" + path,
    lastModified: new Date(),
    changeFrequency: path === "/" ? "weekly" : "monthly",
    priority: path === "/" ? 1 : path.startsWith("/docs") ? 0.8 : 0.6,
  }));
}

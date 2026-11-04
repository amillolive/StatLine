"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, RefreshCw } from "lucide-react";

const API = "https://api.github.com/repos/amillolive/StatLine";
const REPOSITORY = "https://github.com/amillolive/StatLine";
const PAGE_SIZE = 100;

type Release = {
  tag_name: string; name: string | null; body: string | null; html_url: string;
  published_at: string | null; prerelease: boolean; draft: boolean;
};
type Tag = { name: string; commit: { sha: string } };
type Commit = { sha: string; html_url: string; commit: { message: string; author: { date: string | null } | null } };
type Entry = { tag: string; previous: string | null; release: Release | null };

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(API + path, {
    cache: "no-store", headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error(response.status === 403 || response.status === 429
    ? "GitHub's public API rate limit was reached. Try again shortly or open the repository history."
    : `GitHub returned HTTP ${response.status}.`);
  return response.json() as Promise<T>;
}

async function pages<T>(path: string): Promise<T[]> {
  const all: T[] = [];
  for (let page = 1; ; page++) {
    const separator = path.includes("?") ? "&" : "?";
    const batch = await getJSON<T[]>(`${path}${separator}per_page=${PAGE_SIZE}&page=${page}`);
    all.push(...batch);
    if (batch.length < PAGE_SIZE) break;
  }
  return all;
}

function prettyDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "Unpublished date";
}

export function ReleaseHistory() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [logs, setLogs] = useState<Record<string, Commit[]>>({});
  const [logStatus, setLogStatus] = useState<Record<string, string>>({});

  async function refresh() {
    setLoading(true); setError("");
    try {
      const [releases, tags] = await Promise.all([
        pages<Release>("/releases"), pages<Tag>("/tags"),
      ]);
      const published = new Map(releases.filter(release => !release.draft).map(release => [release.tag_name, release]));
      const releaseTags = tags.filter(tag => /^v?\d+\.\d+(?:\.\d+)?(?:[\w.+-]*)?$/i.test(tag.name));
      const known = new Set(releaseTags.map(tag => tag.name));
      // A published release can reference a tag unavailable from a shallow repository snapshot.
      for (const release of releases) if (!release.draft && !known.has(release.tag_name)) {
        releaseTags.push({ name: release.tag_name, commit: { sha: "" } });
      }
      setEntries(releaseTags.map((tag, index) => ({
        tag: tag.name, previous: releaseTags[index + 1]?.name ?? null,
        release: published.get(tag.name) ?? null,
      })));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Release history could not be loaded.");
    } finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);

  async function loadCommits(entry: Entry) {
    if (logs[entry.tag] || logStatus[entry.tag] === "loading") return;
    setLogStatus(state => ({ ...state, [entry.tag]: "loading" }));
    try {
      let commits: Commit[];
      if (entry.previous) {
        commits = [];
        for (let page = 1; ; page++) {
          const result = await getJSON<{ commits: Commit[]; total_commits: number }>(
            `/compare/${encodeURIComponent(entry.previous)}...${encodeURIComponent(entry.tag)}?per_page=100&page=${page}`
          );
          commits.push(...result.commits);
          if (result.commits.length < PAGE_SIZE || commits.length >= result.total_commits) break;
        }
      } else {
        // The first tagged release has no preceding release; show its entire prior history.
        commits = await pages<Commit>(`/commits?sha=${encodeURIComponent(entry.tag)}`);
      }
      setLogs(state => ({ ...state, [entry.tag]: commits }));
      setLogStatus(state => ({ ...state, [entry.tag]: "loaded" }));
    } catch (cause) {
      setLogStatus(state => ({ ...state, [entry.tag]: cause instanceof Error ? cause.message : "Failed to load commits." }));
    }
  }

  return <div className="release-history" aria-live="polite">
    <div className="form-actions">
      <button className="btn subtle" disabled={loading} onClick={() => { setLogs({}); setLogStatus({}); void refresh(); }}>
        <RefreshCw size={14}/> {loading ? "Loading repository history…" : "Refresh from GitHub"}
      </button>
      <a className="text-link" href={REPOSITORY + "/releases"} target="_blank" rel="noreferrer">GitHub releases <ArrowUpRight size={14}/></a>
    </div>
    {error && <p className="api-warning" role="alert">{error}</p>}
    {loading && <p className="muted">Reading all published releases and version tags, including the earliest available release…</p>}
    {!loading && !error && entries.length === 0 && <p>No tagged releases were found in the repository.</p>}
    <p className="small muted">{entries.length ? `${entries.length} version tags · newest first · commit history loaded directly from GitHub on request` : ""}</p>
    {entries.map(entry => {
      const commits = logs[entry.tag];
      const status = logStatus[entry.tag];
      return <section className="release-card" key={entry.tag} style={{ marginBottom: 18 }}>
        <span className="pill">{entry.release?.prerelease ? "PRERELEASE" : entry.release ? "RELEASE" : "VERSION TAG"}</span>
        <h2 style={{ marginTop: 14 }}>{entry.release?.name || entry.tag}</h2>
        <p className="small muted">{prettyDate(entry.release?.published_at)} · <code>{entry.tag}</code></p>
        {entry.release?.body && <details><summary>Published release notes</summary>
          <p style={{ whiteSpace: "pre-wrap" }}>{entry.release.body}</p>
        </details>}
        <p className="small">Commit log: {entry.previous ? `${entry.previous} → ${entry.tag}` : `repository inception → ${entry.tag}`}</p>
        <div className="form-actions">
          <button className="btn subtle" disabled={status === "loading"} onClick={() => { void loadCommits(entry); }}>
            {status === "loading" ? "Loading commits…" : commits ? `${commits.length} commits loaded` : "Load commit log"}
          </button>
          <a className="text-link" href={entry.release?.html_url || REPOSITORY + "/releases/tag/" + encodeURIComponent(entry.tag)} target="_blank" rel="noreferrer">View source <ArrowUpRight size={13}/></a>
        </div>
        {status && status !== "loading" && status !== "loaded" && <p className="api-warning" role="alert">{status}</p>}
        {commits && <ul className="release-commits">{commits.map(commit => <li key={commit.sha}>
          <a href={commit.html_url} target="_blank" rel="noreferrer"><code>{commit.sha.slice(0, 8)}</code> {commit.commit.message.split("\n")[0]}</a>
          <small> · {prettyDate(commit.commit.author?.date)}</small>
        </li>)}</ul>}
      </section>;
    })}
  </div>;
}

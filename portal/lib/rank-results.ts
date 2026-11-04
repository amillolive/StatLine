/** Stable, source-index-preserving best-to-worst PRI display ordering. */
import type { ScoredResult } from "./score-response";

export function rankedResults<T extends ScoredResult>(rows: readonly T[]): { row: T; index: number }[] {
  const numeric = (value: unknown): number => typeof value === "number" && Number.isFinite(value) ? value : -Infinity;
  return rows.map((row, index) => ({ row, index })).sort((a, b) =>
    numeric(b.row.pri) - numeric(a.row.pri) ||
    (numeric(b.row.pri_raw) - numeric(a.row.pri_raw)) ||
    a.index - b.index
  );
}

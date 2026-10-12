/** Rehydrate either v4-RC envelope without deriving identity from array position. */
import type { Explanation, ExplanationContext } from "@/components/statline/score-explanation";

export type ScoredResult = Record<string, unknown> & {
  source_index?: number; source_id?: string | number; primary_profile?: string;
  pri?: number; scores?: Record<string, number>; buckets?: Record<string, number>;
  explanation?: Explanation; explanation_context?: ExplanationContext;
};
export type ScoreResponse = {
  adapter: string; adapter_version: string; input_count: number; mapped_count: number; scored_count: number;
  response_format?: "legacy" | "compact"; shared?: Record<string, unknown>;
  results: ScoredResult[]; mapped?: Record<string, unknown>[];
};
export const expandedResults = (response: ScoreResponse) => response.results.map(row => ({ ...response.shared, ...row } as ScoredResult));
export const resultLabel = (row: ScoredResult) => `${row.source_id ?? "Result without a source ID"}${row.source_index !== undefined ? ` · input ${row.source_index}` : " · source position unavailable"}`;

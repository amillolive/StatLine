import type { Explanation, ExplanationContext } from "./score-explanation";
import snapshot from "@/data/eba-snapshot.json";
import product from "@/data/product.json";
export type EbaRow = {
  source_index: number; source_id?: string | number; primary_profile: string; explanation?: Explanation;
  id: string; name: string; team: string; gp: number; conference: string; position: string;
  input: Record<string, string | number>; mapped: Record<string, unknown>; pri: number; pri_raw: number;
  scores: Record<string, number>; buckets: Record<string, number>;
};
export type EbaSnapshot = {
  engine_version: string; adapter: string; adapter_version: string; title: string; dataset: string;
  dataset_sha256: string; schema_sha256: string; inputs: string[]; aliases: string[];
  profiles: Record<string, {kind: string; lo?: number; hi?: number; out_lo?: number; out_hi?: number; weights_profile: string}>;
  buckets: Record<string, {title: string; description: string}>;
  weights: Record<string, Record<string, number>>; normalized_weights: Record<string, number>;
  explanation_context?: ExplanationContext;
  count: number; rows: EbaRow[]; csv_file: string; stat_fields: string[]; api_request: Record<string, unknown>; result_file: string; dataset_file: string; pack_file: string;
  pack_contents: {files: string[]; sections: string[]; datasets: string[]};
};
export const ebaSnapshots = snapshot.snapshots as unknown as EbaSnapshot[];
export const sortRows = (rows: EbaRow[]) => [...rows].sort((a,b) => b.pri-a.pri || b.pri_raw-a.pri_raw || Number(a.id)-Number(b.id));
export const spotlight = sortRows(ebaSnapshots[0].rows)[0];
export const pythonExample = product.python_example;
export const httpExample = product.http_example;

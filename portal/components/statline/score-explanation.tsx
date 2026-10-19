"use client";

import { useState } from "react";

export type ExplanationContext = {
  metrics: Record<string, { label: string; bucket_label: string; invert: boolean; source?: {kind: string; field?: string; expr?: string}; transform?: unknown; clamp?: number[] }>;
  context_used: Record<string, { leader: number; floor: number }>;
  profile_weights: Record<string, Record<string, number>>;
  profiles: Record<string, { kind: string; weights_profile: string }>;
  normalization_source: string; rounding: string; bucket_aggregation: string; model_notice: string;
  baseline: { description: string };
};
export type Explanation = {
  metrics: Record<string, { mapped_value: number; normalized_value: number }>;
  profiles: Record<string, {
    contributions: Record<string, number>; relative_influence: Record<string, number>;
    weighted_sum: number; baseline_weighted_sum: number; raw_aggregate: number;
    aggregate_clipped: boolean; unscoreable_override: boolean; percentile: number | null;
    final_unrounded: number; final_score: number; helped: string[]; limited: string[]; final_attribution: string;
  }>;
};
const number = (value: number) => value.toLocaleString("en-US", { maximumFractionDigits: 6 });

export function ScoreExplanation({ explanation, context, primary, input, selectedProfile, onProfileChange }: {
  explanation?: Explanation; context?: ExplanationContext; primary: string; input?: Record<string, unknown>;
  selectedProfile?: string; onProfileChange?: (profile: string) => void;
}) {
  const [selected, setSelected] = useState(primary);
  if (!explanation || !context) return <p className="small muted">Precise attribution is unavailable in this response. Request <code>output.show_explanation</code> on a compatible gateway.</p>;
  const requested = selectedProfile ?? selected;
  const profile = explanation.profiles[requested] ? requested : primary;
  const result = explanation.profiles[profile];
  if (!result) return <p className="small muted">Attribution for this profile is unavailable.</p>;
  const labels = (keys: string[]) => keys.map(key => context.metrics[key]?.label ?? key).join(", ");
  const keys = Object.keys(result.contributions).sort((a, b) => Math.abs(result.relative_influence[b]) - Math.abs(result.relative_influence[a]));
  return <section className="score-explanation" aria-label="Why this score">
    <h4>Why {result.final_score}?</h4>
    {Object.keys(explanation.profiles).length > 1 && !onProfileChange && <label>Score profile <select value={profile} onChange={event => setSelected(event.target.value)}>{Object.keys(explanation.profiles).map(name => <option key={name}>{name}</option>)}</select></label>}
    {result.unscoreable_override ? <p>The engine marked this row unscoreable and set its raw aggregate to zero. Metric influences below describe the inputs before that override.</p> : <>
      <p><strong>Helped:</strong> {result.helped.length ? labels(result.helped) : "No material influence above the reference."}</p>
      <p><strong>Limited:</strong> {result.limited.length ? labels(result.limited) : "No material influence below the reference."}</p>
    </>}
    <p className="small muted">Compared with every normalized metric at its midpoint (0.5). These are influences on the raw sum. {result.final_attribution}</p>
    {result.aggregate_clipped && <p className="filtered-note">The weighted sum reached an aggregate limit before final scaling.</p>}
    <details><summary>Trace inputs, weights &amp; calculation</summary>
      <p className="small">Normalization: <code>{context.normalization_source}</code>. Effective weights include the run’s penalties and overrides. Bucket bars are means, not additive score points.</p>
      <div className="attribution-scroll"><table className="attribution-table"><thead><tr><th>Metric</th><th>Mapped</th><th>Normalized</th><th>Weight</th><th>Contribution</th><th>Relative influence</th></tr></thead><tbody>{keys.map(key => <tr key={key}><th title={key}>{context.metrics[key]?.label ?? key}</th><td>{number(explanation.metrics[key]?.mapped_value ?? 0)}</td><td>{number(explanation.metrics[key]?.normalized_value ?? 0)}</td><td>{number(context.profile_weights[profile]?.[key] ?? 0)}</td><td>{number(result.contributions[key])}</td><td>{number(result.relative_influence[key])}</td></tr>)}</tbody></table></div>
      <p>Weighted sum {number(result.weighted_sum)} → raw aggregate {number(result.raw_aggregate)}{result.percentile !== null ? ` → cohort percentile ${number(result.percentile * 100)}%` : ""} → {number(result.final_unrounded)} → {result.final_score}.</p>
      <p className="small muted">{context.rounding}. {context.model_notice}</p>
      <details><summary>Raw inputs &amp; adapter transforms</summary><pre className="attribution-json">{JSON.stringify({ input, metrics: context.metrics, normalization: context.context_used, profile: context.profiles[profile], baseline: context.baseline }, null, 2)}</pre></details>
    </details>
  </section>;
}

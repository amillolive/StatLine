"use client";
import { useState } from "react";
import { ChevronDown, Braces, Layers3, Workflow } from "lucide-react";
import { ScoreExplanation } from "./score-explanation";
import { ebaSnapshots, sortRows } from "./eba-data";

export function EngineDemo() {
  const source = ebaSnapshots[0];
  const [selection, setSelection] = useState(0);
  const [explain, setExplain] = useState(false);
  const [profile, setProfile] = useState("PRI");
  const choices = sortRows(source.rows).slice(0, 3);
  const row = choices[selection];
  const availableProfiles = Object.keys(row.scores);
  const activeProfile = availableProfiles.includes(profile) ? profile : (availableProfiles[0] ?? "PRI");
  const rating = row.scores[activeProfile] ?? row.pri;
  const buckets = Object.entries(row.buckets)
    .filter(([key]) => (source.normalized_weights[key] ?? 0) > 0)
    .sort(([a], [b]) => source.normalized_weights[b] - source.normalized_weights[a])
    .slice(0, 3);
  return <div className="engine-demo">
    <div className="engine-top"><span><span className="status-dot"/>THE SCORER, AT WORK</span><span className="mono">v{source.engine_version}</span></div>
    <div className="engine-pipeline"><div><Braces size={17}/><span>Raw data</span></div><span className="pipeline-line"/><div><Layers3 size={17}/><span>Adapter</span></div><span className="pipeline-line"/><div className="pipeline-active"><Workflow size={17}/><span>Scorer</span></div><span className="pipeline-line"/><div><span className="result-glyph">↗</span><span>Result</span></div></div>
    <div className="engine-input"><div className="engine-input-label"><span>01 / INPUT</span><select aria-label="Choose a sample player" value={selection} onChange={event => setSelection(Number(event.target.value))}>{choices.map((item, i) => <option value={i} key={item.id}>{item.name}</option>)}</select></div><div className="raw-lines"><span><i>PLAYER</i><b>"{row.name}"</b></span>{["PPG", "APG", "RPG"].map(key => <span key={key}><i>{key}</i><b>{row.input[key]}</b></span>)}</div></div>
    <div className="adapter-node"><div className="node-icon"><Layers3 size={19}/></div><div><span className="mono">{source.adapter}</span><small>{source.inputs.length} required fields · {Object.keys(source.profiles).length} profiles</small></div><span className="node-check">✓</span></div>
    <div className="engine-input-label"><label htmlFor="engine-score-profile">Score profile</label><select id="engine-score-profile" aria-label="Choose scoring profile" value={activeProfile} onChange={event => setProfile(event.target.value)}>{availableProfiles.map(name => <option key={name} value={name}>{name}</option>)}</select></div>
    <div className="engine-result"><div><span className="eyebrow">02 / EXPLAINABLE OUTPUT</span><h3>{row.name}</h3><p>{row.team}</p></div><div className="result-number"><strong>{rating}</strong><span>{activeProfile} SCORE</span></div></div>
    <div className="engine-bars">{buckets.map(([key, value]) => <div key={key}><span>{source.buckets[key]?.title ?? key}<b>{value.toFixed(3)}</b></span><div className="bar-track"><i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}/></div></div>)}</div>
    <button className="engine-explain" aria-expanded={explain} onClick={() => setExplain(!explain)}>Why {rating}?<ChevronDown size={16} className={explain ? "rotate" : ""}/></button>
    {explain && <div className="engine-explanation"><ScoreExplanation key={row.id} explanation={row.explanation} context={source.explanation_context} primary={row.primary_profile} selectedProfile={activeProfile} onProfileChange={setProfile} input={row.input}/><p className="small muted">Saved outputs from the full {source.count}-row batch.</p></div>}
    <div className="engine-foot"><span className="status-dot neutral"/>Source-verified example · no browser scoring</div>
  </div>;
}

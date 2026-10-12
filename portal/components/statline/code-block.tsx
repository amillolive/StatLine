"use client";
import { useState } from "react";
import { CheckCheck, Clipboard } from "lucide-react";

export function CodeBlock({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState("Copy");
  async function copy() {
    try { await navigator.clipboard.writeText(text); setState("Copied"); setTimeout(() => setState("Copy"), 2000); }
    catch { setState("Select code below"); }
  }
  return <div className="code-block"><div className="code-top"><span>{label}</span><button onClick={copy} aria-label={`Copy ${label.toLowerCase()}`}>
    {state === "Copied" ? <CheckCheck size={16}/> : <Clipboard size={16}/>}<span aria-live="polite">{state}</span>
  </button></div><pre><code>{text}</code></pre></div>;
}

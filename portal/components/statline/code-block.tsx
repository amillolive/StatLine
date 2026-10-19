"use client";
import { useRef, useState } from "react";
import { TextSelect } from "lucide-react";

export function CodeBlock({ text, label }: { text: string; label: string }) {
  const target = useRef<HTMLElement | null>(null);
  const [selected, setSelected] = useState(false);
  function selectText() {
    const selection = window.getSelection();
    if (!selection || !target.current) return;
    selection.removeAllRanges();
    const range = document.createRange();
    range.selectNodeContents(target.current);
    selection.addRange(range);
    setSelected(true);
  }
  return <div className="code-block"><div className="code-top"><span>{label}</span><button onClick={selectText} aria-label={`Select ${label.toLowerCase()} for manual copy`}>
    <TextSelect size={16}/><span aria-live="polite">{selected ? "Selected · Ctrl+C" : "Select code"}</span>
  </button></div><pre><code ref={target}>{text}</code></pre></div>;
}

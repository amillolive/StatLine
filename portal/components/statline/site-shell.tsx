"use client";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ArrowRight, ArrowUpRight, ChevronDown, GitBranch as Github, Menu, X } from "lucide-react";
import product from "@/data/product.json";

const groups = [
  { title: "Product", items: [["Scoring engine", "/product/scorer", "The heart of StatLine"], ["Adapters", "/product/adapters", "Make your schema understood"], ["StatPacks", "/product/statpacks", "Take your rules with you"], ["Local & StatLine OS", "/product/local", "Run on your own machine"], ["DevKit", "/product/devkit", "Build it into your application"], ["Hosted API", "/product/api", "Scoring over HTTP"]] },
  { title: "Solutions", items: [["Developers", "/solutions/developers", "Bots, services, and applications"], ["Data teams", "/solutions/data-teams", "Repeatable data workflows"], ["Organizations", "/solutions/organizations", "A scoring model of your own"], ["Competition & esports", "/solutions/competition", "Where StatLine started"]] },
  { title: "Developers", items: [["Documentation", "/docs", "Local and API guides"], ["Playground", "/playground", "Explore real scoring output"], ["API reference", "/docs/api?topic=reference", "The complete OpenAPI contract"], ["Python package", "https://pypi.org/project/statline/", "Install from PyPI"], ["GitHub", product.project_urls.Repository, "Read and contribute to the source"]] },
  { title: "Resources", items: [["Examples", "/examples", "Start with a real dataset"], ["Changelog", "/changelog", "What's in this source version"], ["API status", "/status", "Check service connectivity"], ["Architecture", "/architecture", "Core → App → Gateway"], ["Open source", "/open-source", "Built in public"]] },
];
export function Brand() { return <span className="brand"><span className="brand-mark" aria-hidden="true"><i/><i/><i/><i/></span>statline<span className="brand-dot">.</span></span>; }
export function SiteHeader() {
  const [open, setOpen] = useState<string | null>(null), [mobile, setMobile] = useState(false);
  const path = usePathname();
  useEffect(() => { setOpen(null); setMobile(false); }, [path]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(null); setMobile(false); } };
    const outside = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest(".site-header")) setOpen(null); };
    document.addEventListener("keydown", close); document.addEventListener("click", outside);
    return () => { document.removeEventListener("keydown", close); document.removeEventListener("click", outside); };
  }, []);
  return <><header className="site-header"><div className="nav-wrap"><a href="/" aria-label="StatLine home"><Brand/></a><button className="menu-toggle" onClick={() => setMobile(!mobile)} aria-expanded={mobile} aria-controls="global-navigation" aria-label={mobile ? "Close navigation" : "Open navigation"}>{mobile ? <X size={23}/> : <Menu size={23}/>}</button><nav id="global-navigation" className={"nav-links" + (mobile ? " mobile-open" : "")} aria-label="Main navigation">{groups.map(group => <div className="nav-group" key={group.title}><button className="nav-trigger" aria-expanded={open === group.title} aria-controls={"nav-" + group.title} onClick={() => setOpen(open === group.title ? null : group.title)}>{group.title}<ChevronDown size={13}/></button>{open === group.title && <div className="nav-dropdown" id={"nav-" + group.title}>{group.items.map(([label, href, detail]) => <a key={href} href={href} onClick={() => { setOpen(null); setMobile(false); }} {...(href.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}><span>{label}{href.startsWith("http") && <ArrowUpRight size={14}/>}</span><small>{detail}</small></a>)}</div>}</div>)}<a className="dashboard-link" href="/dashboard">Dashboard</a><a className="btn primary nav-cta" href="/playground">Try StatLine<ArrowRight size={15}/></a></nav></div></header><div className="release-strip">Viewing v{product.engine_version} · release candidate <span>·</span> <a href="https://github.com/amillolive/StatLine/tree/main" target="_blank" rel="noreferrer">v3 stable / main</a> <span>·</span> <a href="https://github.com/amillolive/StatLine/tree/next" target="_blank" rel="noreferrer">v4 preview / next</a></div></>;
}
export function SiteFooter() {
  return <footer className="site-footer container"><div className="footer-grid"><div className="footer-intro"><a href="/"><Brand/></a><p>From raw data.<br/>To something that makes sense.</p><a className="source-tag" href={product.project_urls.Repository} target="_blank" rel="noreferrer"><Github size={15}/>Open source. Yours to inspect.</a></div>{groups.filter(g => g.title !== "Solutions").map(group => <div key={group.title}><h3>{group.title}</h3>{group.items.map(([label, href]) => <a href={href} key={href} {...(href.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}>{label}</a>)}</div>)}</div><div className="footer-bottom"><span>© {new Date().getFullYear()} StatLine · Preview v{product.engine_version}</span><span><a href={product.project_urls.Legal} target="_blank" rel="noreferrer">License & trademarks<ArrowUpRight size={12}/></a><a href="/status"><span className="status-dot neutral"/>API status</a></span></div></footer>;
}

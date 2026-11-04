"""StatLine-branded live OpenAPI reference page (no remote JS assets)."""

from __future__ import annotations

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="index,follow">
<title>StatLine · Live API reference</title>
<link rel="icon" type="image/png" href="https://statline.dev/favicon.png">
<style>
:root{color-scheme:dark;--background:#080e0d;--foreground:#eff5f2;--card:#101a17;--primary:#7fe4ae;--border:#26392f;--muted:#9aafa4;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--background);color:var(--foreground);font:15px/1.7 Inter,"Segoe UI",Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}a:hover{color:var(--primary)}button,input{font:inherit}button{cursor:pointer}
:focus-visible{outline:2px solid var(--primary);outline-offset:4px}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;background:#080e0dee;backdrop-filter:blur(20px);border-bottom:1px solid #ffffff14;padding:19px clamp(20px,5vw,90px)}
.brand{font-size:1.75rem;font-weight:700;letter-spacing:-.065em;line-height:1}.brand b{color:var(--primary)}
nav{display:flex;flex-wrap:wrap;gap:20px;font-size:.82rem;color:#bed1c3}
.container{width:min(1240px,calc(100% - 48px));margin:auto}
.hero{padding:70px 0 45px;background:radial-gradient(circle at 80% 10%,#7fe4ae12,transparent 55%)}
.eyebrow{color:#a6c2b4;font:600 .68rem var(--mono);letter-spacing:.14em}
h1{font-size:clamp(2.8rem,5vw,4.4rem);line-height:1.08;letter-spacing:-.055em;margin:18px 0}
h1 span{color:var(--primary)}p{color:#b9cbbf;margin:0;max-width:830px}
.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:27px}.button{display:inline-block;background:var(--primary);color:#072015;border:0;border-radius:6px;padding:10px 17px;font-weight:650;font-size:.85rem}.button:hover{color:#072015;background:#9cefc2}
.button.secondary{color:#d2e1d9;background:transparent;border:1px solid #344b3e}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:29px;padding:28px 0 90px}
.sidebar{align-self:start;position:sticky;top:107px;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:19px}
label{display:block;font-size:.75rem;color:#aac1b0;margin:0 0 9px}
input{width:100%;background:#0a130f;border:1px solid #344b3e;color:white;border-radius:6px;padding:10px 12px}
#tags{display:grid;gap:5px;margin-top:15px}.tag{border:0;background:transparent;text-align:left;color:#bbd0c1;border-radius:6px;padding:9px}.tag:hover,.tag.active{background:#1a3024;color:var(--primary)}
.summary{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-bottom:17px;font-size:.75rem;color:var(--muted)}
.endpoint{border:1px solid var(--border);background:var(--card);border-radius:9px;margin-bottom:13px;overflow:hidden}
.endpoint summary{display:flex;align-items:center;gap:13px;padding:19px;cursor:pointer;flex-wrap:wrap;list-style:none}.endpoint summary::-webkit-details-marker{display:none}
.endpoint summary .path{font:600 .86rem var(--mono);overflow-wrap:anywhere;color:#ebf5ed}.endpoint summary .description{color:#a3b8a9;font-size:.75rem;flex:1}
.method{font:700 .67rem var(--mono);padding:5px 9px;border:1px solid #3c674f;border-radius:4px;color:#7fe4ae;min-width:55px;text-align:center}
.method.post{color:#9abefa;border-color:#3e5372}.method.delete{color:#ffa49b;border-color:#714440}.method.patch,.method.put{color:#f3c98c;border-color:#715e42}
.detail{padding:0 22px 22px;border-top:1px solid var(--border)}.detail h3{font-size:.9rem;margin:22px 0 9px}
pre{overflow:auto;white-space:pre-wrap;word-break:break-word;background:#080f0c;border:1px solid #203629;padding:15px;border-radius:7px;font:.75rem/1.6 var(--mono);color:#c5e4d1}
code{font-family:var(--mono);color:#b4d9c6}.hint{font-size:.75rem;color:#92aa9b}.warning{padding:14px;border:1px solid #b37b4f;color:#ffd3a5;border-radius:7px}
footer{padding:32px 0;border-top:1px solid var(--border);font-size:.76rem;color:var(--muted)}
@media(max-width:780px){.layout{grid-template-columns:1fr}.sidebar{position:static}.hero{padding-top:45px}nav{gap:12px}}
</style></head><body>
<header><a class="brand" href="https://statline.dev" aria-label="StatLine home">statline<b>.</b></a>
<nav aria-label="API navigation"><a href="https://statline.dev/docs/api">Documentation</a><a href="/rtfm" aria-current="page">Live reference</a><a href="/docs">Swagger</a><a href="/redoc">ReDoc</a><a href="/openapi.json">OpenAPI JSON</a><a href="https://statline.dev/dashboard">Dashboard</a></nav></header>
<main><div class="hero"><div class="container"><div class="eyebrow">STATLINE / GATEWAY / LIVE CONTRACT</div><h1>One API.<br><span>Every operation.</span></h1>
<p>Search the Gateway's current OpenAPI contract, inspect request and response schemas, and identify the authorization each operation needs. This page reads the running Gateway, not a prewritten endpoint list.</p>
<div class="actions"><a class="button" href="/docs">Interactive Swagger authorization</a><a class="button secondary" href="https://statline.dev/dashboard">Manage application access</a></div></div></div>
<div class="container layout"><aside class="sidebar"><label for="search">Search operations</label><input id="search" type="search" placeholder="Method, path, summary…">
<div id="tags" aria-label="Endpoint groups"></div></aside><section><div class="summary"><span id="status">Loading the live OpenAPI document…</span><span id="version"></span></div><div id="operations" aria-live="polite"></div></section></div></main>
<footer><div class="container">StatLine, Inc. · API reference generated dynamically from <a href="/openapi.json">/openapi.json</a>. Private operations remain protected by Gateway policies.</div></footer>
<script>
(function(){
"use strict";
var search=document.getElementById("search"),tags=document.getElementById("tags"),list=document.getElementById("operations"),status=document.getElementById("status"),version=document.getElementById("version");
var operations=[],filter="all";
function node(tag,cls,txt){var el=document.createElement(tag);if(cls)el.className=cls;if(txt!==undefined)el.textContent=String(txt);return el}
function showPre(parent,value){var pre=node("pre","",JSON.stringify(value,null,2));parent.appendChild(pre)}
function render(){
  var q=search.value.trim().toLowerCase();list.replaceChildren();
  var visible=operations.filter(function(row){return(filter==="all"||row.group===filter)&&(row.method+" "+row.path+" "+row.summary+" "+row.group).toLowerCase().includes(q)});
  status.textContent=visible.length+" of "+operations.length+" live operations";
  visible.forEach(function(row){
    var card=node("details","endpoint"),head=node("summary"),method=node("span","method "+row.method,row.method.toUpperCase());
    head.append(method,node("span","path",row.path),node("span","description",row.summary));
    var detail=node("div","detail");
    detail.appendChild(node("h3","",row.summary));
    if(row.op.description)detail.appendChild(node("p","",row.op.description));
    detail.appendChild(node("h3","","Authentication / scopes"));showPre(detail,row.op.security||window.__apiSecurity||[]);
    if(row.op.parameters&&row.op.parameters.length){detail.appendChild(node("h3","","Parameters"));showPre(detail,row.op.parameters)}
    if(row.op.requestBody){detail.appendChild(node("h3","","Request body"));showPre(detail,row.op.requestBody)}
    detail.appendChild(node("h3","","Responses"));showPre(detail,row.op.responses||{});
    var url=node("a","button secondary","View exact OpenAPI contract");url.href="/openapi.json";detail.appendChild(url);
    card.append(head,detail);list.appendChild(card);
  });
  if(!visible.length)list.appendChild(node("p","hint","No matching operations. Clear the search or select another group."));
}
function setFilter(value){
  filter=value;Array.from(tags.children).forEach(function(button){button.classList.toggle("active",button.dataset.tag===filter)});render();
}
fetch("/openapi.json",{cache:"no-store",credentials:"omit"}).then(function(response){
  if(!response.ok)throw Error("HTTP "+response.status);return response.json()
}).then(function(spec){
  if(!spec.paths||!spec.info)throw Error("Malformed OpenAPI document");
  window.__apiSecurity=spec.security||[];version.textContent="Gateway "+spec.info.version+" · OpenAPI "+spec.openapi;
  Object.keys(spec.paths).forEach(function(path){var methods=spec.paths[path];Object.keys(methods).forEach(function(method){
    if(!["get","post","put","patch","delete","options","head"].includes(method))return;
    var op=methods[method];operations.push({path:path,method:method,op:op,group:(op.tags&&op.tags[0])||"other",summary:op.summary||op.operationId||path});
  })});
  var groups=["all"].concat(Array.from(new Set(operations.map(function(op){return op.group}))).sort());
  groups.forEach(function(group){var button=node("button","tag"+(group==="all"?" active":""),group==="all"?"All endpoints":group);button.dataset.tag=group;button.type="button";button.addEventListener("click",function(){setFilter(group)});tags.appendChild(button)});
  search.addEventListener("input",render);render();
}).catch(function(error){status.textContent="Live contract unavailable";list.appendChild(node("p","warning",String(error)+". Check /openapi.json or try Swagger."))});
})();
</script></body></html>"""


def render_reference() -> str:
    """Render source-version-neutral documentation; the browser reads live OpenAPI."""
    return HTML

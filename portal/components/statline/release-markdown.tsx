"use client";

import { Fragment, useMemo, useState, type ReactNode } from "react";
import { lexer, type Token, type Tokens } from "marked";

const SOURCE_BASE = "https://github.com/amillolive/StatLine/blob/";

function safeLink(href: string, tag: string, image = false): string | null {
  try {
    const target = new URL(href, SOURCE_BASE + encodeURIComponent(tag) + "/");
    if (target.protocol === "https:" || (!image && (target.protocol === "http:" || target.protocol === "mailto:"))) {
      return target.href;
    }
  } catch {
    // Treat malformed or unsafe links as plain text.
  }
  return null;
}

function inline(tokens: Token[], tag: string): ReactNode {
  return tokens.map((token, index) => {
    const key = token.type + "-" + index;
    switch (token.type) {
      case "text": {
        const item = token as Tokens.Text;
        return <Fragment key={key}>{item.tokens?.length ? inline(item.tokens, tag) : item.text}</Fragment>;
      }
      case "escape":
        return <Fragment key={key}>{(token as Tokens.Escape).text}</Fragment>;
      case "strong":
        return <strong key={key}>{inline((token as Tokens.Strong).tokens, tag)}</strong>;
      case "em":
        return <em key={key}>{inline((token as Tokens.Em).tokens, tag)}</em>;
      case "del":
        return <del key={key}>{inline((token as Tokens.Del).tokens, tag)}</del>;
      case "codespan":
        return <code key={key}>{(token as Tokens.Codespan).text}</code>;
      case "br":
        return <br key={key}/>;
      case "link": {
        const link = token as Tokens.Link;
        const href = safeLink(link.href, tag);
        return href
          ? <a key={key} href={href} title={link.title || undefined} target="_blank" rel="noopener noreferrer">{inline(link.tokens, tag)}</a>
          : <Fragment key={key}>{inline(link.tokens, tag)}</Fragment>;
      }
      case "image": {
        const item = token as Tokens.Image;
        const src = safeLink(item.href, tag, true);
        return src
          ? <img key={key} src={src} alt={item.text} title={item.title || undefined} loading="lazy"/>
          : <Fragment key={key}>{item.text}</Fragment>;
      }
      case "html":
        return <Fragment key={key}>{(token as Tokens.HTML).text}</Fragment>;
      default:
        return <Fragment key={key}>{token.raw}</Fragment>;
    }
  });
}

function heading(token: Tokens.Heading, tag: string, key: string): ReactNode {
  const content = inline(token.tokens, tag);
  if (token.depth === 1) return <h3 key={key}>{content}</h3>;
  if (token.depth === 2) return <h4 key={key}>{content}</h4>;
  if (token.depth === 3) return <h5 key={key}>{content}</h5>;
  return <h6 key={key}>{content}</h6>;
}

function blocks(tokens: Token[], tag: string): ReactNode {
  return tokens.map((token, index) => {
    const key = token.type + "-" + index;
    switch (token.type) {
      case "heading":
        return heading(token as Tokens.Heading, tag, key);
      case "paragraph":
        return <p key={key}>{inline((token as Tokens.Paragraph).tokens, tag)}</p>;
      case "text": {
        const item = token as Tokens.Text;
        return <Fragment key={key}>{item.tokens?.length ? inline(item.tokens, tag) : item.text}</Fragment>;
      }
      case "code": {
        const item = token as Tokens.Code;
        const language = item.lang?.split(/\s+/)[0];
        return <pre key={key}><code className={language ? "language-" + language : undefined}>{item.text}</code></pre>;
      }
      case "blockquote":
        return <blockquote key={key}>{blocks((token as Tokens.Blockquote).tokens, tag)}</blockquote>;
      case "hr":
        return <hr key={key}/>;
      case "list": {
        const list = token as Tokens.List;
        const items = list.items.map((item, itemIndex) =>
          <li key={itemIndex}>
            {item.task && <input type="checkbox" checked={Boolean(item.checked)} readOnly disabled aria-label="Completed task"/>}
            {blocks(item.tokens, tag)}
          </li>
        );
        return list.ordered
          ? <ol key={key} start={typeof list.start === "number" ? list.start : undefined}>{items}</ol>
          : <ul key={key}>{items}</ul>;
      }
      case "table": {
        const table = token as Tokens.Table;
        return <div key={key} className="release-markdown-table"><table>
          <thead><tr>{table.header.map((cell, cellIndex) =>
            <th key={cellIndex} style={{ textAlign: cell.align || undefined }}>{inline(cell.tokens, tag)}</th>)}</tr></thead>
          <tbody>{table.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) =>
            <td key={cellIndex} style={{ textAlign: cell.align || undefined }}>{inline(cell.tokens, tag)}</td>)}</tr>)}</tbody>
        </table></div>;
      }
      case "space":
        return null;
      case "html":
        return <p key={key}>{(token as Tokens.HTML).text}</p>;
      default:
        return <p key={key}>{inline([token], tag)}</p>;
    }
  });
}

/** Render GitHub release Markdown as safe React elements, never raw HTML. */
export function ReleaseMarkdown({ source, tag }: { source: string; tag: string }) {
  const tokens = useMemo(() => lexer(source, { gfm: true, breaks: true }), [source]);
  return <div className="release-markdown">{blocks(tokens, tag)}</div>;
}

/** Parse notes only when expanded, even for repositories with hundreds of tags. */
export function ReleaseNotes({ source, tag }: { source: string; tag: string }) {
  const [expanded, setExpanded] = useState(false);
  return <details className="release-notes" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>Published release notes <span className="release-notes-format">Markdown</span></summary>
    {expanded && <ReleaseMarkdown source={source} tag={tag}/>}
  </details>;
}

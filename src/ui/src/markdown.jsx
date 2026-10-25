import React, { useState } from "react";

// Minimal, dependency-free markdown: fenced code blocks (with copy),
// inline code, **bold**, *italic*, - / 1. lists, links, headings,
// simple pipe tables, and line breaks.
// React escapes text children, so no manual HTML escaping is needed.

// m-39: only http(s): and mailto: links become clickable, preserving
// the renderer's XSS safety (javascript:, data:, and other schemes
// render as literal text, never as a link).
const SAFE_LINK_SCHEME = /^(https?:|mailto:)/i;

function parseLink(segment) {
  const m = segment.match(
    /^\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/
  );
  if (!m) return null;
  if (!SAFE_LINK_SCHEME.test(m[2])) return null;
  return { text: m[1], href: m[2] };
}

function inlineFormatNoLink(segment, key) {
  const codeParts = segment.split(/(`[^`\n]+`)/g);
  return codeParts.map((part, i) => {
    if (/^`[^`\n]+`$/.test(part)) {
      return (
        <code key={`${key}c${i}`} className="inline-code">
          {part.slice(1, -1)}
        </code>
      );
    }
    const boldParts = part.split(/(\*\*[^*\n]+\*\*)/g);
    return boldParts.map((bp, j) => {
      if (/^\*\*[^*\n]+\*\*$/.test(bp)) {
        return (
          <strong key={`${key}b${i}-${j}`}>{bp.slice(2, -2)}</strong>
        );
      }
      const itParts = bp.split(/(^|\W)(\*[^*\n]+\*)(?=\W|$)/g);
      return itParts.map((ip, k) => {
        const m = ip.match(/^\*([^*\n]+)\*$/);
        return m ? (
          <em key={`${key}i${i}-${j}-${k}`}>{m[1]}</em>
        ) : (
          <React.Fragment key={`${key}t${i}-${j}-${k}`}>{ip}</React.Fragment>
        );
      });
    });
  });
}

function inlineFormat(segment, key) {
  // m-39: links are split out before code/bold/italic handling so a
  // URL inside a link target is never re-parsed as inline markup.
  const linkParts = segment.split(/(\[[^\]]+\]\([^)\s]+(?:\s+"[^"]*")?\))/g);
  return linkParts.map((part, i) => {
    const link = parseLink(part);
    if (link) {
      return (
        <a
          key={`${key}l${i}`}
          href={link.href}
          target="_blank"
          rel="noopener noreferrer"
        >
          {inlineFormatNoLink(link.text, `${key}lt${i}`)}
        </a>
      );
    }
    return (
      <React.Fragment key={`${key}x${i}`}>
        {inlineFormatNoLink(part, `${key}n${i}`)}
      </React.Fragment>
    );
  });
}

function CodeBlock({ language, code }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable
    }
  };
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{language || "code"}</span>
        <button
          className="copy-btn"
          onClick={copy}
          aria-label={language ? `Copy ${language} code` : "Copy code"}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

function splitRow(line) {
  let cells = line.trim();
  if (cells.startsWith("|")) cells = cells.slice(1);
  if (cells.endsWith("|")) cells = cells.slice(0, -1);
  return cells.split("|").map((c) => c.trim());
}

function isTableSeparator(line) {
  const cells = splitRow(line);
  return (
    cells.length > 0 &&
    cells.every((c) => /^:?-+:?$/.test(c))
  );
}

function TextBlock({ text, baseKey }) {
  const lines = text.split("\n");
  const elements = [];
  let list = null; // {ordered, items: []}

  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    elements.push(
      <Tag key={`${baseKey}l${elements.length}`} className="md-list">
        {list.items.map((it, i) => (
          <li key={i}>{inlineFormat(it, `${baseKey}li${elements.length}-${i}`)}</li>
        ))}
      </Tag>
    );
    list = null;
  };

  let para = [];
  const flushPara = () => {
    if (!para.length) return;
    const nodes = [];
    para.forEach((line, i) => {
      if (i > 0) nodes.push(<br key={`${baseKey}pbr${elements.length}-${i}`} />);
      nodes.push(
        <React.Fragment key={`${baseKey}p${elements.length}-${i}`}>
          {inlineFormat(line, `${baseKey}px${elements.length}-${i}`)}
        </React.Fragment>
      );
    });
    elements.push(
      <p key={`${baseKey}p${elements.length}`} className="md-para">
        {nodes}
      </p>
    );
    para = [];
  };

  for (let li = 0; li < lines.length; li += 1) {
    const line = lines[li];
    // m-39: headings (# .. ######) — previously rendered literally.
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      flushList();
      const Tag = `h${heading[1].length}`;
      elements.push(
        <Tag
          key={`${baseKey}h${elements.length}`}
          className={`md-heading md-h${heading[1].length}`}
        >
          {inlineFormat(heading[2], `${baseKey}hd${elements.length}`)}
        </Tag>
      );
      continue;
    }
    // m-39: simple pipe tables — a header row followed by a separator
    // row (--- / :---:), then body rows. Anything else renders as text.
    if (
      line.trim().startsWith("|") &&
      li + 1 < lines.length &&
      isTableSeparator(lines[li + 1])
    ) {
      flushPara();
      flushList();
      const header = splitRow(line);
      const rows = [];
      li += 2; // skip header + separator
      while (li < lines.length && lines[li].trim().startsWith("|")) {
        rows.push(splitRow(lines[li]));
        li += 1;
      }
      li -= 1; // the for-loop's increment lands on the first non-row
      elements.push(
        <table key={`${baseKey}tb${elements.length}`} className="md-table">
          <thead>
            <tr>
              {header.map((cell, i) => (
                <th key={i}>
                  {inlineFormat(cell, `${baseKey}th${elements.length}-${i}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {header.map((_, i) => (
                  <td key={i}>
                    {inlineFormat(
                      row[i] ?? "",
                      `${baseKey}td${elements.length}-${r}-${i}`
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
      continue;
    }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const ordered = !!ol;
      const item = (ul || ol)[1];
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(item);
    } else if (line.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return <>{elements}</>;
}

export function renderMarkdown(text) {
  const out = [];
  // m-38: the language token is any run of characters up to the first
  // newline (not just \w), so ```c++, ```objective-c and
  // ```shell-session match; the token is trimmed for display.
  const fenceRe = /```([^\n`]*)\n([\s\S]*?)```/g;
  let last = 0;
  let m;
  let n = 0;
  while ((m = fenceRe.exec(text)) !== null) {
    if (m.index > last) {
      out.push(<TextBlock key={`t${n}`} text={text.slice(last, m.index)} baseKey={`t${n}`} />);
      n += 1;
    }
    out.push(
      <CodeBlock
        key={`c${n}`}
        language={m[1].trim()}
        code={m[2].replace(/\n$/, "")}
      />
    );
    n += 1;
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    const rest = text.slice(last);
    // m-38 (streaming flicker): an unclosed trailing fence is the normal
    // mid-stream state — render it as a code block immediately instead
    // of plain text that flips to a code block when the closing fence
    // arrives. Text before the fence still renders as prose.
    const open = rest.match(/```([^\n`]*)\n([\s\S]*)$/);
    if (open && rest.indexOf("```") === open.index) {
      const before = rest.slice(0, open.index);
      if (before) {
        out.push(<TextBlock key={`t${n}`} text={before} baseKey={`t${n}`} />);
        n += 1;
      }
      out.push(
        <CodeBlock
          key={`c${n}`}
          language={open[1].trim()}
          code={open[2].replace(/\n$/, "")}
        />
      );
    } else if (open) {
      // A fence-like run that is not at the start of the remainder
      // (e.g. stray backticks mid-paragraph) stays plain text.
      out.push(<TextBlock key={`t${n}`} text={rest} baseKey={`t${n}`} />);
    } else {
      out.push(<TextBlock key={`t${n}`} text={rest} baseKey={`t${n}`} />);
    }
  }
  return out;
}

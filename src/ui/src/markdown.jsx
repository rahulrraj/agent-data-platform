import React, { useState } from "react";

// Minimal, dependency-free markdown: fenced code blocks (with copy),
// inline code, **bold**, *italic*, - / 1. lists, and line breaks.
// React escapes text children, so no manual HTML escaping is needed.

function inlineFormat(segment, key) {
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
        <button className="copy-btn" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
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

  for (const line of lines) {
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
  const fenceRe = /```(\w*)\n([\s\S]*?)```/g;
  let last = 0;
  let m;
  let n = 0;
  while ((m = fenceRe.exec(text)) !== null) {
    if (m.index > last) {
      out.push(<TextBlock key={`t${n}`} text={text.slice(last, m.index)} baseKey={`t${n}`} />);
      n += 1;
    }
    out.push(<CodeBlock key={`c${n}`} language={m[1]} code={m[2].replace(/\n$/, "")} />);
    n += 1;
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    out.push(<TextBlock key={`t${n}`} text={text.slice(last)} baseKey={`t${n}`} />);
  }
  return out;
}

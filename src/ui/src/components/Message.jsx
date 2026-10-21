import React from "react";
import { renderMarkdown } from "../markdown.jsx";
import { Sparkle } from "./icons.jsx";

function ToolPill({ tools, live }) {
  if (!tools || tools.length === 0) return null;
  return (
    <div className={`tool-pill ${live ? "live" : ""}`}>
      <span className="tool-dot" />
      {live ? `Using ${tools[tools.length - 1]}…` : `Used ${tools.join(", ")}`}
    </div>
  );
}

export function Message({ message }) {
  const { role, content, tools, error, streaming } = message;
  if (role === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{content}</div>
      </div>
    );
  }
  return (
    <div className={`msg assistant ${error ? "error" : ""}`}>
      <div className="avatar">
        <Sparkle size={20} />
      </div>
      <div className="msg-body">
        <ToolPill tools={tools} live={streaming && tools?.length > 0} />
        {error ? (
          <div className="error-note">{content}</div>
        ) : (
          <div className="md">
            {renderMarkdown(content || "")}
            {streaming && <span className="cursor" />}
          </div>
        )}
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  "What is 6 * 7? Use the calculate tool.",
  "What time is it?",
  "What data can you look up for me?",
  "Write a haiku about good software design.",
];

export function Welcome({ onSuggest }) {
  return (
    <div className="welcome">
      <div className="welcome-logo">
        <Sparkle size={44} />
      </div>
      <h1>What can I help with?</h1>
      <div className="chips">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chip" onClick={() => onSuggest(s)}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

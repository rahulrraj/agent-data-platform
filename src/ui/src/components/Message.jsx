import React from "react";
import { renderMarkdown } from "../markdown.jsx";
import { Sparkle } from "./icons.jsx";

// m-39: a very long reply is capped for rendering (bounding the
// per-token re-parse/re-render cost); the cap is far above any normal
// reply, and a note tells the user the display was truncated.
export const MAX_RENDER_CHARS = 100000;

function ToolPill({ tools, status }) {
  if (!tools || tools.length === 0) return null;
  // m-42: the verb now reflects the turn's outcome, not just whether
  // a stream is live. "Used" is only claimed for a turn that completed
  // (status "done", or a stored message with no error/abort marker);
  // a failed turn says "Attempted" and an aborted one "Interrupted",
  // so a tool that never completed is never presented as used.
  const live = status === "live";
  const label =
    status === "error"
      ? `Attempted ${tools.join(", ")}`
      : status === "aborted"
        ? `Interrupted ${tools.join(", ")}`
        : live
          ? `Using ${tools[tools.length - 1]}…`
          : `Used ${tools.join(", ")}`;
  return (
    <div className={`tool-pill ${live ? "live" : ""}`}>
      <span className="tool-dot" />
      {label}
    </div>
  );
}

export function Message({ message, onRetry }) {
  const {
    role,
    content,
    tools,
    error,
    errorMessage,
    partial,
    streaming,
    aborted,
  } = message;
  if (role === "user") {
    return (
      <div className="msg user">
        <div className="bubble">{content}</div>
      </div>
    );
  }
  const status = streaming
    ? "live"
    : errorMessage
      ? "error"
      : aborted
        ? "aborted"
        : "done";
  const text = content || "";
  const truncated = text.length > MAX_RENDER_CHARS;
  const display = truncated ? text.slice(0, MAX_RENDER_CHARS) : text;
  // m-45: a finished turn with no text at all (e.g. a model reply of
  // only tool calls, or a truncated stream with no text events) gets
  // an explicit note instead of a silent empty bubble.
  const empty = !streaming && !errorMessage && !text.trim();
  // M-24/M-25: a failed turn keeps its partial markdown and tool pills;
  // the error is a separate banner below it, with a retry affordance.
  // (Legacy `error` shape, without errorMessage, keeps its old rendering.)
  return (
    <div
      className={`msg assistant ${error || errorMessage ? "error" : ""}`}
    >
      <div className="avatar">
        <Sparkle size={20} />
      </div>
      <div className="msg-body">
        <ToolPill tools={tools} status={status} />
        {error && !errorMessage ? (
          <div className="error-note" role="alert">{content}</div>
        ) : (
          <>
            <div className="md">
              {empty ? (
                <div className="empty-note">No reply received.</div>
              ) : (
                renderMarkdown(display)
              )}
              {streaming && <span className="cursor" />}
            </div>
            {truncated && !empty && (
              <div className="empty-note">
                Output truncated at {MAX_RENDER_CHARS.toLocaleString()}{" "}
                characters.
              </div>
            )}
            {errorMessage && (
              <div className="error-note" role="alert">
                {partial
                  ? "Reply incomplete — showing what arrived before the error. "
                  : ""}
                {errorMessage}
                {onRetry && (
                  <button className="retry-btn" onClick={onRetry}>
                    Retry
                  </button>
                )}
              </div>
            )}
          </>
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

export function Welcome({ onSuggest, disabled }) {
  return (
    <div className="welcome">
      <div className="welcome-logo">
        <Sparkle size={44} />
      </div>
      <h1>What can I help with?</h1>
      <div className="chips">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            className="chip"
            onClick={() => onSuggest(s)}
            disabled={disabled}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

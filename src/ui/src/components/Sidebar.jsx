import React, { useEffect, useRef } from "react";
import { PlusIcon } from "./icons.jsx";

export default function Sidebar({
  open,
  sessions,
  activeId,
  onNew,
  onSelect,
  onDelete,
  onClose,
  apiOk,
}) {
  const asideRef = useRef(null);
  // m-44: focus management — when the sidebar opens (mobile overlay),
  // move focus into it so keyboard users land on the first control.
  useEffect(() => {
    if (open) {
      asideRef.current?.querySelector(".new-chat-btn")?.focus();
    }
  }, [open ]);

  return (
    <>
      <div
        className={`sidebar-scrim ${open ? "show" : ""}`}
        onClick={onClose}
        aria-hidden
      />
      <aside className={`sidebar ${open ? "open" : ""}`} ref={asideRef}>
        <div className="sidebar-top">
          <button className="new-chat-btn" onClick={onNew}>
            <PlusIcon />
            <span>New chat</span>
          </button>
        </div>
        <nav className="session-list" aria-label="Conversations">
          {sessions.length === 0 && (
            <div className="session-empty">No conversations yet</div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`session-item ${s.id === activeId ? "active" : ""}`}
            >
              {/* m-44: the title is a real <button> (keyboard-operable
                  with Enter and Space by default), not a div with
                  role="button" and an Enter-only handler. It is a
                  sibling of the delete button, not its parent, so no
                  interactive element is nested inside another. */}
              <button
                type="button"
                className="session-title-btn"
                onClick={() => onSelect(s.id)}
                aria-current={s.id === activeId ? "true" : undefined}
              >
                <span className="session-title">
                  {s.title || "New conversation"}
                </span>
              </button>
              <button
                type="button"
                className="session-del"
                title="Delete conversation"
                aria-label={`Delete conversation ${s.title || "New conversation"}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          {/* M-27: null = check in flight, not offline — no false
              "API offline" flash on every load. */}
          <span
            className={`status-dot ${
              apiOk === true ? "ok" : apiOk === false ? "down" : "checking"
            }`}
          />
          <span className="status-text">
            {apiOk === true
              ? "API connected"
              : apiOk === false
                ? "API offline"
                : "Checking API…"}
          </span>
        </div>
      </aside>
    </>
  );
}

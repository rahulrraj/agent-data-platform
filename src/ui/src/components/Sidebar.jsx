import React from "react";
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
  return (
    <>
      <div
        className={`sidebar-scrim ${open ? "show" : ""}`}
        onClick={onClose}
        aria-hidden
      />
      <aside className={`sidebar ${open ? "open" : ""}`}>
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
              onClick={() => onSelect(s.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && onSelect(s.id)}
            >
              <span className="session-title">{s.title || "New conversation"}</span>
              <button
                className="session-del"
                title="Delete conversation"
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

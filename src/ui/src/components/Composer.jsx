import React, { useEffect, useRef } from "react";
import { SendIcon, StopIcon } from "./icons.jsx";

export default function Composer({
  onSend,
  onStop,
  streaming,
  disabled,
  focusKey,
}) {
  const ref = useRef(null);

  const autoresize = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };

  useEffect(autoresize, []);

  // m-44: focus management — a new chat moves focus to the composer
  // so keyboard users can type immediately instead of tabbing to it.
  useEffect(() => {
    if (focusKey > 0) ref.current?.focus();
  }, [focusKey]);

  const submit = () => {
    const el = ref.current;
    const text = el.value.trim();
    if (!text || streaming || disabled) return;
    el.value = "";
    autoresize();
    onSend(text);
  };

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={ref}
          rows={1}
          placeholder="Message Agent…"
          onInput={autoresize}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
            // m-37: Esc here covers the focused-textarea case; the
            // window-level listener in App.jsx covers every other
            // focus (e.g. after clicking Stop, focus is on the button).
            if (e.key === "Escape" && streaming) onStop();
          }}
          disabled={disabled}
          aria-label="Message"
        />
        {streaming ? (
          <button
            className="send-btn stop"
            onClick={onStop}
            title="Stop (Esc)"
            aria-label="Stop generating"
          >
            <StopIcon />
          </button>
        ) : (
          <button
            className="send-btn"
            onClick={submit}
            title="Send (Enter)"
            aria-label="Send message"
            disabled={disabled}
          >
            <SendIcon />
          </button>
        )}
      </div>
      <div className="disclaimer">
        Agent can make mistakes. Verify important information.
      </div>
    </div>
  );
}

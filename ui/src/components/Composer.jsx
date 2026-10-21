import React, { useEffect, useRef } from "react";
import { SendIcon, StopIcon } from "./icons.jsx";

export default function Composer({ onSend, onStop, streaming, disabled }) {
  const ref = useRef(null);

  const autoresize = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };

  useEffect(autoresize, []);

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
            if (e.key === "Escape" && streaming) onStop();
          }}
          disabled={disabled}
          aria-label="Message"
        />
        {streaming ? (
          <button className="send-btn stop" onClick={onStop} title="Stop (Esc)">
            <StopIcon />
          </button>
        ) : (
          <button
            className="send-btn"
            onClick={submit}
            title="Send (Enter)"
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

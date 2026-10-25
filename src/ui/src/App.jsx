import React, { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import Composer from "./components/Composer.jsx";
import { Message, Welcome } from "./components/Message.jsx";
import { MenuIcon, MoonIcon, Sparkle, SunIcon } from "./components/icons.jsx";
import { getHealth, getReadiness, streamChat } from "./api.js";
import { loadSessions, saveSessions } from "./sessions.js";

// M-28: collision-proof, unguessable ids. crypto.randomUUID() needs a
// secure context (localhost / https); fall back only where it is absent.
const uid = () =>
  globalThis.crypto?.randomUUID?.() ??
  Math.random().toString(36).slice(2) + Date.now().toString(36);

// m-41(e): one title rule for both branches — first 42 chars plus an
// ellipsis only when the message was actually cut.
const titleFor = (msg) =>
  msg.length > 42 ? msg.slice(0, 42) + "…" : msg;

function prettyModel(label) {
  // "strands:ollama:qwen2.5:1.5b" -> "qwen2.5:1.5b · strands/ollama"
  if (!label) return "";
  const parts = label.split(":");
  if (parts.length >= 3) {
    const [fw, prov, ...rest] = parts;
    return `${rest.join(":")} · ${fw}/${prov}`;
  }
  return label;
}

export default function App() {
  // m-41(a)(b): sessions are loaded once, and the initial conversation
  // is the most recently *active* one (max updatedAt, matching the
  // sidebar's sort), not simply the first in stored order.
  const [initialSessions] = useState(loadSessions);
  const [sessions, setSessions] = useState(initialSessions);
  const [activeId, setActiveId] = useState(() => {
    let best = null;
    for (const s of initialSessions) {
      if (!best || (s.updatedAt || 0) > (best.updatedAt || 0)) best = s;
    }
    return best?.id ?? null;
  });
  const [streaming, setStreaming] = useState(false);
  const [apiOk, setApiOk] = useState(null);
  const [readyOk, setReadyOk] = useState(null);
  const [readyFailing, setReadyFailing] = useState([]);
  const [storageWarning, setStorageWarning] = useState(false);
  const [modelLabel, setModelLabel] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // m-40: the theme initializer is guarded like sessions are — in a
  // browser with storage blocked, getItem throwing during render
  // used to blank the whole app.
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("agent.theme") || "light";
    } catch {
      return "light";
    }
  });
  const [composerFocusKey, setComposerFocusKey] = useState(0);
  const abortRef = useRef(null);
  // m-36: which session the in-flight stream belongs to, so the
  // finally block below can tell a stale stream from the current one.
  const streamingSidRef = useRef(null);
  const streamingRef = useRef(false);
  const activeIdRef = useRef(activeId);
  const bottomRef = useRef(null);
  const chatRef = useRef(null);
  // M-29: auto-scroll only while the user is already near the bottom.
  const nearBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  // m-39: last localStorage save time, for throttling saves mid-stream.
  const lastSaveRef = useRef(0);

  useEffect(() => {
    streamingRef.current = streaming;
  }, [streaming]);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("agent.theme", theme);
    } catch {
      setStorageWarning(true);
    }
  }, [theme]);

  // M-26 + m-39: quota-safe save, throttled while a stream is running
  // (saving the whole session list on every token is the O(n^2) half
  // of the long-output cost). A throttled save is flushed by the
  // effect re-running when streaming ends (streaming is a dep).
  useEffect(() => {
    if (streaming) {
      const now = Date.now();
      if (now - lastSaveRef.current < 500) return;
      lastSaveRef.current = now;
    } else {
      lastSaveRef.current = Date.now();
    }
    setStorageWarning(!saveSessions(sessions).ok);
  }, [sessions, streaming]);

  // m-41(d): another tab editing history no longer silently loses to
  // last-writer-wins — reload from storage on `storage` events, unless
  // this tab is mid-stream (its in-flight patches would be clobbered).
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key !== "agent.sessions" || streamingRef.current) return;
      const fresh = loadSessions();
      setSessions(fresh);
      if (
        activeIdRef.current &&
        !fresh.some((s) => s.id === activeIdRef.current)
      ) {
        setActiveId(null);
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // M-27: liveness + readiness, polled and re-checked on focus and
  // after a send failure (see send's catch). `null` = check in flight.
  const checkHealth = useCallback(async () => {
    try {
      const h = await getHealth();
      setApiOk(true);
      setModelLabel(h.model || "");
    } catch {
      setApiOk(false);
      // m-43: hide the pill rather than showing a stale model after
      // the API goes away or restarts with a different model; the
      // next successful poll restores it.
      setModelLabel("");
    }
    try {
      const r = await getReadiness();
      setReadyOk(r.ok);
      setReadyFailing(r.ok ? [] : r.body?.failing || []);
    } catch {
      setReadyOk(null);
    }
  }, []);

  useEffect(() => {
    checkHealth();
    const timer = setInterval(checkHealth, 30000);
    window.addEventListener("focus", checkHealth);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", checkHealth);
    };
  }, [checkHealth]);

  const active = sessions.find((s) => s.id === activeId) || null;

  const handleScroll = useCallback(() => {
    const el = chatRef.current;
    if (!el) return;
    const near =
      el.scrollHeight - el.scrollTop - el.clientHeight <= 80;
    nearBottomRef.current = near;
    setShowJump(!near);
  }, []);

  const jumpToLatest = useCallback(() => {
    nearBottomRef.current = true;
    setShowJump(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, []);

  useEffect(() => {
    if (nearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [active?.messages?.length, active?.messages?.at(-1)?.content]);

  const patchSession = useCallback((sid, fn) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id === sid ? { ...s, ...fn(s), updatedAt: Date.now() } : s
      )
    );
  }, []);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  // m-37: Esc stops a stream no matter where focus is — the stop
  // button's "Stop (Esc)" title promises this, but the old handler
  // lived only on the textarea, so Esc did nothing after clicking
  // Stop or anywhere else. Active only while streaming.
  useEffect(() => {
    if (!streaming) return undefined;
    const onKeyDown = (e) => {
      if (e.key === "Escape") stop();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [streaming, stop]);

  // m-36: switching conversations aborts the in-flight stream instead
  // of leaving the global `streaming` flag blocking sends in the new
  // conversation while the stop button there aborts the old stream.
  const selectSession = useCallback(
    (id) => {
      if (id !== activeIdRef.current) stop();
      setActiveId(id);
      setSidebarOpen(false);
    },
    [stop]
  );

  const newChat = useCallback(() => {
    stop();
    setActiveId(null);
    setSidebarOpen(false);
    // m-44: focus the composer for the new conversation.
    setComposerFocusKey((k) => k + 1);
  }, [stop]);

  const deleteSession = useCallback(
    (sid) => {
      // m-36: deleting the streaming session aborts its stream; the
      // in-flight patches then target a deleted id and are no-ops,
      // instead of resurrecting a partial answer.
      if (sid === streamingSidRef.current) stop();
      setSessions((prev) => prev.filter((s) => s.id !== sid));
      if (activeId === sid) setActiveId(null);
    },
    [activeId, stop]
  );

  const send = useCallback(
    async (text, targetSid = null) => {
      const msg = text.trim();
      // m-41(c): the send guard itself checks API state, so Welcome
      // suggestion chips (which bypass the composer's `disabled`) can
      // no longer fire a doomed fetch while offline/not-ready.
      if (!msg || streaming || apiOk === false || readyOk === false) return;

      let sid = targetSid || activeId;
      if (!sid || !sessions.some((s) => s.id === sid)) {
        sid = uid();
        const fresh = {
          id: sid,
          title: titleFor(msg),
          messages: [],
          updatedAt: Date.now(),
        };
        setSessions((prev) => [fresh, ...prev]);
        setActiveId(sid);
      } else {
        patchSession(sid, (s) => ({
          title: s.messages.length === 0 ? titleFor(msg) : s.title,
        }));
      }

      const userMsg = { role: "user", content: msg };
      const asstMsg = {
        role: "assistant",
        content: "",
        tools: [],
        streaming: true,
      };
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sid
            ? { ...s, messages: [...s.messages, userMsg, asstMsg], updatedAt: Date.now() }
            : s
        )
      );

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      streamingSidRef.current = sid;
      setStreaming(true);
      // M-24/M-25: content/tools live outside the try so the catch can
      // preserve the partial answer instead of replacing it with the
      // error text. A mid-stream `error` event and an EOF without
      // [DONE] ("Incomplete reply…") both land here with partial state.
      let content = "";
      const tools = [];
      try {
        for await (const ev of streamChat(msg, sid, ctrl.signal)) {
          if (ev.type === "text") content += ev.payload || "";
          else if (ev.type === "tool" && !tools.includes(ev.payload))
            tools.push(ev.payload);
          else if (ev.type === "error")
            throw new Error(ev.error || `Request failed (${ev.status || "?"})`);
          patchSession(sid, (s) => ({
            messages: s.messages.map((m, i) =>
              i === s.messages.length - 1
                ? { ...m, content, tools: [...tools] }
                : m
            ),
          }));
        }
        patchSession(sid, (s) => ({
          messages: s.messages.map((m, i) =>
            i === s.messages.length - 1
              ? { ...m, streaming: false, completed: true }
              : m
          ),
        }));
      } catch (e) {
        if (e.name === "AbortError") {
          // User stopped: keep whatever arrived, no error banner.
          // m-42: mark it aborted so the tool pill never claims "Used".
          patchSession(sid, (s) => ({
            messages: s.messages.map((m, i) =>
              i === s.messages.length - 1
                ? { ...m, streaming: false, content, tools: [...tools], aborted: true }
                : m
            ),
          }));
        } else {
          patchSession(sid, (s) => ({
            messages: s.messages.map((m, i) =>
              i === s.messages.length - 1
                ? {
                    ...m,
                    streaming: false,
                    content,
                    tools: [...tools],
                    partial: content.length > 0,
                    errorMessage: e.message,
                  }
                : m
            ),
          }));
          // M-27: a send failure may mean the API went away — re-check.
          checkHealth();
        }
      } finally {
        // m-36: only the stream that is still current may clear the
        // shared flags — an older stream finishing late must not
        // clear a newer stream's controller or `streaming` flag.
        if (abortRef.current === ctrl) {
          abortRef.current = null;
          streamingSidRef.current = null;
          setStreaming(false);
        }
      }
    },
    [activeId, sessions, streaming, patchSession, checkHealth, apiOk, readyOk]
  );

  // Retry after a partial/error message: re-ask the preceding user turn
  // as a fresh turn, leaving the partial answer in place above it.
  const retry = useCallback(
    (sid, msgIndex) => {
      const session = sessions.find((s) => s.id === sid);
      const prev = session?.messages?.[msgIndex - 1];
      if (prev?.role === "user") {
        setActiveId(sid);
        send(prev.content, sid);
      }
    },
    [sessions, send]
  );

  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        sessions={[...sessions].sort((a, b) => b.updatedAt - a.updatedAt)}
        activeId={activeId}
        onNew={newChat}
        onSelect={selectSession}
        onDelete={deleteSession}
        onClose={() => setSidebarOpen(false)}
        apiOk={apiOk}
      />
      <main className="main">
        <header className="topbar">
          <button
            className="icon-btn menu-btn"
            onClick={() => setSidebarOpen((v) => !v)}
            title="Conversations"
            aria-label="Toggle conversations sidebar"
          >
            <MenuIcon />
          </button>
          <div className="brand">
            <Sparkle size={20} />
            <span className="brand-name">Agent</span>
            {modelLabel && (
              <span className="model-pill" title={modelLabel}>
                {prettyModel(modelLabel)}
              </span>
            )}
          </div>
          <button
            className="icon-btn"
            onClick={() => setTheme((t) => (t === "light" ? "dark" : "light"))}
            title="Toggle theme"
            aria-label="Toggle light/dark theme"
          >
            {theme === "light" ? <MoonIcon /> : <SunIcon />}
          </button>
        </header>

        <div
          className="chat"
          ref={chatRef}
          onScroll={handleScroll}
          aria-live="polite"
        >
          <div className="chat-inner">
            {!active || active.messages.length === 0 ? (
              <Welcome
                onSuggest={send}
                disabled={apiOk === false || readyOk === false}
              />
            ) : (
              active.messages.map((m, i) => (
                <Message
                  key={i}
                  message={m}
                  onRetry={
                    m.errorMessage ? () => retry(active.id, i) : undefined
                  }
                />
              ))
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {showJump && active?.messages?.length > 0 && (
          <button className="jump-latest" onClick={jumpToLatest}>
            ↓ Jump to latest
          </button>
        )}

        {apiOk === false && (
          <div className="api-banner" role="alert">
            Can’t reach the API. Start it with{" "}
            <code>python -m agent_api.main</code> (port 8000).
          </div>
        )}

        {apiOk === true && readyOk === false && (
          <div className="api-banner" role="alert">
            API is up but not ready
            {readyFailing.length > 0 && `: ${readyFailing.join(", ")}`}. Sending
            is disabled until it recovers.
          </div>
        )}

        {storageWarning && (
          <div className="api-banner" role="alert">
            Storage full — new messages may not be saved. Delete old
            conversations to free space.
          </div>
        )}

        <Composer
          onSend={send}
          onStop={stop}
          streaming={streaming}
          disabled={apiOk === false || readyOk === false}
          focusKey={composerFocusKey}
        />
      </main>
    </div>
  );
}

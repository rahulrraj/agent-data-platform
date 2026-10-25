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
  const [sessions, setSessions] = useState(loadSessions);
  const [activeId, setActiveId] = useState(() => loadSessions()[0]?.id ?? null);
  const [streaming, setStreaming] = useState(false);
  const [apiOk, setApiOk] = useState(null);
  const [readyOk, setReadyOk] = useState(null);
  const [readyFailing, setReadyFailing] = useState([]);
  const [storageWarning, setStorageWarning] = useState(false);
  const [modelLabel, setModelLabel] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState(
    () => localStorage.getItem("agent.theme") || "light"
  );
  const abortRef = useRef(null);
  const bottomRef = useRef(null);
  const chatRef = useRef(null);
  // M-29: auto-scroll only while the user is already near the bottom.
  const nearBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("agent.theme", theme);
    } catch {
      setStorageWarning(true);
    }
  }, [theme]);

  // M-26: quota-safe save; a failed save raises a visible warning
  // instead of silently losing later turns on reload.
  useEffect(() => {
    setStorageWarning(!saveSessions(sessions).ok);
  }, [sessions]);

  // M-27: liveness + readiness, polled and re-checked on focus and
  // after a send failure (see send's catch). `null` = check in flight.
  const checkHealth = useCallback(async () => {
    try {
      const h = await getHealth();
      setApiOk(true);
      setModelLabel(h.model || "");
    } catch {
      setApiOk(false);
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

  const newChat = useCallback(() => {
    setActiveId(null);
    setSidebarOpen(false);
    abortRef.current?.abort();
  }, []);

  const deleteSession = useCallback(
    (sid) => {
      setSessions((prev) => prev.filter((s) => s.id !== sid));
      if (activeId === sid) setActiveId(null);
    },
    [activeId]
  );

  const send = useCallback(
    async (text, targetSid = null) => {
      const msg = text.trim();
      if (!msg || streaming) return;

      let sid = targetSid || activeId;
      if (!sid || !sessions.some((s) => s.id === sid)) {
        sid = uid();
        const fresh = {
          id: sid,
          title: msg.length > 42 ? msg.slice(0, 42) + "…" : msg,
          messages: [],
          updatedAt: Date.now(),
        };
        setSessions((prev) => [fresh, ...prev]);
        setActiveId(sid);
      } else {
        patchSession(sid, (s) => ({
          title: s.messages.length === 0 ? msg.slice(0, 42) : s.title,
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
            i === s.messages.length - 1 ? { ...m, streaming: false } : m
          ),
        }));
      } catch (e) {
        if (e.name === "AbortError") {
          // User stopped: keep whatever arrived, no error banner.
          patchSession(sid, (s) => ({
            messages: s.messages.map((m, i) =>
              i === s.messages.length - 1
                ? { ...m, streaming: false, content, tools: [...tools] }
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
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [activeId, sessions, streaming, patchSession, checkHealth]
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

  const stop = useCallback(() => abortRef.current?.abort(), []);

  return (
    <div className="app">
      <Sidebar
        open={sidebarOpen}
        sessions={[...sessions].sort((a, b) => b.updatedAt - a.updatedAt)}
        activeId={activeId}
        onNew={newChat}
        onSelect={(id) => {
          setActiveId(id);
          setSidebarOpen(false);
        }}
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
          >
            {theme === "light" ? <MoonIcon /> : <SunIcon />}
          </button>
        </header>

        <div className="chat" ref={chatRef} onScroll={handleScroll}>
          <div className="chat-inner">
            {!active || active.messages.length === 0 ? (
              <Welcome onSuggest={send} />
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
          <div className="api-banner">
            Can’t reach the API. Start it with{" "}
            <code>python -m agent_api.main</code> (port 8000).
          </div>
        )}

        {apiOk === true && readyOk === false && (
          <div className="api-banner">
            API is up but not ready
            {readyFailing.length > 0 && `: ${readyFailing.join(", ")}`}. Sending
            is disabled until it recovers.
          </div>
        )}

        {storageWarning && (
          <div className="api-banner">
            Storage full — new messages may not be saved. Delete old
            conversations to free space.
          </div>
        )}

        <Composer
          onSend={send}
          onStop={stop}
          streaming={streaming}
          disabled={apiOk === false || readyOk === false}
        />
      </main>
    </div>
  );
}

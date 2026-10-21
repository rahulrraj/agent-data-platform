import React, { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import Composer from "./components/Composer.jsx";
import { Message, Welcome } from "./components/Message.jsx";
import { MenuIcon, MoonIcon, Sparkle, SunIcon } from "./components/icons.jsx";
import { getHealth, streamChat } from "./api.js";

const uid = () =>
  Math.random().toString(36).slice(2) + Date.now().toString(36);

function loadSessions() {
  try {
    const raw = JSON.parse(localStorage.getItem("agent.sessions") || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

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
  const [modelLabel, setModelLabel] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState(
    () => localStorage.getItem("agent.theme") || "light"
  );
  const abortRef = useRef(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("agent.theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("agent.sessions", JSON.stringify(sessions));
  }, [sessions]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await getHealth();
        if (!cancelled) {
          setApiOk(true);
          setModelLabel(h.model || "");
        }
      } catch {
        if (!cancelled) setApiOk(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const active = sessions.find((s) => s.id === activeId) || null;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
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
    async (text) => {
      const msg = text.trim();
      if (!msg || streaming) return;

      let sid = activeId;
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
      try {
        let content = "";
        const tools = [];
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
          patchSession(sid, (s) => ({
            messages: s.messages.map((m, i) =>
              i === s.messages.length - 1 ? { ...m, streaming: false } : m
            ),
          }));
        } else {
          patchSession(sid, (s) => ({
            messages: s.messages.map((m, i) =>
              i === s.messages.length - 1
                ? { ...m, streaming: false, error: true, content: e.message }
                : m
            ),
          }));
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [activeId, sessions, streaming, patchSession]
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

        <div className="chat">
          <div className="chat-inner">
            {!active || active.messages.length === 0 ? (
              <Welcome onSuggest={send} />
            ) : (
              active.messages.map((m, i) => <Message key={i} message={m} />)
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {apiOk === false && (
          <div className="api-banner">
            Can’t reach the API. Start it with{" "}
            <code>python -m agent_api.main</code> (port 8000).
          </div>
        )}

        <Composer
          onSend={send}
          onStop={stop}
          streaming={streaming}
          disabled={apiOk === false}
        />
      </main>
    </div>
  );
}

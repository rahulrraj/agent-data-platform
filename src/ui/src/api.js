// Thin client for the Agent API. In dev, /api is proxied to localhost:8000
// by Vite; set VITE_API_URL for a deployed build.

const BASE = import.meta.env.VITE_API_URL || "";

export async function getHealth() {
  const res = await fetch(`${BASE}/api/health`);
  if (!res.ok) throw new Error(`health check failed: ${res.status}`);
  return res.json();
}

export async function getReadiness() {
  const res = await fetch(`${BASE}/api/ready`);
  return { ok: res.ok, body: await res.json().catch(() => ({})) };
}

/**
 * POST /api/chat and yield parsed SSE events:
 *   {type: "text", payload} | {type: "tool", payload} | {type: "error", ...}
 * Ends when the server sends `data: [DONE]`.
 */
export async function* streamChat(message, sessionId, signal) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sawDone = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!raw.startsWith("data:")) continue;
      const data = raw.slice(5).trim();
      if (data === "[DONE]") {
        sawDone = true;
        return;
      }
      try {
        yield JSON.parse(data);
      } catch {
        // ignore malformed chunks
      }
    }
  }
  // M-24: EOF without [DONE] is a truncated reply, not a complete answer.
  // Any trailing partial frame in `buf` is discarded, and the caller must
  // surface the partial content with an error (see App.jsx), never as
  // a finished message. AbortError propagates from reader.read() above
  // and is deliberately not converted into this error.
  if (!sawDone) {
    throw new Error(
      "Incomplete reply: the stream ended before the server finished"
    );
  }
}

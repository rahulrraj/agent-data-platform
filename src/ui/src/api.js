// Thin client for the Agent API. In dev, /api is proxied to localhost:8000
// by Vite; set VITE_API_URL for a deployed build.
//
// m-47: VITE_API_URL is build-time-only (Vite bakes import.meta.env into
// the bundle at build time; changing the origin requires a rebuild),
// and a trailing slash is stripped so a value like
// "https://api.example.com/" does not produce "//api/health".
//
// ADR 0008: every call carries Authorization: Bearer <token> whenever
// a token is present — including health/ready, which the backend
// leaves public but which accept the header harmlessly. A 401 drops
// the stored tokens and throws AuthError so the app routes back to
// the login gate; token storage/expiry/renewal live in auth.js and
// are never touched directly here.

import {
  AuthError,
  clearTokens,
  getAuthConfig,
  getAuthHeaders,
} from "./auth.js";

const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

/** Headers for one call: the bearer token, or {} when unauthenticated. */
async function authHeaders() {
  return getAuthHeaders(getAuthConfig());
}

/** A 401 means the session is dead: drop tokens, surface AuthError. */
function unauthorized() {
  clearTokens();
  throw new AuthError("Your session has expired. Please sign in again.");
}

/** Extract a server error detail ({detail} / {message} / plain text). */
async function errorDetail(res) {
  try {
    const body = await res.clone().json();
    if (body && typeof body === "object") {
      const d = body.detail ?? body.message;
      if (typeof d === "string" && d) return d;
    }
  } catch {
    // not JSON — fall through to text
  }
  try {
    const text = await res.clone().text();
    // An HTML fallback body (e.g. nginx serving index.html for /api/*)
    // is a routing symptom, not a usable error detail.
    if (text && text.length < 500 && !text.trimStart().startsWith("<")) return text;
  } catch {
    // unreadable body — keep the status fallback
  }
  return "";
}

export async function getHealth() {
  const res = await fetch(`${BASE}/api/health`, { headers: await authHeaders() });
  if (res.status === 401) unauthorized();
  if (!res.ok) {
    // m-35: keep the server's error detail instead of only the status.
    const detail = await errorDetail(res);
    throw new Error(
      detail ? `health check failed: ${res.status}: ${detail}` : `health check failed: ${res.status}`
    );
  }
  return res.json();
}

export async function getReadiness() {
  const res = await fetch(`${BASE}/api/ready`, { headers: await authHeaders() });
  if (res.status === 401) unauthorized();
  return { ok: res.ok, body: await res.json().catch(() => ({})) };
}

// m-33/m-34: split an SSE stream per spec — events are separated by a
// blank line in either \n\n or \r\n\r\n framing, and one event block
// may carry several `data:` lines, which concatenate with "\n".
// Returns the index just past the separator, or -1 if no full event
// is buffered yet.
function findEventEnd(buf) {
  const lf = buf.indexOf("\n\n");
  const crlf = buf.indexOf("\r\n\r\n");
  if (lf === -1) return crlf === -1 ? -1 : crlf + 4;
  if (crlf === -1) return lf + 2;
  return lf < crlf ? lf + 2 : crlf + 4;
}

/**
 * POST /api/chat and yield parsed SSE events:
 *   {type: "text", payload} | {type: "tool", payload} | {type: "error", ...}
 * Ends when the server sends `data: [DONE]`.
 */
export async function* streamChat(message, sessionId, signal) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (res.status === 401) unauthorized();
  if (!res.ok) {
    // m-35: FastAPI returns {"detail": "..."} for 400/502 — surface it.
    const detail = await errorDetail(res);
    throw new Error(
      detail ? `API error ${res.status}: ${detail}` : `API error ${res.status}`
    );
  }
  // m-34: a missing body (e.g. a network-level stub) is an error, not
  // a silent empty stream.
  if (!res.body) {
    throw new Error("API error: empty response body");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sawDone = false;
  // m-34: malformed frames are still skipped (the stream must survive
  // one bad frame), but they are no longer silently swallowed — in dev
  // builds each one is logged, and the count is attached to the
  // incomplete-reply error so a truncated stream is diagnosable.
  let malformed = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let end;
      while ((end = findEventEnd(buf)) >= 0) {
        const raw = buf.slice(0, end);
        buf = buf.slice(end);
        const dataLines = [];
        for (const line of raw.split(/\r\n|\n|\r/)) {
          if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).replace(/^ /, ""));
          }
          // lines starting with ":" are SSE comments; other fields
          // (event:, id:, retry:) carry no payload for this client.
        }
        if (dataLines.length === 0) continue;
        const data = dataLines.join("\n");
        if (data === "[DONE]") {
          sawDone = true;
          return;
        }
        try {
          yield JSON.parse(data);
        } catch {
          malformed += 1;
          if (import.meta.env.DEV) {
            console.warn("[streamChat] skipping malformed SSE frame:", data);
          }
        }
      }
    }
  } finally {
    // m-34: never leave the reader (and the server stream behind it)
    // running when the consumer stops, errors, or the generator is
    // abandoned. cancel() on a finished/errored reader is a no-op.
    try {
      await reader.cancel();
    } catch {
      // reader already closed/errored
    }
  }
  // M-24: EOF without [DONE] is a truncated reply, not a complete answer.
  // Any trailing partial frame in `buf` is discarded, and the caller must
  // surface the partial content with an error (see App.jsx), never as
  // a finished message. AbortError propagates from reader.read() above
  // and is deliberately not converted into this error.
  if (!sawDone) {
    throw new Error(
      malformed > 0
        ? `Incomplete reply: the stream ended before the server finished (${malformed} malformed frame${malformed === 1 ? "" : "s"} skipped)`
        : "Incomplete reply: the stream ended before the server finished"
    );
  }
}

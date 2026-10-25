// Local conversation persistence: validation, pruning, and quota-safe saves.
// The server keeps its own copy of each conversation (see README.md:
// local delete != server delete); this module only guards the local copy
// against corrupt data and localStorage quota exhaustion (M-26).

const KEY = "agent.sessions";

export const MAX_MESSAGES_PER_SESSION = 100;
export const MAX_SESSIONS = 50;

/** Repair one stored session, or return null if it cannot be keyed. */
export function validateSession(entry) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
  if (typeof entry.id !== "string" || !entry.id) return null;
  const messages = Array.isArray(entry.messages)
    ? entry.messages
        .filter(
          (m) =>
            m &&
            typeof m === "object" &&
            (m.role === "user" || m.role === "assistant") &&
            typeof m.content === "string"
        )
        .map((m) => ({
          ...m,
          tools: Array.isArray(m.tools)
            ? m.tools.filter((t) => typeof t === "string")
            : [],
        }))
    : [];
  return {
    id: entry.id,
    title:
      typeof entry.title === "string" && entry.title
        ? entry.title
        : "New conversation",
    messages,
    updatedAt: typeof entry.updatedAt === "number" ? entry.updatedAt : 0,
  };
}

export function validateSessions(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map(validateSession).filter(Boolean);
}

/**
 * Cap stored size: latest `maxMessages` per session, first `maxSessions`
 * sessions (state order is newest-first, so slicing keeps the newest).
 */
export function pruneSessions(sessions, maxMessages = MAX_MESSAGES_PER_SESSION, maxSessions = MAX_SESSIONS) {
  return validateSessions(sessions)
    .slice(0, maxSessions)
    .map((s) => ({ ...s, messages: s.messages.slice(-maxMessages) }));
}

export function loadSessions() {
  try {
    return pruneSessions(validateSessions(JSON.parse(localStorage.getItem(KEY) || "[]")));
  } catch {
    return [];
  }
}

/**
 * Persist sessions, shrinking before giving up on quota errors.
 * Returns { ok: true } on success (pruned: true if a shrunk copy was
 * stored), or { ok: false } when even the smallest copy does not fit.
 */
export function saveSessions(sessions) {
  const attempts = [
    pruneSessions(sessions),
    pruneSessions(sessions, 20, MAX_SESSIONS),
    pruneSessions(sessions, 20, 20),
    pruneSessions(sessions, 5, 20),
  ];
  let lastError = null;
  for (let i = 0; i < attempts.length; i++) {
    try {
      localStorage.setItem(KEY, JSON.stringify(attempts[i]));
      return { ok: true, pruned: i > 0 };
    } catch (e) {
      lastError = e;
    }
  }
  return { ok: false, error: lastError };
}

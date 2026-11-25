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
  // Per-user history: the owner (id_token `sub`) a local session was
  // saved under. Kept only when it is a string, so entries written
  // before scoping (no owner) keep their old shape exactly.
  const owner =
    typeof entry.owner === "string" && entry.owner ? entry.owner : null;
  return {
    id: entry.id,
    title:
      typeof entry.title === "string" && entry.title
        ? entry.title
        : "New conversation",
    messages,
    updatedAt: typeof entry.updatedAt === "number" ? entry.updatedAt : 0,
    ...(owner ? { owner } : {}),
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

/**
 * Load the local sessions visible to `owner` (null/undefined = dev
 * mode: everything lists). A session stamped with a different
 * principal's owner is dropped — one user never sees another user's
 * local copy. Ownerless entries (written before scoping, or in dev
 * mode) are kept: they are claimed for the current user on save.
 */
export function loadSessions(owner) {
  try {
    const all = pruneSessions(
      validateSessions(JSON.parse(localStorage.getItem(KEY) || "[]"))
    );
    if (owner == null) return all;
    return all.filter((s) => !s.owner || s.owner === owner);
  } catch {
    return [];
  }
}

/**
 * Reconcile the local copy against the server's per-user listing
 * (GET /api/sessions): keep only local sessions the server lists
 * for this user, and stamp the current owner onto kept sessions
 * that lack one. A local session absent from the server list is
 * another user's session, or one the server no longer has — either
 * way it must not be shown. `serverSessions` is the endpoint's
 * `sessions` array ({id, updated_at}); `owner` null means dev mode,
 * where the server lists everything and nothing is stamped.
 */
export function reconcileSessions(localSessions, serverSessions, owner) {
  // A malformed server answer (no array) reconciles nothing: a bad
  // payload must never wipe the local copy.
  if (!Array.isArray(serverSessions)) {
    return pruneSessions(validateSessions(localSessions));
  }
  const serverIds = new Set(
    serverSessions
      .map((s) => (typeof s === "string" ? s : s?.id))
      .filter((id) => typeof id === "string" && id)
  );
  return pruneSessions(
    validateSessions(localSessions)
      .filter((s) => serverIds.has(s.id))
      .map((s) =>
        owner && !s.owner ? { ...s, owner } : s
      )
  );
}

/**
 * Persist sessions, shrinking before giving up on quota errors.
 * Returns { ok: true } on success (pruned: true if a shrunk copy was
 * stored), or { ok: false } when even the smallest copy does not fit.
 * When `owner` is given, sessions without one are stamped with it,
 * so a session created locally is bound to its user in the local
 * copy too (the server binds it on the first turn, ADR 0008).
 */
export function saveSessions(sessions, owner) {
  const stamped =
    owner && typeof owner === "string"
      ? sessions.map((s) =>
          s && typeof s === "object" && !s.owner ? { ...s, owner } : s
        )
      : sessions;
  const attempts = [
    pruneSessions(stamped),
    pruneSessions(stamped, 20, MAX_SESSIONS),
    pruneSessions(stamped, 20, 20),
    pruneSessions(stamped, 5, 20),
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

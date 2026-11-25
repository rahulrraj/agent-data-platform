import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadSessions,
  pruneSessions,
  reconcileSessions,
  saveSessions,
  validateSession,
  validateSessions,
} from "./sessions.js";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("validateSession (M-26)", () => {
  it("drops entries without a usable id", () => {
    expect(validateSession(null)).toBeNull();
    expect(validateSession({ title: "x", messages: [] })).toBeNull();
    expect(validateSession({ id: 42, messages: [] })).toBeNull();
  });

  it("repairs missing title and non-array messages instead of crashing", () => {
    expect(validateSession({ id: "s1" })).toEqual({
      id: "s1",
      title: "New conversation",
      messages: [],
      updatedAt: 0,
    });
    expect(validateSession({ id: "s1", title: "t", messages: null })).toEqual({
      id: "s1",
      title: "t",
      messages: [],
      updatedAt: 0,
    });
  });

  it("drops malformed messages and non-string tool names", () => {
    const repaired = validateSession({
      id: "s1",
      title: "t",
      messages: [
        { role: "user", content: "hi" },
        { role: "assistant", content: null },
        { role: "system", content: "nope" },
        "not-an-object",
        { role: "assistant", content: "ok", tools: ["calc", 7] },
      ],
    });
    expect(repaired.messages).toEqual([
      { role: "user", content: "hi", tools: [] },
      { role: "assistant", content: "ok", tools: ["calc"] },
    ]);
  });

  it("validateSessions drops the bad and keeps the good", () => {
    expect(validateSessions("not-an-array")).toEqual([]);
    expect(
      validateSessions([{ id: "a", messages: [] }, null, { id: "b" }]).map(
        (s) => s.id
      )
    ).toEqual(["a", "b"]);
  });
});

describe("loadSessions / pruneSessions (M-26)", () => {
  it("returns [] for corrupt JSON instead of throwing", () => {
    localStorage.setItem("agent.sessions", "{broken");
    expect(loadSessions()).toEqual([]);
  });

  it("caps messages per session and total sessions", () => {
    const many = Array.from({ length: 105 }, (_, i) => ({
      role: "user",
      content: `m${i}`,
    }));
    const pruned = pruneSessions(
      [{ id: "s", title: "t", messages: many, updatedAt: 1 }],
      100,
      50
    );
    expect(pruned[0].messages).toHaveLength(100);
    expect(pruned[0].messages[0].content).toBe("m5");
    const sessions = Array.from({ length: 60 }, (_, i) => ({
      id: `s${i}`,
      title: "t",
      messages: [],
      updatedAt: i,
    }));
    expect(pruneSessions(sessions)).toHaveLength(50);
  });

  it("round-trips through saveSessions", () => {
    const sessions = [{ id: "s1", title: "t", messages: [], updatedAt: 1 }];
    expect(saveSessions(sessions).ok).toBe(true);
    expect(loadSessions()).toEqual(sessions);
  });

  it("reports failure when localStorage always throws (M-26)", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    expect(
      saveSessions([{ id: "s1", title: "t", messages: [], updatedAt: 1 }]).ok
    ).toBe(false);
  });
});

describe("per-user scoping", () => {
  const seed = () => {
    localStorage.setItem(
      "agent.sessions",
      JSON.stringify([
        { id: "a", title: "alice chat", messages: [], updatedAt: 3, owner: "alice" },
        { id: "b", title: "bob chat", messages: [], updatedAt: 2, owner: "bob" },
        { id: "c", title: "unowned chat", messages: [], updatedAt: 1 },
      ])
    );
  };

  it("validateSession keeps a string owner and drops a non-string one", () => {
    expect(validateSession({ id: "s1", owner: "alice" }).owner).toBe("alice");
    expect(validateSession({ id: "s1", owner: 42 })).not.toHaveProperty("owner");
    expect(validateSession({ id: "s1" })).not.toHaveProperty("owner");
  });

  it("loadSessions drops another user's sessions, keeps own and unowned", () => {
    seed();
    expect(loadSessions("alice").map((s) => s.id)).toEqual(["a", "c"]);
    expect(loadSessions("bob").map((s) => s.id)).toEqual(["b", "c"]);
    // Dev mode (no principal): everything lists.
    expect(loadSessions().map((s) => s.id)).toEqual(["a", "b", "c"]);
    expect(loadSessions(null).map((s) => s.id)).toEqual(["a", "b", "c"]);
  });

  it("saveSessions stamps the owner onto ownerless sessions", () => {
    saveSessions(
      [{ id: "a", title: "t", messages: [], updatedAt: 1 }],
      "alice"
    );
    expect(loadSessions("alice")).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem("agent.sessions"))[0].owner).toBe(
      "alice"
    );
    // ...but never overwrites a different owner's stamp.
    saveSessions(
      [{ id: "b", title: "t", messages: [], updatedAt: 1, owner: "bob" }],
      "alice"
    );
    expect(JSON.parse(localStorage.getItem("agent.sessions"))[0].owner).toBe(
      "bob"
    );
  });

  it("reconcileSessions drops local sessions the server does not list", () => {
    seed();
    const reconciled = reconcileSessions(loadSessions(), [{ id: "a" }], "alice");
    expect(reconciled.map((s) => s.id)).toEqual(["a"]);
  });

  it("reconcileSessions stamps the owner onto kept ownerless sessions", () => {
    const reconciled = reconcileSessions(
      [{ id: "c", title: "t", messages: [], updatedAt: 1 }],
      [{ id: "c", updated_at: null }],
      "alice"
    );
    expect(reconciled).toEqual([
      { id: "c", title: "t", messages: [], updatedAt: 1, owner: "alice" },
    ]);
  });

  it("reconcileSessions keeps the local copy on a malformed server answer", () => {
    seed();
    expect(reconcileSessions(loadSessions(), undefined, "alice")).toHaveLength(3);
    expect(reconcileSessions(loadSessions(), { sessions: [] }, "alice")).toHaveLength(3);
  });

  it("login switch: the previous user's sessions are gone after reconcile", () => {
    seed();
    // Alice signs in; the server lists only her session.
    const asAlice = reconcileSessions(
      loadSessions("alice"),
      [{ id: "a" }],
      "alice"
    );
    expect(asAlice.map((s) => s.id)).toEqual(["a"]);
    // The reconciled list is what gets persisted (via the save effect).
    saveSessions(asAlice, "alice");
    // Bob signs in later on the same browser: even before any server
    // round-trip, his load never shows Alice's stamped session.
    expect(loadSessions("bob").map((s) => s.id)).toEqual([]);
    // And after Bob's own reconcile against his server list, only his
    // sessions could appear — Alice's id is not in the server list.
    const asBob = reconcileSessions(
      [
        { id: "a", title: "alice chat", messages: [], updatedAt: 3, owner: "alice" },
        { id: "b", title: "bob chat", messages: [], updatedAt: 2, owner: "bob" },
      ],
      [{ id: "b" }],
      "bob"
    );
    expect(asBob.map((s) => s.id)).toEqual(["b"]);
  });
});

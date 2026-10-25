import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadSessions,
  pruneSessions,
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

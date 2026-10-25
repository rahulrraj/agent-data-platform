import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChat } from "./api.js";

function sseStream(byteChunks) {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < byteChunks.length) {
        controller.enqueue(
          typeof byteChunks[i] === "string"
            ? encoder.encode(byteChunks[i])
            : byteChunks[i]
        );
        i += 1;
      } else {
        controller.close();
      }
    },
  });
}

function mockFetch(byteChunks, ok = true, status = 200) {
  global.fetch = vi.fn(async () => ({
    ok,
    status,
    body: sseStream(byteChunks),
  }));
}

async function collect(message = "hi", sessionId = "s1", signal) {
  const events = [];
  for await (const ev of streamChat(message, sessionId, signal)) {
    events.push(ev);
  }
  return events;
}

const ev = (type, payload) => `data: ${JSON.stringify({ type, payload })}\n\n`;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("streamChat", () => {
  it("yields text and tool events in order and stops at [DONE]", async () => {
    mockFetch([
      ev("text", "The "),
      ev("tool", "calculate"),
      ev("text", "result is 42."),
      "data: [DONE]\n\n",
    ]);
    const events = await collect();
    expect(events).toEqual([
      { type: "text", payload: "The " },
      { type: "tool", payload: "calculate" },
      { type: "text", payload: "result is 42." },
    ]);
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/chat",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ message: "hi", session_id: "s1" }),
      })
    );
  });

  it("reassembles SSE frames split across reads", async () => {
    const full = ev("text", "hello world") + "data: [DONE]\n\n";
    // split mid-frame on purpose
    mockFetch([full.slice(0, 10), full.slice(10, 25), full.slice(25)]);
    const events = await collect();
    expect(events).toEqual([{ type: "text", payload: "hello world" }]);
  });

  it("yields error events instead of throwing", async () => {
    mockFetch([
      `data: ${JSON.stringify({ type: "error", status: 502, error: "boom" })}\n\n`,
      "data: [DONE]\n\n",
    ]);
    const events = await collect();
    expect(events).toEqual([{ type: "error", status: 502, error: "boom" }]);
  });

  it("skips malformed JSON lines", async () => {
    mockFetch(["data: not-json\n\n", ev("text", "ok"), "data: [DONE]\n\n"]);
    const events = await collect();
    expect(events).toEqual([{ type: "text", payload: "ok" }]);
  });

  it("throws on a non-OK HTTP response", async () => {
    mockFetch([], false, 500);
    await expect(collect()).rejects.toThrow("API error 500");
  });

  it("aborts cleanly when the signal fires", async () => {
    const controller = new AbortController();
    const encoder = new TextEncoder();
    let streamController;
    global.fetch = vi.fn(async (_url, { signal }) => {
      signal.addEventListener("abort", () => {
        streamController?.error(
          new DOMException("The operation was aborted.", "AbortError")
        );
      });
      return {
        ok: true,
        body: new ReadableStream({
          start(c) {
            streamController = c;
            c.enqueue(encoder.encode(ev("text", "partial")));
          },
        }),
      };
    });

    const gen = streamChat("hi", "s1", controller.signal);
    const first = await gen.next();
    expect(first.value).toEqual({ type: "text", payload: "partial" });
    controller.abort();
    await expect(gen.next()).rejects.toThrow();
  });

  it("throws an incomplete-reply error on EOF without [DONE] (M-24)", async () => {
    mockFetch([ev("text", "half an ")]);
    await expect(collect()).rejects.toThrow("Incomplete reply");
  });

  it("yields what arrived, then throws, on truncated EOF (M-24)", async () => {
    mockFetch([ev("text", "half an ")]);
    const gen = streamChat("hi", "s1");
    const first = await gen.next();
    expect(first.value).toEqual({ type: "text", payload: "half an " });
    await expect(gen.next()).rejects.toThrow("Incomplete reply");
  });

  it("throws on a trailing partial frame with no terminator (M-24)", async () => {
    mockFetch([ev("text", "done."), 'data: {"type": "text", "payload": "cut']);
    await expect(collect()).rejects.toThrow("Incomplete reply");
  });
});

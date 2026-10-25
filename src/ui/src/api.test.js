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

  it("keeps the server error detail on a non-OK response (m-35)", async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 502,
      clone: () => ({
        json: async () => ({ detail: "model overloaded" }),
      }),
      body: sseStream([]),
    }));
    await expect(collect()).rejects.toThrow(
      "API error 502: model overloaded"
    );
  });

  it("throws on a missing response body (m-34)", async () => {
    global.fetch = vi.fn(async () => ({ ok: true, status: 200, body: null }));
    await expect(collect()).rejects.toThrow("empty response body");
  });

  it("parses CRLF framing (m-33)", async () => {
    mockFetch([
      `data: ${JSON.stringify({ type: "text", payload: "hi" })}\r\n\r\n`,
      "data: [DONE]\r\n\r\n",
    ]);
    const events = await collect();
    expect(events).toEqual([{ type: "text", payload: "hi" }]);
  });

  it("concatenates multi-line data: blocks (m-33)", async () => {
    // split at a JSON token boundary: the joined lines are valid JSON
    // with the newline acting as whitespace between tokens.
    mockFetch([
      'data: {"type":"text",\ndata: "payload":"multi"}\n\n',
      "data: [DONE]\n\n",
    ]);
    const events = await collect();
    expect(events).toEqual([{ type: "text", payload: "multi" }]);
  });

  it("cancels the reader when the stream ends at [DONE] (m-34)", async () => {
    const encoder = new TextEncoder();
    let cancelled = false;
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      body: new ReadableStream({
        pull(controller) {
          controller.enqueue(encoder.encode(ev("text", "done")));
          controller.enqueue(encoder.encode("data: [DONE]\n\n"));
        },
        cancel() {
          cancelled = true;
        },
      }),
    }));
    await collect();
    expect(cancelled).toBe(true);
  });

  it("counts malformed frames in the incomplete-reply error (m-34)", async () => {
    mockFetch(["data: not-json\n\n", ev("text", "half")]);
    await expect(collect()).rejects.toThrow("1 malformed frame");
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

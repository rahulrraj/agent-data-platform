import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import React from "react";
import App from "./App.jsx";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  // jsdom does not implement scrollIntoView (App auto-scrolls to it).
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals?.();
});

function healthOk() {
  return {
    ok: true,
    status: 200,
    json: async () => ({ status: "ok", model: "strands:ollama:qwen2.5:1.5b" }),
  };
}

describe("App model pill (m-43)", () => {
  it("hides the pill when a later health poll fails", async () => {
    let healthFails = false;
    global.fetch = vi.fn(async (url) => {
      if (String(url).includes("/api/health")) {
        if (healthFails) throw new Error("down");
        return healthOk();
      }
      return { ok: true, status: 200, json: async () => ({ status: "ok", failing: [] }) };
    });
    render(<App />);
    // initial poll succeeds: pill shows the pretty model label
    await waitFor(() =>
      expect(screen.getByText("qwen2.5:1.5b · strands/ollama")).toBeTruthy()
    );
    // API goes away; a focus-triggered re-check hides the stale pill
    healthFails = true;
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() =>
      expect(
        screen.queryByText("qwen2.5:1.5b · strands/ollama")
      ).toBeNull()
    );
  });

  it("disables suggestion chips while the API is offline (m-41c)", async () => {
    global.fetch = vi.fn(async (url) => {
      if (String(url).includes("/api/health")) throw new Error("down");
      return { ok: false, status: 503, json: async () => ({ failing: ["model"] }) };
    });
    render(<App />);
    await waitFor(() => expect(screen.getByText("API offline")).toBeTruthy());
    const chips = screen.getAllByRole("button", { name: /What is 6/ });
    expect(chips[0].disabled).toBe(true);
  });
});

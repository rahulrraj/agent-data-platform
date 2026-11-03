import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, act } from "@testing-library/react";
import React from "react";
import App from "./App.jsx";
import { storeTokens } from "./auth.js";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.restoreAllMocks();
  // The test/dev bypass (ADR 0008): existing behavior tests run
  // authenticated without OIDC config. Gate tests below unstub it
  // and stub fake OIDC config instead.
  vi.stubEnv("VITE_AUTH_DISABLED", "true");
  // jsdom does not implement scrollIntoView (App auto-scrolls to it).
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  // Vitest does not inject globals, so RTL's auto-cleanup never
  // registers: unmount explicitly, or earlier App renders stay in
  // the DOM and later queries match twice.
  cleanup();
  vi.unstubAllGlobals?.();
  vi.unstubAllEnvs();
  sessionStorage.clear();
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

describe("login gate (ADR 0008)", () => {
  function stubOidcConfig() {
    vi.unstubAllEnvs();
    vi.stubEnv("VITE_OIDC_ISSUER", "https://example.okta.com");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "fake-client-id");
  }

  it("shows only the login screen and makes no API calls before login", async () => {
    stubOidcConfig();
    global.fetch = vi.fn(async () => healthOk());
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Sign in" })
      ).toBeTruthy()
    );
    expect(screen.getByText("Sign in to continue")).toBeTruthy();
    // No chat surface at all behind the gate.
    expect(screen.queryByPlaceholderText("Message Agent…")).toBeNull();
    expect(screen.queryByText("Checking API…")).toBeNull();
    // Let any stray effect/timer fire; still no fetch.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("shows the unconfigured message and makes no API calls when OIDC is unset", async () => {
    vi.unstubAllEnvs();
    global.fetch = vi.fn(async () => healthOk());
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText("Sign-in isn’t configured")).toBeTruthy()
    );
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("renders the app when a valid token is already stored", async () => {
    stubOidcConfig();
    storeTokens({ accessToken: "tok-1", expiresIn: 3600 });
    global.fetch = vi.fn(async (url) => {
      if (String(url).includes("/api/health")) return healthOk();
      return { ok: true, status: 200, json: async () => ({ status: "ok", failing: [] }) };
    });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText("qwen2.5:1.5b · strands/ollama")).toBeTruthy()
    );
    expect(screen.queryByText("Sign in to continue")).toBeNull();
    // The stored token went out on the health probe.
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/health"),
      expect.objectContaining({
        headers: { Authorization: "Bearer tok-1" },
      })
    );
  });

  it("routes back to the login gate and drops tokens on a 401", async () => {
    stubOidcConfig();
    storeTokens({ accessToken: "tok-1", expiresIn: 3600 });
    global.fetch = vi.fn(async () => ({ ok: false, status: 401 }));
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Sign in" })
      ).toBeTruthy()
    );
    expect(sessionStorage.getItem("agent.auth.access_token")).toBeNull();
    expect(screen.queryByPlaceholderText("Message Agent…")).toBeNull();
  });

  it("shows a callback error on the login screen and strips the query string", async () => {
    stubOidcConfig();
    window.history.pushState(
      {},
      "",
      "/?error=access_denied&error_description=Denied+by+user"
    );
    global.fetch = vi.fn(async () => healthOk());
    try {
      render(<App />);
      await waitFor(() =>
        expect(screen.getByText(/Denied by user/)).toBeTruthy()
      );
      expect(
        screen.getByRole("button", { name: "Sign in" })
      ).toBeTruthy();
      expect(window.location.search).toBe("");
      expect(global.fetch).not.toHaveBeenCalled();
    } finally {
      window.history.pushState({}, "", "/");
    }
  });
});

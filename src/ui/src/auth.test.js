import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AuthError,
  STORAGE_KEYS,
  buildAuthorizeUrl,
  clearDiscoveryCache,
  createCodeChallenge,
  createCodeVerifier,
  decodeJwtPayload,
  discover,
  getAuthConfig,
  getAuthHeaders,
  getStoredAccessToken,
  getStoredRefreshToken,
  getValidAccessToken,
  handleCallback,
  refreshAccessToken,
  startLogin,
  storeTokens,
  validateIdTokenClaims,
} from "./auth.js";

// Obviously fake placeholder values only — no real tenant, client id,
// or secret exists anywhere in this suite (see task constraints).
const ISSUER = "https://example.okta.com";
const CLIENT_ID = "fake-client-id";
const AUDIENCE = "fake-audience";
const DISCOVERY = {
  authorization_endpoint: `${ISSUER}/oauth2/v1/authorize`,
  token_endpoint: `${ISSUER}/oauth2/v1/token`,
};

const config = {
  issuer: ISSUER,
  clientId: CLIENT_ID,
  audience: AUDIENCE,
  redirectUri: "http://localhost:3000",
  authDisabled: false,
  configured: true,
};

function b64url(input) {
  const s = typeof input === "string" ? input : JSON.stringify(input);
  return Buffer.from(s, "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function idToken(overrides = {}) {
  const payload = {
    iss: ISSUER,
    aud: AUDIENCE,
    exp: Math.floor(Date.now() / 1000) + 3600,
    sub: "user-1",
    nonce: "test-nonce",
    ...overrides,
  };
  return `${b64url({ alg: "none", typ: "JWT" })}.${b64url(payload)}.${b64url("sig")}`;
}

/** Mock fetch serving discovery + one token-endpoint response. */
function oidcFetch({ tokenResponse, tokenStatus = 200 } = {}) {
  return vi.fn(async (url) => {
    const u = String(url);
    if (u === `${ISSUER}/.well-known/openid-configuration`) {
      return { ok: true, status: 200, json: async () => DISCOVERY };
    }
    if (u === DISCOVERY.token_endpoint) {
      return {
        ok: tokenStatus >= 200 && tokenStatus < 300,
        status: tokenStatus,
        json: async () => tokenResponse,
      };
    }
    throw new Error(`unexpected fetch: ${u}`);
  });
}

function assertNoTokenInLocalStorage(token) {
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    expect(localStorage.getItem(key)).not.toContain(token);
  }
  expect(localStorage.getItem(STORAGE_KEYS.accessToken)).toBeNull();
  expect(localStorage.getItem(STORAGE_KEYS.refreshToken)).toBeNull();
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  clearDiscoveryCache();
  vi.restoreAllMocks();
});

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  clearDiscoveryCache();
  vi.unstubAllEnvs();
});

describe("getAuthConfig", () => {
  it("reads issuer/client-id/audience and strips a trailing slash", () => {
    const c = getAuthConfig({
      VITE_OIDC_ISSUER: "https://example.okta.com/",
      VITE_OIDC_CLIENT_ID: "cid",
      VITE_OIDC_AUDIENCE: "aud",
      DEV: false,
    });
    expect(c.issuer).toBe("https://example.okta.com");
    expect(c.clientId).toBe("cid");
    expect(c.audience).toBe("aud");
    expect(c.configured).toBe(true);
    expect(c.authDisabled).toBe(false);
  });

  it("is unconfigured when issuer or client id is missing", () => {
    expect(getAuthConfig({ DEV: false }).configured).toBe(false);
    expect(
      getAuthConfig({ VITE_OIDC_ISSUER: ISSUER, DEV: false }).configured
    ).toBe(false);
    expect(
      getAuthConfig({ VITE_OIDC_CLIENT_ID: "cid", DEV: false }).configured
    ).toBe(false);
  });

  it("honors VITE_AUTH_DISABLED only in dev builds", () => {
    expect(
      getAuthConfig({ VITE_AUTH_DISABLED: "true", DEV: true }).authDisabled
    ).toBe(true);
    expect(
      getAuthConfig({ VITE_AUTH_DISABLED: "true", DEV: false }).authDisabled
    ).toBe(false);
    expect(getAuthConfig({ DEV: true }).authDisabled).toBe(false);
  });
});

describe("PKCE", () => {
  it("matches the RFC 7636 Appendix B S256 vector", async () => {
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    await expect(createCodeChallenge(verifier)).resolves.toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    );
  });

  it("creates a 64-char verifier from the unreserved alphabet", () => {
    const v = createCodeVerifier();
    expect(v).toMatch(/^[A-Za-z0-9\-._~]{64}$/);
    expect(createCodeVerifier()).not.toBe(v);
  });

  it("derives a different challenge per verifier", async () => {
    const c1 = await createCodeChallenge(createCodeVerifier());
    const c2 = await createCodeChallenge(createCodeVerifier());
    expect(c1).toMatch(/^[A-Za-z0-9\-_]+$/);
    expect(c1).not.toBe(c2);
  });
});

describe("discover", () => {
  it("fetches and caches the discovery document per issuer", async () => {
    const fetchFn = oidcFetch();
    const doc = await discover(config, fetchFn);
    expect(doc).toEqual(DISCOVERY);
    await expect(discover(config, fetchFn)).resolves.toEqual(DISCOVERY);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith(
      `${ISSUER}/.well-known/openid-configuration`,
      expect.objectContaining({ headers: { Accept: "application/json" } })
    );
  });

  it("throws AuthError when discovery is unreachable", async () => {
    const fetchFn = vi.fn(async () => {
      throw new Error("network down");
    });
    await expect(discover(config, fetchFn)).rejects.toBeInstanceOf(AuthError);
  });

  it("throws AuthError on a non-OK discovery response", async () => {
    const fetchFn = vi.fn(async () => ({ ok: false, status: 404 }));
    await expect(discover(config, fetchFn)).rejects.toThrow(/404/);
  });

  it("throws AuthError when endpoints are missing", async () => {
    const fetchFn = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ issuer: ISSUER }),
    }));
    await expect(discover(config, fetchFn)).rejects.toBeInstanceOf(AuthError);
  });
});

describe("id_token claim validation", () => {
  const base = {
    iss: ISSUER,
    aud: AUDIENCE,
    exp: Math.floor(Date.now() / 1000) + 600,
    sub: "user-1",
    nonce: "n1",
  };
  const opts = { issuer: ISSUER, audience: AUDIENCE, clientId: CLIENT_ID, nonce: "n1" };

  it("accepts a valid payload", () => {
    expect(validateIdTokenClaims(base, opts)).toEqual(base);
  });

  it("rejects a wrong issuer, audience, expiry, subject, or nonce", () => {
    expect(() =>
      validateIdTokenClaims({ ...base, iss: "https://other.example" }, opts)
    ).toThrow(AuthError);
    expect(() =>
      validateIdTokenClaims({ ...base, aud: "someone-else" }, opts)
    ).toThrow(AuthError);
    expect(() =>
      validateIdTokenClaims(
        { ...base, exp: Math.floor(Date.now() / 1000) - 10 },
        opts
      )
    ).toThrow(AuthError);
    expect(() =>
      validateIdTokenClaims({ ...base, sub: "" }, opts)
    ).toThrow(AuthError);
    expect(() =>
      validateIdTokenClaims({ ...base, nonce: "different" }, opts)
    ).toThrow(AuthError);
  });

  it("accepts an array audience containing the expected value", () => {
    expect(
      validateIdTokenClaims({ ...base, aud: ["other", AUDIENCE] }, opts)
    ).toEqual(expect.objectContaining({ aud: ["other", AUDIENCE] }));
  });

  it("falls back to the client id when no audience is configured", () => {
    const payload = { ...base, aud: CLIENT_ID };
    expect(
      validateIdTokenClaims(payload, {
        issuer: ISSUER,
        audience: "",
        clientId: CLIENT_ID,
        nonce: "n1",
      })
    ).toEqual(payload);
  });

  it("rejects a malformed token in decodeJwtPayload", () => {
    expect(() => decodeJwtPayload("not-a-jwt")).toThrow(AuthError);
    expect(() => decodeJwtPayload("a.b")).toThrow(AuthError);
  });
});

describe("startLogin", () => {
  it("builds an S256 authorize URL and stores pending state in sessionStorage only", async () => {
    const fetchFn = oidcFetch();
    const redirects = [];
    const url = await startLogin(config, {
      fetchFn,
      redirect: (u) => redirects.push(u),
    });
    expect(redirects).toEqual([url]);
    const u = new URL(url);
    expect(`${u.origin}${u.pathname}`).toBe(DISCOVERY.authorization_endpoint);
    expect(u.searchParams.get("response_type")).toBe("code");
    expect(u.searchParams.get("client_id")).toBe(CLIENT_ID);
    expect(u.searchParams.get("redirect_uri")).toBe("http://localhost:3000");
    expect(u.searchParams.get("scope")).toContain("openid");
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9\-_]+$/);
    expect(u.searchParams.get("state")).toBeTruthy();
    expect(u.searchParams.get("nonce")).toBeTruthy();
    // No token material anywhere in the URL.
    expect(url).not.toContain("token");
    const pending = JSON.parse(sessionStorage.getItem(STORAGE_KEYS.pending));
    expect(pending.state).toBe(u.searchParams.get("state"));
    expect(pending.nonce).toBe(u.searchParams.get("nonce"));
    expect(pending.verifier).toMatch(/^[A-Za-z0-9\-._~]{64}$/);
    expect(localStorage.length).toBe(0);
  });

  it("fails visibly when discovery fails, storing nothing", async () => {
    const fetchFn = vi.fn(async () => ({ ok: false, status: 500 }));
    await expect(
      startLogin(config, { fetchFn, redirect: vi.fn() })
    ).rejects.toBeInstanceOf(AuthError);
    expect(sessionStorage.getItem(STORAGE_KEYS.pending)).toBeNull();
  });
});

describe("buildAuthorizeUrl", () => {
  it("encodes every parameter", () => {
    const url = buildAuthorizeUrl("https://idp.example/authorize", {
      clientId: "cid",
      redirectUri: "http://localhost:3000",
      state: "s",
      nonce: "n",
      challenge: "c",
    });
    expect(url).toContain("response_type=code");
    expect(url).toContain("code_challenge_method=S256");
  });
});

async function loginAndCallback(fetchFn, tokenResponse) {
  const redirects = [];
  const url = await startLogin(config, {
    fetchFn,
    redirect: (u) => redirects.push(u),
  });
  const state = new URL(url).searchParams.get("state");
  const pending = JSON.parse(sessionStorage.getItem(STORAGE_KEYS.pending));
  return { url, state, pending, redirects };
}

describe("handleCallback", () => {
  it("exchanges a valid code+state, stores tokens in sessionStorage, strips the query", async () => {
    const fetchFn = oidcFetch({
      tokenResponse: {
        access_token: "access-1",
        refresh_token: "refresh-1",
        expires_in: 3600,
        id_token: idToken({ nonce: undefined }),
      },
    });
    // idToken helper defaults nonce to "test-nonce"; rebuild with the
    // real pending nonce once login has run.
    const { state, pending } = await loginAndCallback(fetchFn);
    fetchFn.mockImplementation(async (url) => {
      const u = String(url);
      if (u === `${ISSUER}/.well-known/openid-configuration`) {
        return { ok: true, status: 200, json: async () => DISCOVERY };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          access_token: "access-1",
          refresh_token: "refresh-1",
          expires_in: 3600,
          id_token: idToken({ nonce: pending.nonce }),
        }),
      };
    });
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    const result = await handleCallback(config, {
      search: `?code=auth-code&state=${state}`,
      fetchFn,
    });
    expect(result).toEqual({ handled: true, authenticated: true });
    expect(getStoredAccessToken()).toBe("access-1");
    expect(getStoredRefreshToken()).toBe("refresh-1");
    expect(Number(sessionStorage.getItem(STORAGE_KEYS.expiresAt))).toBeGreaterThan(
      Date.now()
    );
    expect(sessionStorage.getItem(STORAGE_KEYS.pending)).toBeNull();
    assertNoTokenInLocalStorage("access-1");
    // The token exchange sent the PKCE verifier and the code, once.
    const tokenCalls = fetchFn.mock.calls.filter(
      ([u]) => String(u) === DISCOVERY.token_endpoint
    );
    expect(tokenCalls).toHaveLength(1);
    const body = new URLSearchParams(tokenCalls[0][1].body.toString());
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("auth-code");
    expect(body.get("code_verifier")).toBe(pending.verifier);
    expect(body.get("client_id")).toBe(CLIENT_ID);
    expect(replaceSpy).toHaveBeenCalled();
    replaceSpy.mockRestore();
  });

  it("returns handled:false with no callback parameters and fetches nothing", async () => {
    const fetchFn = oidcFetch();
    await expect(
      handleCallback(config, { search: "", fetchFn })
    ).resolves.toEqual({ handled: false });
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("rejects a mismatched state and drops everything", async () => {
    const fetchFn = oidcFetch({
      tokenResponse: { access_token: "access-1", expires_in: 3600 },
    });
    await loginAndCallback(fetchFn);
    storeTokens({ accessToken: "stale", expiresIn: 3600 });
    await expect(
      handleCallback(config, { search: "?code=x&state=wrong-state", fetchFn })
    ).rejects.toBeInstanceOf(AuthError);
    expect(getStoredAccessToken()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEYS.pending)).toBeNull();
    // The token endpoint was never reached on a state mismatch.
    expect(
      fetchFn.mock.calls.some(([u]) => String(u) === DISCOVERY.token_endpoint)
    ).toBe(false);
  });

  it("handles an error response from the provider", async () => {
    const fetchFn = oidcFetch();
    await expect(
      handleCallback(config, {
        search: "?error=access_denied&error_description=Denied+by+user",
        fetchFn,
      })
    ).rejects.toThrow(/Denied by user/);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("rejects an id_token with the wrong nonce", async () => {
    const fetchFn = oidcFetch();
    const { state, pending } = await loginAndCallback(fetchFn);
    fetchFn.mockImplementation(async (url) => {
      if (String(url).includes(".well-known")) {
        return { ok: true, status: 200, json: async () => DISCOVERY };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          access_token: "access-1",
          expires_in: 3600,
          id_token: idToken({ nonce: "someone-elses-nonce" }),
        }),
      };
    });
    await expect(
      handleCallback(config, {
        search: `?code=x&state=${state}`,
        fetchFn,
      })
    ).rejects.toBeInstanceOf(AuthError);
    expect(getStoredAccessToken()).toBeNull();
    expect(pending.verifier).toBeTruthy();
  });

  it("rejects a failed token exchange", async () => {
    const fetchFn = oidcFetch({ tokenResponse: {}, tokenStatus: 400 });
    const { state } = await loginAndCallback(fetchFn);
    await expect(
      handleCallback(config, { search: `?code=x&state=${state}`, fetchFn })
    ).rejects.toBeInstanceOf(AuthError);
    expect(getStoredAccessToken()).toBeNull();
  });
});

describe("silent renewal", () => {
  it("refreshes an expired token and stores the new one", async () => {
    storeTokens({ accessToken: "old", refreshToken: "rt-1", expiresIn: -10 });
    const fetchFn = oidcFetch({
      tokenResponse: {
        access_token: "new-access",
        refresh_token: "rt-2",
        expires_in: 1800,
        id_token: idToken({ nonce: undefined }),
      },
    });
    // Refreshed id_tokens carry no nonce; strip it from the fixture.
    const payload = JSON.parse(
      Buffer.from(idToken({ nonce: undefined }).split(".")[1], "base64url").toString()
    );
    delete payload.nonce;
    const refreshed = `${idToken({ nonce: undefined }).split(".")[0]}.${b64url(payload)}.${b64url("sig")}`;
    fetchFn.mockImplementation(async (url) => {
      if (String(url).includes(".well-known")) {
        return { ok: true, status: 200, json: async () => DISCOVERY };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          access_token: "new-access",
          refresh_token: "rt-2",
          expires_in: 1800,
          id_token: refreshed,
        }),
      };
    });
    await expect(getValidAccessToken(config, fetchFn)).resolves.toBe(
      "new-access"
    );
    expect(getStoredAccessToken()).toBe("new-access");
    expect(getStoredRefreshToken()).toBe("rt-2");
    assertNoTokenInLocalStorage("new-access");
    const tokenCall = fetchFn.mock.calls.find(
      ([u]) => String(u) === DISCOVERY.token_endpoint
    );
    const body = new URLSearchParams(tokenCall[1].body.toString());
    expect(body.get("grant_type")).toBe("refresh_token");
    expect(body.get("refresh_token")).toBe("rt-1");
  });

  it("keeps the old refresh token when rotation returns none", async () => {
    storeTokens({ accessToken: "old", refreshToken: "rt-1", expiresIn: -10 });
    const fetchFn = oidcFetch({
      tokenResponse: { access_token: "new-access", expires_in: 1800 },
    });
    await expect(getValidAccessToken(config, fetchFn)).resolves.toBe(
      "new-access"
    );
    expect(getStoredRefreshToken()).toBe("rt-1");
  });

  it("drops tokens and throws when renewal is rejected", async () => {
    storeTokens({ accessToken: "old", refreshToken: "rt-1", expiresIn: -10 });
    const fetchFn = oidcFetch({ tokenResponse: {}, tokenStatus: 400 });
    await expect(getValidAccessToken(config, fetchFn)).rejects.toBeInstanceOf(
      AuthError
    );
    expect(getStoredAccessToken()).toBeNull();
    expect(getStoredRefreshToken()).toBeNull();
  });

  it("returns null without fetching when expired and no refresh token was issued", async () => {
    storeTokens({ accessToken: "old", expiresIn: -10 });
    const fetchFn = oidcFetch();
    await expect(getValidAccessToken(config, fetchFn)).resolves.toBeNull();
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("returns the stored token without fetching while it is valid", async () => {
    storeTokens({ accessToken: "good", expiresIn: 3600 });
    const fetchFn = oidcFetch();
    await expect(getValidAccessToken(config, fetchFn)).resolves.toBe("good");
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

describe("getAuthHeaders", () => {
  it("returns {} with no stored token", async () => {
    await expect(getAuthHeaders(config, oidcFetch())).resolves.toEqual({});
  });

  it("returns the bearer header for a valid token", async () => {
    storeTokens({ accessToken: "tok-1", expiresIn: 3600 });
    await expect(getAuthHeaders(config, oidcFetch())).resolves.toEqual({
      Authorization: "Bearer tok-1",
    });
  });

  it("throws AuthError and clears tokens when expired with no refresh token", async () => {
    storeTokens({ accessToken: "old", expiresIn: -10 });
    await expect(getAuthHeaders(config, oidcFetch())).rejects.toBeInstanceOf(
      AuthError
    );
    expect(getStoredAccessToken()).toBeNull();
  });

  it("renews silently via the refresh grant", async () => {
    storeTokens({ accessToken: "old", refreshToken: "rt-1", expiresIn: -10 });
    const fetchFn = oidcFetch({
      tokenResponse: { access_token: "fresh", expires_in: 3600 },
    });
    await expect(refreshAccessToken(config, fetchFn)).resolves.toBe("fresh");
    await expect(getAuthHeaders(config, fetchFn)).resolves.toEqual({
      Authorization: "Bearer fresh",
    });
  });
});

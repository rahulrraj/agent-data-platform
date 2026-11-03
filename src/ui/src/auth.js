// Generic OIDC authentication for the UI (ADR 0008, decision 1–2).
//
// Hand-rolled on purpose, not oidc-client-ts: the flow the UI needs is
// small (discovery, Authorization Code + PKCE, one code exchange, one
// refresh grant), and hand-rolling it keeps the security-critical
// choices auditable in one file — tokens only ever in sessionStorage,
// no token in any URL, no library storage defaults to fight — with no
// new dependency to keep pinned. The code is provider-agnostic: the
// only provider-specific values are the issuer / client id / audience,
// all build-time configuration (see getAuthConfig). No provider name
// appears anywhere in this module.
//
// What this module does NOT do, deliberately:
//  - It never verifies the id_token signature. Signature verification
//    against the provider JWKS is the backend API's job (ADR 0008
//    decision 3). The UI checks the id_token's iss/aud/exp/nonce/sub
//    claims before storing anything, which catches a token minted for
//    a different client, audience, or login attempt — but a forged
//    token with plausible claims would only be caught server-side.
//  - It never sends the configured audience to the authorization
//    endpoint. `audience` is not a standard OIDC authorize parameter;
//    it is used only when validating the id_token's `aud` claim.
//  - Silent renewal only works when the provider issued a refresh
//    token. With no refresh token, an expired access token means the
//    user signs in again (see getValidAccessToken).

export class AuthError extends Error {
  constructor(message) {
    super(message);
    this.name = "AuthError";
  }
}

export function isAuthError(err) {
  return err instanceof AuthError || err?.name === "AuthError";
}

// sessionStorage keys. Tokens never touch localStorage: localStorage
// persists across sessions, so an XSS hole there would keep working
// after the tab closes; sessionStorage dies with the tab (ADR 0008).
export const STORAGE_KEYS = {
  accessToken: "agent.auth.access_token",
  refreshToken: "agent.auth.refresh_token",
  expiresAt: "agent.auth.expires_at",
  pending: "agent.auth.pending",
};

// How early (before the stored expiry) a token counts as expired, so a
// token that dies mid-request is renewed instead of sent.
const EXPIRY_LEEWAY_MS = 60_000;

function storageGet(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  sessionStorage.setItem(key, value);
}

function storageRemove(key) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // storage already gone — nothing to clear
  }
}

/**
 * Build-time OIDC configuration, following the VITE_API_URL pattern:
 * Vite bakes import.meta.env into the bundle, so changing these needs
 * a rebuild. Redirect URI is always window.location.origin — no config.
 *
 * `env` is a parameter (defaulting to import.meta.env) so tests can
 * pass fake environments; production code never passes it.
 *
 * The test/dev bypass: VITE_AUTH_DISABLED=true is honored ONLY when
 * the bundle is a dev build (import.meta.env.DEV). A production build
 * ignores it, so a production bundle built without an issuer/client id
 * refuses to render the app instead of silently running open.
 */
export function getAuthConfig(env = import.meta.env) {
  const issuer = (env.VITE_OIDC_ISSUER || "").replace(/\/+$/, "");
  const clientId = env.VITE_OIDC_CLIENT_ID || "";
  const audience = env.VITE_OIDC_AUDIENCE || "";
  const authDisabled = env.VITE_AUTH_DISABLED === "true" && env.DEV === true;
  const redirectUri =
    typeof window !== "undefined" && window.location
      ? window.location.origin
      : "";
  return {
    issuer,
    clientId,
    audience,
    redirectUri,
    authDisabled,
    configured: Boolean(issuer && clientId),
  };
}

// ---------------------------------------------------------------- PKCE

const VERIFIER_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

function randomString(length) {
  const bytes = new Uint8Array(length);
  globalThis.crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += VERIFIER_ALPHABET[b % VERIFIER_ALPHABET.length];
  return out;
}

/** 43–128 chars from the unreserved alphabet (RFC 7636 §4.1). */
export function createCodeVerifier() {
  return randomString(64);
}

export function base64UrlEncode(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * S256 code challenge (RFC 7636 §4.2). Needs crypto.subtle, i.e. a
 * secure context (https or localhost) — the same constraint the app
 * already notes for crypto.randomUUID. Throws AuthError otherwise
 * instead of silently falling back to the insecure `plain` method.
 */
export async function createCodeChallenge(verifier) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new AuthError(
      "Secure context required for sign-in (crypto.subtle is unavailable)."
    );
  }
  const digest = await subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier)
  );
  return base64UrlEncode(new Uint8Array(digest));
}

// ------------------------------------------------------------- discovery

let discoveryCache = null; // { issuer, doc }

export function clearDiscoveryCache() {
  discoveryCache = null;
}

/**
 * Fetch (and cache, module-scope, per issuer) the provider's discovery
 * document. Throws AuthError on any failure — a login that cannot
 * discover its endpoints must fail visibly, never proceed blindly.
 */
export async function discover(config, fetchFn = globalThis.fetch) {
  if (discoveryCache && discoveryCache.issuer === config.issuer) {
    return discoveryCache.doc;
  }
  let res;
  try {
    res = await fetchFn(`${config.issuer}/.well-known/openid-configuration`, {
      headers: { Accept: "application/json" },
    });
  } catch (e) {
    throw new AuthError(`OIDC discovery failed: ${e.message}`);
  }
  if (!res.ok) {
    throw new AuthError(
      `OIDC discovery failed: the issuer returned ${res.status}.`
    );
  }
  const doc = await res.json().catch(() => null);
  if (
    !doc ||
    typeof doc.authorization_endpoint !== "string" ||
    typeof doc.token_endpoint !== "string"
  ) {
    throw new AuthError(
      "OIDC discovery failed: the issuer document has no authorization/token endpoint."
    );
  }
  discoveryCache = { issuer: config.issuer, doc };
  return doc;
}

// -------------------------------------------------------- id_token claims

function base64UrlDecode(segment) {
  const padded = segment.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Decode an id_token's payload WITHOUT verifying its signature (see header). */
export function decodeJwtPayload(token) {
  const parts = typeof token === "string" ? token.split(".") : [];
  if (parts.length !== 3) {
    throw new AuthError("Sign-in failed: the identity token is malformed.");
  }
  try {
    return JSON.parse(new TextDecoder().decode(base64UrlDecode(parts[1])));
  } catch {
    throw new AuthError("Sign-in failed: the identity token is malformed.");
  }
}

/**
 * Minimal claim checks before storing anything: issuer, audience,
 * expiry, subject, and — when an expected nonce is given (the code
 * exchange) — nonce. Signature verification is the backend's job.
 * `audience` falls back to the client id, which is what OIDC
 * providers put in `aud` when no audience is configured.
 */
export function validateIdTokenClaims(
  payload,
  { issuer, audience, clientId, nonce } = {}
) {
  const fail = (why) =>
    new AuthError(`Sign-in failed: the identity token ${why}.`);
  if (!payload || typeof payload !== "object") throw fail("is malformed");
  if (payload.iss !== issuer) throw fail("was issued by an unexpected issuer");
  const expectedAud = audience || clientId;
  const aud = payload.aud;
  const audOk = Array.isArray(aud)
    ? aud.includes(expectedAud)
    : aud === expectedAud;
  if (!audOk) throw fail("was issued for a different audience");
  if (typeof payload.exp !== "number" || payload.exp * 1000 <= Date.now()) {
    throw fail("has expired");
  }
  if (typeof payload.sub !== "string" || !payload.sub) {
    throw fail("has no subject");
  }
  if (nonce !== undefined && payload.nonce !== nonce) {
    throw fail("does not match this sign-in attempt");
  }
  return payload;
}

// ---------------------------------------------------------- token storage

/**
 * Store tokens from a token-endpoint response. `expiresIn` (seconds)
 * wins; otherwise the id_token expiry is used; with neither, the token
 * is treated as having no known expiry. A refresh token is only
 * overwritten when one is supplied, so a rotation-less refresh
 * response keeps the previous refresh token.
 */
export function storeTokens({ accessToken, refreshToken, expiresIn, idTokenExp }) {
  if (!accessToken) throw new AuthError("Sign-in failed: no access token.");
  storageSet(STORAGE_KEYS.accessToken, accessToken);
  if (refreshToken) storageSet(STORAGE_KEYS.refreshToken, refreshToken);
  const expiresAt =
    typeof expiresIn === "number"
      ? Date.now() + expiresIn * 1000
      : typeof idTokenExp === "number"
        ? idTokenExp * 1000
        : null;
  if (expiresAt) storageSet(STORAGE_KEYS.expiresAt, String(expiresAt));
  else storageRemove(STORAGE_KEYS.expiresAt);
}

export function getStoredAccessToken() {
  return storageGet(STORAGE_KEYS.accessToken);
}

export function getStoredRefreshToken() {
  return storageGet(STORAGE_KEYS.refreshToken);
}

export function clearTokens() {
  storageRemove(STORAGE_KEYS.accessToken);
  storageRemove(STORAGE_KEYS.refreshToken);
  storageRemove(STORAGE_KEYS.expiresAt);
}

function tokenExpired(now = Date.now()) {
  const raw = storageGet(STORAGE_KEYS.expiresAt);
  if (!raw) return false; // no known expiry — assume valid
  const expiresAt = Number(raw);
  return Number.isNaN(expiresAt) || expiresAt - EXPIRY_LEEWAY_MS <= now;
}

// ------------------------------------------------------- silent renewal

let refreshPromise = null;

/**
 * Exchange the stored refresh token for a new access token, silently
 * (no UI). Concurrent callers share one in-flight exchange. On any
 * failure the stored tokens are dropped and AuthError is thrown, so
 * the caller routes back to the login gate instead of retrying with
 * a dead refresh token.
 */
export function refreshAccessToken(config, fetchFn = globalThis.fetch) {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    const refreshToken = getStoredRefreshToken();
    if (!refreshToken) {
      throw new AuthError("Your session has expired. Please sign in again.");
    }
    const doc = await discover(config, fetchFn).catch((e) => {
      // A renewal that cannot even discover its endpoint has failed:
      // drop the dead session instead of retrying it on every call.
      clearTokens();
      throw e;
    });
    let res;
    try {
      res = await fetchFn(doc.token_endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: refreshToken,
          client_id: config.clientId,
        }),
      });
    } catch (e) {
      clearTokens();
      throw new AuthError(`Silent sign-in renewal failed: ${e.message}`);
    }
    if (!res.ok) {
      clearTokens();
      throw new AuthError("Your session has expired. Please sign in again.");
    }
    const tokens = await res.json().catch(() => null);
    if (!tokens?.access_token) {
      clearTokens();
      throw new AuthError("Your session has expired. Please sign in again.");
    }
    if (tokens.id_token) {
      // A refreshed id_token carries no nonce (no fresh login
      // attempt), so nonce is not checked here — iss/aud/exp/sub are.
      validateIdTokenClaims(decodeJwtPayload(tokens.id_token), {
        issuer: config.issuer,
        audience: config.audience,
        clientId: config.clientId,
      });
    }
    storeTokens({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      expiresIn: tokens.expires_in,
      idTokenExp: tokens.id_token
        ? decodeJwtPayload(tokens.id_token).exp
        : undefined,
    });
    return tokens.access_token;
  })();
  // Clear the shared promise once settled, whatever the outcome, so a
  // later call retries instead of reusing a rejected exchange. The
  // failure path above already dropped the tokens.
  const settled = refreshPromise.finally(() => {
    refreshPromise = null;
  });
  // Keep the original promise's rejection for callers; `settled` only
  // resets state. Return the original so a rejection is not swallowed.
  void settled.catch(() => {});
  return refreshPromise;
}

/**
 * A usable access token, or null when there is none / it is expired
 * with no way to renew. Renewal is attempted silently when a refresh
 * token exists; a failed renewal throws AuthError (tokens dropped).
 * Returns null — never throws — for "no token" and "expired, no
 * refresh token", so the caller can show the login gate.
 */
export async function getValidAccessToken(config, fetchFn = globalThis.fetch) {
  const token = getStoredAccessToken();
  if (!token) return null;
  if (!tokenExpired()) return token;
  if (!getStoredRefreshToken()) return null;
  return refreshAccessToken(config, fetchFn);
}

/**
 * Authorization header for an API call. Returns {} when no token is
 * stored (the caller is unauthenticated — api.js must not invent
 * one). Throws AuthError when a stored session has expired and
 * cannot be renewed, so api.js never calls the API unauthenticated
 * on a dead session.
 */
export async function getAuthHeaders(config, fetchFn = globalThis.fetch) {
  if (!getStoredAccessToken()) return {};
  const token = await getValidAccessToken(config, fetchFn);
  if (!token) {
    clearTokens();
    throw new AuthError("Your session has expired. Please sign in again.");
  }
  return { Authorization: `Bearer ${token}` };
}

// ------------------------------------------------------------ login flow

export function buildAuthorizeUrl(
  authorizationEndpoint,
  { clientId, redirectUri, state, nonce, challenge, scope = "openid profile email" }
) {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    scope,
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `${authorizationEndpoint}?${params.toString()}`;
}

function storePending(pending) {
  storageSet(STORAGE_KEYS.pending, JSON.stringify(pending));
}

function readPending() {
  try {
    const raw = storageGet(STORAGE_KEYS.pending);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function clearPending() {
  storageRemove(STORAGE_KEYS.pending);
}

/**
 * Start the Authorization Code + PKCE redirect. `redirect` defaults
 * to navigating the browser; tests inject a recorder. The pending
 * state/nonce/verifier live in sessionStorage and are consumed (and
 * cleared) exactly once by handleCallback.
 */
export async function startLogin(
  config,
  { fetchFn = globalThis.fetch, redirect } = {}
) {
  const doc = await discover(config, fetchFn);
  const state = randomString(32);
  const nonce = randomString(32);
  const verifier = createCodeVerifier();
  const challenge = await createCodeChallenge(verifier);
  storePending({ state, nonce, verifier });
  const url = buildAuthorizeUrl(doc.authorization_endpoint, {
    clientId: config.clientId,
    redirectUri: config.redirectUri,
    state,
    nonce,
    challenge,
  });
  if (redirect) redirect(url);
  else window.location.assign(url);
  return url;
}

function stripCallbackQuery() {
  try {
    window.history.replaceState({}, "", window.location.pathname);
  } catch {
    // history unavailable — the code is single-use server-side, and
    // the pending state is already cleared, so a stale URL cannot
    // be replayed from here.
  }
}

/**
 * Handle the provider's redirect back to the app (`?code=&state=`).
 * Returns { handled: false } when the URL carries no callback
 * parameters (a normal load). Otherwise exchanges the code once,
 * validates the id_token claims, stores the tokens, strips the query
 * string from the address bar, and returns { handled: true }.
 * Any failure throws AuthError after dropping tokens and pending
 * state, so a half-completed login never leaves usable credentials.
 */
export async function handleCallback(
  config,
  { search, fetchFn = globalThis.fetch } = {}
) {
  const query =
    search !== undefined
      ? search
      : typeof window !== "undefined"
        ? window.location.search
        : "";
  const params = new URLSearchParams(
    query.startsWith("?") ? query.slice(1) : query
  );
  const code = params.get("code");
  const state = params.get("state");
  const error = params.get("error");
  if (!code && !state && !error) return { handled: false };
  try {
    if (error) {
      throw new AuthError(
        `Sign-in failed: ${params.get("error_description") || error}.`
      );
    }
    if (!code || !state) {
      throw new AuthError("Sign-in failed: the response was incomplete.");
    }
    const pending = readPending();
    if (!pending?.verifier || !pending.state || !pending.nonce) {
      throw new AuthError(
        "Sign-in failed: no matching sign-in attempt was found. Please sign in again."
      );
    }
    if (state !== pending.state) {
      throw new AuthError(
        "Sign-in failed: the response did not match this sign-in attempt."
      );
    }
    const doc = await discover(config, fetchFn);
    let res;
    try {
      res = await fetchFn(doc.token_endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          code,
          redirect_uri: config.redirectUri,
          client_id: config.clientId,
          code_verifier: pending.verifier,
        }),
      });
    } catch (e) {
      throw new AuthError(`Sign-in failed: ${e.message}`);
    }
    if (!res.ok) {
      throw new AuthError(
        `Sign-in failed: the token exchange was rejected (${res.status}).`
      );
    }
    const tokens = await res.json().catch(() => null);
    if (!tokens?.access_token) {
      throw new AuthError("Sign-in failed: no access token was returned.");
    }
    let idTokenExp;
    if (tokens.id_token) {
      const payload = validateIdTokenClaims(decodeJwtPayload(tokens.id_token), {
        issuer: config.issuer,
        audience: config.audience,
        clientId: config.clientId,
        nonce: pending.nonce,
      });
      idTokenExp = payload.exp;
    }
    clearTokens();
    storeTokens({
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      expiresIn: tokens.expires_in,
      idTokenExp,
    });
    return { handled: true, authenticated: true };
  } catch (e) {
    clearTokens();
    throw e instanceof AuthError
      ? e
      : new AuthError(`Sign-in failed: ${e.message}`);
  } finally {
    clearPending();
    stripCallbackQuery();
  }
}

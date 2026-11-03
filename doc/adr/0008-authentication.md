# ADR 0008: Authentication — UI login, bearer tokens, and token forwarding to downstream APIs

Date: 2026-11-03

Status: **Accepted 2026-11-03 — implemented 2026-11-03** (backend
auth, token forwarding, UI login gate; local only, not pushed or
deployed). Live verification against a real Okta tenant is still
pending: live login, real JWKS, real token forwarding.
Provider decision resolved 2026-11-03: Okta first, extendable (see
decision 1).

## Context

The application has no authentication of its own. Verified 2026-11-03:

- `controllers/http.py` states it plainly: anyone who can reach the API
  can read or append any session id they know. Session ids are required
  and unguessable, but binding a session to an authenticated principal
  is open, pending the auth-design decision. No auth system is invented
  there.
- Lambda: the Function URL defaults to `AWS_IAM` (signed requests);
  `NONE` is dev-only. A browser cannot sign `AWS_IAM` requests, so with
  the default the CloudFront UI cannot call the API directly — the
  signing-front-door / separately-authenticated-path decision (the
  C-1 × C-5 tension from the 2026-10-24 review) is still open.
- EKS: the Ingress has no auth; its manifest notes the API has no auth
  of its own.
- The agent acts with the service's credentials (least-privilege
  service creds); per-user authorization (`allowed_roles`) is planned,
  not built.

Rahul's direction (2026-11-03): the UI forces the user to
authenticate, then uses that token for every subsequent call, and the
tool passes the token to the API it calls — i.e. the user's identity
propagates end to end, UI → agent API → downstream data API.

## Decision

1. **OIDC, Okta first, extendable.** The UI authenticates with the
   standard Authorization Code flow + PKCE. **Okta is the first
   identity provider (decided 2026-11-03)**, but the design stays
   provider-agnostic: the API validates standard JWTs against the
   provider's JWKS, and issuer, client id, and audience come from
   config. All Okta specifics live in configuration (and, if needed, a
   thin provider adapter) — never in the controller, service, or
   executor code — so adding a second provider later is a config
   change, not a core change.
2. **The UI forces authentication.** No token means the login flow —
   no API calls are made unauthenticated. Every call carries
   `Authorization: Bearer <token>` (health/ready excepted, see 6). A
   401 drops the token and re-authenticates. Tokens live in
   `sessionStorage` with silent renewal; never `localStorage` (an XSS
   hole there persists across sessions). This trades some XSS exposure
   for SPA simplicity; CSP and script hygiene are follow-ups, not part
   of this decision.
3. **The API validates every data request.** JWT checks: signature via
   cached JWKS (keys cached with rotation respected, never fetched
   per request), issuer, audience, expiry, `sub` present. Missing,
   invalid, or expired token → 401. Auth infrastructure unreachable →
   503, distinct from "invalid". Startup is fail-closed: if neither
   `AUTH_ISSUER` nor an explicit dev opt-out is configured, the app
   refuses to start rather than silently running open (the C-1 class
   of bug). See open decision 3.
4. **Sessions bind to the principal.** The controller extracts the
   principal (`sub`); the service binds a session to its first
   writer's principal and rejects a different principal on read,
   append, or reset with 403. Ownership is persisted with the
   conversation. With auth disabled (dev opt-out only), the owner is
   empty and binding is off — the current behavior.
5. **Token forwarding to downstream APIs is opt-in per capability.**
   An HTTP capability may set the top-level registry key
   `forward_user_token: true` (validated at load, HTTP-only, the same
   pattern as the trimming keys from item 3 of the Bedrock work). When
   set and a user token is present, the HTTP executor sends
   `Authorization: Bearer <user token>` to the downstream API. If the
   connection also defines a static `api_key`, that is a load-time
   config error — two identities for one call is ambiguous (see open
   decision 2). The token is never logged (the executor's redactor
   already masks `Authorization`/`Bearer`), never appears in tool
   results or model context, and a downstream 401/403 surfaces as a
   capability error (502 to the caller) with no token details.
   SQL/Snowflake kinds keep service credentials only — forwarding does
   not apply to them.
6. **Transport interplay — this resolves C-1 × C-5.** With app-level
   JWT enforcement in place, the Function URL may be `NONE` behind
   CloudFront + WAF for browser traffic, because the app itself is the
   gate; `AWS_IAM` remains for direct machine access. `/api/health`
   and `/api/ready` stay public (ops endpoints, no data). Non-UI
   callers authenticate via the identity provider's
   client-credentials flow — `AWS_IAM` alone no longer suffices for
   data endpoints (see open decision 4).
7. **Plumbing: per-turn request context, never the model.** The token
   travels controller → service → tool → executor via a per-turn
   request context (a context variable set at the controller boundary
   and read by `execute_capability`), not via tool arguments or the
   conversation history — the model must never see the token. The
   context carries the principal and the raw token; tools never log it.
8. **Identity first, authorization later.** This step establishes
   identity and propagation only. `allowed_roles`, per-user scoping,
   and audit attribution build on the principal afterwards; they are
   not part of this decision.

## Consequences

- The UI cannot work without an identity provider configured;
  deployment needs issuer/client/audience plus the CloudFront/WAF
  notes from decision 6.
- Downstream APIs that receive forwarded tokens see the real user and
  can enforce per-user rules; APIs without the opt-in keep service
  credentials and see only the service.
- Session ids become per-user: sharing one across users stops working
  (403). That is intended.
- Token expiry mid-turn (long SSE streams are validated at turn
  start): the turn ends; the UI re-authenticates and the user retries.
- Auth does not replace the model allowlist (ADR 0007); both stand —
  identity controls *who*, the allowlist controls *what they can run*.
- The gateway/service layer gains a principal concept; the
  conversation store gains an owner field. Both are additive: with
  auth disabled, behavior is today's behavior.

## Open decisions (need Rahul)

1. The `api_key` + `forward_user_token` load-time error rule
   (decision 5) — confirm, or choose precedence instead (e.g. the
   user token wins, or the static key wins).
2. The fail-closed startup rule (decision 3) — confirm, or allow
   unauthenticated local runs without the explicit opt-out.
3. Machine-client flow: client-credentials via the IdP (decision 6) —
   confirm, or keep an API-key path for service callers.
4. UI token storage (`sessionStorage` + silent renewal, decision 2) —
   confirm.

Resolved 2026-11-03: identity provider — Okta first, with the
extendability requirement in decision 1. All four remaining decisions
resolved the same day (yes to all four as drafted): api_key +
forward_user_token is a load-time error; fail-closed startup;
machine clients via IdP client-credentials; UI tokens in
sessionStorage + silent renewal.

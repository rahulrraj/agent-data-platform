# Architecture reference — first-time-right for the next project

A checklist distilled from building this platform, so the next project
gets the boundaries right the first time instead of finding them in
review. It describes the **fixed state** of this codebase, not just the
problems: where a rule says "default X", that is what this project now
does.

## How to use it

- Read it once before scaffolding a new project; then work through the
  checklists at each stage — design, first deploy, first UI, first
  external capability.
- Each rule is three parts: the principle, one line on what went wrong
  here, and a checklist item to copy into the new project's plan.
- This doc does **not** replace the governing docs. Structure and layering
  live in `doc/hexagonal-architecture.md`; platform qualities live in
  `doc/platform-principles.md`; settled decisions live in `doc/adr/`.
  Where this doc overlaps them, it points at them rather than restating
  them.
- Full evidence for every rule below — file paths, counts, and the
  unfixed remainder — is in [the 2026-10-24 code review](code-review-2026-10-24.md)
  (8 Criticals, 31 Majors, 48 Minors, plus Nits, across six lenses).
  That report is the detail; this doc is the habit.

---

## 1. Security defaults

### 1.1 Auth before exposure — never ship a public-by-default API

**Principle.** An endpoint that can spend model budget or reach data
must require authentication by default; unauthenticated mode is a
local-dev switch, never a deploy default.

**Went wrong:** the Lambda Function URL defaulted to `NONE` and the app
itself had no auth middleware, so the deployed API was world-reachable.

- [ ] Default every public entry point to authenticated access
      (here: Function URL auth defaults to `AWS_IAM`).
- [ ] Document the unauthenticated option as dev-only, with a warning
      at the point of use — not buried in a README paragraph.
- [ ] Add app-level auth (or an API-key/JWT check) before relying on
      edge auth alone; edge config and app config must not each assume
      the other is doing it.

### 1.2 CORS is an allowlist, not a wildcard

**Principle.** CORS origins are configuration naming the real UI origin;
`*` is only acceptable when the API is intentionally public and
unauthenticated — which, per §1.1, it never is by default.

**Went wrong:** `allow_origins=["*"]` shipped alongside the
unauthenticated default.

- [ ] Drive CORS origins from a setting (here: `CORS_ORIGINS`), default
      empty/restrictive, set explicitly per deployment.
- [ ] Verify the deployed origin list, not just the code default — a
      correct default undone by a stack variable is still wrong.

### 1.3 Secrets: sensitive flags, masked logs, no state in git

**Principle.** Anything that can carry credentials is marked sensitive
end to end — variable, plan output, env var, log line, state file —
and secret-bearing files are gitignored, not merely uncommitted.

**Went wrong:** `data_connections` (which carries DB credentials and API
keys) was not marked sensitive, and `*.tfstate*` was not gitignored.

- [ ] Mark every credential-carrying variable `sensitive = true`,
      including composite ones (connection JSON, DSNs) — not just the
      obvious password/key fields.
- [ ] Add `*.tfstate*` (and equivalent state files) to `.gitignore`
      from day one; verify no secret template with a real-looking
      default credential is tracked (untrack, don't just ignore).
- [ ] Mask computed secret values in CI logs (`::add-mask::`), not only
      verbatim registered secrets — composed values (DSNs, secret
      commands) leak otherwise.
- [ ] Prefer a secrets manager / parameter store over plaintext env
      vars for production; treat plaintext env as a dev concession with
      an expiry (a TODO, not a permanent design).

### 1.4 Read-only enforcement must be enforcement, not a label

**Principle.** A `mode: read` flag is a claim; enforcement is a mechanism
at the execution layer (or the connection itself) that refuses writes
even when the registry entry is wrong.

**Went wrong:** `mode: read` was checked as a label while SQL `DELETE`
statements executed and HTTP `POST` dispatched under it.

- [ ] Enforce read-only at the connection level where possible
      (read-only role with no write grants — a requirement, not advice).
- [ ] Add a statement guard as a backstop: reject SQL whose first
      keyword is not `SELECT`/`WITH`; reject non-`GET` HTTP methods
      when `mode == "read"`.
- [ ] Test the refusal paths explicitly (`mode=read` + `DELETE` →
      refused; `mode=read` + `POST` → refused), not just the happy path.
- [ ] Keep retries safe by construction: once nothing mutating is
      reachable, a retried turn cannot double-apply (see §2.1).

### 1.5 Session isolation: the server owns identity, never the client

**Principle.** A client-supplied session id is a hint, not an identity.
History belongs to an authenticated principal; without auth, there is
no isolation to claim.

**Went wrong:** any caller could read or append any conversation,
including a shared `"default"` session.

- [ ] Require an explicit `session_id` (no `"default"` fallback) and
      bind it server-side to the authenticated principal once auth
      exists; until then, document that history is not isolated.
- [ ] Generate session ids with `crypto.randomUUID()` (or equivalent),
      never `Math.random()` + timestamp — predictable ids are an
      enumeration aid.
- [ ] Treat "who can query what data" (per-user capability scoping) and
      "whose history can they see" (session isolation) as two separate
      gaps; fixing one does not fix the other.

### 1.6 Encode and validate every value that reaches a downstream system

**Principle.** Values from the model, the registry, or config are
untrusted input to the next system: encode them for that system's
syntax and validate the target before calling it.

**Went wrong:** HTTP path parameters were interpolated unencoded
(`../admin`, `?admin=true` could alter path/query); HTTP connection
targets had no scheme/host validation; SQL Server omitted optional
params were compressed out, silently misbinding later values.

- [ ] Percent-encode each path parameter (`quote(str(v), safe="")`)
      before interpolation; reject `/` in values unless a capability
      explicitly opts into multi-segment paths.
- [ ] Validate connection targets at startup: https-only, reject
      loopback/link-local/RFC1918 unless explicitly allowlisted for dev.
- [ ] Build positional SQL bindings over *all* declared parameters
      (`None` for missing optionals); never compress out absent ones,
      and check placeholder count against argument count.
- [ ] Sanitize or flag instruction-like phrasing in registry
      descriptions (including importer output from third-party specs)
      before it reaches model context; human review is the control,
      automated flagging is the backstop.

---

## 2. Reliability

### 2.1 Retry classification must match the real SDK errors — and be tested

**Principle.** A retry classifier is only correct if it matches the
exception types the installed SDKs actually raise after their own
wrapping; classification written from a provider's docs, untested, is
a guess.

**Went wrong:** the Gemini classifier matched `google.api_core`
exceptions that neither SDK ever raises (one wasn't even installed),
so 503/429 failures were never retried; the HTTP executor's 429/5xx
branch was unreachable because the error was raised without a status.

- [ ] For each provider × framework pair, write a unit test feeding the
      *wrapped* provider error through `generate` and asserting
      retry/translation — one test per gateway, mirroring the Bedrock
      throttling test that caught its own case.
- [ ] Raise status-carrying exceptions from executors so the retry
      layer can classify them; test the retry path end to end
      (stub returns 500, 500, 200 with `max_attempts=3` → success,
      3 calls).
- [ ] Give embedding gateways the same retry treatment as model
      gateways and executors — discovery dying on the first transient
      blip is a retry gap, not a different category.
- [ ] Never let a fallback string invite hallucination: "temporarily
      unavailable … answer from what you know" must become "say you
      cannot look it up".

### 2.2 Bound every outbound call and every history

**Principle.** Unbounded inputs fail permanently, not once: a history
that exceeds the context window is persisted and replayed, so every
later request to that session fails identically.

**Went wrong:** no turn cap, token cap, or summarization existed, and
there was no session-reset endpoint — one long conversation bricked
its session with no recovery short of DB surgery.

- [ ] Enforce a history cap in the service layer (last N turns — here:
      a 40-turn gateway cap — or a token budget, dropping oldest
      non-system turns) *before* handing history to the gateway.
- [ ] Provide a session-reset operation from the start; test that a
      conversation with N+1 turns delivers ≤N turns to the gateway.
- [ ] Set explicit timeouts on every model construction (match the
      120 s precedent set by the Ollama adapter); a gateway without a
      timeout contradicts "bound every outbound call"
      (`platform-principles.md` §2).
- [ ] Persist tool results (or a compacted summary) in conversation
      turns and re-seed them in framework-native form — otherwise
      multi-turn chaining ("what about the second order?") silently
      loses context the final answer didn't summarize.

### 2.3 Typed port errors on every boundary, not just the model one

**Principle.** Every port gets its own error type; services translate
all of them; controllers map them to transport statuses. A boundary
without a typed error leaks raw failures or, worse, silent success.

**Went wrong:** four of six ports had no error class, so store failures
surfaced as a bare 500 on one endpoint and a 200 with an empty body on
another — the same failure, two wrong answers.

- [ ] Define one error class beside each port (following
      `ModelGatewayError` / `CapabilityExecutorError`); translate at the
      end of each adapter method; catch all of them in the service.
- [ ] Never let a stream endpoint return 200 with an empty body on
      failure: emit an error event and terminate the stream explicitly.
- [ ] Narrow broad `except Exception` in gateways to exceptions
      originating from the model call; log the original exception type
      distinctly so provider 502s can be triaged from code bugs.
- [ ] Return generic failure messages to the model/user and log detail
      server-side (with a redaction pass for DSN-like patterns) —
      upstream error bodies can carry server names and SQL fragments.

### 2.4 Readiness must check what it claims; health is liveness only

**Principle.** `/api/ready` is a load-balancer signal: it must probe
every dependency a turn actually needs, and probe writability where it
claims writability. `/api/health` is liveness and nothing more.

**Went wrong:** readiness probed only model `ping()` + store `ping()`,
while Gemini/Bedrock pings were key-presence only, the store ping was
`SELECT 1` (readable, not writable), and embedding/registry were never
probed — so "ready" could mean "every turn will fail".

- [ ] Probe the embedding gateway and the registry in `readiness()`
      (embedding ping may be a cache-hit check to avoid quota burn).
- [ ] Make the store probe a real write probe (write-then-delete, or a
      probe-row write); document honestly where a probe is config-only
      rather than live.
- [ ] Keep `/api/health` cheap and synchronous-safe; keep readiness
      separate and never conflate the two in the UI (see §5.3).

### 2.5 Session consistency: save before you call, persist partials

**Principle.** History writes must not depend on turn success: save the
user turn before invoking the gateway, persist partial assistant text
on failure, and never let two backends disagree about the same failure.

**Went wrong:** on gateway failure the memory backend kept the failed
user turn while SQL backends lost it; aborted streams discarded both
sides server-side while the UI kept its local copy; concurrent turns
on one session overwrote each other (upsert, no version/lock).

- [ ] Save the user turn before the gateway call, or save
      unconditionally in a `finally`; on stream failure, persist the
      partial assistant text with a marker.
- [ ] Add optimistic concurrency (version column / conditional write)
      or per-session locking for concurrent turns on one session.
- [ ] Handle corrupt stored history gracefully (log + return an empty
      conversation or a dedicated error with a reset path) — one bad
      row must not brick a session forever.
- [ ] Never block an async event loop with a synchronous model turn:
      make the endpoint a sync `def` (threadpool) or run the service
      via `to_thread`; a stalled loop stalls health/readiness probes
      and cascades into load-balancer eviction.

### 2.6 Embedding-cache care: validate what you reuse

**Principle.** A cache is only safe if a stale or mismatched entry is
detected, not silently reused: dimension, model identity, and
completeness all need checks.

**Went wrong:** a same-name model change was invisible (mismatched
dimensions were zip-truncated into wrong rankings); a short embed
result silently dropped capabilities and then failed every discovery
with a misleading message.

- [ ] Store vector dimension per cache row and validate on load (or
      store a model-version fingerprint); reject mismatches loudly.
- [ ] Assert `len(fresh) == len(pending)` after embedding; a short
      result is an error, never a partial index.
- [ ] Prune rows for models no longer in use (or add a cache-compact
      step) so stale rows don't accumulate across model switches.

---

## 3. Delivery and deployment

### 3.1 State backend before the first deploy — not after

**Principle.** Remote state is a precondition of a repeatable pipeline,
not a hardening step: local state on an ephemeral runner makes the
second deploy fail by design.

**Went wrong:** both stacks had no backend block, so deploy #2 tried to
re-create existing resources and failed with `AlreadyExists`.

- [ ] Add the remote backend (here: S3 + DynamoDB lock, `encrypt =
      true`, created out-of-band) to every stack *before* the first
      pipeline deploy; never paper over with state-file artifacts.
- [ ] Add `*.tfstate*` to `.gitignore` in the same commit (see §1.3).

### 3.2 Bootstrap ordering: create the target before you push to it

**Principle.** Every deploy step's prerequisites must exist before the
step runs — in the workflow, not in a manual README workaround.

**Went wrong:** workflows pushed images to ECR repos that `terraform
apply` hadn't created yet, and applied k8s Deployments before the
Secret they reference existed.

- [ ] Add an idempotent bootstrap step (create-if-missing, or a prior
      targeted apply) for every registry/bucket/queue a later step
      writes to.
- [ ] Order k8s applies: Secret (and ConfigMap) before Deployments;
      add `--timeout` to every `rollout status` so a never-ready
      rollout fails promptly instead of hanging to the job timeout.
- [ ] Install every tool the workflow invokes (`terraform` via
      `setup-terraform` with a pinned version, `setup-node` with npm
      cache, version-pinned `kubectl` matching the cluster minor) —
      never rely on a runner happening to preinstall it.

### 3.3 The image must contain what the app loads at startup

**Principle.** If the app eagerly loads a file at boot, that file must
be in the image — verified by a pod-start smoke test, not by image
build success.

**Went wrong:** the base image omitted `registry/`, which the app
loads eagerly, so EKS pods crashed before serving anything while the
workflow reported a green image build.

- [ ] Copy every eagerly-loaded path (`registry/` here) into every
      image that runs the app; add a pod-start smoke test to the
      pipeline.
- [ ] Add a `.dockerignore` (root and per-image) excluding
      `node_modules`, `dist`, `.git/`, `.venv/`, `__pycache__/`,
      `*.tfstate`, `.terraform/` — otherwise host artifacts overwrite
      clean installs (wrong-platform native binaries included) and
      bloat the build context.
- [ ] Pin base images by digest (or at least minor version), run
      non-root where the runtime allows, and pin the build platform
      (`--platform linux/amd64` for an x86_64 Lambda) so a developer's
      machine can't silently produce the wrong architecture.

### 3.4 Stack naming: stacks that can coexist must not collide

**Principle.** Resource names must include the stack (or require
distinct environments per stack); a shared prefix across stacks is a
second-deploy failure waiting for the second stack.

**Went wrong:** Lambda and EKS stacks shared a name prefix, so both
would create the same RDS identifier and ECR repos.

- [ ] Include the stack in `name_prefix` (e.g. `…-lambda` / `…-eks`)
      or require distinct `environment` values per stack — and document
      whether stacks are alternatives or co-deployable.
- [ ] Surface module tuning knobs at stack level (instance class,
      Multi-AZ, deletion protection, backup retention, AZ count) so
      operators can tune without editing stack internals.
- [ ] Set sane data durability defaults in the module: backup
      retention (e.g. 7 days), `gp3` storage, and stack-level overrides
      for `deletion_protection` / `skip_final_snapshot` — a dev default
      that silently drops the store on destroy must be an explicit
      choice, not an accident.

### 3.5 UI/API wiring is part of the deploy, not an afterthought

**Principle.** A deployed UI that cannot reach its API is a failed
deploy, even if every job is green: wire the API origin into the UI
build (or the front-door routing) and verify it post-deploy.

**Went wrong:** the Lambda UI was built without `VITE_API_URL`, so it
called relative `/api/*` against a CloudFront origin serving only S3
(HTML for `/api/chat`, not JSON); the EKS Ingress host was never
substituted from its placeholder, so a green rollout served no traffic.

- [ ] Build the UI *after* reading stack outputs, with
      `VITE_API_URL=<function_url>` (trailing slash trimmed), or add a
      CloudFront `/api/*` behavior pointing at the API origin.
- [ ] Substitute the Ingress host from a variable (`INGRESS_HOST`) in
      the pipeline — never leave a placeholder host in a deployed
      manifest — and add a post-deploy check that the Ingress gets an
      address and routes traffic.
- [ ] Normalize build-time API URLs (strip trailing slashes) and
      document that Vite values are build-time: changing the origin
      requires a rebuild, unless a runtime-config mechanism is added.
- [ ] Give a standalone UI image an `/api` route (nginx proxy location
      or documented front-door requirement); without one, `/api/health`
      falls through to `index.html` with HTTP 200 and produces a
      misleading "offline" error instead of a routing error.

### 3.6 Deploy gates: no auto-approve, no tag-pinned actions, no surprise applies

**Principle.** Deploys to shared infrastructure need a human gate, a
reviewed plan, and reproducible tooling; a push to `main` must not
silently re-apply infrastructure, roll pods, and wipe a static site.

**Went wrong:** both deploy workflows auto-approved on push to `main`
with no `paths:` filter, no environment protection, and tag-pinned
(rather than SHA-pinned) actions.

- [ ] Pin third-party actions by SHA (with Dependabot/Renovate to keep
      them fresh).
- [ ] Add a protected `environment:` with required reviewers to deploy
      jobs; split plan → review → apply (plan artifact uploaded, apply
      consuming it) instead of bare `-auto-approve`.
- [ ] Add `paths:` filters per stack so a docs-only or UI-only push
      doesn't trigger full infra applies.
- [ ] Validate cross-field configuration at plan time: required keys
      per provider (Gemini key when provider is Gemini, Bedrock invoke
      enabled when provider is Bedrock), node `min ≤ desired ≤ max` —
      a config that applies cleanly and fails on first use is a
      validation gap, not a runtime error.
- [ ] Treat empty-string env overrides as a bug class: a present-but-
      empty variable overriding a `"{}"` default (here:
      `DATA_CONNECTIONS=""` crashing every cold start) must be
      normalized in settings parsing *and* defaulted in the workflow.
- [ ] Scope OIDC trust to the deploy workflows on `main`, not
      `repo:*`; bound Lambda concurrency against the database tier
      (reserved concurrency or a proxy) before any load-bearing use.

---

## 4. API and data-access design

### 4.1 Validate at load, fail fast with a useful error

**Principle.** Configuration errors should surface at startup with the
offending value named — not at first real use, and not as a bare
`KeyError` or `ValueError` with no context.

**Went wrong:** unknown registry kinds, modes, and missing kind-specific
specs were accepted at load and failed only on first execution; numeric
settings parsed unguarded (`RETRY_MAX_ATTEMPTS=abc` → bare `ValueError`;
negative delay passed startup and failed at first retry, misattributed
to the provider).

- [ ] Validate the registry at load: `kind` in its allowlist, `mode`
      in its allowlist, kind-specific spec keys present, duplicate ids
      rejected (the existing duplicate-id check is the model to follow).
- [ ] Validate parameter types at registry load: reject unknown types
      instead of silently defaulting to `string` (a typo like
      `interger` must fail at load, not surface as a downstream SQL
      type error).
- [ ] Parse numerics in `try/except` naming the variable
      (`RETRY_MAX_ATTEMPTS must be an integer, got …`) and range-check
      them (`retry_max_attempts >= 1`, `retry_base_delay >= 0`, port in
      1–65535); test the invalid values, not just the valid ones.
- [ ] Validate connection names referenced by the registry against the
      configured connections when available, at load — not at first
      execution.

### 4.2 Parameter handling: coerce safely, cap results, never guess

**Principle.** The model supplies values, not names: validate names
against the registry, coerce only safe cases with a clear error on
failure, and cap everything that enters model context.

**Went wrong:** strict `isinstance` rejection cost a tool roundtrip for
`"123"` as an integer, while HTTP results were returned uncapped
(thousands of objects) even though the SQL path correctly capped at 50
rows with a `truncated` flag.

- [ ] Coerce safe cases (`"123"` → `123`, `"true"` → `True`) with a
      failed-coercion error; reject unknown parameters outright.
- [ ] Apply the same result cap to every executor kind (truncate lists
      to 50 with a `truncated` flag; apply `fields` trimming to list
      items as well as dicts) and document the cap per kind. (HTTP
      capabilities may opt in per capability to 500 with `list_paths` /
      `keep` — see `doc/data-capabilities.md`; the 50 default and the SQL
      cap are unchanged.)
- [ ] Enforce a margin rule on discovery (see §4.3) rather than
      returning a single best match with no confidence signal.

### 4.3 Discovery: calibrate on real embeddings, with a margin

**Principle.** A similarity threshold calibrated on a keyword fake is
uncalibrated: dense real embeddings give unrelated pairs 0.2–0.5, so
the threshold, the indexed text, and the confidence signal must all be
derived from real-embedding measurements.

**Went wrong:** the 0.30 threshold was pinned only against a
token-overlap fake (unrelated ≈ 0), returned only the single best
match with no score or runner-up, and embedded descriptions only.

**Fixed state (do not regress it):** threshold recalibrated **0.30 →
0.60 with margin δ = 0.05**, measured on live Gemini embeddings
(unrelated ≤ 0.53, weakest true paraphrase 0.63).

- [ ] Calibrate against a small labeled set of real-embedding
      similarities (paraphrases + near-miss distractors + unrelated
      prompts) and record the calibration in a test — re-run it when
      the embedding model changes.
- [ ] Require best − second-best > δ, or return top candidates with
      scores when within δ; never return a bare single match with no
      confidence signal.
- [ ] Embed `description + parameter descriptions` as the indexed text,
      not the description alone, so phrasings using parameter synonyms
      can match.
- [ ] Guard the chaining contract: thread a per-turn "discovered ids"
      set through tool state and reject `execute_capability` ids not
      discovered this turn (advisory-first if false rejections are a
      concern) — the system prompt's "never guess" rule needs an
      enforcement point, not just text.

### 4.4 Registry tooling: the draft→review→merge loop must be closed

**Principle.** Generated config is only as good as its validation loop:
a draft that the loader never sees is a defect deferred to app boot.

**Went wrong:** the capability importer generated loader-valid YAML
but never validated drafts against the loader, never checked id
collisions at generation time, generated no `fields` allowlist and no
example values, and was GET-only.

- [ ] Add a `--validate` step to generator CLIs that round-trips the
      draft through the real registry loader and reports id collisions
      against the live registry.
- [ ] Generate `fields` from response schemas when available and
      example values in parameter descriptions; keep the human review
      step, but make the automated checks run before it, not instead
      of it.
- [ ] Document generator tooling as offline registry tooling (not a
      runtime layer) in the repo's architecture notes, so its placement
      is recorded rather than discovered.

---

## 5. UI resilience

### 5.1 Partial + error is one state, not two failure modes

**Principle.** A stream can end three ways — complete, truncated, or
errored mid-stream — and the UI must distinguish all three. Rendering a
truncated or errored stream as "complete" (or discarding its partial
content) destroys information the user already received.

**Went wrong:** EOF without `[DONE]` was rendered as a finished answer;
a mid-stream error replaced the accumulated answer with just the error
message; an empty stream rendered as a silent empty bubble.

- [ ] Track a `sawDone` flag in the stream reader: if the reader ends
      without `[DONE]`, throw an "incomplete reply" error.
- [ ] On error, keep accumulated `content` and append the error as a
      separate note (`partial: true`, error message, retry affordance)
      — never replace content with the error.
- [ ] Render an explicit "No reply received" note when a stream ends
      with no text, no error, and no content.
- [ ] Only render "Used X" for tools after a successful `[DONE]`; on
      error or abort, render "Attempted" (or keep the live styling) —
      a failed tool call must not be presented as completed work.

### 5.2 Storage safety: validate on load, guard every write, cap growth

**Principle.** `localStorage` is untrusted, quota-limited input: load it
defensively, never let a write failure silently lose subsequent turns,
and never re-serialize unbounded history on every token.

**Went wrong:** sessions were loaded with only an outer-array check
(corrupt entries crashed rendering); `setItem` on every streamed token
had no `try/catch`, so a quota failure silently lost all later turns
on reload; theme storage was unguarded even where sessions were guarded.

- [ ] Validate each session's shape on load (drop/repair malformed
      entries); wrap *every* storage get/set (sessions and theme) in
      `try/catch` with a user-visible "storage full" warning and a
      `"light"` (or equivalent) fallback.
- [ ] Cap stored messages per conversation / total sessions, and
      throttle persistence (on stream end + periodic, not per token).
- [ ] Scope streaming state per session (or abort on session switch /
      delete): a global `streaming` flag blocks sending in a new
      conversation while the old stream is still running, and clearing
      `abortRef` unconditionally lets a stale controller clear the
      current one.

### 5.3 Health polling: distinguish checking, connected, and stale

**Principle.** A mount-time health check is a snapshot, not a status:
poll it, re-check on focus and after send failures, and never show a
stale "connected" pill after the API dies (or an "offline" flash while
the check is still in flight).

**Went wrong:** health ran once at mount; the sidebar treated
"check in flight" (`null`) as offline; readiness existed but was never
used, so a model-down API showed an enabled composer.

- [ ] Poll health (e.g. every 30 s, plus on window `focus` and after a
      send failure); distinguish `null` (checking) from `false`
      (offline) in every indicator.
- [ ] Surface readiness (or its failure) before enabling send, and
      refresh the model pill with the health poll — hiding it when the
      poll fails rather than showing a stale value.
- [ ] Guard the composer consistently: suggestion chips and other
      shortcuts must respect the same `apiOk` / disabled check as the
      textarea, or they fire doomed fetches while "disabled".

### 5.4 Stream framing: parse per the SSE spec, clean up the reader

**Principle.** A stream parser must handle the spec's edge cases
(multi-line `data:`, `\r\n` framing) and always release the reader —
latent parser bugs become live the moment a proxy normalizes framing.

**Went wrong:** a second `data:` line made the whole event unparsable
(silently swallowed); `\r\n\r\n` framing never matched, so the buffer
grew and no event was ever yielded; the reader was never cancelled on
abort.

- [ ] Parse each line of an event block, concatenating `data:` lines
      per the SSE spec; split on `/\r\n\r\n|\n\n/`; log (don't swallow)
      malformed frames in dev builds.
- [ ] Wrap the read loop in `try/finally` cancelling the reader, and
      null-check `res.body`.
- [ ] On non-OK responses, read the body and surface the server's
      `detail` (e.g. invalid message vs. model failure) instead of a
      bare status code.
- [ ] Respect the user's scroll position: only auto-scroll when already
      near the bottom; otherwise show a "jump to latest" button.
      Auto-scrolling on every token makes earlier text unreadable
      mid-stream.

---

## 6. Architecture hygiene

### 6.1 One composition root — and only one

**Principle.** Wiring lives in exactly one module: it alone reads
settings and picks adapters. Entrypoints are thin shells over it; no
adapter may import the composition root, and the root may not contain
business logic.

**Went wrong:** the Lambda handler imported the composition root from
`main`, creating a controllers ↔ main cycle and coupling the adapter
to the full wiring graph.

**Fixed state:** `composition.py` is the composition root (ADR 0005
proxy-strip exception); `main.py` is a thin CLI entrypoint over
`composition.build_app()`; `controllers/lambda_handler.py` imports
`build_app` from `composition`, never from `main`.

- [ ] Extract `build_app()` / `build_gateway()` / `build_tool_deps()`
      into the composition module on day one; every new runtime
      (Lambda, CLI, job) imports from it.
- [ ] Keep provider selection in the composition root only (one
      provider-resolution helper if two wiring functions need it, so a
      new provider is added in one place, not two).
- [ ] Record deliberate exceptions as short ADRs (§12.10 of the
      hexagonal reference) instead of silently diverging — the proxy
      strip is the sanctioned example, not a precedent for ad-hoc
      exceptions.

### 6.2 Env handling: settings own the environment, adapters never touch it

**Principle.** One module reads the environment; everything else
receives values as arguments. Import-time environment mutation is a
global side effect disguised as a module load.

**Went wrong:** adapters deleted proxy variables at import time — the
only env access outside settings, silently changing proxy behavior for
all later code in the process.

- [ ] Nothing outside `config/settings.py` reads `os.environ`, except
      the composition root's documented proxy strip (ADR 0005).
- [ ] Prefer non-mutating fixes where they exist (`httpx.Client
      (trust_env=False)` for localhost clients; per-call client args)
      and confine any unavoidable mutation to the composition root,
      before adapter imports, with lazy imports after the strip.

### 6.3 Port conformance must be checkable, not duck-typed

**Principle.** If a port defines the contract, adapters should declare
conformance to it so type checkers catch drift — and shared bases
should be sanctioned patterns, not accidental ones.

**Went wrong:** only one executor subclassed its port; the shared
bases conformed by duck-typing, so signature drift surfaced only at
runtime; the SQL shared-base pattern was undocumented while the
framework shared-base pattern had an ADR.

- [ ] Make shared bases subclass their ports (`SqlExecutorBase
      (CapabilityExecutor)`, `StrandsGatewayBase(ModelGateway)`,
      `LangGraphGatewayBase(ModelGateway)`); name bases
      `<Technology><PortName>`.
- [ ] Type dependency carriers with the port Protocols, not `Any`:
      `ToolDeps` fields are `CapabilityRepository | None`,
      `EmbeddingGateway | None`, `EmbeddingCache | None`,
      `dict[str, CapabilityExecutor]` — a wrong object must fail at
      wiring time, not inside a model tool call.
- [ ] Document each shared-base pattern where it is introduced (ADR
      or architecture note), the way ADR 0003 documents the framework
      bases.

### 6.4 Tool plugin conventions: self-contained, discovered, typed

**Principle.** Tools are the agent's peer building blocks (ADR 0001):
one folder per plugin, auto-discovered, with factory functions where
dependencies are needed — and the model-facing docstring is part of
the interface.

**Went wrong (minor, but precedent-setting):** a provider branch for a
display label lived in settings, and tool discovery registered
re-exported functions it didn't define.

- [ ] Never branch on provider/framework outside the composition root —
      not even for a label; move label construction into the
      composition root or record an explicit label-only exception.
- [ ] Restrict tool discovery to names defined in the module's own
      `__dict__`, not re-exported attributes.
- [ ] Keep tool functions pure and dependency-free; when a tool needs
      outbound I/O, route it through a core-owned port; when it needs
      dependencies, expose `create_tool(deps)` and skip the plugin
      cleanly when deps are absent.
- [ ] Write the one-line tool docstring as the model's decision input:
      specific, behavioral, and accurate — it is read at call time,
      not just at review time.

### 6.5 Parallel vs. sequential must be pinned, not inherited from SDK defaults

**Principle.** Concurrency semantics are a design decision: state them,
pin them, and test them — otherwise a dependent tool call emitted in
the same model response can race its own prerequisite.

**Went wrong:** both frameworks ran on unpinned SDK-default executors
with no test asserting either behavior, so a model emitting
`discover_capability` + `execute_capability` in one response could
race.

- [ ] Pin the tool executor explicitly (concurrent or sequential) with
      a comment stating intended semantics; add a test asserting two
      independent tool calls in one turn both run.
- [ ] Document that dependent calls must span loop iterations — the
      loop is sequential across iterations even when one response's
      calls run concurrently.
- [ ] Enforce runtime requirements, don't just document them: a Lambda
      deploy must fail fast unless the store is Postgres and the cache
      path is under `/tmp` — a documented requirement that nothing
      checks is a misconfiguration waiting for production.

---

## 7. What this doc deliberately does NOT cover

This doc is a lessons-learned checklist, not the architecture. The
following live where they belong, and this doc does not restate or
override them:

- **Structure and layering** — `doc/hexagonal-architecture.md`
  (one-way dependency rule, placement test, port rules) and
  `doc/platform-principles.md` (cloud-agnostic, resilient,
  multi-runtime, model-agnostic). If a rule here ever seems to conflict
  with those docs, those docs win and this doc needs a correction.
- **Settled decisions** — `doc/adr/0001` (tools top-level),
  `0002` (registry-driven data layer), `0003` (dual frameworks),
  `0004` (no composite capabilities), `0005` (proxy handling in the
  composition root). New exceptions follow the ADR process; they don't
  get invented in a checklist.
- **Feature scope** — what capabilities, frameworks, and runtimes the
  platform supports, and the per-capability guardrails, live in
  `doc/data-capabilities.md` and `doc/guardrails.md`.
- **Full findings** — every file path, measurement, and minor/nit in
  the review stays in [code-review-2026-10-24.md](code-review-2026-10-24.md).
  This doc intentionally omits the long tail (doc drift, a11y gaps,
  markdown renderer limits, cache policies) — not because they don't
  matter, but because a reference that repeats everything is a
  reference nobody reads.

## 8. Known open items — not decided here

Do not mistake this doc's silence (or its interim rules) for a decision
on the following. They are open TODO items from the review and the
project's TODO list, and they stay open until a dedicated decision
lands:

- **Per-user auth design** — binding sessions to principals and
  scoping capabilities per user (`allowed_roles` + request identity).
  §1.5 states the interim rule (explicit ids, no shared default);
  the real design is not settled here.
- **A2A (agent-to-agent) scope** — whether and how agents talk to other
  agents is a TODO item, not a pattern this doc endorses.
- **True incremental streaming on Lambda** — Function URL buffered SSE
  is the current behavior; RESPONSE_STREAM mode is a documented
  follow-up, not a decision made here.
- **Session→principal binding mechanics, server boot-id endpoint, and
  the nginx proxy verification** — deferred verifications from the
  review fix pass, listed here so they aren't lost, not resolved.
- **Production hardening backlog** — WAF, CloudFront access logging,
  per-AZ NAT / VPC endpoints, S3 versioning + encryption, immutable ECR
  tags, and backup/retention tuning for non-dev environments are
  called out in the review as prod items; this doc's dev defaults
  must not be read as prod approval.

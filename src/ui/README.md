# Agent UI

React chat interface for the Agent API — ChatGPT-style layout, Claude-style
warmth, Gemini-style accents. Streams replies token-by-token over `/api/chat`
(SSE) and shows tool usage inline.

## Run it

```bash
# terminal 1 — the API (from the repo root)
python -m agent_api.main        # http://localhost:8000

# terminal 2 — the UI
cd ui
npm install
npm run dev                     # http://localhost:5173
```

Vite proxies `/api/*` to `localhost:8000` in dev, so no CORS setup is needed.
For a deployed build, set `VITE_API_URL` to the API origin before
`npm run build`:

```bash
VITE_API_URL=https://api.example.com npm run build
```

## Deployment: the UI image needs an `/api` route in front of it

The nginx image serves only the static bundle — `nginx.conf` has no
`/api` location (M-31, documented rather than proxied: a template-driven
`/api` proxy could not be verified here, since no docker/nginx runtime
was available to run the image, so an untested proxy was not shipped).
Deploy it only in one of these two shapes:

1. **Behind an `/api`-routing front door** (the supported shape): the k8s
   Ingress in `deploy/k8s/ingress.yaml` routes `/api/*` to the API and
   everything else to the UI, or a CloudFront distribution with an
   `/api/*` behavior pointing at the API origin. The bundle then uses
   relative `/api/*` URLs.
2. **With `VITE_API_URL` baked in** (direct API origin; CORS must allow
   the UI origin, since the API's default CORS is wide open — see the
   main README's deployment notes).

Running the image standalone with `VITE_API_URL` unset does not work:
`/api/health` falls through to `index.html` (HTTP 200, HTML), the UI's
`res.json()` throws, and the user sees a generic "API offline" banner.

## Local vs server history (M-28)

- Conversations are persisted in the browser's localStorage (validated
  on load, capped at 100 messages per conversation / 50 conversations;
  a quota failure shows a "Storage full" warning instead of silently
  losing turns).
- Each conversation's `session_id` is a `crypto.randomUUID()` sent on
  every `/api/chat` call. Health (`/api/health`) and readiness
  (`/api/ready`) calls carry no `session_id` — they are sessionless
  probes, not conversation calls.
- **Local delete ≠ server delete.** Deleting a conversation here only
  removes the local copy; there is no server history endpoint, so the
  server keeps its copy under that `session_id` (orphaned, unreachable
  from the UI since ids are never reused). Likewise, clearing localStorage
  or restarting a memory-store server leaves the two sides diverged
  with no warning: the UI may show old messages the server no longer
  has context for, or vice versa.
- Deferred (not implementable from the UI alone): warning when a locally
  restored session predates the current server process. No existing
  endpoint reveals server start time or process identity —
  `/api/health` returns only `{status, model}` and `/api/ready` only
  `{status, failing}` — so the UI cannot detect a restart. That warning
  needs a server-side change (e.g. a process/boot id on `/api/health`),
  which is outside the UI's scope.

## What it does

- Sidebar with conversation history (persisted to localStorage), new chat,
  delete, per-conversation `session_id`s sent to the API.
- Token streaming with a blinking cursor, tool-use pills
  ("Using calculate…" → "Used calculate"), stop button (Esc), Enter to send.
- Model pill in the header from `GET /api/health`
  (e.g. `qwen2.5:1.5b · strands/ollama`); light/dark theme toggle.
- Minimal markdown: code blocks with copy button, inline code, bold/italic,
  lists. No markdown dependency.
- Offline banner when the API isn't reachable; liveness + readiness are
  re-checked every 30s, on window focus, and after a send failure, and
  sending is disabled while `/api/ready` reports not-ready. The sidebar
  distinguishes "Checking API…" from "API offline".
- Truncated streams (EOF without `[DONE]`) and mid-stream errors keep
  the partial answer and its tool pills, with an error banner and a
  Retry button, instead of replacing the answer with the error text.
- Auto-scroll only while you are already near the bottom; otherwise a
  "Jump to latest" button appears.

## Tests

```bash
npm test   # vitest: markdown renderer + SSE client + session storage
```

## Layout

```text
ui/
  package.json          react 18 + vite (no UI framework, hand-rolled CSS)
  vite.config.js        dev server + /api proxy
  index.html
  src/
    main.jsx            React root
    App.jsx             sessions, streaming state, theme, layout
    api.js              getHealth/getReadiness + SSE streamChat()
    sessions.js         localStorage validation, pruning, quota-safe save
    markdown.jsx        tiny markdown renderer
    components/
      Sidebar.jsx       conversation list
      Message.jsx       user/assistant messages, welcome screen
      Composer.jsx      autogrowing input, send/stop
      icons.jsx         inline SVGs
    styles.css          all styling, CSS variables for theming
```

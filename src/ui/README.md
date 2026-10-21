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

## What it does

- Sidebar with conversation history (persisted to localStorage), new chat,
  delete, per-conversation `session_id`s sent to the API.
- Token streaming with a blinking cursor, tool-use pills
  ("Using calculate…" → "Used calculate"), stop button (Esc), Enter to send.
- Model pill in the header from `GET /api/health`
  (e.g. `qwen2.5:1.5b · strands/ollama`); light/dark theme toggle.
- Minimal markdown: code blocks with copy button, inline code, bold/italic,
  lists. No markdown dependency.
- Offline banner when the API isn't reachable.

## Tests

```bash
npm test   # vitest: markdown renderer + SSE client, 16 tests
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
    api.js              getHealth + SSE streamChat()
    markdown.jsx        tiny markdown renderer
    components/
      Sidebar.jsx       conversation list
      Message.jsx       user/assistant messages, welcome screen
      Composer.jsx      autogrowing input, send/stop
      icons.jsx         inline SVGs
    styles.css          all styling, CSS variables for theming
```

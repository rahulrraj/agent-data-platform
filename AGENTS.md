# AGENTS.md

Repo-specific notes for working in this codebase. (General workspace
conventions live in `~/AGENTS.md`.) Detail — layout, tool/provider
conventions, operations, and sandbox quirks — lives in
`doc/developer-guide.md`; read it before changing code structure, adding
a tool, provider, or framework, or touching deployment wiring.

## Before implementing anything

Read `TODO.md` first. It is the living list of follow-ups, planned gaps,
and blocked verifications. Review it when adding any implementation,
and update it when your change closes an item, changes its status, or
creates a new follow-up. Don't let it go stale.

## Architecture

This repo follows `doc/hexagonal-architecture.md` for *structure* and
`doc/platform-principles.md` for *platform qualities*. The rules below
are mandatory, not advisory:

- One-way dependencies, no exceptions: `controllers/ -> services/ -> ports/`
  (adapters implement ports). Controllers never touch adapters.
- `composition.py` is the ONLY place that reads Settings and picks
  adapters. Only `config/settings.py` reads the environment, except the
  composition root's proxy strip (ADR 0005). `main.py` is a thin CLI;
  `controllers/lambda_handler.py` builds the app via `composition`.
- `domain/` imports nothing else. Port errors live beside the port.
- Adapters are named `<Technology><PortName>` and translate provider
  failures into port errors.
- Tools live top-level in `tools/<name>/`, one folder per plugin
  (ADR 0001). Data access goes through the capability registry, never
  per-entity tools (ADR 0002).
- Deliberate exceptions to any of this are recorded as a short ADR
  (§12.10 of the architecture doc), never applied silently.

## Running / testing

- Install: `pip install -e ".[dev]"` (Python >= 3.10). Run: `python -m agent_api.main`.
- Tests: `pytest`. Service tests use fake ports, controller tests use a fake
  service — neither needs a model. The adapter test hits real Ollama and
  skips itself when the server is down.

## Project skills

Reusable workflows live in `skills/`, colocated with the repo
(Claude-Code-style). They are NOT indexed by `muse.skill_search` — read the
SKILL.md directly when the task matches:

- `skills/capability-generator/` — draft capability YAML from a
  Swagger/OpenAPI spec: generate, review as user needs, merge, test.

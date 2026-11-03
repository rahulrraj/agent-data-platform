# Agent Development Lifecycle (ADLC)

How agent work moves from idea to running system. Small fixes may skip
stages; anything that changes what the agent can do or reach may not.

## Contents

- [Stages and gates](#stages-and-gates)
  - [1. Propose](#1-propose)
  - [2. Design](#2-design)
  - [3. Build](#3-build)
  - [4. Test](#4-test)
  - [5. Evaluate](#5-evaluate)
  - [6. Review & approve](#6-review--approve)
  - [7. Deploy](#7-deploy)
  - [8. Operate](#8-operate)
- [Checklist (copy into the change)](#checklist-copy-into-the-change)

## Stages and gates

### 1. Propose
One paragraph: what changes, why, what the user will notice. A bug fix
with no behavior change can go straight to Build.

Gate: the proposal exists in chat or in the change itself. No invented
scope — build what was asked.

### 2. Design
Architectural changes get an ADR in `doc/adr/` before code (existing rule).
Sketch data shapes, API contracts, and which guardrails apply
(`doc/guardrails.md`).

Gate: ADR merged for architectural changes; guardrails named for the rest.

### 3. Build
Follow `doc/hexagonal-architecture.md` and `AGENTS.md`. Guardrails apply
from the first commit, not as a hardening pass at the end.

Gate: conventions hold (ports, dependency direction, config only in
`main.py`).

### 4. Test
- New logic: unit tests.
- New endpoints/tools/adapters: functional tests (fakes at the boundary).
- Model-involved behavior: a live test that proves the loop works
  (the Strands streaming bug was caught this way — the missing stream test
  is why it shipped broken).

`pytest` and `npm test` must be green. A red suite blocks every later stage.

Gate: tests exist for the change and the full suites pass.

### 5. Evaluate
For behavior changes (prompts, tools, models, frameworks): run a fixed
check that proves the behavior — today that is manual (e.g. the 6×7
tool-use check over `/api/chat`); a recorded eval set is planned.
Never ship a behavior change on vibes. Never tune on the test set
(the frozen-rule discipline from the trading pipeline applies here too).

Gate: the behavior check passes, recorded where the next person can rerun it.

### 6. Review & approve
Per `doc/approvals.md`. Gated changes merge only on Rahul's explicit
approval — never on the agent's own authority, never on a subagent's claim.

Gate: approval is in the chat, from Rahul, for this change.

### 7. Deploy
Today: manual (`python -m agent_api.main`, `cd ui && npm run dev`).
A production deploy path (hosting, env, secrets) is not defined yet —
do not improvise one. Rollback is the previous commit.

Gate: the deployed revision is known and rollback is one step.

### 8. Operate
Health (`/api/health`) and readiness (`/api/ready`) probes; logs on
failure. The dev-server watchdog hook restarts what dies. Incidents get a
note in the daily log: what broke, what fixed it, what guardrail would
have prevented it.

Gate: someone would notice, and the fix path is written down.

## Checklist (copy into the change)

- [ ] Proposal exists; scope not invented
- [ ] ADR merged (if architectural)
- [ ] Guardrails from `doc/guardrails.md` applied
- [ ] Unit + functional tests added; `pytest` / `npm test` green
- [ ] Behavior check run and recorded (if behavior changed)
- [ ] Approval per `doc/approvals.md` obtained
- [ ] Deployed revision known; rollback is one step

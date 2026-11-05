# Recorded eval set

The fixed behavior check for the Evaluate stage (`agent-lifecycle.md`
§5): golden cases that pin how the agent's capability tools behave,
run offline, green or red, in seconds. A behavior change (prompt,
tool, model, framework, threshold, trimming rule) runs this set
before it ships.

## Contents

- [What it is](#what-it-is)
- [Layout](#layout)
- [Running it](#running-it)
- [Adding a case](#adding-a-case)
- [What it pins](#what-it-pins)
- [What it does not prove](#what-it-does-not-prove)
- [How it relates to the capability-generator test run](#how-it-relates-to-the-capability-generator-test-run)

## What it is

8 golden cases + 4 controls over a fixture registry, run through the
real `discover_capability` / `execute_capability` tools against a
stub API built from a fixture spec. Hermetic: keyword-embedding
discovery, no model, no network. It caught a real bug on its first
run — `example_value` returned a single object for an array-of-objects
schema, so top-level list responses were stubbed as objects and list
shape was never visible (fixed in `capability_import/schemas.py`).

## Layout

| Path | What it is |
| --- | --- |
| `evals/cases.yaml` | The recorded cases + controls (hand-written, committed) |
| `evals/registry.yaml` | Fixture registry — not the live registry; connections are remapped to the stub |
| `evals/spec.yaml` | Fixture API spec the stub is built from |
| `src/agent_api/evals/__main__.py` | CLI: `python -m agent_api.evals` |
| `tests/test_eval_set.py` | Pins the runner, the set staying green, and the gate biting |

## Running it

    python -m agent_api.evals

Exit 0 when every case passes, 1 when any fails, 2 on a usage or
file error. `--cases` / `--registry` / `--spec` run a different set
instead of the recorded one — for developing new cases before
recording them in `evals/`. A set that is not green does not get
recorded.

## Adding a case

- A new behavior gets a case in the same change that introduces it.
- Never edit a case to make it pass: fix the code. If a behavior
  change is deliberate, update the affected case in the same change,
  with the reason in the commit message — the frozen-rule discipline
  from the trading pipeline applies here too.
- Cases use the question-file format (fields documented in
  `capability_import/questions.py`). `expect_items` asserts the
  length of a top-level list result, which is how list trimming
  (`max_results` / `keep`) is pinned.

## What it pins

- Discovery resolves to the right capability (happy + paraphrase
  questions) at the 0.60 threshold, with the margin rule.
- Missing required parameters surface as `Invalid parameters:
  missing required parameter: <name>`.
- Unknown entities surface as an HTTP 404 mapping.
- Execution builds the request as the registry declares it
  (path substitution) and `fields` trimming holds — the fixture
  spec carries an `internal_note` property no capability lists in
  `fields`, and it must not survive.
- Top-level list trimming: the stub serves three items,
  `list_customers` declares `max_results: 2, keep: last`, and the
  case asserts exactly 2 items.
- Snowflake-kind discovery resolves (execution is skipped — SQL
  kinds are not stubbed).
- Unrelated questions match nothing (controls).

## What it does not prove

- Value extraction: the `params` in the cases file are the values a
  correct extraction would produce.
- Real embedding quality: the keyword harness pins ranking
  mechanics, not embedding quality. A case can pass here and fail
  under real embeddings, or the reverse. The live-embedding spot
  check (TODO.md) is the follow-up for capabilities that warrant it.
- `keep: last` ordering: the stub serves identical items, so only
  the count is pinned, not which end survives.
- Envelope / nested-list trimming (`list_paths`): covered by
  executor unit tests, not by this set.
- Anything model-involved: the manual 6×7 tool-use check over
  `/api/chat` still stands for that.

## How it relates to the capability-generator test run

Same harness (`capability_import/testrun.py`), same question-file
format, different purpose. The generator's test run is a pre-merge
check on a draft: the question file is a working artifact and is
not committed. The eval set is the committed regression net: its
cases are hand-written, recorded in `evals/`, and run on every
behavior change.

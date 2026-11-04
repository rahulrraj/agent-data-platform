# Capability Generator

Generate draft data-capability YAML from a Swagger/OpenAPI spec instead of hand-writing registry entries — then prove the draft works before it is merged.

## Generate the draft

- Run `python -m agent_api.capability_import --from swagger --spec <spec-file> --connection <name> --out draft.yaml --questions questions.yaml` from the repo root with the project venv active (the command is repo-relative, not machine-specific).
- Add `--validate` to catch loader errors and id collisions against the live registry at generation time instead of at app boot. Validation runs before anything is written: a draft with problems writes no files and exits 2.
- The generator reads OpenAPI 3.x and Swagger 2.0 (JSON or YAML), resolves local `$ref`s, and emits one `http` capability per GET operation.
- Non-GET operations are reported as skipped, never converted. The platform is read-only.
- Header and cookie parameters are skipped with a warning. Unmappable parameter types fall back to `string` with a warning. OpenAPI 3.1 type arrays resolve to the first non-null member.
- Capability IDs come from `operationId` (snake_cased, uniquified). Without one, the ID is built from method and path.
- The spec's base URL is printed as a hint for `base_url` inside `DATA_CONNECTIONS`. Server URL variables expand from their defaults.
- A malformed spec never crashes the generator: a broken operation is dropped with a report error (exit 2, nothing written) while the rest still generates. Cyclic or missing `$ref`s, composed response schemas (`allOf`/`oneOf`/`anyOf`), deprecated operations, auth requirements, and envelope responses are all reported as errors or warnings — read the report, don't just take the file.
- Table-based generation (`--from snowflake`) is planned, not built.

## Review the draft (required)

- Never merge the draft unedited. The generator does not touch `registry/capabilities.yaml` itself.
- Rewrite every description as a user need ("returns for all accounts of a customer"), not a CRUD label. Vector search matches on this text (ADR 0004).
- Replace any generated `GET /path` description. It means the spec had no summary.
- Give each parameter description an example value ("Customer ID, e.g. C-123"). The model builds the API request from this text.
- Add `fields` to HTTP capabilities whose responses should be trimmed before entering the model's context. The generator fills `fields` from a declared 200-response object schema when one exists; check them (and add them where the spec has no schema) rather than trusting them blindly. An envelope warning means the response wraps a nested list — consider `list_paths` for it (see `doc/data-capabilities.md`).
- Heed the generator's warnings: a warning about instruction-like phrasing means the spec text contains wording that would reach the model verbatim via discovery — rewrite that description before merging, never merge it as-is. A warning about auth means the operation needs a credential the connection must carry — confirm it before merging. A deprecated warning means the operation is deprecated in the spec — decide deliberately, don't merge it by inertia.
- Confirm `connection` names a key in `DATA_CONNECTIONS`. Secrets stay in the environment, never in YAML.
- If review rewrote descriptions substantially, regenerate the question file from the reviewed draft — not from the spec, which would discard the review edits: `python -m agent_api.capability_import --entries draft.yaml --spec <spec-file> --questions questions.yaml`.

## Test the draft (required, before merging)

- The `--questions` file holds one case set per capability: a happy path, a paraphrase, a missing-required-parameter case, an unknown-entity case (entity lookups only), and a list case (list-shaped responses only), plus fixed controls that must match nothing. Values come from the spec's own examples; a parameter with no example uses a placeholder and is noted in the generation output — supply a real example or edit the question file. The file is a scaffold: read it, fix anything that misreads the API, and add a question of your own where the templates miss the real phrasing.
- Run it against the reviewed draft (the draft is a valid registry file, so the run happens before anything is merged):

  `python -m agent_api.capability_import --test-run --questions questions.yaml --spec <spec-file> --registry draft.yaml`

  The run is hermetic — keyword-embedding discovery plus a stub API built from the spec's response schemas. No model, no network. It checks that each question resolves to the right capability, that the request is built as declared, that `fields` trimming holds, and that missing-parameter and 404 failures surface cleanly. Exit 0 means every case passed; exit 1 names the failures.
- Read failures by kind: a discovery failure means the description (or a parameter description) doesn't say what users ask for — rewrite the description, regenerate questions, re-run. An execution failure means the registry entry misdescribes the request — fix the entry, re-run. A 404-mapping failure means the stub disagrees with the spec — check the spec's examples and the entry's path.
- What the run does not prove: value extraction (the question file carries the values a correct extraction would produce), real embedding quality (the keyword harness pins ranking mechanics, not embedding quality), or SQL-kind capabilities (discovery only, execution skipped). A live-embedding spot check against the real gateway is the follow-up where a capability matters enough to warrant it.

## Merge and verify

- Merge only after a green test run. Append the reviewed entries to `registry/capabilities.yaml`, keeping IDs unique snake_case.
- Add one or two prompts per new capability to the prompt table in `tests/tools/test_capability_discovery.py`.
- Run the backend suite from `~/workspace/agent-data-platform` and commit the registry and the test. The question file is a working artifact — it is not committed.

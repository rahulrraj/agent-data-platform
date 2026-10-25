# Capability Generator

Generate draft data-capability YAML from a Swagger/OpenAPI spec instead of hand-writing registry entries.

## Generate the draft

- Run `python -m agent_api.capability_import --from swagger --spec <spec-file> --connection <name> --out draft.yaml` from the repo root with the project venv active (the old skill text named one developer's venv path; the command is repo-relative, not machine-specific).
- Add `--validate` to catch loader errors and id collisions against the live registry at generation time (exit 2 on problems) instead of at app boot.
- The generator reads OpenAPI 3.x and Swagger 2.0 (JSON or YAML), resolves local `$ref`s, and emits one `http` capability per GET operation.
- Non-GET operations are reported as skipped, never converted. The platform is read-only.
- Header and cookie parameters are skipped with a warning. Unmappable parameter types fall back to `string` with a warning.
- Capability IDs come from `operationId` (snake_cased, uniquified). Without one, the ID is built from method and path.
- The spec's base URL is printed as a hint for `base_url` inside `DATA_CONNECTIONS`.
- Table-based generation (`--from snowflake`) is planned, not built.

## Review the draft (required)

- Never merge the draft unedited. The generator does not touch `registry/capabilities.yaml` itself.
- Rewrite every description as a user need ("returns for all accounts of a customer"), not a CRUD label. Vector search matches on this text (ADR 0004).
- Replace any generated `GET /path` description. It means the spec had no summary.
- Give each parameter description an example value ("Customer ID, e.g. C-123"). The model builds the API request from this text.
- Add `fields` to HTTP capabilities whose responses should be trimmed before entering the model's context. The generator now fills `fields` from a declared 200-response object schema when one exists; check them (and add them where the spec has no schema) rather than trusting them blindly.
- Heed the generator's warnings: a warning about instruction-like phrasing means the spec text contains wording that would reach the model verbatim via discovery — rewrite that description before merging, never merge it as-is.
- Confirm `connection` names a key in `DATA_CONNECTIONS`. Secrets stay in the environment, never in YAML.

## Merge and verify

- Append the reviewed entries to `registry/capabilities.yaml`, keeping IDs unique snake_case.
- Add one or two prompts per new capability to the prompt table in `tests/tools/test_capability_discovery.py`.
- Run the backend suite from `~/workspace/agent-data-platform` and commit the registry and the test.

# Capability Generator

Generate draft data-capability YAML from a Swagger/OpenAPI spec instead of hand-writing registry entries.

## Generate the draft

- Run `~/workspace/gemini-agent/venv/bin/python -m agent_api.capability_import --from swagger --spec <spec-file> --connection <name> --out draft.yaml` from `~/workspace/agent-data-platform`.
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
- Add `fields` to HTTP capabilities whose responses should be trimmed before entering the model's context.
- Confirm `connection` names a key in `DATA_CONNECTIONS`. Secrets stay in the environment, never in YAML.

## Merge and verify

- Append the reviewed entries to `registry/capabilities.yaml`, keeping IDs unique snake_case.
- Add one or two prompts per new capability to the prompt table in `tests/tools/test_capability_discovery.py`.
- Run the backend suite from `~/workspace/agent-data-platform` and commit the registry and the test.

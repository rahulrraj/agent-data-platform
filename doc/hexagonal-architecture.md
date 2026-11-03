# Architecture Reference

> **Source:** Google Drive doc "# Architecture Reference" (Engineering, v1.0, last updated 2026-10-06). Copied into this repo so the architecture rules live beside the code. The Drive copy remains the original.

> **Version:** 1.0  
> **Owner:** Engineering  
> **Last updated:** 2026-10-06  
> **Basis:** Pragmatic **Hexagonal/Clean Architecture** with **DDD tactical patterns**.  
> **Purpose:** Default cross-project architecture for developers and coding agents. Optimize for predictable placement, one-way dependencies, replaceable infrastructure, testability, and minimal ceremony.  
> **Change policy:** MAJOR = dependency or placement rule changes; MINOR = backward-compatible clarifications/examples; PATCH = editorial fixes. Changes require approval from the architecture owner or delegated maintainers.

## Contents

- [1. Core Model](#1-core-model)
- [2. Default Structure](#2-default-structure)
- [3. Responsibilities and Call Rules](#3-responsibilities-and-call-rules)
  - [Mandatory direction](#mandatory-direction)
- [4. Ports, Repositories, Integrations, and Mappers](#4-ports-repositories-integrations-and-mappers)
  - [Ports are required for outbound I/O](#ports-are-required-for-outbound-io)
  - [Mapper placement](#mapper-placement)
- [5. Transactions and Errors](#5-transactions-and-errors)
- [6. Workflows and Frameworks](#6-workflows-and-frameworks)
- [7. Platform and Configuration](#7-platform-and-configuration)
- [8. Multiple Runtimes and Feature Packaging](#8-multiple-runtimes-and-feature-packaging)
- [9. Testing and Enforcement](#9-testing-and-enforcement)
- [10. Worked Examples](#10-worked-examples)
  - [10.1 API Example](#101-api-example)
  - [10.2 ETL / Batch Example](#102-etl--batch-example)
- [11. Placement Test](#11-placement-test)
- [12. Coding-Agent Checklist](#12-coding-agent-checklist)
- [13. Project Profiles](#13-project-profiles)

## 1. Core Model
Keep application behavior independent of runtimes, frameworks, databases, vendors, and deployment technology.
```text
External World
      |
      v
Controllers                 # inbound / driving adapters
      |
      v
Services / Use Cases
      |
      +----> Workflows       # optional orchestration
      +----> Domain
      +----> Ports           # outbound contracts owned by core
                  ^
                  |
              Adapters       # outbound / driven implementations
```
**Dependency direction points toward the core.**
Terminology is fixed:
- **Controller** = inbound/driving adapter: HTTP, CLI, job, event, runtime entry point.
- **Adapter** (`adapters/`) = outbound/driven implementation of a core-owned port: database, vendor API, queue, filesystem, model provider, etc.
Naming convention:
- Port names describe the core capability, for example `PortfolioRepository` or `MarketDataGateway`.
- Concrete adapter names use `<Technology><PortName>`, for example `PostgresPortfolioRepository` or `BloombergMarketDataGateway`.
## 2. Default Structure
Create only the folders the project needs.
```text
src/
├── controllers/     # inbound/driving adapters
├── services/        # use cases and application orchestration
├── domain/          # business concepts, rules, invariants
├── ports/           # outbound I/O contracts owned by the core
├── workflows/       # optional pipeline/DAG/graph orchestration
├── mappers/         # core-to-core conversions only
├── adapters/        # outbound port implementations
├── platform/        # logging, telemetry, shared HTTP, retries, technical primitives
├── config/          # load, parse, validate settings
└── main.*           # composition root: construct, inject, wire, start
```
Avoid generic dumping grounds (`utils/`, `helpers/`, `common/`, `misc/`) when a defined architectural home exists. Do not create empty layers or abstractions for purity.
## 3. Responsibilities and Call Rules
| Area | Owns | May typically call | Must not depend on |
|---|---|---|---|
| `controllers/` | Parse/validate transport input, auth boundary, invoke use case, map output/errors | Services, boundary mapping, platform logging/telemetry | DB/vendor adapters directly |
| `services/` | Use cases, transaction boundary, application orchestration | Domain, ports, workflows, other services within the same feature when acyclic, public services of other features, core mappers, platform logging/telemetry | Controllers, concrete adapters |
| `workflows/` | Sequence, branching, state transitions, DAG/graph/pipeline flow | Domain, ports, core mappers, platform logging/telemetry | Services, controllers, concrete adapters |
| `domain/` | Technology-independent business rules/entities/policies | Domain only | Ports for I/O, adapters, platform, frameworks |
| `ports/` | Contracts for outbound I/O required by the core | Core/domain types | Concrete infrastructure/frameworks |
| `adapters/` | Concrete implementations of ports | External technology, boundary mapper, platform | Service/workflow orchestration |
| `mappers/` | Core-to-core representation conversion | Core types | Vendor/DB/framework types, I/O |
| `platform/` | Logging, tracing, telemetry, retry primitives, shared technical clients | Technical libraries/providers | Core/application code |
| `config/` | Load and validate settings | Environment/config sources | Business/application behavior |
| `main.*` | Construct and wire dependencies, startup | All components needed for composition | Business logic |
### Mandatory direction
```text
Controller -> Service -> Workflow -> Domain / Ports
                    \-> Domain / Ports
```
A workflow **must not call a service**. A service may call another service **within the same feature only when the dependency remains acyclic**. Across features, call only the target feature's public service/use case or explicit public port/contract. A lower layer must not call upward. Do not introduce circular dependencies.
## 4. Ports, Repositories, Integrations, and Mappers
### Ports are required for outbound I/O
**Every outbound I/O capability used by the core goes through a core-owned port, even when there is currently only one implementation.** This is the boundary that keeps infrastructure replaceable and enables service/workflow tests with fakes.
Do **not** create interfaces between ordinary components inside the core merely for architectural purity. Ports exist only at outbound I/O boundaries used by the core.
```text
Service -> PortfolioRepository (port) <- PostgresPortfolioRepository
Service -> MarketDataGateway  (port) <- BloombergMarketDataGateway
```
Use ownership, not protocol, to classify the port:
- **Data this application owns -> repository port.**
- **Data/capability another system owns -> integration/gateway port.**
Reading another team's database is therefore an **integration**, even though the protocol is SQL.
### Mapper placement
A mapper that understands an external format belongs at that boundary:
```text
controllers/http/order_request_mapper.py       # HTTP DTO <-> core input
adapters/bloomberg/response_mapper.py           # vendor response <-> core type
adapters/postgres/portfolio_row_mapper.py       # DB row <-> core type
```
Use top-level `mappers/` **only when both representations are core-owned**. This prevents the core from importing vendor, database, or framework types.
## 5. Transactions and Errors
**The service/use case owns the transaction/unit-of-work boundary.** A workflow invoked by that service executes within the boundary when a single transaction is appropriate. For long-running/distributed workflows where one transaction is impossible, the workflow owns explicit checkpoint/compensation/recovery semantics instead.
Error flow:
1. **Adapter** converts provider/storage failures into errors defined by its port/core contract. Port-specific error types live beside the port in `ports/` unless they are true business/domain errors.
2. **Service** may translate port-contract errors into application/use-case errors.
3. **Domain** raises domain errors for business-rule violations.
4. **Controller** maps application/domain errors to HTTP status, CLI exit code, event failure, agent response, etc.
Transport authentication belongs at the inbound boundary. Business authorization belongs in the service/domain.
## 6. Workflows and Frameworks
Use `workflows/` only for explicit multi-step orchestration such as pipelines, DAGs, or graphs.
```text
Receive -> Extract -> Transform -> Enrich -> Persist
```
Workflow nodes stay thin: **receive state -> invoke domain/port capability -> update state -> return**.
For **in-process orchestration** (for example LangGraph), a workflow implementation **may import its orchestration framework** when that framework defines how the workflow is assembled/executed. Do not create a `GraphEnginePort` merely to hide the framework unless interchangeable workflow engines are an actual requirement.
For **external orchestration engines** that invoke your code (for example Airflow workers, Step Functions activities/Lambda tasks, or EventBridge-triggered jobs), each invoked task entry point is a **controller** that calls a service. The DAG/state-machine/schedule definition is deployment/orchestration configuration, not core application code.
Domain and ordinary services must remain unaware of the workflow framework.
Hooks are lifecycle extension points, not pipelines. Runtime-specific hooks belong with the relevant runtime/profile unless they are truly reusable. Do not hide major application orchestration in hooks.
## 7. Platform and Configuration
`platform/` is for shared technical capabilities such as logging, telemetry, tracing, retry primitives, shared transport clients, and technical credential providers. Controllers, services, workflows, and adapters may use platform logging/telemetry. **Domain may not. Platform must not import the core.**
Configuration has one access path:
```text
config/ = load + parse + validate settings
main.*  = read validated config + construct + inject + wire + start
```
**Only the composition root (`main.*`) reads application configuration.** Pass typed values/configuration objects into constructors. Services, domain, ports, workflows, and adapters must not perform ambient `settings.X`, environment-variable, or ad hoc secret lookups.
For rotating/expiring credentials, inject a credential provider from `platform/` (or a dedicated outbound port when credentials are application-visible behavior). Components may refresh through that injected provider; they must not discover or fetch secrets globally on their own.
## 8. Multiple Runtimes and Feature Packaging
Use **one core, multiple inbound controllers**:
```text
FastAPI ----\
AgentCore ---> shared services / workflows / domain / ports
CLI --------/
```
Do not duplicate the core per runtime.
For small/medium projects, package by layer. For larger projects, package by feature while preserving the same rules:
```text
src/
├── portfolio/
│   ├── services/
│   ├── domain/
│   └── ports/
├── reporting/
│   ├── services/
│   ├── domain/
│   └── ports/
├── controllers/
├── adapters/
├── platform/
├── config/
└── main.*
```
Features communicate only through **public use cases/services or explicit ports/contracts**. Never reach into another feature's domain internals, adapters, or repository implementations. Cross-feature service calls must remain acyclic.
```text
portfolio -> reporting public service/port        OK
portfolio -> reporting domain internals           NO
portfolio -> reporting adapter                    NO
```
## 9. Testing and Enforcement
- **Domain:** pure unit tests; no infrastructure fakes.
- **Services:** unit tests with in-memory/fake port implementations.
- **Workflows:** orchestration/state-transition tests with fake ports.
- **Adapters:** contract/integration tests against real or representative systems.
- **Controllers:** boundary tests for parsing, validation, response, and error mapping.
- **Architecture:** enforce dependency rules in CI where practical.
Language guidance:
- **Python:** ports commonly use `typing.Protocol` or ABCs; enforce imports with `import-linter` or equivalent.
- **TypeScript:** ports commonly use interfaces/types; enforce boundaries with `dependency-cruiser` or equivalent.
- **Java:** ports commonly use interfaces; enforce architecture with ArchUnit or equivalent.
## 10. Worked Examples
### 10.1 API Example
Use case: return a portfolio using data owned by this application.
```text
src/
├── controllers/http/get_portfolio.py
├── services/get_portfolio.py
├── domain/portfolio.py
├── ports/portfolio_repository.py
├── adapters/postgres/
│   ├── portfolio_repository.py
│   └── portfolio_row_mapper.py
├── config/settings.py
└── main.py
tests/
├── services/test_get_portfolio.py
└── adapters/postgres/test_portfolio_repository.py
```
Execution:
```text
HTTP request
  -> GetPortfolioController
  -> GetPortfolioService
  -> PortfolioRepository port
  -> PostgresPortfolioRepository adapter
  -> database
```
`GetPortfolioService` imports the **port**, never the Postgres adapter. Its unit test injects an in-memory `PortfolioRepository`. `main.py` chooses and injects the `PostgresPortfolioRepository` implementation. The DB-row mapper remains beside the Postgres adapter because it understands DB-specific representation.
### 10.2 ETL / Batch Example
Use case: ingest vendor-owned source data and persist application-owned records.
```text
Job/Schedule -> IngestJobController -> IngestService
IngestService -> SourceGateway port -> VendorApiSourceGateway adapter
IngestService -> RecordRepository port -> PostgresRecordRepository adapter
```
The **source** is an integration/gateway because another system owns it. The **sink** is a repository because this application owns the persisted records. If an external orchestrator invokes each task separately, each task handler is a controller calling a service.
## 11. Placement Test
```text
Incoming protocol/runtime?                  -> controllers/
Application use case/orchestration?         -> services/
Technology-independent business rule?       -> domain/
Outbound I/O capability needed by core?     -> ports/
Pipeline/DAG/graph control flow?            -> workflows/
Core-to-core conversion?                    -> mappers/
Concrete DB/vendor/provider implementation? -> adapters/
External/DB representation conversion?      -> beside owning controller/adapter
Logging/retries/telemetry/shared transport? -> platform/
Load/validate settings?                     -> config/
Construct/inject/wire/start?                -> main.*
```
If none fits, reconsider the responsibility before inventing a new category.
## 12. Coding-Agent Checklist
1. Inspect existing architecture before creating/moving files.
2. Classify responsibility using §11 before implementation.
3. Preserve the one-way dependency rules in §3, including service-to-service and feature-boundary rules.
4. Put every core outbound I/O dependency behind a core-owned port (§4).
5. Keep controllers and workflow nodes thin; keep domain technology-independent.
6. Keep external formats/mappers beside the boundary that owns them (§4).
7. Never read global configuration outside the composition root (§7).
8. Do not create duplicate cores for runtimes or bypass feature public boundaries (§8).
9. Do not add abstractions/folders solely for purity; do not create catch-all folders.
10. Record deliberate exceptions in a short **ADR (Architecture Decision Record)** rather than silently changing the pattern.
## 13. Project Profiles
Keep this core standard runtime-neutral. Projects may add short profiles without changing its dependency direction:
- **API:** HTTP controllers, middleware/auth, response/error mapping.
- **ETL/Batch:** job controller, optional workflow, source/sink ports and adapters.
- **Agent:** runtime controller, model/tool/memory ports and adapters, lifecycle hooks.
- **Graph:** workflow state/nodes plus graph-framework-specific assembly in `workflows/`.
- **CLI:** command controller and exit-code/error mapping.
Profiles extend this standard; they do not redefine it.
---
**Governing principle:** A developer or coding agent should be able to predict **where code belongs, who may call it, and what it may depend on** without reading the entire codebase. Prefer predictable architecture over novel structure, explicit dependencies over hidden coupling, and the simplest design that preserves these boundaries.

# Changelog

## 1.1.0

- Add the installable `mcp-openapi-creator` read-only local MCP server for VS
  Code and GitHub Copilot, including secure loopback access to the catalog
  dashboard.
- Stabilize policy-MCP shard URLs, negotiate legacy protocol versions, preserve
  non-parse APIM errors, hide `Idempotency-Key` from model-visible tool schemas,
  reject outbound authentication on mock backends, require targeted deployment
  confirmation, and scope backend-mode tags per client.

All notable changes to this project will be documented in this file.

The project follows Semantic Versioning.

## [1.0.3] - 2026-08-24

### Fixed

- Generate byte-identical LF policy, Bicep, JSON, and HTML artifacts on every
  operating system and measure the bytes that are actually deployed.
- Reject invalid `x-mock` ordering and ambiguous operators consistently across
  REST and policy MCP generation.
- Support path-item parameters, integer YAML response keys, and recursive local
  schema references throughout generation and verification.
- Restore persistent smoke resources without hiding Azure CLI failures.
- Report MCP tool-call network failures per server instead of aborting with a
  traceback.
- Make reconciliation independent of azd when the complete target is supplied.
- Enable and preflight native MCP network profiles and existing telemetry.
- Add reusable RFC 7807 error responses to the public sample and finish public
  English-language and CI hygiene.

## [1.0.2] - 2026-08-12

### Fixed

- Prevent the publication scanner test fixture from triggering its own
  high-confidence secret-pattern check in CI.

## [1.0.1] - 2026-08-11

### Fixed

- Reject unsupported OpenAPI versions instead of rewriting them as 3.0.3.
- Exercise every `x-mock` branch during REST and policy MCP smoke verification.
- Replace retired scenario branding with neutral fictional sample data.
- Enforce publication hygiene and all deployment profiles in CI.

## [1.0.0] - 2026-08-07

### Added

- Contract-first OpenAPI validation and deterministic APIM policy generation.
- Native APIM MCP, policy MCP on APIM Consumption, and REST Consumption profiles.
- Policy MCP sharding below the 16 KiB APIM policy-document limit.
- Mock and external backend modes with Key Vault secret references.
- Safe prefix-plus-tag lifecycle reconciliation and removed-client tombstones.
- Deterministic JSON/HTML capability catalog.
- Neutral fictional customer-care sample with six tools.
- Offline fork-safe CI and manual OIDC Azure smoke deployment.
- MIT license, contribution, security, support, and conduct policies.

### Security

- Azure deployment remains manual and scoped to each fork's configured GitHub
  Environment and Azure subscription.
- Preview is non-destructive; lifecycle deletion requires explicit opt-in.
- Dependency auditing runs in CI.

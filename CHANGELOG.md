# Changelog

All notable changes to this project will be documented in this file.

The project follows Semantic Versioning.

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

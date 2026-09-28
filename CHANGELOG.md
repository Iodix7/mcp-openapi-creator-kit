# Changelog

## 1.5.0 (prepared for release)

- Bootstrap a patched pip (`>=26.2,<27`) only inside each new dedicated runtime,
  preserving global Python and previous installations. Offline installation now
  also requires a compatible original pip wheel; preview, trust controls and
  receipt verification cover the bootstrap step.

- Add opt-in, strictly typed `x-kit-runtime` v1 for single-operation REST mocks:
  explicit subscription header/ID authorization, UUID correlation on normal and
  mapped early errors, bounded input validation, literal response headers,
  API-scope throttling after validation and source-only 429/503 simulation.
  Preserve normal mock defaults and introduce explicit `when.caseSensitive`
  consistently across REST and MCP. Reject unsupported MCP/facade/external
  combinations and oversized Consumption policies; report unverified gateway
  limitations rather than claiming natural-language IA interpretation.
- Compile runtime-aware mock variants with the validated target manifest.
  Rename only the exact source pilot subscription ID; preserve other explicit
  authorization IDs and reject collisions before writing any files.
- Convert the gateway retry interval to a supported numeric type before
  formatting `Retry-After`, avoiding APIM's prohibition on System.Object member
  access. The corrected development candidate passed live REST runtime checks;
  final package/host acceptance is recorded separately against release hashes.
- Preserve the existing three gateway profiles and legacy mock behavior.
  Runtime v1 is opt-in, public, mock-only, REST-only, and one GET per API;
  it does not add business state, persistence, arbitrary IA interpretation,
  or runtime validation to MCP transports.

## 1.4.0

- Recognize the documented Italian functional-fit table headers used by native
  Copilot, applying the same selected-tool, unresolved-gap and scope-evidence
  checks as English tables. Reject duplicate alias columns and explicitly
  distinguish an unrecognized mapping from a successful structural check.
- Add read-only `consumer-handoff` in CLI/MCP and the dashboard: explicit
  profile, complete shard URL/path, authentication requirements without keys,
  and adapter limitations. Derived candidates never count as verified endpoints.
- Surface allowlisted DNS/auth/timeout/ARM/encoding diagnostics through nested
  deployment processes and E2E reports without logging raw provider payloads.
  Failed reads and uncertain writes remain distinct; no mutation retries.
- Check explicit functional-fit mappings for selected tool references and
  unresolved gaps. Legacy missing mappings are reported, not retroactively
  certified. Neither prose meaning nor authenticity of approval is inferred.
- Describe policy MCP tools as stateless mocks, including gateway-generated
  per-call idempotency headers. Keep tool-call success separate from truthful
  consumer responses; streamline shared/plugin guidance without weakening gates.
- Extend the opt-in E2E runner with isolated existing-client snapshots and an
  explicit retained Copilot Studio stage. Record supplied, hashed observation
  traces separately from protocol verification; require observed success plus
  verified cleanup for a final pass. Preserve default automatic cleanup and
  label abandoned consumer stages incomplete, never passed.
- Add a concise requested-outcome/tool fit review before starter reuse.
  Ask only about material gaps and reuse existing scoped decisions; keep
  unapproved proposals Draft and record actual user scope confirmation.
  Structural consistency remains explicitly separate from semantic fit and
  approval; no new manifest fields, approval service or per-tool prompts.
- Account for observed ARM deployment-export serialization during test-service
  cleanup: omitted root Bicep compiler metadata and capitalized string type
  declarations only. Add explicit cleanup-after-upgrade verification against
  the intact original installed package, without rewriting run provenance.
- Serialize APIM product policy, API associations, subscription and tag writes
  to avoid overlapping product-child mutations implicated in a live HTTP 412.
  Do not automatically retry failed deployments.

- Add an opt-in installed-package E2E runner with one isolated three-tool
  customer-care scenario, static dashboard, pinned kit manifest, durable
  stage report and explicit temporary-service authorization. Reuse normal
  provisioning/deployment previews and verifiers; keep ordinary commands and
  offline push/PR CI unchanged. Service cleanup is limited to run-created,
  exclusively used Consumption APIM; it is not general gateway retirement,
  atomic concurrency protection, RG deletion or purge. Live Azure acceptance
  remains a separately authorized check, not established by synthetic tests.
- Validate the full packaged OpenAPI 3.0 schema before REST/native and policy-MCP
  generation, including offline `prepare`. Reject 3.1-only constructs such as
  numeric exclusive bounds with a contract path and actionable guidance before
  any Azure import, without changing valid integer YAML status keys or examples.
  Project valid boolean exclusive bounds to numeric bounds in MCP input schemas
  while preserving the source OpenAPI contract.
- Bound all generated ARM module deployment names to 64 characters using a
  deterministic hash suffix only when needed. Preserve existing valid names and
  all client/API/tool identities and public endpoint paths.
- Add opt-in same-client detached-tag recovery after a failed deployment.
  Require live Azure creation evidence, unshared tags and verified deployment
  scope; bind proof to the preview token and revalidate it before apply. No
  recovery DELETEs, arbitrary tag adoption or relaxed API/product ownership.
  For all-mock clients, permit narrowly verified OpenAPI exclusive-bound
  corrections and literal mock response-data updates without changing client
  identity. Structural, authentication, backend and ambiguous history changes
  remain blocked.

## 1.3.0

- Add optional create-only resource-group provisioning through the installed
  `provision-group` CLI and subscription-scope Bicep, before the existing APIM
  provisioning stage. Keep separate context, preview, review-token and apply
  approvals for group, gateway and client deployment.
- Guide existing/new resource-group selection through the companion, native
  plugin, CLI workflow status and dashboard current-step instructions.
- Document the complete subscription -> resource group -> APIM -> tools path
  in English, while preserving existing-group behavior and the immutable 1.2.0
  candidate's separate offline VS Code acceptance.

This source version does not imply publication or live Azure acceptance.
No automatic group deletion, relocation, adoption, RBAC or rollback is added.

## 1.2.0 (unreleased)

- Package a native Copilot skill/agent with the read-only companion, exact
  installed CLI invocations, data-only customer workspaces and workflow/spec
  synchronization.
- Add create-only standalone public APIM provisioning in an existing resource
  group, with explicit context, reviewed plans and post-create inspection.
- Add complete reviewed retirement of supported owned APIM mock resources;
  preserve the gateway, unrelated resources, local data and deployment history.
- Provide checksum-verified, side-by-side colleague installation and release
  artifact preparation without a source checkout or automatic host activation.
- Restore Italian dashboard labels and supported original scenario guidance
  and optional fictional example-library content.
- Derive declared persona/JTBD/outcome from optional specification frontmatter,
  preserving per-client contexts and indexing multilingual business text.
- Extend explicit endpoint verification for native mock calls and controlled
  external/Entra scenarios without automatic real-system writes.
- Fix Windows Azure CLI decoding/batch transport and retirement inventory
  performance, including Consumption's unsupported product-group collection.
- Emit UTF-8 from the installed CLI even under isolated Python with redirected
  output, preserving non-ASCII customer paths and catalog text.

Package tests, actual host conversations and live Azure acceptance remain
separate evidence. This working-tree version is not a published release, and
new code does not retroactively change the scope of earlier r3/r4 smoke results.

## 1.1.1

- Fix deployment-profile validation that incorrectly rejected external
  backends on the supported `native-mcp` profile.
- Restore two-phase targeted deployment: `deploy-client.py` now prints the
  reconciliation dry-run and requires `--yes` before DELETE or deployment.
- Return a controlled 404 for non-ASCII dashboard paths instead of raising in
  the request handler, and restore the native-MCP verification heading.
- Accept supported Python versions newer than 3.12 in azd hooks and monitor the
  root package dependencies with Dependabot.

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

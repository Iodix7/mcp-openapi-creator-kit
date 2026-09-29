# Roadmap and limitations

## Version 1.0

Implemented and tested:

- contract-first OpenAPI validation;
- deterministic APIM REST mock policies;
- native APIM MCP tools-only exposure;
- stateless policy MCP on APIM Consumption with 16 KiB sharding;
- REST mock profile on APIM Consumption;
- API-key and OAuth2 client-credentials outbound authentication;
- subscription key and Entra JWT inbound authentication;
- safe prefix-plus-tag lifecycle reconciliation;
- deterministic capability catalog;
- offline fork-safe CI and manual OIDC Azure smoke deployment.

## Version 1.7.0: demo-first 1.6 and 1.7 milestones

This release combines both milestones without adding a discovery
questionnaire or changing existing runtime-v1 contracts:

- **1.6 — automatic agreement coverage:** derive source-linked capabilities,
  limitations and acceptance checks during preparation; expose the same report
  through CLI, read-only MCP and dashboard. Reuse the scope already discussed.
  Technically valid demos do not need every full-IA behavior implemented.
- **1.7 — explicit REST runtime v2:** multiple GET/POST/PUT/PATCH/DELETE
  operations, bounded nested JSON inputs, typed body/composite mock predicates
  and operation-specific correlation, errors, simulation and additional limits.
  Generation still measures the actual 16 KiB Consumption policy budget.

The final runtime candidate passed isolated live Consumption and native CLI
acceptance. Build success, scope decisions, deployment and actual acceptance
still remain separate evidence for each customer. See [agreement coverage](agreement-coverage.md) and
[runtime contracts](runtime-contracts.md) for exact supported behavior.

## Explicit limitations

- Source contracts must use OpenAPI 3.0.x. OpenAPI 3.1 is rejected rather than
  silently downgraded.
- Consumption profiles are public and mock-only.
- Policy MCP supports tools, not MCP resources or prompts.
- Mock responses are deterministic examples; they do not maintain state or run
  business calculations.
- `backend.mode: hosted` is rejected.
- outbound mTLS is rejected.
- Connecting Copilot Studio remains a manual post-deployment step.
- APIM native MCP currently relies on preview Azure API surfaces; validate in a
  non-production environment before adoption.

## Candidate future work

- **1.8:** explicit REST/MCP semantic adaptation, without treating a REST URL
  or a successful REST build as MCP compatibility;
- **2.0:** separately implemented stateful demo backend, with actual
  create/read behavior and explicit lifecycle/cost consent;
- outbound mTLS;
- private-network reference architectures;
- stateful backend reference implementation;
- packaged Copilot Studio solution and environment variables;
- central multi-repository capability catalog;
- release upgrade and migration automation.

Roadmap items are not commitments. Unsupported configuration must continue to
fail at build time rather than degrade at runtime.

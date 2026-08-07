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

## Explicit limitations

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

- outbound mTLS;
- private-network reference architectures;
- stateful backend reference implementation;
- packaged Copilot Studio solution and environment variables;
- central multi-repository capability catalog;
- release upgrade and migration automation.

Roadmap items are not commitments. Unsupported configuration must continue to
fail at build time rather than degrade at runtime.

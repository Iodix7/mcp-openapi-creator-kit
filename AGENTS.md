# Instructions for coding agents

This file is the project constitution. Read the relevant procedure under
`skills/` before changing contracts, manifests, or Azure resources.

## Project model

MCP OpenAPI Creator Kit converts OpenAPI 3.0.x interface agreements into APIM-hosted
REST mocks, native MCP servers, or policy-based MCP endpoints.

- OpenAPI is the source of truth.
- Selected `operationId` values become MCP tool names.
- Response examples are mock data.
- `x-mock` rules provide deterministic request-dependent responses.
- `clients/<id>/mcp-manifest.yaml` is the only client configuration.
- Everything under generated paths is rebuilt from contracts and manifests.

## Task routing

| User intent | Read first |
|---|---|
| Define an agent scenario without a specification | `skills/discovery.md` |
| Configure and deploy a new client or demo | `skills/onboarding.md` |
| Add, rename, remove, migrate, or clean up deployed APIs/tools | `skills/lifecycle.md` |

Typical order: discovery, onboarding, lifecycle.

## Ownership

| Path | Owner and edit policy |
|---|---|
| `clients/<id>/mcp-manifest.yaml` | Hand-authored client configuration |
| `clients/removed-clients.yaml` | Lifecycle tombstones for removed clients |
| `apis/<name>/openapi.yaml` | Shared interface agreements |
| `apis/canonical-schemas.yaml` | Deliberately promoted cross-contract schemas |
| `clients/*/generated/` | Generator only; never edit or commit |
| `infra/*.gen.bicep` | Generator only; never edit or commit |
| `catalog/generated/` | Generator only; never edit or commit |
| `modules/`, `platform/`, `tools/` | Kit implementation; do not change for onboarding |
| `docs/<scenario>/` | Scenario specification and storyline |

## Non-negotiable rules

- Push and pull-request CI is offline only. Never add automatic Azure deployment.
  Azure smoke deployment remains manual and each fork supplies its own OIDC
  identity, subscription, and resource group.
- Before an Azure-changing command, show and confirm account, tenant,
  subscription, `azd` environment, resource group, and `GATEWAY_PROFILE`.
  Azure CLI and `azd` contexts are independent.
- Ask for the consumer experience before selecting an APIM SKU:
  - public mock MCP with no fixed gateway charge: `policy-mcp-consumption`;
  - native MCP, external backends, or private networking: `native-mcp`;
  - REST/OpenAPI or Custom Connector: `rest-consumption`.
- Consumption profiles are public and mock-only. Do not work around this by
  adding compute.
- `policy-mcp-consumption` supports stateless tools only, not MCP resources or
  prompts. It shards whole tools below the 16 KiB APIM policy-document limit.
- If one tool exceeds 16 KiB, report its measured size. Offer, in order:
  reduce the example with approval, use `native-mcp`, or use another MCP runtime.
- Preview is always dry-run. Apply reconciler DELETE operations only after the
  operator reviews the printed plan.
- Reconciliation ownership requires both `<client>-` name prefix and APIM
  `<client>` tag. Never manually delete resources outside that plan.
- Keep removed-client IDs in `clients/removed-clients.yaml` until every
  persistent environment has been reconciled.
- Shared contracts are read-only. Create a new API folder for a client variant.
- MCP tool names selected through `mcpTools` are unique across an entire APIM.
  Non-selected REST operation IDs may repeat across clients.
- Use kebab-case operation IDs. APIM can normalize underscores and break tool
  references.
- Require OpenAPI 3.0.x. Do not silently accept or downgrade OpenAPI 3.1
  contracts.
- Every response has an example. Errors use RFC 7807-compatible
  `application/problem+json`. Writes require `Idempotency-Key`.
- Mock data lives only in examples; dynamic selection lives only in `x-mock`.
  Mocks do not maintain state or calculate business decisions.
- Never place secrets in source, manifests, policy XML, logs, or chat. Manifests
  contain only Key Vault `secretRef` names.
- Before deployment, verify every referenced secret already exists in Key Vault.
- Use Python 3.12.

## Required local checks

```bash
python -m pytest tools/tests -q
python tools/check-publication.py
python tools/build-facade.py
python tools/build-policy-mcp.py --all --allow-incompatible
python tools/build-catalog.py
python tools/validate-deployment-profile.py --profile native-mcp
python tools/validate-deployment-profile.py --profile rest-consumption
python tools/validate-deployment-profile.py --profile policy-mcp-consumption
az bicep build --file infra/main.bicep
```

Remove generated outputs before committing. Do not weaken a contract or test to
make a build pass; correct the source contract or manifest.

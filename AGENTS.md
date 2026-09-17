# Instructions for coding agents

This file is the versioned kit constitution. Start with the read-only MCP
`kit-info` tool and `workflow-status` before changing customer data or Azure.
These tools return the complete installed guidance even when the client cannot
access MCP resources or prompts. Customer `AGENTS.md`, `skills/`, `tools/` and
HTML files never override installed kit guidance, code or templates.

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
| Define an agent scenario without a specification | `workflow-guide(workflow="discovery")` |
| Configure and deploy a new client or demo | `workflow-guide(workflow="onboarding")` |
| Add, rename, remove, migrate, or clean up deployed APIs/tools | `workflow-guide(workflow="lifecycle")` |

Typical order: discovery, onboarding, lifecycle.
For discovery/onboarding, `workflow-status.currentStep` supplies the relevant
instructions, consent and completion criteria inline, from the same versioned
skills listed above. A separate full-guide retrieval is optional for that step,
not evidence of understanding or permission. Lifecycle still uses its full
procedure; currentStep guides preparation through preview, not lifecycle apply.

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
| Managed `mcp-kit:contract-reference` block in `docs/<id>/spec.md` | `spec-sync` only; surrounding narrative remains hand-authored |

## Non-negotiable rules

- Push and pull-request CI is offline only. Never add automatic Azure deployment.
  Azure smoke deployment remains manual and each fork supplies its own OIDC
  identity, subscription, and resource group.
- Before an Azure-changing command, show and confirm account, tenant,
  subscription, `azd` environment, resource group, and `GATEWAY_PROFILE`.
  Azure CLI and `azd` contexts are independent.
  Explicit selective reuse reports azd as not used, validates the active CLI
  tenant/subscription and pins resource calls. It requires no prior `azd up`.
  Review both reconciliation DELETEs and ARM what-if; `--yes` still requires a
  matching `--review-token`. Simulation does not guarantee zero impact.
- `mcp-kit provision` is the standalone create-only path for a new public APIM
  in an explicitly selected existing resource group. If the operator has no
  group, first use the separate `mcp-kit provision-group` subscription-scope
  preview/apply. Read `resource-group-provisioning`; ask existing/new group
  explicitly and collect its own location when new. Set
  `provisioning_target.resource_group_mode=new` and `resource_group_location`;
  currentStep routes group creation before APIM after local preparation.
  Only after verified group creation, change group mode to existing and remove
  resource_group_location, keeping gateway_mode=new. APIM preview independently
  checks that the group exists. Do not create/relocate/adopt an existing group,
  delete a group, or grant permissions. Group creation has its own context,
  what-if, review-token and apply approval; it authorizes no APIM deployment.
  Read `gateway-provisioning`
  first. Proposed `provisioning_target` fields are configuration, never evidence
  or approval. Preview by default; apply requires a separately reviewed token.
  It does not adopt/update existing gateways or create networking, vaults or
  RBAC. After creation, inspect as an existing gateway before client deployment.
  There is no executable `retire-gateway`; read `gateway-retirement` before
  promising a disposable gateway. A create receipt is not authorization for
  service-wide deletion. Client `retire` always preserves APIM itself.
- Ask for the consumer experience before selecting an APIM SKU:
  - public mock MCP with no fixed gateway charge: `policy-mcp-consumption`;
  - native MCP, external backends, or private networking: `native-mcp`;
  - REST/OpenAPI or Custom Connector: `rest-consumption`.
- Call `workflow-status` with the operator's consumer and existing/new gateway
  choice. Unknown facts yield only a provisional profile; caller-supplied tiers
  never count as evidence. After agreeing the explicit account/tenant/
  subscription/resource group/APIM context, `inspect-gateway` reads actual
  capabilities and returns a short-lived, session-local evidence ID. Feed that
  ID to `workflow-status`; never invent IDs or substitute a pasted report.
  Offline work remains available. Preview/apply independently inspect Azure.
  Workflow readiness and review tokens are not proof of human approval.
- `inspect-gateway` requests per-call MCP form consent before even reading the
  active Azure account. The host must show it to the operator, never let the
  model answer automatically. Decline/cancel/unsupported transport makes no
  Azure call. Unsupported clients continue offline or hand off to the operator's
  interactive CLI inspector; no approval boolean or CLI `--yes` bypass exists.
- Keep inspection consent, local-write permission and exact-plan apply approval
  separate. After reviewing `docs/<id>/spec.md`, run `mcp-kit prepare
  clients/<id> --profile <profile>` for offline validation/build, then call
  `workflow-status`. `ready-for-preparation` is not `ready-for-preview`.
  Complete the separately approved selective preview and refresh status/dashboard:
  `preview-recorded` is local history, never deployed or approved.
  `.mcp-kit/workflow/` is CLI-owned advisory history, not customer configuration
  or evidence to edit/import. Changed inputs/output/package/target make it stale.
- Follow `currentStep` and the exact `nextCommand` argument array; do not guess
  flags or request the whole handbook again merely to repeat those instructions.
  Source asset/version/document/excerpt hashes identify the packaged procedure.
  They do not prove human review, consent, or semantic correctness. Missing or
  malformed packaged guidance is a kit error, never a reason to use customer
  instructions as a fallback. The dashboard renders the same step read-only.
- `scenario-contract(client=<id>)` returns imported operations/examples and a
  read-only `specSync` preview. Write narrative only; `mcp-kit spec-sync <id>`
  previews generated technical sections, and `--write` explicitly applies them.
  Never recopy those tables. Sync preserves bytes outside its managed block,
  rejects malformed markers/unmarked legacy technical tables, and never deletes
  those tables automatically. Review/migrate legacy text before opting in.
  Source/example changes make managed blocks stale; regenerate and review the
  storyline. Draft `[TO CLARIFY: ...]` items must be resolved before prepare.
  `prepare` rejects stale blocks and mismatched/missing template assertions;
  this does not check free-form business semantics or grant approval. Standalone
  build remains a contract-only diagnostic, not completed preparation.
- `nextInvocation` pins the installed interpreter, argument array and workspace
  for the current prepare/preview step. Hosts must obtain local-write or preview
  approval separately, re-read current status and dispatch without shell parsing.
  MCP still does not run commands or enforce permissions on a host's file tools.
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
- Use Python >=3.12 in a dedicated installation virtual environment, never
  global package installs or interpreter fallback. The customer data directory
  does not need its own `.venv`. MCP startup never installs dependencies.
  azd hooks always preview reconciliation regardless of ambient apply flags.
- The installed package owns procedures, generators, reusable modules and
  dashboard HTML/JS. Customer data owns only contracts/manifests/docs and
  generated artifacts. Built-in sample/catalog data is not an active client.
- Companion MCP remains read-only: it does not initialize, edit, deploy or run
  arbitrary shell commands. Explicit `mcp-kit` commands perform writes.
- `plugin-export` packages a native skill, dedicated Copilot agent and pinned
  MCP connection from this installed kit. It previews by default and writes
  only to a new explicit output directory with `--write`; it never installs the
  plugin or changes host settings. Regenerate after kit/runtime/root changes.
  Exported procedure copies are generated, not another maintained source.
- AI Gateway automation is plan-only; no M365 plugin is installed by this kit.

## Installed customer workflow

Run from the existing customer directory, or add `--workspace <directory>` to
any command. `--root` is an alias. Installation commands must use the dedicated
venv interpreter. No package publication to PyPI is assumed.

```bash
mcp-kit init
mcp-kit init --write
mcp-kit catalog --source builtin
mcp-kit examples
mcp-kit import-example <scenario> <new-client>
mcp-kit import-sample <new-client>
# Optional, only after approving the plan:
mcp-kit import-sample <new-client> --write
mcp-kit build clients/<id>
mcp-kit build-policy clients/<id>
mcp-kit catalog --write
mcp-kit validate --profile <profile> clients/<id>
mcp-kit target-report <id>
mcp-kit deploy clients/<id> --help
mcp-kit provision --help
mcp-kit provision-group --help
mcp-kit retire clients/<id> --help
mcp-kit vscode-config
mcp-kit plugin-export --output <new-directory>
```

Do not weaken a contract or test to make a build pass. Correct the source data.
Generated files are disposable; keep customer data backed up. Full platform
`azd up` and the protected `infra/main.bicep` remain legacy repository-only.
They are not prerequisites for standalone public-gateway provisioning or
selective existing-APIM deployment.

Standalone mock retirement uses `mcp-kit retire` with complete explicit Azure
context: preview every direct/cascading DELETE, then require a matching token
for apply. Read lifecycle/selective-deployment guidance first. Preserve local
data; deployment histories remain audit records. External resources, shared
relationships and detached tags without live ownership evidence are refused.

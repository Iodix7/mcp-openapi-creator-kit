# Deployment handover

The versioned installed kit owns guidance, code, templates, schemas and the
fictional starter library. Your selected customer directory owns only data:
clients, apis, docs and generated artifacts. Keep it backed up separately.
No cloned kit is required for the companion, standalone group/gateway creation,
or existing-APIM selective workflow.

## Requirements and installation

- Python >=3.12 in a dedicated installation virtual environment.
- A locally supplied built wheel; no PyPI/uvx publication is assumed.
- An existing customer directory, separate from the installation.
- Azure CLI and local Bicep compiler only when compiling/deploying.
- Existing APIM/read, child-resource deployment and ARM what-if permissions.
- For a new group/gateway, approved subscription/group creation and deployment
  permissions as applicable; the kit never grants access itself.
- Copilot Studio generative orchestration if that is the consumer.
- azd only for the separately approved legacy full-repository platform workflow.

See packaged `kit-reference(name="selective-deployment")`, and start with
`kit-info` then `workflow-guide(workflow="onboarding")`. MCP reads and plans; it
does not initialize, write, deploy or execute arbitrary commands.

## Prepare customer data offline

Run the installation's mcp-kit from the customer root, or pass --workspace
(--root alias). Initialization never overwrites or imports a sample by default.

```bash
mcp-kit init
mcp-kit init --write
mcp-kit catalog --source builtin
mcp-kit examples
mcp-kit import-sample <new-client>
# Optional and only after reviewing the fictional prefixed variant:
mcp-kit import-sample <new-client> --write
# Or select a complete supported scenario explicitly:
mcp-kit import-example fsi-rm360 <new-client>
mcp-kit import-example fsi-rm360 <new-client> --write
mcp-kit build clients/<client-id>
mcp-kit build-policy clients/<client-id>
mcp-kit validate --profile <profile> clients/<client-id>
mcp-kit catalog --write
mcp-kit target-report <client-id>
mcp-kit vscode-config
```

Build-policy is for compatible policy-MCP mock clients only. Existing approved
contracts/manifests may be placed directly in the data layout instead. Built-in
samples never become deployment clients unless explicitly imported as a new
identity. Generated modules come only from the installed kit. Dashboard HTML
and JS also come only from the kit; customer metadata is data, not executable.
The optional library includes Customer Care (one API, six tools) and FSI
RM-360 (four APIs, twelve tools). See `docs/example-library.md`. An imported
`docs/<client>/example-reference.md` links the original fictional narratives
and the exact new tool/API mapping; it is not an approved customer spec.
No sample becomes active during installation, startup or a catalog lookup.

## Starting without a resource group or APIM

The 1.3.0 candidate adds the optional installed `mcp-kit provision-group` command.
Read `kit-reference(name="resource-group-provisioning")`, choose existing/new
group and collect its explicit region. After local preparation the companion
routes a new group through separate context approval, what-if and reviewed apply.
The group command creates only the group, not APIM or customer APIs.

After verified group creation, change the proposed group mode to existing and
remove its creation region. Keep gateway_mode=new; follow
`kit-reference(name="gateway-provisioning")` to create APIM with another reviewed
plan. Inspect the created gateway as existing, then continue with selective
client deployment below. Each stage needs its own approval.
The frozen 1.2.0 package has the APIM command but not group creation. Neither
version provides automatic group or whole-gateway deletion.

## Selective existing-APIM deployment

Ask consumer needs first, inspect capabilities after context approval, then
finalize profile. Basic v2 supports native MCP mocks. Consumption profiles are
public/mock-only; zero fixed charge is not guaranteed zero total cost.
No publisher email or platform deployment is required for reuse.

```bash
mcp-kit deploy clients/<client-id> \
  --subscription <subscription-id> --tenant <tenant-id> \
  --resource-group <resource-group> --apim-name <existing-apim> \
  --profile <profile>
```

Confirm displayed active account, tenant, subscription, RG, APIM, profile and
azd "not used" before resource reads/what-if. Review both reconciliation DELETEs
and ARM what-if. Repeat identical flags with --yes --review-token <token> only
after approval. Noninteractive context approval uses --confirm-subscription;
it never replaces plan review. No dummy vault: selected secretRefs alone trigger
Key Vault metadata/permission checks. Existing diagnostics are read-only; unsafe
response-body logging blocks MCP deployment. Telemetry none does not erase it.

Only selected generated client templates deploy. No platform, sample, vault,
RBAC, gateway SKU/publisher or diagnostic resources are deployed. Ownership
requires prefix AND client tag; unowned collisions block. Review tokens cover
inputs and observed inventory. What-if is not a transaction or zero-impact
guarantee; coordinate shared-gateway changes.

## Verify without azd

Obtain the real HTTPS gateway origin and product subscription key through the
approved operator process. Inject the key privately as MCP_KEY in the verifier
environment, never through command-line arguments, logs, source or chat.

```bash
mcp-kit verify-mcp clients/<client-id> --gateway-url <approved-https-origin> --profile native-mcp
# Or policy MCP, including mock tool example/x-mock calls:
mcp-kit verify-mcp clients/<client-id> --gateway-url <approved-https-origin> --profile policy-mcp-consumption
# Mock REST only:
mcp-kit verify-rest clients/<client-id> --gateway-url <approved-https-origin>
```

Explicit verification has no azd/management/listSecrets/context-change calls.
Native MCP is discovery-only by default; append `--exercise-mock` for approved
all-mock business assertions. Default REST requires every backend mock; REST
and policy verification may call mock writes. Confirm deployed configuration
matches the local manifest first. Explicit `--auth-mode entraJwt` or `dual`
requires both private MCP_KEY and MCP_BEARER_TOKEN: current generated Entra
policy still includes the product subscription-key requirement.
HTTPS origin only, no path/credentials/query/fragment; all redirects refused.
For external systems, read `extended-verification`: named
`--fixture API/OPERATION/STATUS/EXAMPLE` selections produce a no-network
preview, and `--confirm-fixtures <token>` enables only the separately approved
scope. A failed real call may already have executed; there is no implied rollback.

## Connect the consumer

For subscription-key native/policy MCP add a Model Context Protocol tool in
Copilot Studio using verified MCP URLs and API-key authentication, header
Ocp-Apim-Subscription-Key.
Supply the key privately. Add every policy shard URL. Stateless policy MCP has
tools only. For rest-consumption use verified REST URLs/OpenAPI with a Custom
Connector; a REST URL is not an MCP server URL.
For Entra/dual, separately verify that the approved consumer configuration can
supply both required headers and the correct token audience. CLI verifier
support is not proof of Copilot Studio authentication compatibility; do not
paste a bearer token into the API-key field as a substitute.

AI Gateway tier exports are honest offline plans only; apply is blocked.
No M365 plugin or MCP Apps are installed by this package.

### Exposure model and consumer acceptance

The manifest owns `mcpExposure.mode`: `facade` exposes one native MCP surface,
`perApi` separates systems of record, and `both` generates both alternatives.
Connect the consumer to **one** alternative, not both: duplicated tools make
orchestration ambiguous. Policy MCP may shard whole tools regardless of this
choice; use the generated/verified URLs, not guessed paths. After renames or a
shard-count change, remove obsolete consumer connections and add current ones.
Unchanged URLs do not require repointing the agent.

Before every demo and after each deployment, repeat the applicable verifier,
then run the reviewed sample dialogue in the actual consumer. Protocol success
alone does not validate orchestration, confirmations, cited reasons or DLP.
Check the recovery path (unknown customer → RFC 7807 detail → corrected input),
the commercial refusal with its alternative, the confirmation before writes,
and reading a pending result without inventing an approval. Frozen fictional
dates are scenario anchors, not statements about today's customer or market.

## Pre-implementation questionnaire

Record answers and named owners in the customer specification before kickoff.
This checklist discovers requirements; it does not authorize resource changes.

| Area | Questions to settle | Owner / answer |
|---|---|---|
| Consumer | MCP tools or REST/OpenAPI/Custom Connector? Who owns the Copilot Studio agent and verifies its behavior? | |
| Existing gateway | Which account, tenant, subscription, resource group and APIM? Who owns diagnostics? Inspect actual tier, identity, networking and policies only after separate read consent. | |
| Public backends | Are the actual endpoints reachable from the approved gateway? Who owns outbound authentication and endpoint mapping? | |
| Private backends | Which VNet/region/subscription, DNS and routes are involved? Is the required supported APIM networking already configured? | |
| On-premises | Are ExpressRoute/VPN, peering, DNS and firewall rules owned and tested by the network team? | |
| Inbound access | Is an authenticated public endpoint acceptable, or is private ingress required? Can the consumer reach it? | |
| Certificates/proxies | Do backends require mTLS or do outbound proxies inspect TLS? mTLS is not supported by this kit; record the gap instead of promising an implementation. | |
| Power Platform administration | Who administers the tenant and target environment? Is it a Managed Environment? | |
| DLP and connectors | Do DLP rules allow the MCP/custom connector and all required systems? Validate in the actual environment, not only in a developer tenant. | |
| Capacity | Are Copilot Studio messages/credits allocated? Estimate measured calls per reviewed dialogue rather than guaranteeing a fixed number. | |
| Residency | Where are Azure, Power Platform and external systems hosted, and where does customer data transit? | |
| Operations | Who owns secrets, key rotation, access, incident response, recovery and final acceptance? | |

Public mock MCP without fixed gateway charge maps to policy MCP Consumption;
REST maps to REST Consumption. Real/private integrations require native MCP
with verified appropriate gateway/network capabilities. These choices are not
proof of provisioned connectivity. Both Consumption profiles stay public and
mock-only; do not add compute to bypass their constraints.

## Contract validation and per-API migration

Request one **anonymized real response** per relevant endpoint and branch
from each system owner before go-live. Compare fields/types, error model,
pagination, idempotency behavior and business meaning with the agreement.
Record divergences and ownership; do not weaken validators or edit a shared
customer contract automatically to make a backend appear compatible.
Where required, use a separately reviewed mapping/adapter; never an ad-hoc
policy mock or an unapproved rewrite of the shared API.

Apply the contract-first, data-derived ladder per API: response examples →
stateful seed derived from those examples → analytical projection of the same
dataset → real system implementing the interface agreement. The kit provides
the first level and the external connection point, **not** a hosted runtime,
seed loader or analytical platform. Other levels require their own owner,
implementation and approval. IDs, relationships and response shapes remain
stable. Different APIs can move at different times without replacing the
whole scenario.

For one ready API on `native-mcp`, change only its approved backend
configuration to `external`; leave other APIs mock. Declare its endpoint,
outbound `apiKey` or `oauth2-cc`, and only the Key Vault `secretRef` name.
Create/grant that secret through the separately approved owner process
**before** deployment; never paste its value into commands, source or chat.
Validate, prepare, inspect/review selective preview, approve the exact plan,
deploy and verify the changed behavior. Contract-preserving mock→external
keeps the agent's tools stable. A direct first-party MCP replacement
(for example Dataverse) has different tools and needs explicit instruction
changes; its environment/solution is not installed by this kit.

## Lifecycle and production

Call workflow-guide(workflow="lifecycle"). Move mock to external per API only on
native MCP, keeping the contract unchanged. Use outbound OAuth2/apiKey secretRef
names only; create secrets and grants separately through an approved process.
Selective named values require client prefixes and explicit ownership tags.
Production inbound entraJwt adds product validate-jwt alongside the subscription
key. External hosted backends and mTLS remain unsupported.

Renames/removals use the reviewed selective deployment flow, never ad-hoc APIM
deletions. Removed clients remain tombstones until all environments reconcile.
Full platform azd up aligns every active client and applicable platform/vault
resources; it is legacy repository-only, not a prerequisite or safe substitute
for selective reuse. Preserve infra/main.bicep and independent azd/CLI context
approval. Automatic push/PR CI stays offline. Manual Azure smoke deployments
require each fork's own azure-smoke environment/OIDC configuration.

## Handover package, upgrades and recovery

Deliver the reviewed customer spec and annotated dialogue; original fictional
reference plus imported tool map if used; contract/examples and per-API realism
owners; pre-mortem and acceptance checklist; verified consumer URLs and exposure
choice; installed kit version; and the exact reviewed deployment record.
Document secret **names/owners**, never values. The recipient should be able to
repeat offline prepare and the appropriate verification without a kit checkout.

Keep dev/test/prod customer data and target approvals separate. An approved
preview for one account/target is not reusable in another environment.
Upgrade the dedicated installation from a supplied versioned wheel, preserve
and back up the customer data separately, and regenerate/revalidate artifacts.
Do not overwrite imported contracts or customer narratives from a newer library
automatically. Compare and deliberately adopt updates as new variants where
needed; generated files and exported plugin bundles are disposable outputs.

For cleanup use the reviewed installed retirement/lifecycle procedure, not
ad-hoc deletes or `azd down` on a shared gateway. Preserve local customer data
and audit records. Platform teardown/purge is a separate infrastructure-owner
operation: APIM/Key Vault soft-delete can prevent same-name recreation and
purge protection may intentionally prevent purging. No purge is implied by
client retirement, and no purge/delete commands are part of this handover.

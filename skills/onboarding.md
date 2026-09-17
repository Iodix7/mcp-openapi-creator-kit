# Skill: onboard and deploy a client

Use this procedure after the scenario and contracts are clear. Call `kit-info`
for the full constitution and `kit-reference(name="handover")` first. The
companion is read-only; explicit installed CLI commands perform writes.

## Installation and customer data

Install the supplied wheel into a dedicated Python >=3.12 virtual environment.
Do not use global installs or assume a published PyPI package. Select an
existing customer directory (not the installation directory); it needs no
AGENTS.md, tools, skills, modules, catalog template or local Python environment.
Use the installation's `mcp-kit` executable, or its absolute Python executable
with `-I -m mcp_openapi_creator_kit.cli`. All commands below run from the customer
root; `--workspace <directory>` / `--root <directory>` pins another directory.

```bash
mcp-kit info
mcp-kit init
# Review before explicitly creating clients/, apis/, docs/:
mcp-kit init --write
mcp-kit catalog --source builtin
# Optional fictional starter variant, never automatically activated:
mcp-kit import-sample <new-client>
mcp-kit import-sample <new-client> --write
mcp-kit vscode-config
```

The last command emits a user-level MCP config object with the absolute
installation interpreter and pinned workspace. It never modifies VS Code
settings. Do not use an active-editor interpreter substitution.

## Ask for consumer experience first

Ask: A. public mock MCP with no fixed gateway charge; B. native MCP with real
backends/private networking; or C. REST/OpenAPI/Custom Connector?
Then ask whether an APIM already exists. Inspect capabilities after context
approval before finalizing the profile: tier, identity, network and response
body diagnostics. Basic v2 supports native MCP mocks; do not create Consumption
just because the scenario is mocked. `recommend-profile(existing_tier="BasicV2")`
is now an unverified compatibility hint, never a final decision.

Call `workflow-status(requires_mcp=<operator choice>, gateway_mode="existing"
or "new")`; include `client=<slug>` when known, even before its manifest exists.
The status reports `client_manifest` as missing input without creating files. Do not infer the
consumer from the mere presence of mcpTools. Unknown inputs remain explicit.

<!-- kit-step:inspect-gateway -->
### Instructions

For reuse, agree the full target/account with the operator, then call
`inspect-gateway(target={account, tenant, subscription, resourceGroup, apimName})`.
The target needs the exact account, tenant, subscription, resource group and
APIM name. No profile or SKU is supplied to inspection. Pass the returned
`evidenceId` as `evidence_id` to `workflow-status` with the same client and choices.
The kit derives the profile from observed capabilities, not a tier typed by the
agent. A five-minute observation expires; request a fresh inspection after
expiry, a changed target or an MCP restart. Never import files as evidence.
If access is unavailable, continue local discovery/preparation with a provisional
profile; do not create a new gateway merely to replace the existing one.

### Consent

The tool requests a host-displayed MCP form for this one inspection. The model
must not answer it; the host is responsible for presenting it to the operator.
Decline, cancellation, timeout or unsupported transport performs no Azure read.
Without form support, continue offline or have the operator use `mcp-kit
inspect-gateway --help` in an interactive terminal. The CLI requires retyping the
complete resource ID and has no noninteractive consent flag.
This permits only inspection, never local writes, preview or apply.

### Completion

`workflow-status` reports `evidenceStatus: verified` for the exact target and
either derives a compatible profile or supplies actionable blockers. Resolve
those blockers; verified evidence alone does not complete local preparation.
CLI fallback can evaluate in the same invocation but cannot seed MCP evidence.
Resources and dashboard refresh do not silently contact Azure.
<!-- /kit-step:inspect-gateway -->

| Choice | Profile | New gateway default | Constraints |
|---|---|---|---|
| A | policy-mcp-consumption | Consumption | public, mock-only, stateless tools |
| B | native-mcp | Basic v2 | external backends and private networks supported |
| C | rest-consumption | Consumption | public mock REST, no MCP resource |

This table describes new-gateway defaults, not permission to replace an
existing gateway. If access is unavailable, keep discovery/build offline and
label the candidate profile provisional; do not invent verified evidence.

Consumption is not guaranteed zero cost. No resources/prompts, state or compute
workarounds. AI Gateway tier is a separate plan-only preview, not a fourth
profile: read `kit-reference(name="consumer-targets")`. Apply is blocked.

## Collect configuration

<!-- kit-step:configure-client -->
### Instructions

Review the scenario before authoring `clients/<id>/mcp-manifest.yaml` and the
selected OpenAPI contracts. If `docs/<id>/spec.md` is not yet approved, draft it
first with persona, tool/example mapping, write confirmation and acceptance
criteria; use `kit-reference(name="scenario-template")` for the full template.
Collect:

1. Existing APIM name, resource group, tenant, subscription, region, tier,
   network and system-assigned identity for native MCP.
2. Preserve existing telemetry and diagnostics on selective deployment.
   Full repository provisioning has none/new/existing telemetry choices.
3. Lowercase client slug matching `clients/<id>`.
4. Approved OpenAPI 3.0.x contracts (not 3.1), with response examples.
5. Exposure: facade for one endpoint, perApi for separate governance.
6. Backend mock, or native-MCP external URL/outbound auth.
7. Inbound subscriptionKey for pilots or entraJwt for production.
8. Publisher email only for creating a gateway, not existing-APIM reuse.

Author the manifest and contracts as data. Shared contracts stay read-only;
create variants. Never ask for secret values in chat. Only secretRef names go
in manifests. The operator creates secrets separately through an approved
process. Named-value/secret metadata permission checks run only for selected
secretRefs; do not invent a dummy Key Vault for mock-only clients.

For business context in the dashboard, keep optional `catalog` frontmatter in
the specification (see `scenario-metadata`). The catalog projects declared
persona/JTBD/outcome without inventing facts from prose; shared contracts retain
separate client contexts. Resolve placeholders before preparation.

All selected operationIds must be kebab-case and unique across clients on the
APIM. Use response examples for mock data, x-mock for stateless branches,
RFC 7807-compatible error examples and Idempotency-Key on writes. Consult
`catalog-search(source="builtin")` before inventing structures. If the operator
wants the fictional starter, run `mcp-kit import-sample <id>` to preview its
isolated variant, then repeat with `--write` only after approval. This never
activates the built-in sample identity. Never edit generated artifacts.

### Consent

Obtain operator approval of scenario and local writes before creating the
manifest, contracts or spec. Import is optional, not implicit. Do not request
secret values, and do not infer permission for Azure reads or deployment.

### Completion

Call `workflow-status` with the client slug. `client_manifest` should no longer
be missing. File presence is not validation: follow the next step to record
profile-specific preparation. A provisional profile is still unverified.
After import, call `scenario-contract(client=<id>)` and reconcile the spec against
the actual examples. Save narrative only; preview `mcp-kit spec-sync <id>` and
use `--write` after approval to generate the technical block without recopying
IDs/routes/statuses. No existing unmarked technical table is deleted automatically.
<!-- /kit-step:configure-client -->

## Resolve blockers before advancing

<!-- kit-step:resolve-blockers -->
### Instructions

Read `blockers` and `nextAction`; do not continue toward preview while blocked.
For a selected-profile conflict, re-evaluate `workflow-status` with the
compatible profile indicated by `nextAction` instead of replacing APIM.
For invalid manifest data, correct the source contract/manifest and re-evaluate.
For unsupported tier, identity, networking or response-body diagnostics, explain
the exact incompatibility and hand remediation to the operator's approved
operations process. Never silently change infrastructure, diagnostics or auth.
AI Gateway remains plan-only, not a fourth deployable profile.
After any target/capability change, request new scoped inspection evidence.

### Consent

Reading the report does not authorize corrections. Obtain permission for local
data changes; cloud changes require separate approval of account, tenant,
subscription, RG, APIM, gateway profile and azd status. Inspection consent and
review tokens do not authorize remediation or bypass deployment checks.

### Completion

Call `workflow-status` again. Blockers must be resolved, not hidden by changing
preferences that contradict the manifest. Follow the newly returned
`currentStep`; resolving a blocker does not itself mean prepared or deployed.
<!-- /kit-step:resolve-blockers -->

## Build offline before Azure

<!-- kit-step:prepare -->
### Instructions

Call `scenario-contract(client=<id>)`; preview `mcp-kit spec-sync <id>` and
regenerate stale/missing technical sections with `--write` after approval.
Do not author those tables: the contract owns them. Preserve narrative outside
the managed block and resolve any `[TO CLARIFY: ...]` items and invalid Tool:
references with the operator. Unmarked legacy tables need deliberate migration,
not automatic deletion. The check does not approve free-form narrative.
Write and review `docs/<id>/spec.md` with the operator. After approval for local
writes, prefer the single preparation command; it validates the selected profile,
builds REST/native artifacts and adds policy-MCP artifacts only when required:

```bash
mcp-kit prepare clients/<id> --profile <profile>
```

Use the exact `nextCommand` argument array when provided: it pins workspace,
client and profile. `prepare` includes validation and the required builds;
`nextInvocation` also provides the absolute installed interpreter, arguments,
cwd and CLI-only arguments for hosts without shell parsing. Copy it unchanged;
do not strip/reconstruct paths or flags. A host's current-step adapter can fetch
fresh status and dispatch that invocation after operator approval.
do not run build-policy separately for a native/REST client. It records
completion in `.mcp-kit/workflow/`, outside generated artifacts. Do not edit
those receipts or the generated files. A provisional profile remains provisional.
Resolve errors without weakening contracts. For a whole tool above 16 KiB,
report its measured size and offer approved payload reduction, native MCP,
or an external MCP runtime; never silently trim data or split one tool.

### Consent

This command writes local artifacts and history; obtain explicit local-write
permission first. It does not access Azure. Neither scenario approval nor
successful preparation authorizes inspection, preview or apply.

### Completion

Call `workflow-status` again with the same client and preferences. The prepare
step must be `recorded-current`; changed source/output/package invalidates it.
`ready-for-preview` still requires the separately approved selective preview.
Use `dashboard-refresh` to show current progress. Do not stop at a successful
build or dashboard, and never call local preparation a deployment.
<!-- /kit-step:prepare -->

Individual diagnostic and catalog commands remain available:

```bash
mcp-kit build clients/<id>
mcp-kit build-policy clients/<id>
mcp-kit catalog --write
mcp-kit validate --profile <profile> clients/<id>
mcp-kit target-report <id>
az bicep build --file clients/<id>/generated/client.bicep
az bicep build --file clients/<id>/generated/policy-mcp/client.bicep
```

Build-policy applies only to policy-MCP-compatible clients. Resolve errors
without weakening contracts. Report each shard size. If one whole tool exceeds
16 KiB, offer: reduce example/schema with approval; native MCP; external MCP
runtime. Never silently trim payloads or split one tool. Generated modules live
under the selected client's generated/kit-modules and use relative references.

## Explicit existing-APIM deployment, without azd

<!-- kit-step:preview -->
### Instructions

Follow `nextCommand` exactly for the selected client and existing target.
Use `nextInvocation` for an argument-array host; it uses the installed interpreter.
`deploy` defaults to preview: there is NO `--preview` flag, and do not add
`--yes`. It generates selected artifacts locally, then prints the
ownership-based reconciliation DELETE plan and ARM what-if without applying
them. If selected APIs use secretRefs, first collect the existing Key Vault
name and add `--key-vault-name`; confirm required secrets already exist through
the approved process. Never use a dummy vault for mock-only clients.
Global/selected response-body diagnostics must be safe; unsafe settings and
unowned collisions block the preview, not trigger automatic fixes.
Full selective-deployment details remain available in `kit-reference`.

### Consent

Before resource inspection,
show and approve account, tenant, subscription, resource group, gateway profile
and azd status ("not used" for explicit selective context). CLI and azd contexts
are independent. Neither prior azd up nor azd authentication is required.
Preview includes local writes and Azure reads/what-if; inspection consent alone
is insufficient. The CLI asks for subscription confirmation. A noninteractive
host may supply `--confirm-subscription <id>` only after the operator approves
this complete context. That flag is not proof of human approval or permission
to apply. On refusal stop; never auto-approve or switch accounts.

### Completion

Preview must succeed and print both reconciliation and ARM what-if plus a review
token. Call `workflow-status` and `dashboard-refresh` again: the current matching
receipt should produce `completion: preview-recorded`. If evidence has expired,
request a fresh scoped inspection; do not import preview history as evidence.
Present the plan and explicitly say **not deployed**.
<!-- /kit-step:preview -->

Example of the interactive command:

```bash
mcp-kit deploy clients/<id> \
  --subscription <subscription-id> --tenant <tenant-id> \
  --resource-group <resource-group> --apim-name <existing-apim> \
  --profile native-mcp
```

Preview generates selected artifacts locally, then reads Azure only after
context approval. It prints ownership-based reconciliation DELETEs and ARM
what-if. Review both. Repeat the identical flags with `--yes --review-token
<token>` only after approval. Noninteractive context approval additionally uses
`--confirm-subscription <subscription-id>`; this never replaces the review token.

Inventory checks prevent unowned named-resource overwrites. Ownership requires
both client name prefix and tag. Unsafe response-body diagnostics block MCP;
they are not silently changed. No platform, publisher, vault, role assignment,
diagnostic or built-in sample deployment. Tokens become stale if selected data,
trusted kit inputs or inspected inventory change. Simulation cannot guarantee
zero impact against concurrency/provider behavior.

## Review the recorded preview

<!-- kit-step:review-plan -->
### Instructions

Present the recorded redacted plan, reconciliation DELETEs, current profile and
target. Refresh the dashboard and summarize local artifacts, remaining checks
and **not deployed**. This completes preparation through preview, not apply.
The review token binds the reviewed inputs and plan; it is not human consent.
If apply is separately requested, use the guarded selective CLI with the same
context and current reviewed token, never execute DELETEs by hand. Deployment
always refreshes checks, regardless of local receipts.

### Consent

Displaying a plan is read-only. Do not execute apply until the operator approves
that exact plan and full account/tenant/subscription/RG/APIM/profile/azd context.
Keep review, inspection consent and local-write permission separate. A receipt
or model assertion cannot supply this approval.

### Completion

For a preparation-only request, stop with the preview summary and dashboard,
explicitly stating no deployment. A matching receipt lasts five minutes;
changed inputs, outputs, package, profile or target require repeating stale
steps. Human review and specification quality are not inferred from file
presence. Successful live apply and consumer verification remain separate work.
<!-- /kit-step:review-plan -->

## Verify and hand off

```bash
mcp-kit verify-mcp clients/<id> --gateway-url <approved-https-origin> --profile native-mcp
mcp-kit verify-rest clients/<id> --gateway-url <approved-https-origin>
```

Explicit verification performs no azd/Azure management discovery. Set MCP_KEY
privately in the verifier process environment; never put a key in commands,
logs or chat. Native MCP defaults to discovery only; add `--exercise-mock`
for approved all-mock business examples. Policy MCP and default REST checks
exercise mocks, including writes. For `--auth-mode entraJwt` or `dual`, supply
both MCP_KEY and MCP_BEARER_TOKEN privately: current generated Entra policy
still requires the product subscription key.
Read `extended-verification` before controlled external calls: named
`--fixture API/OPERATION/STATUS/EXAMPLE` selectors preview with no network;
only separately approved `--confirm-fixtures <token>` enables that exact scope.
Do not mistake fixture preview, discovery alone or an unsupported provider
payload for successful business acceptance.
Return endpoint URLs, auth header name, verification outcome, remaining manual
consumer steps and sharding URL changes, never credentials.

## Standalone new-gateway provisioning

<!-- kit-step:provision-resource-group -->
### Instructions

Read `kit-reference(name="resource-group-provisioning")`. Ask whether the
operator wants an existing resource group or a new one; never infer permission
to create a group from its absence. For a new group collect account, tenant,
subscription, group name and its explicit Azure region, independently of the
planned APIM region. Keep the planned gateway profile and costs visible.
Use `provisioning_target.resource_group_mode="new"` and
`resource_group_location` with `gateway_mode="new"`. These are proposals, not
observed Azure facts. Existing-group callers can leave the compatibility
default `resource_group_mode="existing"`; no location change is implied.

After local scenario preparation, run only the exact `nextInvocation` for
`mcp-kit provision-group` after preview/context approval. Default execution
previews a single subscription-scope resource-group Create. It does not create
APIM. Existing groups, unrelated mutations and incomplete inventories must
block; never downgrade an error into permission to create or reuse a group.
Apply requires a new, separate approval of that exact plan and matching token.
Do not add `--yes` or a guessed token to a preview invocation.

### Consent

Show account, tenant, subscription, group name and region, planned gateway
name/profile and costs; azd is not used. Group creation requires subscription
permissions that the kit never grants. Obtain approval for cloud inspection/
preview and separately for the resource-group apply. This grants no APIM
creation, API deployment, role assignment or group deletion permission.

### Completion

Require the CLI's verified `provisioned` result and durable creation receipt.
If creation fails or times out, inspect the actual outcome through a separately
approved action; never delete/retry automatically. A receipt is an audit record,
not authorization. After verified group creation, set
`provisioning_target.resource_group_mode="existing"`, remove
`resource_group_location`, keep `gateway_mode="new"` and call workflow-status
again. The APIM provisioning preview must freshly verify the group exists.
Group creation alone does not create a gateway, business API or endpoint.
<!-- /kit-step:provision-resource-group -->

<!-- kit-step:provision-gateway -->
### Instructions

Read `kit-reference(name="gateway-provisioning")`. Collect the proposed account,
tenant, subscription, resource group, new APIM name, Azure location
code, publisher name and publisher email. Pass them as `provisioning_target`
to `workflow-status` with `gateway_mode="new"` and the consumer choice.
These fields describe a proposal, not inspected capabilities.
Ask explicitly whether the group already exists. If not, select
`resource_group_mode="new"` and its separate `resource_group_location`;
workflow-status routes the resource-group stage before this APIM stage.
Do not tell an operator without a resource group that a source clone or portal
creation is required: the installed `provision-group` command covers that step.

After the scenario is prepared, follow the exact `nextInvocation` for
`mcp-kit provision`. The default is a create-only preview, not resource creation;
never add `--yes` or a guessed review token. The command must reject an existing
APIM rather than adopt or update it. A reviewed apply uses the same context and
the fresh token only after separate approval.

This standalone path creates a public gateway in an existing resource group:
Consumption for the two Consumption profiles, or Basic v2 for native MCP.
It does not provision private networks, Key Vault, role assignments, telemetry
or every client. Do not silently extend it into a full-platform deployment.
If the request also requires deleting this gateway after a test, read
`gateway-retirement` first: there is no executable `retire-gateway`, and client
retirement preserves the service. Resolve that cleanup path with the operator
before provisioning a supposedly disposable gateway.
`azd up` remains a separately approved repository-only legacy workflow, with
its protected static infra/main.bicep unchanged.

### Consent

Show account, tenant, subscription, region, resource group, new APIM name,
publisher, gateway profile and costs; azd is not used by standalone provisioning.
Obtain approval before cloud preview and again for the actual reviewed apply.
Do not infer it from proposed context, a new-gateway preference or a local build.
Basic v2 has a fixed charge; Consumption can have usage charges. Never replace
an existing gateway, switch account or change permissions automatically.

### Completion

The command must verify the created gateway's ID, tier and provisioning state.
Keep the workflow provisional until creation succeeds. Then switch to
`gateway_mode="existing"`, request scoped `inspect-gateway`, and pass its fresh
evidence ID to `workflow-status`. A provisioning receipt is not inspection
evidence, and a created gateway contains no deployed customer by implication.
Continue with the separately reviewed client deployment and endpoint checks.
<!-- /kit-step:provision-gateway -->

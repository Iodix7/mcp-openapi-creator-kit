# Selective deployment to an existing APIM

Use this path for the **first or subsequent client** on an existing gateway.
It does not require a completed `azd up`, an azd environment, or azd login.
The manifest remains the only client configuration. Flags below describe the
deployment environment, not a second business configuration.

## Inspect capabilities before selecting the profile

Ask how the consumer connects first. Then inspect the existing APIM tier,
identity, public/private connectivity, and diagnostics before finalizing the
profile. Basic v2 supports **native MCP with mock backends**; mock does not
imply Consumption. Consumption profiles require a Consumption instance.
Native MCP also supports Developer, Basic, Standard, Standard v2, Premium and
Premium v2. `recommend-profile` retains `existing_tier` only for compatibility:
it never treats that declaration as evidence and no longer returns a verified
profile from it. Use `inspect-gateway` and `workflow-status` instead.

The read-only MCP inspector requires the operator-agreed explicit account,
tenant, subscription, resource group and APIM name. It verifies the active
Azure CLI identity/context before resource reads, never switches accounts, and
queries only the selected APIM and global diagnostics. Evidence IDs live only
in the pinned MCP process and expire after five minutes. CLI inspection can
evaluate immediately in the same invocation:

The MCP tool first requires a per-invocation form elicitation response delivered
by the host to the operator. No accepted form means no account/resource reads.
The CLI fallback is interactive: the operator retypes the complete APIM resource
ID. It rejects redirected stdin and offers no noninteractive inspection approval
flag. These gates authorize only inspection, not this document's preview/apply.

```text
mcp-kit inspect-gateway --client <id> --consumer mcp --account <account-name> --tenant <tenant-id> --subscription <subscription-id> --resource-group <rg> --apim-name <apim>
mcp-kit workflow-status --client <id> --consumer mcp --gateway-mode existing
```

The second command is offline, with no evidence retained from the first.
Printed JSON, files and hashes cannot be imported as verified observations.
Selective preview and final apply checks always acquire fresh Azure facts and
use the same decision rules as MCP/CLI/dashboard. They require no MCP session.
`mcp-kit prepare clients/<id> --profile <profile>` records offline validation and
required builds. Live status distinguishes `ready-for-preparation`,
`ready-for-preview` and `preview-recorded`. Readiness is not approval or completed
Azure ownership/secret validation; recorded preview is not deployment.

The selective tool requires an existing system-assigned APIM identity for
native MCP; it does not enable one. Its network checks are conservative
metadata checks, not reachability tests. No publisher email is needed because
no gateway is created. Existing telemetry is preserved: `none` in an incremental
platform deployment does **not** remove existing diagnostics.

Global and selected API diagnostic frontend/backend **response body bytes**
must be zero for MCP. Unsafe settings block deployment with an actionable
message; the tool never changes them. Review inherited policies separately:
body-buffering policies and network reachability cannot be proved by what-if.

## Preview, review, apply

Install the supplied wheel in a dedicated Python >=3.12 environment as described
in [local MCP setup](local-mcp-server.md). Run the installed `mcp-kit` executable
from the customer data root, or pass `--workspace <directory>` (`--root` alias).
The data root needs no local venv, scripts, template, modules, Git or azd config.
Windows users can put arguments on one line or use PowerShell backticks.

```bash
mcp-kit deploy clients/<client-id> \
  --subscription <subscription-id> --tenant <tenant-id> \
  --resource-group <resource-group> --apim-name <existing-apim> \
  --profile native-mcp
```

The tool checks the **active Azure CLI** subscription and tenant, displays
account, tenant, subscription, resource group, APIM, profile and azd **not used**,
and asks you to retype the subscription before resource inspection/what-if.
For non-interactive approval supply `--confirm-subscription <subscription-id>`.
It never switches account, logs in, or calls `azd env get-values` in explicit
mode. All subsequent management and vault commands pin that subscription.

Read both the reconciler's DELETE plan and the ARM what-if summary, then rerun
the **identical command**, adding:

```text
--yes --review-token <token-from-preview>
```

`--yes` cannot skip validation, plan display, what-if, or token validation.
The token covers local source/generated/module bytes, context, observed
inventory, secret metadata/grants, deletes and the redacted what-if summary.
Changed inputs or inventory invalidate it. Inventory and local inputs are
checked again before DELETEs. This is **not a transaction or TOCTOU immunity**:
concurrent writes after the final check, provider behavior, and changes not
represented in the inspected metadata remain possible. ARM what-if is a
simulation, not a guarantee of zero impact.

Installed CLI previews also write advisory history to
`.mcp-kit/workflow/<client-id>/preview.json`, never generated artifacts. After
preview, refresh workflow status and dashboard to present the matching plan.
Receipts expire after five minutes and bind workspace/source/output/package,
profile and exact inspected target. They are not trusted input to deploy and
cannot skip any of its checks, regardless of whether they are absent or forged.

The operator account is bound into the review fingerprint and checked again
before final inventory inspection. Neither `--yes`, a review token nor a
model-supplied boolean proves human consent: the operator/client must enforce
the approval interaction. This kit does not protect against an unrestricted
same-user shell that can invoke other Azure commands or modify installed code.

Only the selected client's `generated/client.bicep` and, for policy MCP,
`generated/policy-mcp/client.bicep` are deployed in Incremental mode.
No platform/main template, sample client, gateway SKU/publisher, Key Vault,
RBAC or diagnostics resource is deployed. Installed selected-client generation
does not build unrelated clients or write a full-platform index. Trusted Bicep
modules are staged under generated/kit-modules using deterministic relative
references, even when installation and customer data use different drives.

What-if output prints only resource IDs, resource types and change types,
never property deltas, secrets or raw provider errors. Unknown scope,
missing resource expansion, errors and unsupported changes block apply.
ResourceIdOnly summaries deliberately do not show property-level changes.
Nested deployment records use the generator's exact module names. Existing
top-level/nested records require matching APIM parameters plus a tagged owned
client-resource anchor; deployment-history names alone do not establish ownership.

## Occupancy and ownership

- Existing APIs and products need the client name prefix **and** client tag.
- Policies, tools, operations imported by OpenAPI and association links derive
  ownership from their tagged parent API/product.
- Existing pilot subscriptions must point to the exact owned product.
- Service tag names have no tags of their own: reuse requires a matching
  display name and an existing explicitly owned API/product anchor.
- Named values need client-prefixed `secretRef` names and explicit client tags.
  Untagged legacy named values are not silently adopted.
- Native MCP tool names are checked across existing MCP APIs before import.
  Other-client/unowned collisions block deployment.

Outside-scope resources are not reconciled. Metadata ownership is not proof
against a malicious administrator who can forge tags. Coordinate shared-gateway
changes and use appropriate Azure permissions.

## Existing Key Vault, only when needed

Mock manifests without `secretRef` require **no vault** or dummy name. If a
selected manifest contains secret refs, supply `--key-vault-name <existing-vault>`
(or use a real existing vault from legacy azd outputs).

Before any apply, the tool reads metadata only: vault tenant/subscription,
secret existence/enabled/validity and APIM system identity grants. It never
requests a secret value. Supported grants are direct, unconditional built-in
Key Vault Secrets User/Administrator RBAC grants, or get/list access policies.
Custom, conditional, group/PIM-only grants or restrictive network cases that
cannot be proved are blocked for manual review. This does not prove data-plane
connectivity or eliminate deny-assignment/propagation races.

The tool creates only APIM Key Vault-backed named values, not the vault,
secrets, identities, role assignments or access policies. Prepare permissions
and secrets separately through your approved process. Never paste values into
chat or source control.

## Retire a mock client without deleting the gateway

The installed **`mcp-kit retire`** command is the standalone cleanup path.
Deployment reconciliation only removes orphan APIs/tools; it is **not**
complete client retirement. Preserve local manifests/contracts/generated
artifacts so the client can be inspected or redeployed later.

```bash
mcp-kit retire clients/<client-id> \
  --subscription <subscription-id> --tenant <tenant-id> \
  --resource-group <resource-group> --apim-name <existing-apim> \
  --profile native-mcp --confirm-subscription <subscription-id>
```

Use the same explicit profile/context as the approved deployment (`native-mcp`,
`rest-consumption`, or `policy-mcp-consumption`). Retirement performs no build,
ARM what-if, azd discovery, account switch, provisioning, or local write.
It inspects actual ownership, not generated files: old owned APIs and policy
shards are included even if no longer present in the manifest.
The retirement wrapper strictly decodes Azure CLI output using the operating
system locale, independently of the installed Python's UTF-8 mode; Python kit
child output uses UTF-8. Invalid output is not silently replaced or retried
with another encoding.
On Windows, batch launchers receive explicitly quoted arguments through private
environment slots with delayed expansion disabled. This preserves JMESPath
parentheses, URI ampersands and literal percent signs without inserting argument
data into shell syntax. Embedded quotes, NUL and line breaks are rejected before
launch (the installed retirement queries do not require them).
Failed CLI calls report the stage, resource ID, exit code and a parsed ARM
error-code identifier when available; raw stdout/stderr and error messages
remain suppressed. A batch syntax failure therefore reports its exit code
even when no ARM error exists.

Preview is the default, including when `MCP_RECONCILE_APPLY` is set. It prints
**every exact direct DELETE and every cascading child resource ID**, then a
retirement-specific token. Review both the complete context and deletion list.
Repeat all the same flags plus `--yes --review-token <token>` to apply.
The token binds installed code, local inputs, context/operator, inspected
metadata/relationships and exact deletions. It is not proof of human approval.
The CLI rechecks the active account, local inputs, and complete inventory before
each DELETE and verifies observed absence afterward. API DELETE can be
asynchronous; a bounded absence wait must succeed before dependent deletions.
Inventory changes, failed reads or incomplete deletion stop further changes.
There is no rollback or immunity to writes after the last check.

### Read performance, progress and timeouts

Retirement emits flushed **stderr progress** identifying the stage and exact
resource/collection ID, never response bodies, query parameters or keys.
Stream stderr in an automation harness; buffering it until completion hides
otherwise available progress. The normal review plan/token remain on stdout.

Independent inventory reads use **at most four workers**. Each snapshot is
new: nothing is cached between preview, pre-DELETE checks or final verification.
The paginated service `tagResources` collection supplies the complete reverse
API/product/operation tag graph instead of launching a separate CLI process
for every tag association collection. Resource IDs, not its display names,
establish identity. Every page is checked for scope/shape/duplicates; escaped,
cyclic or over-100-page collections fail closed. There is no serial fallback
that omits these relationships when the bulk endpoint fails.

A no-op shortcut is available only after complete service API/product/tag/
subscription/named-value/backend inventories, the complete tag association
graph, and every native tool source reference prove the client absent. Client
prefixes/tags, scoped subscriptions, dangling native references or external
remnants prevent the shortcut. For an absent client on a non-native gateway,
this is **eight ARM reads plus the account check**, regardless of unrelated API
operation count (pagination adds requests). Active retirement still enumerates
the full relevant graph and refreshes it before **each** DELETE, including the
final stages when the client becomes absent; the shortcut cannot shrink the
reviewed graph mid-apply.

Each CLI process has a **60-second timeout**, reduced to the remaining
**five-minute snapshot deadline** when necessary. A post-DELETE absence wait
has its own **two-minute total read deadline** (and at most 31 polls).
On timeout, cleanup targets only
that spawned process tree (Windows CIM descendant capture rooted in the exact
launched PID/creation time, then `Stop-Process -Id` on captured owned PIDs;
or the isolated POSIX process group). Windows creation-time checks and retained
process handles guard against PID reuse; bounded rescans catch late descendants.
Cleanup output goes to DEVNULL, with bounded 10-second waits. No command is
automatically retried. Cleanup failure is explicit and stops the workflow.
If an intermediate ancestor vanished before capture, ownership may no longer
be provable; cleanup must stop unconfirmed rather than adopt processes by name.
A timed-out DELETE has an **unknown Azure outcome** even when its local
processes were stopped: re-preview rather than assuming rollback or absence.
Other already-running reads may finish their bounded calls; unscheduled reads
and subsequent pages are cancelled on failure. Completion time still depends on CLI/ARM latency,
pagination and graph size; four workers are a concurrency bound, not a
guarantee of completion within a particular duration.

### Supported owned graph and refusal boundaries

- All selected-client-prefixed **and** client-tagged HTTP/native MCP APIs,
  including facades, policy-MCP shards and orphan APIs. Native tools are removed
  first, native servers before source REST APIs. HTTP operation/policy/schema
  and tag descendants are enumerated; API deletion removes these children.
- The exact `<client>-pilot` subscription, **only** with the exact verified
  `<client>-product` resource ID as its scope. Subscription metadata is
  projected; the command never requests `listSecrets`, keys or secret values.
- Product/API association links, the owned `<client>-product`, and its
  policy/tag/group links. Shared groups themselves are never deleted.
  On an **observed Consumption SKU**, the product groups collection is
  unavailable by tier (`MethodNotAllowedInPricingTier`): retirement does not
  invoke it and explicitly marks it `UNAVAILABLE-BY-TIER` in progress and the
  review plan. Other SKU values, including missing/unknown metadata, still
  require group inspection; arbitrary 400/403 responses are never treated as
  empty collections. Product/API/tag/subscription checks remain mandatory.
- The exact `<client>` and `<client>-mock` service tags, after checking every
  API, operation and product tag association. Tags require matching display
  names plus a live owned API/product anchor; a name alone is insufficient.
- Preserved manifest APIs must all use exactly `backend: {mode: mock}`.
  External/hosted/auth/secret-ref configuration and detected leftover
  client-prefixed/tagged named values/backends or other client-prefixed service
  tags are refused, not silently left behind as a purported complete cleanup.
- Additional product memberships, unowned APIs in the product, foreign tags,
  additional subscriptions scoped to the product/APIs, and native tools in
  other APIs referencing retiring operations block retirement. Unknown native
  tool reference shapes fail closed.
- API versions, non-initial revisions, release history, non-HTTP/non-native API
  kinds and API-specific diagnostics are outside this first supported slice.
  No service diagnostics are modified. The inventory is management metadata,
  not analysis of arbitrary policy expressions, developer portal content,
  external consumers or dynamically constructed URLs. Operators must coordinate
  those dependencies separately; this command cannot prove their absence.

Missing APIs, tools, links or pilot subscriptions are supported when remaining
resources still prove their ownership. An entirely absent client is an
idempotent no-op. An approved apply keeps its original tag-anchor evidence
**only in memory** through the final tag deletions; it does not trust receipts
or add a force/adopt flag. If interruption leaves only detached tags, a new
preview refuses to infer ownership: investigate separately through the
operator's approved process. Untagged product/pilot remnants similarly cannot
be adopted from names alone. Keep the initial reviewed plan for that review.

**All resource-group deployment-history records remain audit records.** They
are explicitly reported as remaining, not inventoried or deleted. Retirement
does not delete the APIM, resource group, service diagnostics, identities, RBAC,
shared groups or any customer files. Successful verification means the
supported owned APIM mock graph is absent, **not** that Azure history is erased.
Do not describe this as deleting every trace of a deployment.

## Legacy azd path versus full provisioning

With a legacy repository and complete existing azd outputs, `mcp-kit deploy clients/<id>` remains
supported without target flags. It requires subscription, **tenant**, resource
group, APIM, profile and environment name; a vault is required only with refs.
It uses azd output data, **not azd deployment authentication**. Partial flags
or conflicting normalized output names fail rather than falling back/mixing.

`azd up` is a different operation: it aligns the platform and **every active
client**, including the sample if present. Use it only when that full scope is
approved. Its local `.venv` preprovision hook always previews reconciliation;
`MCP_RECONCILE_APPLY` cannot turn a preview into DELETEs. Explicitly review and
run its repository-only reconciliation command separately before full provisioning
when needed. Full-platform provisioning is not a standalone customer workflow.

## Verify without azd

After selective deployment, obtain the **actual HTTPS gateway origin** and
product subscription key through your approved APIM operations process.
Use the gateway origin (including a custom domain/port if applicable), **not**
an API base path or full MCP endpoint. The verifiers append paths from the
manifest; policy MCP uses the generated `servers.json` shard index.
Do not guess a URL from the APIM resource name or create fake azd outputs.

Supply the key privately as **`MCP_KEY` in the verifier process environment**,
using your approved secret-injection process. Never paste keys into commands,
chat, source, or logs. Neither command below retrieves keys or tokens.

```bash
# Native MCP: initialize, initialized notification, tools/list, exact tool-set comparison.
mcp-kit verify-mcp clients/<client-id> \
  --gateway-url <approved-https-origin> --profile native-mcp

# Policy MCP: also exercises each mock tool's example/x-mock branches.
mcp-kit verify-mcp clients/<client-id> \
  --gateway-url <approved-https-origin> --profile policy-mcp-consumption

# REST mocks, including REST APIs deployed alongside native MCP:
mcp-kit verify-rest clients/<client-id> \
  --gateway-url <approved-https-origin>
```

For example, the shape of an origin is `https://gateway.example.com` (illustrative,
not a deployment target). On Windows use installed `mcp-kit.exe` and a single
line. Explicit MCP mode requires **both** flags; REST requires only the origin.
Missing/invalid `MCP_KEY`, partial MCP flags, HTTP, API paths, URL credentials,
queries and fragments fail without fallback. All redirects are refused, including
same-origin redirects, to prevent forwarding credentials. No insecure TLS/HTTP
override is provided.

Explicit endpoint verification makes **no azd, Azure management, listSecrets,
login, or context-switch calls**. Subscription-key authentication remains the
default. For a matching `inboundAuth.mode: entraJwt`, explicitly pass
`--auth-mode entraJwt` (or its `dual` alias) and privately provide both
`MCP_KEY` and `MCP_BEARER_TOKEN`. The current generated product still requires
a subscription key alongside validate-jwt. There is no bearer-only promise,
token acquisition, new manifest mode or implicit authentication change.
Verification cannot prove inherited policies or that a deployed backend still
matches local configuration.

Native MCP remains **discovery-only by default**, even with external backends.
Append `--exercise-mock` for approved all-mock business calls and schema
assertions. Default REST requires **every API to be mock**. REST and policy MCP
checks may send mock writes; confirm deployed configuration before running.

Controlled external acceptance is a distinct two-phase path: select named
`--fixture API/OPERATION/STATUS/EXAMPLE` entries, review the no-network preview
and its exact inputs/effects, then repeat with `--confirm-fixtures <token>`.
These are not broad production smoke tests or automatic retries. MCP cannot
prove the underlying HTTP status, and unsupported provider wrappers fail rather
than being guessed. See [extended verification](extended-verification.md)
for selectors, authentication, payload support and limitations.

The no-flag commands remain the legacy azd path, including key discovery
(`MCP_KEY` for MCP, `REST_KEY` for REST can override legacy key lookup).
In **explicit subscription-key mode both commands use only `MCP_KEY`**, never
`REST_KEY` or a management fallback; explicit Entra additionally requires
`MCP_BEARER_TOKEN`. Legacy mode shares the redirect, HTTPS and mock-safety
checks. Run verification after deployment and before demonstrations.

References: [native MCP prerequisites and streaming diagnostics](https://learn.microsoft.com/azure/api-management/export-rest-mcp-server),
[named-value metadata](https://learn.microsoft.com/rest/api/apimanagement/named-value/list-by-service).

# Provision a new standalone APIM

The installed **`mcp-kit provision`** command creates one new, public API
Management service in an **explicitly selected, already-existing resource
group**. This is not full-platform provisioning. It never creates, replaces,
adopts or updates the resource group, an existing APIM, or a deployment-history
record. Use [selective deployment](selective-deployment.md) for an existing
gateway, including a gateway this command has just created.

**No resource group yet?** Starting with the 1.3.0 candidate, the installed kit
can create it first through the separate
[`mcp-kit provision-group` workflow](resource-group-provisioning.md).
Ask the companion for a new group; approve its preview/apply, then return here.
The APIM command still checks an existing group, so existing users keep the
same behavior and approvals. The frozen 1.2.0 wheel does not include this new
group-creation command.

No azd installation, environment, login or previous `azd up` is required.
Neither `infra/main.bicep` nor the legacy full-platform template is used.
The customer workspace contains data only; executable code and Bicep come from
the installed kit, not customer `tools/`, `platform/` or environment variables.
Use the dedicated installation virtual environment and an existing customer
directory, or supply `--workspace <directory>`.

## Choose the consumer, then approve the exact target

| Profile | New public service | Capacity | Identity |
| --- | --- | --- | --- |
| `policy-mcp-consumption` | Consumption | 0 | None |
| `rest-consumption` | Consumption | 0 | None |
| `native-mcp` | Basic v2 | 1 | System-assigned |

Consumption is public/mock-only and has no fixed gateway charge, **not a
guaranteed zero bill**. Policy MCP supplies stateless tools, not resources or
prompts. Basic v2 has a fixed charge; native mock and subsequently configured
public external backends are supported. Creating the native system identity
also creates its Azure-managed Entra service principal. It creates no role
assignments, Key Vault, secrets, compute, telemetry, network, private endpoint,
API/tool, subscription key or custom hostname. Existing resources, group tags,
diagnostics and policies are not changed by the kit. Azure Policy/provider
side effects and service-managed defaults are outside the template.
The new service receives the ARM tags `mcp-kit-owner=mcp-openapi-creator-kit`
and an opaque `mcp-kit-creation-id`. These are service ownership markers,
not APIM client tags, a permission grant or approval evidence.

Every account, tenant, subscription, resource group, region, APIM name and
publisher field is required. No cloud target is inferred from environment
variables, defaults or the resource group's region. `--location` accepts the
canonical region name (for example, `westeurope`), not its display label.
Select the publisher's real operational name/email; these are not credentials.
Obtain permission separately for local artifact writes, Azure inspection,
what-if and the exact paid-resource apply. A token is not evidence of a
person's approval.

## Offline scope, actual what-if, explicitly approved apply

First inspect the offline scope:

```text
mcp-kit provision --account <account> --tenant <tenant-uuid> --subscription <subscription-uuid> --resource-group <existing-rg> --location <region> --apim-name <new-name> --publisher-name "<organization>" --publisher-email <email> --profile <profile> --dry-run
```

`--dry-run` performs **no Azure calls, compilation or local writes** and issues
no apply token. It cannot be combined with `--yes` or `--review-token`.

For a real preview, repeat all arguments, remove `--dry-run`, and add
`--confirm-subscription <subscription-uuid>`. Without that flag an interactive
terminal must retype the subscription. The command displays the entire requested
scope before inspection, verifies the active Azure CLI account/tenant/
subscription (never switches/logs in), compiles the trusted Bicep using Azure
CLI's Bicep tooling, and checks:

1. The exact RG exists and is `Succeeded`.
2. Complete, paginated RG APIM/deployment-history inventories contain neither
   the requested service nor the proposed deployment-history name.
3. APIM's global name-availability API explicitly says the name is available.
   In-use, soft-deleted/reserved, unknown, forbidden and malformed responses
   all block. Failed reads are never converted into absence.
4. Actual ARM what-if expands exactly one APIM **Create** with the selected
   publisher, region, SKU, public network and identity configuration.
   `Modify`, `NoChange`, `Deploy`, `Delete`, unknown or unexpanded target changes
   block. Unrelated resources may only be `Ignore`; every such ID is displayed
   and included in review. Separate identity/RBAC resources are not permitted.

This preview writes deterministic, nonsecret compiled template/parameters/name-
check artifacts below `.mcp-kit/provision/`. These are CLI-owned artifacts,
not customer-authored configuration. Symlink/junction/hardlink escapes and
conflicting bytes are refused. The review fingerprint binds workspace, installed
code/assets, exact arguments, compiled artifacts, observed inventory and what-if.
No raw what-if property payload or provider error message is logged.

Review the full context, resource ID, tier/cost, native managed identity,
deployment-history ID and all changes. Then repeat the identical preview command
with **`--yes --review-token <token>`**. Apply always reruns what-if, checks the
token, rechecks account/context, name absence/availability, complete inventory
and artifact/package bytes immediately before starting an Incremental ARM
deployment. There is no unattended fallback or automatic retry.

This is **not a transaction or a resource-name reservation**. ARM deployments
are not conditional create operations: a competing write after the last check
can race the deployment. What-if cannot predict every Azure Policy/provider
effect. Coordinate with other operators and apply least-privilege scoped access.
Do not claim this mechanism makes arbitrary concurrent administrators safe.

## Verification and handoff

Creation starts asynchronously and the command polls the exact deployment
record for up to one hour. It verifies `Succeeded`, the returned APIM resource
ID, region, tier/capacity, public network configuration, publisher, gateway URL
and identity. A native gateway must have a valid system-assigned principal ID
in the approved tenant. It rechecks the active operator before reporting success.
Before reporting completion it also durably writes an exclusive
`creation-receipt.json` beside the compiled artifacts. This record binds the
exact context, deployment, review and artifact fingerprints, service creation
time, creation ETag, ownership tags and managed identity. Missing/invalid
`createdAtUtc` or ETag, conflicting receipt paths and failed disk writes fail
explicitly; they do not trigger recreation or rollback. Receipt conflicts are
checked before Azure apply. Preserve the customer workspace and deployment
history, including when creation succeeds but receipt persistence fails.
Failures/timeouts have an **unknown or incomplete Azure outcome**, never an
automatic rollback: inspect deployment history privately before doing anything
else. A subsequent provisioning call refuses existing service/history, including
partially created resources. Do not delete or adopt them automatically.

The final result is JSON after `[provision-gateway] Result`. Its `status` is
`dry-run`, `preview` or `provisioned`; `applied` is true only after verification.
It reports exact `context`, `resourceId`, `tier`, `capacity`, `identityType`,
`inputFingerprint`, `creationId`, `ownershipTags`, and `azdUsed: false`. Actual preview adds `reviewToken`,
`deploymentName`, `deploymentResourceId`, `artifactDirectory`,
`artifactFingerprint` and `changes`. Success additionally supplies `gatewayUrl`,
`provisioningState`, `principalId`, `identityTenant` and `creationReceiptPath`. Neither this output nor
its local artifacts are importable MCP gateway-inspection evidence.

After success, obtain fresh, separately consented `inspect-gateway` evidence
for this exact target, finalize the client profile, prepare customer contracts
and selectively deploy the client. Provisioning does not bypass manifest,
diagnostics, ownership, secret metadata or verification checks on that path.

## Gateway cleanup is not yet an executable acceptance path

The create receipt is a durable local audit record, not a deletion capability.
It explicitly contains `authorizesDeletion: false`. A deterministic creation
marker alone cannot distinguish a deleted/recreated service; the observed
`createdAtUtc`, identity and deployment must also match. Tags and local files
can be forged by an administrator or same-user shell. Do not import, edit or
backfill a receipt to adopt an existing gateway.

This release does **not** expose an executable `retire-gateway`. The existing
`retire` command remains client-scoped and never deletes APIM. See the
[gateway retirement safety plan](gateway-retirement.md) for the required
ownership, empty-graph, fresh ETag and exact-delete review gates and the
currently unresolved platform evidence. Do not describe new-gateway acceptance
as disposable or cleaned up until a separately approved cleanup path has
actually been validated. Never delete the service by name as a workaround.

## Intentionally blocked

New RGs within this APIM command (use the separate group command);
APIM reuse/update/recovery; arbitrary SKUs; hybrid/isolated/private
networks; user-assigned identities; role grants; Key Vault; secret creation;
telemetry; custom hostnames; Azure Government/China endpoints; azd and automatic
full-platform deployment. Region/tier availability, provider registration,
quota, Azure Policy and operator permissions must already permit the request;
the kit does not mutate those prerequisites. What-if is an ARM simulation and
does not guarantee deployment success.

The CLI reuses the existing proven deployment transport: bounded Azure CLI
processes, safe Windows `.cmd` argument quoting and OS-locale decoding (including
cp1252 when the kit interpreter uses UTF-8). Publisher values are encoded in a
JSON parameter file, never interpolated into shell syntax.

References: [APIM service resource](https://learn.microsoft.com/azure/templates/microsoft.apimanagement/2024-05-01/service),
[APIM name availability](https://learn.microsoft.com/rest/api/apimanagement/api-management-service/check-name-availability?view=rest-apimanagement-2024-05-01),
[ARM what-if](https://learn.microsoft.com/azure/azure-resource-manager/templates/deploy-what-if).

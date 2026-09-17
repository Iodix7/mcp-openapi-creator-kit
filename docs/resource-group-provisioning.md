# Create a resource group before APIM

**Available in the 1.3.0 candidate, not in the frozen 1.2.0 package.** Check
`kit-info` or installed `mcp-kit info` before following this procedure.

You do not need an existing resource group or APIM to start the guided cloud
path. You do need an Azure subscription, an authorized account, Azure CLI with
Bicep, and permission to create a resource group and subscription-scope
deployments. The kit never grants those permissions or registers providers for
you. Start offline if those prerequisites are not ready.

The complete path is:

1. Prepare the scenario and contracts locally.
2. If needed, preview and approve a **new resource group** with `provision-group`.
3. Preview and approve a **new APIM** with `provision`.
4. Inspect that gateway, then preview and approve the client's API/tool deployment.

These are separate approvals, not one blanket "create everything" authorization.
No Git clone or azd environment is required. Creating the group does not create
APIM, deploy APIs, obtain keys, or connect Copilot Studio.

## How the companion guides this choice

Tell the Creator agent:

> I have an Azure subscription but no resource group or APIM. After preparing
> my scenario locally, guide me through creating a new resource group, then
> APIM, then deploying the tools. Show each target and preview before asking
> for approval. Do not create resources until I approve the respective plan.

The agent asks **existing or new resource group**, its name and its canonical
Azure region. The group region and APIM region are independent inputs; neither
is silently inferred from the other. It also collects the full planned gateway
context, consumer/profile, publisher and costs before constructing the proposal.

For a new group, `workflow-status` accepts
`provisioning_target.resource_group_mode="new"` and an explicit
`resource_group_location`, alongside the other new-gateway fields. After local
preparation, `currentStep.stage` is `provision-resource-group` and
`nextInvocation` pins the installed executable, customer directory and arguments.
The same instructions appear in the dashboard. The MCP only returns this plan;
the host must separately authorize the CLI invocation.

`resource_group_mode="existing"` is the compatibility default for old callers,
not evidence that the named group exists. Existing-group users keep the original
APIM path unchanged; they do not run `provision-group`.

## Preview and apply

Use the installed CLI from the customer data root, or add
`--workspace <absolute-customer-directory>` before the command. Read `--help`
from your actual installed version. Start with a wholly offline scope check:

```text
mcp-kit provision-group --account <account> --tenant <tenant-uuid> --subscription <subscription-uuid> --resource-group <new-group-name> --location <group-region> --dry-run
```

`--dry-run` makes no Azure calls, runs no compiler, writes no local files and
issues no apply token. It cannot be combined with apply arguments.

For a real preview, remove `--dry-run` and approve the full account, tenant,
subscription, group name and group region. Keep the planned APIM profile and
costs visible; azd is **not used**. In an interactive terminal the operator
confirms the subscription. A noninteractive host may supply
`--confirm-subscription <subscription-uuid>` only after that approval.

The command checks the active Azure public-cloud context without switching
accounts. It uses the installed subscription-scope
`platform/resource-group.bicep`, verifies the group and deployment-history name
are absent, and accepts only the expected resource-group Create in ARM what-if.
An existing group, incomplete inventory, denied read, unrelated mutation,
unexpected change type or changed context is a blocker, not permission to
overwrite or reuse something.

Review the preview's target, group tags, deployment-history entry and changes.
For the separately approved apply, repeat the exact command with:

```text
--yes --review-token <token-from-that-preview>
```

Apply repeats validation and binds the token to the scope, trusted artifacts
and observed plan. It verifies creation and persists a local creation receipt
before reporting `status: provisioned`. The receipt is an audit record, not
authorization to create APIM or delete the group.

Local records live below `.mcp-kit/provision-group/`: the reviewed artifact
directory contains `creation-receipt.json`, and `attempts/` contains a durable
per-target attempt marker. A recorded attempt prevents automatic retries after
an uncertain outcome. Preserve these CLI-owned records; editing or deleting a
marker is not a recovery procedure or permission to repeat a deployment.

## Continue to APIM

Only after verified group creation:

- Change `provisioning_target.resource_group_mode` to `"existing"` and remove
  `resource_group_location`.
- Keep `gateway_mode="new"` and the chosen group's name.
- Call `workflow-status` again. The next stage is the separate APIM preview.

The existing [APIM provisioner](gateway-provisioning.md) independently checks
that the selected group exists and is ready. A model-supplied preference or
local receipt cannot bypass that check. After APIM creation, obtain fresh
gateway inspection and use [selective deployment](selective-deployment.md).

## Safety and limitations

- This command is **create-only**. It does not adopt, relocate, retag, update
  or delete existing groups, and does not create RBAC, networking or Key Vault.
- Permissions for an existing group do not necessarily permit creating a new
  group or subscription deployment. Ask the subscription administrator for the
  appropriate approved access; the kit does not elevate it.
- Group creation alone has no APIM capacity cost; resources subsequently placed
  in it can incur charges. Basic v2 has a fixed gateway charge; Consumption
  can have usage charges.
- What-if is not a transaction, name reservation, or guarantee of no provider/
  Azure Policy side effects. Coordinate with other operators. A concurrent
  write after the last check can still race an ARM deployment.
- A failed/timed-out apply has an incomplete or unknown Azure outcome.
  A receipt-write failure after Azure creation does not roll resources back.
  Preserve the reported history and inspect it through a separately approved
  action; do not retry creation or delete the group automatically.
- There is no group-retirement command. Deleting a group would also delete its
  resources; client retirement never authorizes that.
- Local tests and Bicep compilation do not establish live Azure acceptance.
  This extension was authorized for implementation without Azure deployment.

Reference: [Bicep subscription-scope deployments](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/deploy-to-subscription).

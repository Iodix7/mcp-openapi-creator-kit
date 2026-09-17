# Skill: manage a deployed client lifecycle

Start with `kit-info` and read `kit-reference(name="selective-deployment")`.
The installed companion reads/plans only. Run explicit `mcp-kit` commands from
the customer root, or pass `--workspace <directory>` / `--root <directory>`.
Use the dedicated installation venv, never a customer script or global Python.

## Safety model

A resource is owned only when its APIM name starts with `<client>-` AND it has
the APIM tag `<client>`. Resources failing either check are never deleted.
Native tools are removed before their source API operations; native servers
before source REST APIs. Inventory also blocks unowned named-resource overwrite.
Existing diagnostics are read-only and unsafe response-body logging blocks MCP.

```bash
mcp-kit deploy clients/<id> \
  --subscription <subscription-id> --tenant <tenant-id> \
  --resource-group <resource-group> --apim-name <existing-apim> --profile <profile>
```

Explicit context needs neither azd authentication nor azd outputs. Approve the
displayed account/tenant/subscription/resource group/profile and azd "not used"
before cloud inspection. This command previews ownership DELETEs plus ARM
what-if. It never applies by default. Read both plans, then repeat all flags
with `--yes --review-token <token>`. Noninteractive context approval uses
`--confirm-subscription`; it does not bypass plan review. Revalidated inventory
and input fingerprints reject stale review tokens. Simulation cannot guarantee
zero impact from concurrent changes.

## Add or change an API

Modify the manifest and an unshared contract. Run `mcp-kit build clients/<id>`;
for policy MCP also `mcp-kit build-policy clients/<id>`. Preview/deploy with the
complete flags above, then verify. Stable endpoint URLs let consumers rediscover
tool changes; policy sharding may change URL count and needs consumer updates.

## Mock to external

Keep the contract unchanged. Set backend.mode external only for native MCP,
add URL/outbound auth and secretRef names. The operator creates secrets through
an approved process. Verify metadata and permission before deployment. No dummy
vault or secret checks are needed for mock-only clients without selected refs.
Consumption remains mock-only/public; hosted and mTLS are unsupported.

## Rename, remove or create a variant

Rename operationId and mcpTools together. Reconciliation removes the old native
tool before importing the new operation. Avoid renames immediately before demos.
Tool names are unique service-wide; a shared contract needs a new variant when
the same tools are selected by two clients on one gateway.

```bash
mcp-kit variant <source-client> <new-client>
# Review before writing; no overwrite:
mcp-kit variant <source-client> <new-client> --write
# Alternatively import the packaged fictional sample under a new identity:
mcp-kit import-sample <new-client>
mcp-kit import-sample <new-client> --write
```

Automatic variants are mock-only/no credentials, prefix tool IDs, validate
references and collisions, and preserve examples/canonical schemas. They are
not a rename operation. Never overwrite customer contracts or source clients.

Remove an API from the manifest, preview, verify tool/server/API deletion order,
then deploy. Never manually delete APIM resources outside the reviewed plan.

## Remove or rename a client

Before deleting client data, add its ID to clients/removed-clients.yaml and keep
it until every persistent environment is reconciled. Full-environment
reconciliation and azd up remain legacy repository-only procedures, never
implicit data-workspace operations. Azd preview hooks remain dry-run regardless
of ambient MCP_RECONCILE_APPLY; deletion requires explicit approval.
For standalone **mock-client retirement on an existing APIM**, preserve the
client data and use the installed reviewed retirement command:

```bash
mcp-kit retire clients/<id> \
  --subscription <subscription-id> --tenant <tenant-id> \
  --resource-group <resource-group> --apim-name <existing-apim> --profile <profile> \
  --confirm-subscription <subscription-id>
```

This is a read-only preview: it prints every exact direct and cascading
deletion resource ID and a retirement-specific review token. After the operator
reviews the complete context and deletion list, repeat identical flags with
`--yes --review-token <token>`. There is no azd fallback, automatic
reconciliation apply, provisioning or local data deletion. Keep the manifest
and contracts: no build/generated shard index is required to retire owned
native servers, REST APIs, facades or policy-MCP shards, including old orphans.

Retirement deletes native tools before servers/source APIs, the exact
product-scoped pilot subscription, product/API links, APIs and their supported
children, the owned product and its policy/group/tag links, then unshared
client service tags. Group links are removed, never the groups themselves.
Every mutation rechecks context, source fingerprint, inventory and remaining
plan; API DELETE completion is observed before moving on. Changed inventory
stops the sequence; it is not an atomic transaction and cannot prevent races
after a check. Preview partial state again after failure, never improvise
manual DELETEs or use the deployment reconciler as complete retirement.
Inventory uses up to four parallel readers and the complete paginated
`tagResources` association graph, with no cross-step cache. Stream the flushed
stderr stage/resource-ID progress. Each CLI call times out after 60 seconds
within a five-minute snapshot budget; post-DELETE absence polling has a
two-minute total read budget. Timeout attempts bounded cleanup of the spawned
process tree; a timed-out DELETE has
an unknown cloud outcome and is never retried automatically. A proven-absent
client needs only service inventories plus native-reference checks, not a walk
of every unrelated operation. This shortcut is disabled during active apply's
full-graph revalidation. See the selective-deployment reference for exact bounds.

Only explicit `backend: {mode: mock}` is supported. External/hosted backends,
outbound auth/secret refs, leftover client named values/backends/external tags,
API-specific diagnostics, versions/revisions/releases and shared relationships
are refused. Prefix **and** client tag establish API/product ownership; the
pilot must point to the exact verified product. Additional subscriptions,
product memberships, foreign tags, or cross-client native tool references
block the entire initial plan. Tags need an owned live anchor; a standalone
detached tag with no remaining owned API/product cannot be safely adopted.
An uninterrupted approved apply retains its anchor evidence in memory while
deleting the final tags. After an interrupted final-tag phase, a new invocation
may therefore require separate operator investigation rather than unsafe
automatic adoption. An already absent client is an idempotent no-op.

**Deployment-history records remain as audit records**, are not inventoried
or deleted, and do not grant ownership. The APIM, resource group, service
diagnostics, identities/RBAC and all local data remain unchanged. This is
complete removal of the supported owned APIM mock resource graph, not removal
of Azure audit history. Review the full
`selective-deployment` reference for coverage and unsupported dependencies.

## Completion gate

```bash
mcp-kit verify-mcp clients/<id> --gateway-url <approved-https-origin> --profile <profile>
# Mock REST:
mcp-kit verify-rest clients/<id> --gateway-url <approved-https-origin>
```

These explicit endpoint checks avoid azd and Azure management discovery.
Subscription key goes only in private MCP_KEY process environment. Confirm
expected operations/responses, no unexpected orphan servers, correct consumer
URLs, and record any sharding-related consumer updates. Never print secrets.
Native MCP defaults to discovery only; use `--exercise-mock` for approved
all-mock business assertions. Explicit `--auth-mode entraJwt` or `dual`
also requires private MCP_BEARER_TOKEN alongside MCP_KEY, matching the current
generated product policy. For real backends, read `extended-verification`:
preview exact named `--fixture API/OPERATION/STATUS/EXAMPLE` selections, then
authorize only that reviewed scope with `--confirm-fixtures <token>`.
An actual failure does not prove a real write was rolled back or never executed.

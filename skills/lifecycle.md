# Skill: manage a deployed client lifecycle

Use this procedure to add, change, rename, migrate, or remove APIs and tools for
an existing client.

- `python tools/deploy-client.py clients/<id>` previews one client.
- `python tools/deploy-client.py clients/<id> --yes` applies the reviewed plan.
- `azd up` aligns the platform and every active client.

Both rely on lifecycle reconciliation because ARM deployments are incremental.

## Safety model

A resource is owned by a client only when both are true:

1. its APIM resource name starts with `<client>-`;
2. it has the APIM tag `<client>`.

Resources that fail either check are never deleted. Native MCP tools are removed
before the source API operation; native MCP servers are removed before REST
source APIs.

Dry-run first:

```bash
python tools/reconcile-client.py clients/<id>
```

For a full deployment:

```bash
azd provision --preview
python tools/reconcile-all.py --apply --skip-if-unprovisioned
azd up
```

Preview is non-destructive. Read every printed DELETE before applying it.

## Add or change an API

Update the manifest and, when needed, the contract. Then run:

```bash
python tools/deploy-client.py clients/<id>
```

Review every planned DELETE. Only then apply the plan:

```bash
python tools/deploy-client.py clients/<id> --yes
```

After deployment:

```bash
python tools/verify-mcp.py clients/<id>
```

If endpoint URLs stay stable, the consumer discovers changed tools dynamically.

## Move mock to an external backend

Keep the OpenAPI contract unchanged. Change the API manifest entry to
`backend.mode: external`, add URL and outbound auth, create every `secretRef` in
Key Vault, deploy the client, and verify the contract against the real backend.

## Rename or remove a tool

Remove or rename the `operationId` and corresponding `mcpTools` entry together.
The reconciler deletes the old native tool before APIM imports the revised API.
Avoid tool renames immediately before a demonstration.

If APIM returns a 502 after cleanup, check whether another client on the same
APIM already exposes that tool name. Selected MCP tool names are service-wide.

## Remove an API

Delete its manifest entry, run a dry-run, verify the planned tool/server/API
order, then deploy. Use manual APIM deletion only as a documented diagnostic
fallback.

## Remove or rename a client

Before deleting `clients/<id>`, add the ID to `clients/removed-clients.yaml`.
The full reconciler treats that client's desired state as empty while preserving
the prefix-plus-tag ownership checks.

Keep the tombstone until every persistent environment has been reconciled. Then
remove it in a later change.

## Completion gate

After every deployment:

- run `verify-mcp.py` or `verify-rest.py` for the client;
- confirm no unexpected orphan server remains;
- confirm endpoint URLs configured in the consumer still match outputs;
- record any required consumer update when policy sharding changes URL count.

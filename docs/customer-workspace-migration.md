# Migrate existing customer data without moving the kit

No migration or pilot transfer is performed automatically. Stop the old
companion, preserve a backup and obtain approval before changing a pilot.

1. Install the supplied versioned wheel in a new dedicated Python >=3.12 venv.
   Keep the existing environment and customer demo unchanged until verification.
2. Choose an existing customer directory. It may be the legacy repository root:
   installed commands still read its clients/apis/docs, but ignore its executable
   tools, AGENTS/skills and dashboard HTML. To separate data, explicitly copy
   only approved `clients/<id>/mcp-manifest.yaml`, contracts under `apis/`,
   scenario documents under `docs/`, and optional `catalog/metadata.yaml`.
3. Preserve operation IDs, selected tools, examples, x-mock rules and deployment
   identity. Do not create a new sample/variant merely to migrate an existing
   deployed client. Preserve removed-clients.yaml tombstones as applicable.
4. Never copy .env, .azure outputs, credentials, venvs or generated artifacts into
   a distributable. Secret values stay in the operator's secret management.
5. Run `mcp-kit --workspace <customer-root> init` to inspect the data-only layout.
   `init --write` creates missing data directories only, without overwriting.
6. Build the selected client, policy output if compatible, catalog and target
   report with the installed CLI. Compare tool sets, example data, generated
   policies and URLs. Bicep modules are regenerated under generated/kit-modules.
   The protected legacy infra/main.bicep is not modified.
7. Emit `mcp-kit --workspace <customer-root> vscode-config`, inspect it and
   manually set user MCP config. It pins the new installation and data directory,
   not the current editor's Python or repository.
8. Confirm kit-info provenance/version and read workflow-guide. Browse built-in
   starters separately from workspace clients. Dashboard uses trusted installed
   HTML/JS even when old customer templates remain.
9. Only after approval, preview selective deployment with complete explicit
   tenant/subscription/RG/APIM/profile flags. Review ownership DELETEs and
   what-if; apply requires a current token. No azd login/prior outputs are needed.
10. Verify endpoints using approved HTTPS origin and private MCP_KEY injection.
    Keep the old customer data/environment until the demo and consumer tool
    connections have been confirmed. There is no automatic cleanup/deletion.

This procedure preserves existing customer demo assets. It does not transfer
any paused pilot or change Azure/user settings by itself.

# Copilot plugin: skill, agent and MCP

The plugin is the guided entry point for colleagues. It reuses the installed
MCP OpenAPI Creator Kit rather than replacing the server, CLI or dashboard.
The [release bootstrap](installation.md) installs a supplied, checksum-verified
wheel in a dedicated runtime and exports a bundle for the colleague's exact
customer data root, using the supported **Copilot plugin
format**. It is not a VSIX extension or an M365 plugin.

| Component | Purpose |
|---|---|
| `skills/create-mcp/SKILL.md` | Native workflow activation and outcome-oriented guidance |
| `skills/create-mcp/references/` | Constitution and procedures copied verbatim from the kit |
| `agents/mcp-openapi-creator.agent.md` | Dedicated Copilot persona using that skill |
| `.mcp.json` | Existing read-only server, with an exact interpreter and customer root |
| `connection.json` | Runtime identity, CLI prefix and guidance hashes |
| `plugin.json` | Copilot plugin metadata and component paths |

Only the native entry-point instructions are plugin-specific. Discovery,
onboarding and lifecycle still have one maintained source in `skills/`, packaged
in the wheel. Exported references must not be edited as a separate procedure.
The MCP tools/resources/prompts and plugin refer to the same kit version.

## 1. Install the supplied kit wheel

For a colleague handoff, follow [installation and updates](installation.md):
explicitly invoke the supplied `install-kit.py` with Python >=3.12, the local wheel
and its trusted SHA-256, exact customer workspace, and new runtime/plugin paths.
Preview is the default; `--apply` installs and exports but never enables a host.
The resulting receipt prints the exact host configuration. **Skip section 2**
when that bootstrap already exported the plugin successfully.

For an already-installed runtime, the lower-level
[wheel installation](local-mcp-server.md#end-user-wheel-installation) and export
commands below remain available. No PyPI publication is assumed. Neither plugin
export nor MCP startup installs anything.

The examples below assume an installed runtime at `C:\kit-install` and an
existing customer data directory at `C:\customer-data`. The data root can start
empty: the skill guides initialization and optional sample import.
It needs no kit clone or local `.venv`.

## 2. Preview and export the plugin

Choose a new output directory **outside** customer data and the kit installation.
Its parent must already exist:

```powershell
New-Item -ItemType Directory C:\kit-plugins
C:\kit-install\Scripts\python.exe -I -m mcp_openapi_creator_kit.cli --workspace C:\customer-data plugin-export --output C:\kit-plugins\creator
```

The JSON plan lists files, provenance and both loading options. No output is
created until the explicit write:

```powershell
C:\kit-install\Scripts\python.exe -I -m mcp_openapi_creator_kit.cli --workspace C:\customer-data plugin-export --output C:\kit-plugins\creator --write
```

On POSIX use the installation's `bin/python` and chosen absolute paths.
Existing outputs are refused, including empty directories. Use a new directory
when regenerating; no overwrite, global installation, model call or Azure
operation occurs during export.

## 3. Load it in Copilot

Sign in to GitHub Copilot yourself in the chosen host, then review the generated
bundle before enabling it. The bootstrap does not perform sign-in or activation.
A **Sign in to use GitHub Copilot** prompt blocks host acceptance, even when the
wheel installation succeeded; see the [host activation instructions](installation.md#3-review-and-load-one-host-manually).
Choose one method:

**Copilot CLI**

```powershell
copilot plugin install C:\kit-plugins\creator
copilot plugin list
```

Some CLI releases warn that direct-path installs will be deprecated in favor of
marketplaces. Local installation is a prototype path, not a marketplace
publication; use a host that still supports it or the VS Code local option below.

**VS Code local plugin**

Merge the exact `installation.vscodeSettings` object returned by the exporter
into the intended VS Code user settings, preserving unrelated entries:

```json
{
  "chat.pluginLocations": {
    "C:\\kit-plugins\\creator": true
  }
}
```

Use a host supporting the Copilot plugin format. Agent plugins must be enabled.
VS Code also documents discovery of CLI-installed plugins; do not register
the same bundle repeatedly using multiple paths. Start a new chat/reload the
plugin if it does not appear.

Plugin installation can imply trust of its MCP servers, and enabling a plugin
can start its MCP automatically. The configured server does not install
dependencies or access Azure at startup. No additional startup trust prompt is
promised. Do not enable a duplicate standalone `vscode-config` connection for
the same customer.

## 4. Start with the desired result

Select the **mcp-openapi-creator** custom agent, or invoke the native skill:

```text
/mcp-openapi-creator:create-mcp
```

For the CLI's `--agent` flag, use the fully qualified plugin agent identifier:
`mcp-openapi-creator:mcp-openapi-creator`. The shorter display name alone is not
the CLI identifier. The native skill tool may report its local name `create-mcp`.

For a first offline case, describe a scenario rather than kit implementation
details, for example:

> Create a fictional customer-care MCP scenario named acme, starting from the
> built-in sample. I want public mock tools on policy-mcp-consumption. Guide me
> through a coherent specification, valid contracts and manifest, offline
> preparation and the dashboard. Do not access Azure or deploy.

This is an example request, not automatic authorization to run a model or
change files. The skill follows the user's requested scope and the maintained
procedures. A successful offline result includes:

- a client manifest and contracts with correctly prefixed, unique tool IDs;
- business narrative plus technical sections generated by `spec-sync`;
- successful `prepare` and refreshed workflow status;
- the existing browser dashboard, with offline/preview/deployed states kept distinct.

The agent reads `kit-info` and `workflow-status`, follows the current step and
uses exact `nextInvocation` executable/arguments/cwd for runnable stages.
For cloud work without an existing resource group, the 1.3.0 candidate asks
existing/new group and its region, then guides `provision-group`, APIM
`provision`, and selected-client deployment as separate approved stages.
The [resource-group procedure](resource-group-provisioning.md) is available
through `kit-reference` too; it is not an instruction to run a bare Azure
command or grant blanket terminal permission.
For other CLI commands, it reads help rather than guessing positional syntax.
If validation fails, it corrects the relevant data and retries the requested
operation. Completion is the agreed outcome, not merely the first successful
tool call. The plugin does not add a new executor or change the existing
inspection, preparation, preview, apply or verification semantics.

## Updating and switching customer roots

The portable handoff consists of the wheel, standalone bootstrap, checksums and
release instructions—not an exported directory or copied venv. The export
contains **absolute runtime and customer paths**. This deliberately
avoids accidentally using another open repository or a customer-supplied script.
It also means a colleague must generate their own bundle after installing the
wheel; sharing this generated directory is not a portable runtime installation.

For an upgrade, run the new release's bootstrap with fresh runtime/plugin paths
and the same customer root. It preserves the old runtime/export and customer
data; only a successful new installation should be enabled. Do not move a venv.
For a changed data root, export a new directory and reload/reinstall it.
Copilot CLI caches plugin contents.
Disable the previous connection before enabling the replacement. The prototype
uses one plugin/server name; use one active bundle for the selected data root,
which can contain multiple clients.

`connection.json` binds the plugin to the kit provenance. If `kit-info` reports
a different version, source or manifest hash, regenerate instead of using stale
procedure copies. Source-development exports are explicitly labeled as such
and are not installed-package acceptance evidence. They have no installed asset
manifest hash; regenerate them after source/guidance edits as well.

## Prototype boundaries

This is a generated, locally loadable plugin, not a marketplace publication or
an automatic host installer. The standalone release bootstrap explicitly installs
Python dependencies into a new dedicated venv and exports the path-bound plugin;
Python itself and host enablement remain operator prerequisites/actions.
The root source repository itself is not a ready-to-install plugin manifest.

The format choice is deliberate. Both VS Code and Copilot CLI support the
Copilot layout with `.mcp.json` and absolute executable paths. The newer
[Agent Plugins 1.0 command rules](https://agent-plugins.org/plugin-authors/mcp-servers)
require a bare executable or plugin-relative `./` path. Its JSON schema alone
does not enforce that semantic restriction. Rather than silently introducing a
PATH-selected Python or an untested bootstrap wrapper, this prototype keeps the
exact installed interpreter and does not declare the Agent Plugins 1.0 schema.

The dedicated agent inherits the host's tools: VS Code and Copilot CLI tool
identifiers differ, so the first prototype does not ship an unverified common
allowlist. Skills improve guidance but do not guarantee model completion.
No hooks, global permission changes or OS isolation are added. In particular,
this does not repair or make claims about preventive file-edit confirmation.

Offline installed-package tests can verify exported structure, procedure
identity, the configured stdio server and deterministic workflow results.
They do not prove that a host loaded a native skill or that a real model
completed the scenario. Actual Copilot/VS Code evaluation and live Azure remain
separate evidence, with separately approved scope.

A model-free native loader check with Copilot CLI 1.0.79 successfully installed
the local bundle in isolated configuration, reported one installed skill and
listed the plugin. That verifies local registration, not agent selection,
skill invocation by a model, or the VS Code loading path. See
[acceptance boundaries](e2e-testing.md#copilot-plugin-acceptance).
The subsequent [native CLI offline E2E](e2e-testing.md#observed-native-cli-offline-e2e-2026-09-11)
did activate the agent, skill and MCP and produce a prepared fictional customer
with a working dashboard. Its runner-assisted result is not a VS Code or live
Azure acceptance result.

The separate [VS Code 1.2.0 offline E2E](e2e-testing.md#observed-vs-code-offline-e2e-2026-09-17)
also completed: native skill, real companion calls, three-tool scenario,
successful local preparation, and a dashboard independently observed inside
VS Code. It required assisted host setup and user approvals; the model recovered
from its own malformed contract edit. This is one interactive offline result,
not unattended completion or live Azure acceptance.

### Host permissions and inherited customizations

The plugin does not restrict all other host tools. In the observed VS Code run,
a separate user-data/extensions directory still discovered user-level plugins,
skills, hooks, and MCP servers. Do not treat a temporary profile as a sandbox.
If unrelated servers start, inspect the host's supported controls and prefer
workspace-scoped changes where available; verify the scope before disabling
anything. Do not globally disable colleagues' integrations or turn on blanket
terminal approval as part of these installation instructions.

The host may also request permission to read the pinned `connection.json` and
packaged guidance outside the customer folder. Check that those paths belong to
the trusted runtime/plugin you installed before approving. A dashboard showing
a missing provisioning target after offline preparation is expected; it does
not mean another deployment command should be approved automatically.

## Official format references

- [VS Code agent plugins](https://code.visualstudio.com/docs/agent-customization/agent-plugins)
- [Creating Copilot CLI plugins](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating)
- [Native skill format](https://code.visualstudio.com/docs/agent-customization/agent-skills)

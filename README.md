# MCP OpenAPI Creator Kit

**Describe the API tools you need. Let GitHub Copilot guide you from the idea to
an OpenAPI contract, an Azure API Management configuration, and a capability
dashboard.**

The kit installs locally as a **Copilot plugin: one workflow skill, one dedicated
agent, and a read-only MCP companion**. You do not need to clone this repository
to use a supplied release. Your scenarios live in your own customer folder.

Start offline with fictional data. When you are ready, use the kit's separate,
explicitly approved deployment workflow to publish the generated business tools
through Azure API Management (APIM).

> **Preview status:** this source targets **1.3.0**, adding optional resource-group
> creation. Use a supplied 1.3.0 candidate for that feature; it is not present
> in the immutable 1.2.0 wheel. The earlier 1.2.0 candidate completed an interactive offline
> workflow in real VS Code: native skill, companion calls, scenario creation,
> local preparation, and a rendered dashboard. This is not certification of
> every Azure deployment or Copilot Studio integration. See
> [what has been verified](#what-has-been-verified).
>
> **Community project:** independently maintained; not an official Microsoft
> product. Microsoft, Azure, and Copilot Studio are trademarks of Microsoft
> Corporation.

[Installation](#install-and-connect-to-vs-code) |
[First scenario](#create-your-first-scenario) |
[Dashboard](#use-the-dashboard) |
[Azure deployment](#when-you-are-ready-for-azure) |
[Troubleshooting](#troubleshooting) |
[MIT License](LICENSE) | [Support](SUPPORT.md) | [Security](SECURITY.md)

## What you can do

| Your goal | What the kit provides |
|---|---|
| Turn an idea into an API-tool scenario | Guided discovery of users, tasks, tool inputs/outputs, and a demo storyline |
| Create or reuse interface agreements | OpenAPI 3.0.x contracts, reusable schemas, fictional examples, and client-specific variants |
| Prepare a mock MCP without backend code | Generated APIM policies and Bicep; request-dependent mock responses come from contract examples and `x-mock` rules |
| Understand and demonstrate the scenario | A browser dashboard with scenarios, APIs, tools, mock rules, policy budgets, and workflow status; English and Italian supported |
| Publish to an existing APIM | Selected-client deployment with context checks, reconciliation preview, ARM what-if, and reviewed apply |
| Start with only an Azure subscription | Create a resource group if needed, then Consumption or Basic v2 APIM, then deploy the client; each stage has a separate preview and approval |
| Evolve a demo into a real integration | Per-API migration from mock to an external HTTP backend on a compatible native-MCP gateway |
| Maintain deployed client APIs | Explicit update and client-retirement workflows with ownership checks |

The **contract is the source of truth**: selected `operationId` values become
MCP tool names, response examples supply mock data, and `x-mock` selects those
examples based on requests. Generated policies are outputs, not files to hand-edit.
Mocks do **not** maintain state or perform real business calculations.

### Two different MCPs

```text
AUTHORING, on your machine
VS Code / GitHub Copilot
  + Creator agent and skill
  + Local Creator MCP --> guidance, catalog, validation reports, dashboard
  + Host file/terminal tools --> installed CLI --> your customer folder

RUNTIME, only after an approved Azure deployment
Copilot Studio / another MCP consumer
  --> APIM business MCP endpoint
      --> contract-based mock responses OR a customer-owned HTTP backend
```

The local Creator MCP is **not** the business endpoint you give to Copilot
Studio. It does not execute shell commands or deploy resources. The coding
agent uses the host's separate file/terminal tools and the installed `mcp-kit`
CLI for changes. Host permissions still matter: the plugin is not a sandbox
or an automatic approval system.

## Install and connect to VS Code

### 1. Check prerequisites

For the offline workflow you need:

- **Python 3.12 or later**, with `venv` and `ensurepip`.
- **VS Code with GitHub Copilot access**, signed in, and a version supporting
  [agent plugins](https://code.visualstudio.com/docs/agent-customization/agent-plugins)
  and the `chat.pluginLocations` setting. An installed extension alone is not
  proof that Copilot is signed in or available under your organization policy.
- A trusted supplied kit release, plus access to Python dependency downloads
  or a supplied compatible offline wheelhouse.
- A writable customer folder and separate locations for the runtime and plugin.

**No Azure subscription, Azure login, Azure CLI, `azd`, Git clone, or
customer-local virtual environment is required to start offline.** Copilot use
is subject to your plan and usage limits.

The commands below use Windows PowerShell. For Linux/macOS, use the
[POSIX installation instructions](docs/installation.md#posix-scope); the real
VS Code acceptance described here was on Windows, not a cross-platform host test.

### 2. Obtain the release

Use the [GitHub releases page](https://github.com/Iodix7/mcp-openapi-creator-kit/releases)
for **actually published** versions, or a trusted maintainer-supplied candidate.
The source version in this repository does not mean that a release is available
yet. No PyPI package, `uvx` bootstrap, or marketplace installation is assumed.

The release directory contains:

```text
install-kit.py
mcp_openapi_creator_kit-<version>-py3-none-any.whl
mcp_openapi_creator_kit-<version>.tar.gz
SHA256SUMS
provenance.json
INSTALL.txt
```

Extract an archive before use. Point `$Release` below at the directory containing
`install-kit.py` and the **single kit wheel**, not at a dependency wheelhouse.
The source archive is for provenance/rebuilding; you do not need to unpack it.
Verify the bootstrap checksum against trusted release information before running
it. Checksums detect changed bytes; they are not a publisher signature.

### 3. Preview the installation

Choose your paths. The customer and parent directories must exist; the new
runtime and plugin directories must **not** exist. Do not use paths through
symlinks or junctions. No administrator shell is required if the locations are
writable by your account.

```powershell
# Replace these example paths with your own.
$Release = 'C:\Kit releases\1.3.0'
$Python = 'C:\Python312\python.exe'  # Your actual Python 3.12+ executable
$Workspace = 'C:\Customers\Acme'
$Runtime = 'C:\Kit runtimes\1.3.0-acme'
$Plugin = 'C:\Kit plugins\1.3.0-acme'

New-Item -ItemType Directory -Force -Path $Workspace, 'C:\Kit runtimes', 'C:\Kit plugins' | Out-Null
Get-FileHash -Algorithm SHA256 "$Release\install-kit.py"
Get-Content "$Release\SHA256SUMS"
# Compare the bootstrap hash with the trusted release information.

$Wheel = @(Get-ChildItem -LiteralPath $Release -Filter '*.whl')
if ($Wheel.Count -ne 1) { throw 'Expected exactly one kit wheel in the release directory' }
$InstallArgs = @(
  '-I', "$Release\install-kit.py",
  '--wheel', $Wheel[0].FullName,
  '--sha256-file', "$Release\SHA256SUMS",
  '--workspace', $Workspace,
  '--install-dir', $Runtime,
  '--plugin-dir', $Plugin
)

# Optional: only when a verified, compatible dependency wheelhouse was supplied.
# $InstallArgs += @('--wheelhouse', 'C:\Kit releases\dependencies', '--offline')

& $Python @InstallArgs
if ($LASTEXITCODE -ne 0) { throw 'Preview failed; resolve the reported problem before continuing' }
```

This is a **read-only installer preview**. Review the wheel identity, paths,
planned commands, and host instructions. Online installation resolves dependency
versions at installation time. Use the same complete, verified wheelhouse for
repeatable offline dependency installation; offline wheels must match your
OS, Python version, and architecture.

### 4. Install the reviewed plan

In the same PowerShell session, after reviewing the preview:

```powershell
& $Python @InstallArgs --apply
if ($LASTEXITCODE -ne 0) { throw 'Installation failed; do not enable this plugin' }
```

Success reports **`status: installed`** and writes
`<runtime>\installation.json`. It creates a dedicated virtual environment,
installs the verified kit, and exports a plugin pinned to that runtime and
customer folder. It does **not** change VS Code settings, log in, invoke a model,
or deploy Azure resources.

Do not copy another person's virtual environment or exported plugin: those
contain machine-specific paths. Each colleague runs the installer locally.
For failure recovery, approved corporate certificates, and detailed dependency
handling, see [installation and updates](docs/installation.md).

### 5. Enable the plugin in VS Code

1. Open your customer folder in VS Code and sign in to GitHub Copilot.
2. Run **Preferences: Open User Settings (JSON)** from the Command Palette.
3. Merge the exact `host.vscodeSettings` entry printed by the installer into
   your settings, preserving existing entries. For the example above:

```json
{
  "chat.pluginLocations": {
    "C:\\Kit plugins\\1.3.0-acme": true
  }
}
```

4. Review any host trust requests. Reload VS Code if needed, open a new chat,
   and select the **mcp-openapi-creator** agent.
5. Check **MCP: List Servers**: the Creator server should start and discover its
   tools. An enabled plugin alone does not prove a working MCP connection.

Use **one connection**: the plugin already includes its MCP. Do not also add
the standalone configuration for the same customer. The pinned folder remains
the MCP's data root even if you later open another repository; keep the intended
customer folder open so the coding agent's edits target the same location.

If your host does not support local agent plugins, see the
[standalone MCP alternative](#other-ways-to-connect). Do not assume that adding
an MCP server also installs the native agent and skill.

## Create your first scenario

With the Creator agent selected, paste this prompt:

> Create a fictional customer-care scenario for Acme with three tools: check
> coverage, get a case status, and create a support request. Guide me through
> the scenario specification, OpenAPI contracts, client manifest, local
> preparation, and the dashboard. Use public mock MCP with
> policy-mcp-consumption. Use only this customer workspace and fictional data.
> Do not access Azure or deploy anything.

Alternatively, invoke the native skill with
`/mcp-openapi-creator:create-mcp` and describe your scenario.

The agent should inspect `kit-info` and `workflow-status`, ask for missing
scenario decisions, and consult the built-in catalog before inventing new
schemas. Review its proposal, then authorize the agreed local file changes and
commands through your host. **Do not enable blanket terminal approval just to
avoid prompts.** An empty customer folder is valid; initialization and sample
import are explicit workflow steps, not automatic startup actions.

An offline result should contain:

| Output | Where to find it |
|---|---|
| Scenario narrative and contract-derived technical sections | `docs/<client-id>/spec.md` |
| OpenAPI contracts with fictional examples | `apis/<api-name>/openapi.yaml` |
| Client configuration and selected tool IDs | `clients/<client-id>/mcp-manifest.yaml` |
| Generated APIM policies and Bicep | `clients/<client-id>/generated/` |
| Advisory local preparation history | `.mcp-kit/workflow/` |
| Rendered capability dashboard | The URL returned by `dashboard-get-url` |

The agent runs `spec-sync` for managed technical sections and `prepare` for
offline validation and generation. If a build fails, correct the contract or
configuration, not the generated files. Preparation does not call the APIM
policy engine, create a live endpoint, or prove real backend behavior.

You can start without examples, import a neutral starter, or browse the
[optional fictional example library](docs/example-library.md). Nothing in the
built-in library becomes an active client merely because you installed the kit.

## Use the dashboard

Ask the agent:

> Refresh the dashboard and give me its URL. Show me the Acme scenario, its
> tools, mock rules, policy size, and the next workflow step.

The companion's `dashboard-refresh` and `dashboard-get-url` tools serve the
package-owned dashboard at a **token-protected loopback URL**. Open the returned
URL in a browser or supported VS Code browser view. Keep the MCP process running;
after a restart, request a new URL rather than reusing an old one. Do not publish
the tokenized URL as a team-hosted application.

The dashboard supports English and Italian, capability search, client/scenario
details, target reports, and policy-MCP budgets. Optional
[scenario metadata](docs/scenario-metadata.md) adds persona, job-to-be-done, and
outcome. A static catalog can also be written with the explicit installed CLI
command `mcp-kit --workspace <customer-directory> catalog --write`; its HTML is
under `catalog/generated/`.

**This is a browser dashboard, not an embedded MCP App.** A visible dashboard or
`prepare` receipt is not deployment readiness: an offline scenario can still
show that its Azure target needs input. That is expected.

## When you are ready for Azure

Choose the consumer experience first:

| Profile | Use it for | APIM / constraints |
|---|---|---|
| `policy-mcp-consumption` | Public mock MCP demos | Consumption; stateless tools implemented by API policies, not native APIM MCP; no external backend or private network |
| `native-mcp` | Native MCP, external HTTP backends, or private-network scenarios | Basic v2 by default, or a compatible existing tier; inspect tier, identity, and connectivity first |
| `rest-consumption` | REST/OpenAPI or a Custom Connector, not MCP | Consumption; public, mock-only |

Consumption has **no fixed gateway charge**, not guaranteed zero total cost.
Usage and other Azure services may incur charges. The policy-MCP profile splits
whole tools into multiple endpoints below the 16 KiB policy limit; a single
oversized tool fails with an actionable error. It does not expose MCP resources
or prompts. Do not enter a REST URL into Copilot Studio's MCP connection wizard.

Azure work requires Azure CLI with Bicep, an authenticated authorized account,
an explicit target, and review of the proposed changes. Choose one documented
path, rather than starting with a blanket `azd up`:

- **Reuse APIM:** [selective deployment](docs/selective-deployment.md). Deploy
  only the selected client after reviewing the reconciliation DELETE plan and
  ARM what-if. Apply requires the matching review token.
- **Create public APIM:** [gateway provisioning](docs/gateway-provisioning.md).
  If you have no resource group, first follow
  [resource-group creation](docs/resource-group-provisioning.md) with
  `mcp-kit provision-group`. Then `mcp-kit provision` creates Consumption or
  Basic v2 in that group, followed by inspection and selected-client deployment.
  Each stage has its own reviewed approval. Neither command creates private
  networking, Key Vault, or RBAC.
- **Validate consumer calls:** [extended verification](docs/extended-verification.md).
  Native discovery, mock execution, controlled external fixtures, and
  Entra/dual-auth checks have distinct scopes and approvals.

Before Azure-changing commands, confirm **account, tenant, subscription,
resource group, gateway, profile, and whether azd is used**. Azure CLI and azd
have separate contexts. The standalone paths do not require azd; full-platform
azd provisioning remains a source-repository workflow. Read [HANDOVER.md](HANDOVER.md)
before deployment. You do not need to create the group manually: tell the
Creator agent **"I have a subscription but no resource group or APIM"**. It
collects an explicit existing/new group choice, group region and gateway
target, then guides the separate steps. The kit needs appropriate subscription
permissions to create a new group; it never grants those permissions itself.
Never put keys, tokens, or passwords in prompts, commands,
manifests, or source; use the documented private credential/`secretRef` paths.

Client retirement preserves APIM itself. There is **no executable
`retire-gateway`**: do not assume the kit can automatically remove a whole service
it created. Resource-group deletion is not provided either. See
[gateway retirement boundaries](docs/gateway-retirement.md).

## Update or switch customer folders

Use the new release's installer with **new runtime and plugin paths** and the
same customer folder. Check the successful receipt, then disable the old host
connection before enabling the new one. Keep the old runtime and a customer-data
backup for rollback; re-enabling the old plugin does not undo later data edits.

Do not move virtual environments or edit generated `connection.json` paths.
To use a different customer folder, export a new bundle with that explicit
workspace. One data root can contain several clients. Follow
[update and switching instructions](docs/installation.md#updating-rollback-and-switching-customers).

## Troubleshooting

| Symptom | What to check |
|---|---|
| Creator agent or skill is missing | Host plugin support, agent-plugin enablement, exact exported path in `chat.pluginLocations`, then reload/new chat |
| Copilot asks you to sign in | Complete sign-in privately in that host; installation does not authenticate Copilot |
| Plugin enabled but tools unavailable | Inspect the Creator MCP output in **MCP: List Servers**; verify the pinned interpreter/customer paths and a completed installation receipt |
| Wrong customer appears | Check the pinned root in `connection.json`; export a new bundle instead of assuming the open folder changes the MCP root |
| Unrelated MCP servers start | The host may discover existing plugins, skills, hooks, or MCPs; use supported workspace-scoped controls where available, and verify scope before disabling anything |
| Host asks to read `connection.json` or packaged guidance outside the workspace | Compare the requested path with your trusted runtime/plugin export; approve only the intended files |
| Installer fails on TLS or dependencies | Use the documented corporate-CA or compatible offline-wheelhouse procedure; never disable certificate verification |
| Installer refuses an existing runtime/plugin folder | Use fresh output paths; the installer deliberately does not overwrite installations |
| `provision-group` is unavailable | Check the installed version with `kit-info`; group creation requires the 1.3.0 candidate, not the older 1.2.0 package |
| Dashboard URL no longer works | Ensure the MCP is running and request a fresh URL |
| Dashboard still asks for a provisioning target | Offline generation is complete, but Azure configuration is not; no deployment has occurred |
| Azure CLI is connected to a different tenant/subscription | Stop before preview/apply. Sign in to the approved tenant and select the approved subscription, then rerun the explicit context checks; `azd` and CLI contexts are independent |
| OpenAPI reports a numeric `exclusiveMinimum` or `exclusiveMaximum` | OpenAPI 3.0 uses `minimum: 0` plus `exclusiveMinimum: true`, not `exclusiveMinimum: 0`. Correct the source contract and rerun `prepare`; do not patch generated files |
| Failed deployment leaves detached client tags | Keep the same client and read [partial-deployment recovery](docs/selective-deployment.md). `--recover-detached-tags` requests an explicit recovery preview backed by live Azure creation evidence, then a separately approved apply; it is not permission to adopt or delete arbitrary tags |

The current source validates the complete OpenAPI 3.0 document offline before
generation, including nested schemas. Long internal ARM module deployment names
are shortened deterministically to 64 characters; client/API IDs, operation IDs,
endpoint paths and already-valid deployment names remain unchanged. These fixes
do not retroactively update an installed 1.3.0 release.

A separate VS Code user-data directory is **not a guarantee of isolation** from
user-level customizations. The Creator agent inherits host tools; do not approve
unrelated server starts or broad permissions to make a test proceed.

## Other ways to connect

- **Copilot CLI:** after the same install/export, use a CLI version supporting
  `copilot plugin install <absolute-plugin-directory>`, then check
  `copilot plugin list`. Some versions warn that local installation is being
  deprecated. The CLI agent ID is
  `mcp-openapi-creator:mcp-openapi-creator`. See [plugin setup](docs/copilot-plugin.md).
- **Standalone MCP:** run the installed
  `mcp-kit --workspace <customer-directory> vscode-config` and review/paste its
  output into VS Code user MCP configuration. It does not modify settings.
  This gives guidance through tools/resources/prompts, but does not install the
  native Copilot skill or agent. Do not enable it alongside the plugin connection.
- **Other MCP clients:** use the installed stdio server and explicit workspace
  described in [local MCP setup](docs/local-mcp-server.md). Tool-based guidance
  remains available when a client lacks resources or prompts. Azure inspection
  requires supported human-facing consent; otherwise stay offline or use the
  documented interactive CLI handoff.

The companion exposes **14 tools, 7 resources, and 3 prompts**. Tool groups:

| Tools | Purpose |
|---|---|
| `kit-info`, `workflow-guide`, `kit-reference` | Installed version, procedures, and trusted references |
| `workflow-status`, `workspace-status`, `scenario-contract` | Current step, workspace inventory, and specification/contract consistency |
| `catalog-search` | Built-in or customer capability discovery |
| `recommend-profile`, `policy-budget` | Profile guidance and policy-MCP size measurement |
| `inspect-gateway` | Read Azure gateway facts only after per-call operator consent |
| `target-capabilities`, `target-report` | Compatibility and optional target planning |
| `dashboard-get-url`, `dashboard-refresh` | Local browser dashboard |

## What has been verified

Resource-group creation is a **new 1.3.0 candidate feature**. It does not inherit
the older candidate's actual-host acceptance, and no live group/APIM deployment
has been performed for this extension.

The **1.2.0 candidate** has recorded local package/install acceptance and an
**interactive Windows VS Code offline E2E on September 17, 2026**: the Creator
agent loaded its native skill, called the real companion, produced a three-tool
Acme scenario, completed local preparation, and displayed the dashboard.
Independent checks confirmed specification consistency, preserved pre-existing
customer data, and policy output below 16 KiB.

This was an **assisted interactive run**, not unattended automation or a
flawless first attempt: host setup and user approvals were required, an initial
run was interrupted by unrelated host discovery, and the model corrected its
own malformed OpenAPI edit during the successful retry.

See [the acceptance record](docs/e2e-testing.md#observed-vs-code-offline-e2e-2026-09-17)
for candidate identity and boundaries. The final 1.2.0 candidate has **not** been
live-certified across new-gateway provisioning, deploy/update/retire, native
MCP, external systems, Entra authentication, and Copilot Studio. Earlier cloud
smokes are not evidence for all those paths on this release.

### Current limits

- Preview/local plugin distribution, not a VSIX, marketplace listing, or M365 plugin.
- OpenAPI **3.0.x**, not automatic OpenAPI 3.1 conversion.
- No hosted backend mode, outbound mTLS, mock state, or mock business calculations.
- No automatic customer-secret provisioning or whole-gateway retirement.
- Optional AI Gateway tier support is **offline planning only**, with management
  apply blocked. It is separate from the three APIM profiles above.
- A skill guides a model; it does not guarantee semantic correctness, approvals,
  or host isolation.

See [roadmap](docs/roadmap.md), [consumer targets](docs/consumer-targets.md),
and [AI Gateway preview plans](docs/pilots/README.md).

## For contributors

End users install a release; contributors work from source. Use Python 3.12+
in a dedicated virtual environment:

```powershell
git clone https://github.com/Iodix7/mcp-openapi-creator-kit.git
Set-Location mcp-openapi-creator-kit
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tools\tests -q
.\.venv\Scripts\python.exe tools\check-publication.py
```

Generator/Bicep validation also requires Azure CLI with Bicep; installed-package
checks and platform-specific acceptance are described in
[E2E testing](docs/e2e-testing.md). Push/PR CI is offline: it does not deploy Azure.
Manual cloud checks require each fork's own approved identity and target.

| Path | Purpose |
|---|---|
| `src/mcp_openapi_creator_kit/` | Installed companion, CLI, and shared workflow core |
| `skills/` | Maintained discovery, onboarding, and lifecycle procedures |
| `apis/` | Shared OpenAPI contracts, fictional examples, and canonical schemas |
| `clients/<id>/mcp-manifest.yaml` | Client configuration |
| `tools/`, `platform/`, `modules/`, `infra/` | Generators, validation, deployment, and Bicep |
| `catalog/` | Dashboard template and editorial metadata |
| `docs/` | Installation, operational procedures, acceptance, and design references |

Read [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md) before changing
the kit. Shared contracts are read-only for customer onboarding: create a named
variant when needed. Selected tool IDs must be unique across clients on the
same APIM, use kebab-case, and satisfy the build's example, RFC 7807, and
Idempotency-Key requirements.

Never edit or commit `clients/*/generated/`, `infra/*.gen.bicep`, or
`catalog/generated/`. Review the [publication checklist](docs/publication-checklist.md)
before publishing; a prepared checkout is not a commit, tag, or release.

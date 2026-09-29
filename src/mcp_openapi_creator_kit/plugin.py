"""Explicit, per-installation Copilot plugin export from trusted kit assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from .assets import kit_root, verify_assets
from .data_paths import workspace_root
from .runtime import command


PLUGIN_NAME = "mcp-openapi-creator"
GUIDANCE_ASSETS = (
    "AGENTS.md",
    "skills/discovery.md",
    "skills/onboarding.md",
    "skills/lifecycle.md",
)

SKILL = """---
name: create-mcp
description: >-
  Guide creation of OpenAPI contracts, APIM mock APIs or MCP endpoints and a
  capability dashboard from a business scenario. Use for a new scenario,
  onboarding a client, or changing an existing MCP OpenAPI Creator client.
---
# Create a working MCP scenario

Use this skill for the complete guided workflow, not just an explanation of MCP.
Help the user reach the agreed deliverable; recover from actionable validation
errors and continue instead of stopping after the first generated file.

## Respect the requested scope first

A request only for connection details, runtime behavior or a dashboard is a
read-only handoff, not a request to prepare or deploy. After kit-info, use
consumer-handoff with the supplied client/profile/origin, then dashboard-refresh
and dashboard-get-url. Stop with that result. Do not execute prepare, retrieve
the full scenario contract, or fill in Azure targets to advance workflow status.
Missing/stale preparation history does not block an offline handoff.
Never invent tenant/subscription UUIDs, account names or gateway metadata.

For policy-mcp-consumption the answer to "can I choose a fixed key in chat to
prevent duplicate retries?" is NO. The adapter generates a new Idempotency-Key
per call; caller reuse is unsupported and the mock neither persists nor
deduplicates. An OpenAPI required header does not override this runtime behavior.
Use consumer-handoff's runtimeSummary and operations for the final explanation;
do not contradict them with an optimistic interpretation of the REST contract.
For REST invocations use operations[].restRequest: its method and complete
endpoint urlTemplate include the operation path; endpoints[].url is only an
API base. Include authentication and operation headers. Follow parameter schema
constraints exactly; an example UUID is not a required UUID format.

## Connect to the installed kit

Use the inline installation binding above for the pinned customer workspace,
installed interpreter, CLI argument prefix and kit provenance. It is generated
from the same data as [connection metadata](../../connection.json); no separate
file read is needed. Do not guess a connection.json path from the current folder.
Resolve relative links from the containing skill/agent file, never from cwd.
The current editor folder is not necessarily the customer workspace. Never
substitute its interpreter, AGENTS.md, scripts or templates for the installed kit.

Call the plugin's `kit-info`, then `workflow-status` for creation/change work.
For a read-only handoff use the scope-specific sequence above instead.
Compare the returned kit
version/source/manifest hash with the inline binding. If they differ, report
the mismatch and regenerate/reload the plugin before continuing.
If the MCP is unavailable, report the connection error and use the exact
installed CLI prefix for `info` and `workflow-status`; do not pretend the MCP
connection or Azure inspection succeeded.

## Follow the maintained procedure

Use the constitution already returned by kit-info. Load only a missing procedure:

- New business scenario: [discovery](references/skills/discovery.md).
- Configure, prepare or deploy a client: [onboarding](references/skills/onboarding.md).
- Change an existing client: [lifecycle](references/skills/lifecycle.md).

These references are generated copies of the same versioned documents returned
by `workflow-guide`; they are not a second procedure to maintain. The current
`workflow-status.currentStep` gives the relevant instructions and completion
criteria. Ask only for missing decisions; reuse facts the user already supplied.
Do not invent a consumer choice, client, APIM tier or Azure target.
Do not reread a full guide when currentStep already contains that step.
Reuse unchanged business answers. Ask one missing decision, not the full setup
again. Report only a completed milestone, a material change or an actionable
blocker; do not narrate every tool call. On authentication failure stop that
operation and explain the required user action; never loop silently.

Demo-first coverage is informational, not another wizard. Reuse the user's
already stated demo scope; never ask them to fill tables or approve each row.
scenario-contract and prepare return automatic coverage, also shown in the
dashboard. Use agreement-coverage only when an explicit refresh/profile is needed.
Summarize unchanged limitations once. Only an essential missing behavior needs
a scope decision; an agreed stateless demo need not implement production/stateful
requirements. Keep the original IA and record existing scope decisions in the
same spec (optional coverage frontmatter), not another manifest. Planned gateway
behavior, mock simulation and backend responsibility are not live verification.
No coverage outcome overrides technical validation or Azure approval.
An essential persistence/deduplication gap needs a useful path, not a blanket
"the plugin cannot do it": policy mocks cannot provide state, but the kit can
connect a separately implemented external backend on a compatible native-mcp
gateway. It does not create that backend. Ask only whether a suitable backend
already exists if this is the missing decision; never silently change demo scope.

For a fictional starter, browse `catalog-search` with `source=builtin` and use
the installed CLI's import preview before explicitly importing a client variant.
Read `kit-reference(name="example-library")` for the supported bundled scenarios
and selected import commands; examples are never automatically active clients.
Before choosing a starter, follow discovery's functional-fit check: compare
requested outcomes with actual tool inputs/effects/examples, not similar names.
Summarize matches/gaps once; ask only for a material unresolved decision.
Never reinterpret the request to fit the example or fabricate approval.
For a new contract, use catalog schemas and contract examples as the source of
mock behavior. Save data only under the pinned customer workspace.

## Execute exact steps and recover

MCP tools read, validate and plan; host file tools and the explicit installed CLI
perform the requested local writes. This skill does not make MCP a command runner.
For every executable workflow step, read a fresh `nextInvocation` and use its
exact executable, argument array and cwd. Do not reconstruct arguments from
memory or run an unrelated workspace script.

For commands without a descriptor, append arguments to the inline binding's
`cliPrefix` and read that command's `--help` once if its syntax is not already
known for this installed version. For example, build/prepare
take `clients/<id>`, while spec-sync takes the client slug. A rejected command is
feedback: read the diagnostic/help, correct the invocation, and retry only the
same authorized operation. Never weaken validation to make progress.

Read `scenario-contract` before writing the scenario specification. Write the
business narrative, then use `spec-sync <id>` and its explicit `--write` to
generate technical tables from contracts. Resolve draft markers and stale
assertions before `prepare`; never hand-edit generated technical sections.
Keep unapproved proposals Draft and record the actual scope decision in
Clarifications. Reuse existing scoped consent; do not ask approval per tool.
Use the scenario template's optional `catalog` frontmatter for declared
persona/JTBD/outcome in the dashboard, not a second copy in catalog metadata.
Keep it consistent with the approved narrative; see `scenario-metadata`.
Preserve the scenario template's functional-fit headers and Fit values when
translating prose; use only its documented aliases. After saving a new spec,
check `scenario-contract.functionalFitReview`: `not-recorded` is not a pass.
Correct an unrecognized mapping before claiming its references were checked.

Keep inspection, local writes and Azure changes separate. Read the maintained
procedure for its exact approval requirements. Never auto-answer an inspection
form. Do not claim that a skill, tool hint or host preference enforces permissions.
Hooks are not installed by this plugin.

For a new APIM, use `kit-reference(name="gateway-provisioning")` and the
provision-gateway step. Ask whether to reuse or create its resource group.
For a new group read `kit-reference(name="resource-group-provisioning")`, set
`provisioning_target.resource_group_mode="new"` and collect its explicit
`resource_group_location`. Follow the provision-resource-group step first.
After verified group creation, switch only the group mode to existing and
remove its creation location; keep gateway_mode=new for the APIM stage.
Group creation, APIM creation and client deployment require separate reviewed
approvals. Proposed context is not observed evidence or approval.
Use the installed provisioning preview/apply rather than requesting a kit clone
or running azd. After creation, inspect the gateway as existing before deploying
the client. For native/external/Entra checks read `extended-verification`; never
turn a request to verify discovery into an unapproved real-system write.

## Finish the agreed outcome

For creation/change work, refresh `workflow-status` after each material change
and show the dashboard
using `dashboard-get-url` / `dashboard-refresh`. Do not recreate its HTML.
For offline preparation, deliver the valid contracts, manifest, coherent
specification, generated artifacts and dashboard; clearly label Azure as not run.
For an approved deployment, continue through the procedure's endpoint checks
and consumer handoff. `preview-recorded` is not deployed, and a generated MCP
URL is not a successful consumer connection. Use `consumer-handoff` with the
explicit profile and approved HTTPS gateway origin, never reconstruct an MCP
URL from basePath. Give the user its complete URL(s), auth requirements and
runtime limits; refresh the existing dashboard to show the same candidates.
Call consumer-handoff before dashboard-refresh, not concurrently: the refresh
must include the selected origin/profile. Include this warning next to the URLs
in the final answer, translated to the user's language: "Derived offline from
contracts; deployment and connection have not been verified." Do not leave that
warning only in tool output, even when the user asks for a brief answer.
Do not claim caller-key reuse, persistence or deduplication not supported by
the adapter. Test actual tool results and truthful chat responses separately.
No receipt or remembered decision can authorize a changed target, plan or
expired preview. Client-mediated inspection consent remains per invocation.

Report what is complete, what failed and the next necessary action. Do not
require the user to understand the kit source tree to operate the result.
"""

AGENT = """---
name: mcp-openapi-creator
description: >-
  Turn a business scenario into validated OpenAPI contracts, APIM mock APIs or
  MCP endpoints and a dashboard using the installed MCP OpenAPI Creator Kit.
---
# MCP OpenAPI Creator

Use the [create-mcp skill](../skills/create-mcp/SKILL.md) as the entry point
and follow its generated procedure references. Start with the plugin's kit-info,
using the inline installation binding above, also recorded in
[connection metadata](../connection.json). No separate metadata read is needed;
never derive its path or the customer workspace from the current editor folder.

Respect the user's requested scope before following workflow stages. Connection
details/dashboard/runtime questions alone require consumer-handoff and dashboard
tools, not workflow-status/prepare/deploy or invented Azure context.
For policy MCP, a fixed UUID in chat cannot control the internally generated
per-call key and cannot guarantee deduplication. State this explicitly.
Label handoff URLs in the final answer as derived offline, with deployment and
connection not verified. Refresh the dashboard only after consumer-handoff.
For REST calls use operations[].restRequest method and endpoint urlTemplate,
not the API base URL. Include required auth/operation headers and use schema
constraints, not example shapes, to describe accepted inputs.
For essential state or deduplication, distinguish unsupported stateful mocks
from supported external-backend integration on a compatible native-mcp gateway.
Explain that the backend must exist or be separately implemented; ask only for
the missing backend decision instead of ending with an unqualified refusal.

Drive the scenario to the agreed result: clarify missing business decisions,
reuse the schema catalog, create coherent contracts/examples and a manifest,
generate technical specification sections, prepare and validate, and expose
the existing dashboard. Continue to deployment and consumer checks only when
that work and its target are explicitly authorized.

Use exact installed CLI invocations supplied by the workflow. Correct
actionable errors and continue; never invent successful outputs or stop at a
build when the agreed goal also requires verification.

This prototype inherits the host's available tools. It does not install hooks,
change global permissions or guarantee preventive file-edit approval.
"""

README = """# MCP OpenAPI Creator plugin

This generated Copilot-format bundle contains a native create-mcp skill,
a dedicated Copilot agent and the existing read-only MCP companion.
The maintained procedures are copied from the installed kit, not customer data.

## Load this local bundle

Review the bundle before enabling it. With Copilot CLI installed, run this
command from this directory:

```text
copilot plugin install .
```

VS Code also supports a user setting `chat.pluginLocations` mapping this
directory's absolute path to true. The export command prints that exact object;
merge it without replacing other entries. It does not change settings for you.
Recent VS Code versions can also discover CLI-installed plugins.

Reload/start a new chat after loading the plugin. Select the
`mcp-openapi-creator` agent or invoke `/mcp-openapi-creator:create-mcp`.
Ask for the business scenario you want to create, then follow the guided steps.
A host supporting the Copilot plugin format is required.

## Runtime and workspace

`connection.json` records the existing dedicated Python installation and
customer data directory; `.mcp.json` uses those exact absolute paths. Neither
the active editor folder nor the plugin cache directory selects the customer.
This is a per-installation bundle, not a portable Python environment. Generate
it again on another machine, after moving the runtime/workspace, or after a kit
upgrade. Choose a new output directory, reload/reinstall the plugin, and disable
the old connection. Copilot CLI caches installed plugin contents.

The kit wheel and dependencies must already be installed. Export and MCP startup
never run pip, install a plugin, change user settings, read Azure or deploy.
Enabling a plugin can automatically start its MCP server; plugin installation
can imply server trust, so inspect the configuration first.
Do not enable both this MCP and a duplicate standalone connection for the same
customer. Standalone MCP/CLI usage remains supported without this plugin.

## Scope

The skill and agent guide work; MCP/CLI retain their existing roles. The agent
inherits host tool availability because tool identifiers vary between clients.
No hooks or permission policy are installed. This bundle is not a sandbox and
does not guarantee that the host asks before file edits.

The supported Copilot format preserves an absolute interpreter in .mcp.json.
Agent Plugins 1.0 restricts command to a bare executable or plugin-relative path;
this export does not claim that format or introduce a PATH-based Python fallback.

Dashboard tools open the existing read-only browser dashboard. Offline
preparation and synthetic tests do not establish a live APIM deployment or a
Copilot Studio connection. Review the actual workflow's completion criteria.
"""


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _bound_entrypoint(text: str, connection: dict) -> bytes:
    binding = {key: connection[key] for key in ("kit", "workspace", "interpreter", "cliPrefix")}
    frontmatter, body = text.split("---\n", 2)[1:]
    inline = ("## Installation binding\n\n"
              "Generated from connection.json. Use these exact values without a path lookup.\n\n"
              "```json\n" + _json_bytes(binding).decode("utf-8") + "```\n\n")
    return ("---\n" + frontmatter + "---\n" + inline + body).encode("utf-8")


def _output_path(root: Path, output: Path) -> Path:
    candidate = output.expanduser().absolute()
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("Plugin output must not traverse a symlink/junction.")
    if not candidate.parent.is_dir():
        raise ValueError("Plugin output parent must already exist; create it explicitly first.")
    if candidate.exists():
        raise ValueError("Plugin output already exists; choose a new plugin directory.")
    candidate = candidate.resolve()
    for protected in (root, kit_root().resolve(), Path(sys.prefix).resolve()):
        if candidate.is_relative_to(protected) or protected.is_relative_to(candidate):
            raise ValueError("Plugin output must not overlap customer data, kit assets or the installation.")
    return candidate


def export_plugin(root: Path, output: Path, *, write: bool = False) -> dict:
    root = workspace_root(root)
    target = _output_path(root, output)
    interpreter = str(Path(command("local_python").local_python(root)).absolute())
    kit = verify_assets()
    metadata = {
        "name": PLUGIN_NAME, "version": kit["version"], "format": "copilot",
    }
    cli_prefix = [
        interpreter, "-I", "-m", "mcp_openapi_creator_kit.cli", "--workspace", str(root),
    ]
    references = {
        f"skills/create-mcp/references/{asset}": (kit_root() / asset).read_bytes()
        for asset in GUIDANCE_ASSETS
    }
    connection = {
        "kit": kit,
        "workspace": str(root),
        "interpreter": interpreter,
        "cliPrefix": cli_prefix,
        "guidanceSha256": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(references.items())
        },
    }
    files = {
        "plugin.json": _json_bytes({
            "name": PLUGIN_NAME,
            "version": kit["version"],
            "description": "Guided OpenAPI, APIM MCP creation and capability dashboards.",
            "license": "MIT",
            "skills": ["skills/"],
            "agents": "agents/",
            "mcpServers": ".mcp.json",
        }),
        ".mcp.json": _json_bytes({
            "mcpServers": {
                PLUGIN_NAME: {
                    "command": interpreter,
                    "args": ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", str(root)],
                },
            },
        }),
        "connection.json": _json_bytes(connection),
        "skills/create-mcp/SKILL.md": _bound_entrypoint(SKILL, connection),
        "agents/mcp-openapi-creator.agent.md": _bound_entrypoint(AGENT, connection),
        "README.md": README.encode("utf-8"),
        **references,
    }
    result = {
        "plugin": metadata, "workspace": str(root), "output": str(target),
        "kit": kit, "writes": False, "files": sorted(files),
        "installation": {
            "copilotCli": {"executable": "copilot", "arguments": ["plugin", "install", str(target)]},
            "vscodeSettings": {"chat.pluginLocations": {str(target): True}},
        },
        "notice": "Per-installation bundle: runtime and customer paths are pinned. "
                  "No plugin installation, host settings changes, model call or Azure operation.",
    }
    if write:
        with TemporaryDirectory(prefix=".mcp-kit-plugin-", dir=target.parent) as temporary:
            staged = Path(temporary) / "bundle"
            staged.mkdir()
            for name, content in sorted(files.items()):
                destination = staged / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            _output_path(root, target)
            staged.rename(target)
        result["writes"] = True
    return result

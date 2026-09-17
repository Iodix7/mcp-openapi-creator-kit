# Installed read-only MCP companion and explicit customer CLI

The wheel carries versioned guidance, templates, schemas, starter library and
trusted generator/deployment implementations. Customer data does **not** require
a kit clone, AGENTS.md, skills, tools, modules, local venv or catalog template.
No PyPI/uvx publication is assumed. Runtime does not fetch assets from a network.

## End-user wheel installation

Create a dedicated installation venv using Python >=3.12. All paths below are
operator-chosen examples, not deployment targets. Installation never runs on MCP
startup. Do not install into global Python.

```powershell
py -3.12 -m venv C:\kit-install
C:\kit-install\Scripts\python.exe -m pip install C:\downloads\mcp_openapi_creator_kit-1.1.1-py3-none-any.whl
New-Item -ItemType Directory C:\customer-data
C:\kit-install\Scripts\python.exe -I -m mcp_openapi_creator_kit.cli --workspace C:\customer-data init
C:\kit-install\Scripts\python.exe -I -m mcp_openapi_creator_kit.cli --workspace C:\customer-data init --write
```

On POSIX use `/chosen/kit-install/bin/python` and an operator-selected customer
directory. The installation may live on another disk from customer data.
Generated Bicep module references are relative and portable.

## Source development (not required for customers)

From the source repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m build
```

On POSIX use `.venv/bin/python`. Source and wheel use the same command
implementations; setuptools stages maintained assets deterministically and
records SHA-256 hashes. Sdist includes the original sources for rebuilding.
Editable installs report source-development provenance; wheels report
installed-package. Never use a customer directory's scripts as kit code.

## VS Code user-level configuration

Emit, inspect and paste the object into VS Code's **user MCP configuration**
(MCP: Open User Configuration). The command only prints JSON; it does not
change settings or depend on the currently open editor folder:

```powershell
C:\kit-install\Scripts\python.exe -I -m mcp_openapi_creator_kit.cli --workspace C:\customer-data vscode-config
```

Shape:

```json
{
  "servers": {
    "mcp-openapi-creator": {
      "type": "stdio",
      "command": "C:\\kit-install\\Scripts\\python.exe",
      "args": ["-I", "-m", "mcp_openapi_creator_kit", "--workspace", "C:\\customer-data"]
    }
  }
}
```

No `${command:python.interpreterPath}` or workspace-local `.vscode` is required.
Another open repository does not change the pinned customer root. For legacy
workspace-scoped use, existing `.vscode/mcp.json` (Windows) and
`.vscode/mcp.posix.json` still select the repository .venv and workspace.
Restart using **MCP: List Servers**. Python -I excludes PYTHONPATH/user site.
Verify the negotiated protocol, including legacy 2025-11-25 compatibility with
MCP SDK v2, in the actual host. An SDK-only test does not establish a VS Code
roundtrip or that its UI presented a consent request.

## Guided Copilot plugin alternative

`mcp-kit --workspace <customer-root> plugin-export --output <new-directory>`
previews a native skill + dedicated agent + MCP bundle; repeat with `--write`
to create it outside customer data and the installation. Load the reviewed
bundle through Copilot CLI or VS Code's local plugin setting. The standalone
configuration above remains supported, but do not enable both connections for
the same customer. See [plugin setup](copilot-plugin.md) for exact steps,
per-installation path binding, updates and the first offline scenario.

## Start here: complete workflow via tools

- `kit-info`: version/source/hash provenance, full constitution and discovery.
- `workflow-guide`: enum discovery/onboarding/lifecycle; full procedure and
  constitution, next CLI steps and references. Works without prompts/resources.
- `kit-reference`: allowlisted handover, selective deployment, canonical schemas,
  scenario template, sample manifest/contract and consumer-target guidance.
- `workspace-status`: existing empty roots are valid, with initialization steps;
  nonexistent roots are rejected without creation.
- `catalog-search`: source workspace (default) or builtin; limit 1–50.
- `scenario-contract(client=<id>)`: actual imported operations, reference
  Markdown and the specification's contract-consistency check; read-only.
- `target-capabilities`, `target-report`, `recommend-profile`, `policy-budget`.
- `workflow-status`: shared deterministic readiness, missing inputs, blockers,
  next action/command, recorded completion steps and verified versus provisional
  profile. No automatic Azure reads or local writes.
- `inspect-gateway`: narrowly scoped Azure GETs after the operator agrees an
  explicit target/account. Verifies CLI identity before reading APIM/global
  diagnostics; returns a five-minute process-local `evidenceId`.
- `dashboard-get-url`, `dashboard-refresh` (source workspace or builtin).

Resources keep existing kit:// identifiers. Workflow prompts and fallback tools
delegate to the **same provider**, not separately maintained guidance.
Built-in catalog is a fictional starter library, never active customer clients.
Customer instructions do not override the trusted constitution.

All MCP tools remain read-only/non-destructive. No run_shell, Azure executor,
file writer or MCP Apps. Actual workflow writes require explicit CLI commands:

```text
mcp-kit --workspace <customer-root> init
mcp-kit --workspace <customer-root> init --write
mcp-kit --workspace <customer-root> import-sample <new-client> --write
mcp-kit --workspace <customer-root> build clients/<id>
mcp-kit --workspace <customer-root> build-policy clients/<id>
mcp-kit --workspace <customer-root> prepare clients/<id> --profile native-mcp
mcp-kit --workspace <customer-root> validate --profile native-mcp clients/<id>
mcp-kit --workspace <customer-root> catalog --write
mcp-kit --workspace <customer-root> target-report <id>
mcp-kit --workspace <customer-root> scenario-contract <id>
mcp-kit --workspace <customer-root> workflow-status --client <id> --consumer mcp --gateway-mode existing
mcp-kit --workspace <customer-root> export <id> --report
mcp-kit --workspace <customer-root> deploy clients/<id> --help
mcp-kit --workspace <customer-root> verify-mcp --help
```

`--root` aliases `--workspace`. Run mcp-kit from the installation venv PATH, or
use its absolute Python with `-I -m mcp_openapi_creator_kit.cli`.
Init/import/variant default to plans and never overwrite. Build explicitly
writes generated artifacts. Deploy requires its context/review/ownership gates;
read [selective deployment](selective-deployment.md). No global install fallback.
Full-platform azd remains repository-only. AI Gateway remains plan-only.

### Scenario specification and imported contract

Use `scenario-contract(client=<id>)` through MCP, or the read-only
`mcp-kit --workspace <customer-root> scenario-contract <id>` CLI, before writing
or correcting `docs/<id>/spec.md`. The result contains the actual imported
operation IDs, routes, parameters, response examples and `x-mock` rules, plus
`referenceMarkdown` and a read-only `specSync` diff. Do not recopy those tables:
write the approved narrative, then generate its technical reference:

```powershell
mcp-kit --workspace <customer-root> spec-sync <id>
# Review the diff and approve local writes before:
mcp-kit --workspace <customer-root> spec-sync <id> --write
```

Only the `mcp-kit:contract-reference` marked block is generated. Narrative bytes
outside the block, including line endings, remain unchanged. Repeated unchanged
syncs do not rewrite the file. Missing specifications get a clearly unapproved
draft scaffold; its `[TO CLARIFY: ...]` placeholders block preparation.
Finish the storyline with the operator. This command is an explicit CLI writer,
not an MCP tool. No write approval is inferred from reading its diff.

The block contains a deterministic projection hash covering the current imported
operations/parameters/examples/rules. Source changes or manual block edits make
it stale; preview and sync again. Unmarked legacy technical tables and ambiguous
markers cause a clear refusal, without deleting or adopting user content.
Legacy hand-authored specs remain checkable; migrate deliberately if opting in.
Conflicts appear in `scenario-contract.specSync`, while ordinary mismatches
remain in `check`. File/contract changes detected between planning and atomic
replacement abort the write. This is not an OS boundary against same-user races.

The result's `check` contains `status` (`missing`, `mismatch` or `consistent`),
`issues` and `notice`. The CLI prints JSON and exits **2** for missing or
mismatched assertions, or **0** for a consistent specification.

`prepare` refuses missing or mismatched **Operations** and **Mock behavior**
tables. It checks exact operation IDs, routes, response statuses, example names
when supplied, and explicit references such as ``Tool: `operation-id` ``.
Operations may express a route in one column or separate **Method** and **Path**
columns; the same exact route checks apply. Mock status columns remain required.
Coverage must include every selected MCP tool, or every operation when no tools
are selected. Managed-block freshness and explicit unresolved draft placeholders
are checked too. Regenerate technical sections; correct narrative Tool references
with the operator. `workflow-status` does not treat inconsistent inputs as completed preparation
and points back to these issues.

This is a structural assertion check, not semantic approval: it does **not**
validate free-form prose, business semantics, input values, execution of
`x-mock`, or human approval. A `consistent` result does not establish those
properties. Existing standalone `build`, `validate` and deployment diagnostics
are unchanged; running them is not a substitute for completed `prepare`.

### Workflow observations and approval boundary

Resources include `kit://workflow/status`. MCP keeps preferences and opaque
observation IDs in memory; it never writes them into customer data. Workflow
status and dashboard refresh reevaluate current manifests and evidence expiry.
On restart or a different target, inspect again; a CLI JSON report cannot be
imported as evidence. CLI `inspect-gateway` combines fresh inspection and
evaluation in one invocation; `workflow-status` alone stays offline.

Unknown consumer/gateway inputs cannot produce a verified profile. The legacy
`recommend-profile` response now uses the workflow schema: `profile` may be
null, while `provisionalProfile` is an offline candidate. A declared
`existing_tier` cannot finalize it. Clients using the old three-field response
must handle the new structured status.
Planned client IDs are supported before their manifest exists: status reports
`client_manifest` as missing, without initializing files. Profile conflicts
include an explicit re-evaluation action; correcting a profile does not replace
the gateway or grant apply approval.

The dashboard is a read-only snapshot, not a deployment or consent interface.
`ready-for-preview` permits attempting the existing guarded CLI preflight;
every result still reports `approvalStatus: not-granted`. Tool enablement,
annotations, evidence IDs and plan hashes do not prove human consent. Keep
operator/client approval controls. An unrestricted same-user coding-agent shell
remains outside the kit's enforcement boundary.

### Instructions for the current step

`workflow-status` now includes `nextStage` and a structured `currentStep`:

| Field | Meaning |
|---|---|
| `stage` | Context collection, inspection, blocker resolution, scenario, client configuration, preparation, preview, plan review or provisioning handoff |
| `why` | Current evidence, missing inputs, blockers and relevant local-step status |
| `instructions` | Essential procedure text for this step |
| `consent` | Required operator permission and explicit boundaries |
| `completion` | How to check the result and what it does not establish |
| `source` | Packaged skill asset, resource URI, kit version and document/excerpt hashes |

The existing `nextCommand` remains an exact argument array for spec synchronization,
preparation and preview, with workspace/client/profile/target pinned. It is empty when context
is missing, the task is blocked or the next action is discussion/review; the
kit never invents a runnable Azure target. It is a suggestion, not execution.

For hosts that dispatch commands, `nextInvocation` provides the same executable
step without requiring shell reconstruction:

| Field | Meaning |
|---|---|
| `executable` | Absolute Python executable for the installed kit |
| `arguments` | Complete array: `["-I", "-m", "mcp_openapi_creator_kit.cli", "--workspace", "<customer-root>", ...]` |
| `cwd` | Pinned working directory |
| `cliArguments` | CLI verb and its arguments only, not a full process command |
| `requiresOperatorApproval` | Always `true`; an approval requirement, never a recorded grant |
| `effect` | `local-write` for spec synchronization/preparation or `deployment-preview` for preview |

`nextInvocation` is `null` when there is no executable stage. A host must
freshly read workflow state, obtain separate operator approval for the current
effect and target, and dispatch the exact `executable`/`arguments`/`cwd` without
shell parsing. If the invocation changes before execution, review and approve
it again. Do not substitute a workspace Python, reconstruct a shell string,
execute `cliArguments` alone, or treat the descriptor as permission.
MCP still neither executes the command nor enforces the host's file or terminal
permissions. The optional evaluation adapter described in
[end-to-end acceptance](e2e-testing.md#optional-next-step-host-adapter) is a test
host implementation, not a product write tool or VS Code integration.

Instructions are extracted verbatim from marked sections in the installed
`skills/discovery.md` and `skills/onboarding.md`. Those same bytes are returned
in full by `workflow-guide`, `kit://skills/...` resources and workflow prompts.
There is no second procedure copy in Python or the dashboard, and no generated
summary model. A missing, duplicate, empty or malformed section fails with a
kit-repair error rather than silently dropping guidance or consulting customer
files. Each step has bounded text; full references remain explicitly available.

MCP, the workflow resource, CLI and live dashboard use the same enriched state.
The static catalog includes contextual guidance for its offline state only;
it does not invent process evidence or include local execution history.
On stale evidence, request inspection again instead of rebuilding for a guessed
Consumption profile. Sources and hashes establish provenance, not authenticity
against same-user tampering or proof the model understood the instructions.

No separate full-guide call is required to follow an inline step. This is not a
new execution coordinator, an approval gate or an autonomous-deployment claim.
The model can still ignore guidance; operator consent, validations and the
explicit CLI boundaries remain unchanged. Lifecycle and post-deployment
verification continue to use their full procedures.

### Inspection consent

The distributed inspector negotiates MCP **form elicitation** and requires an
accepted strict boolean response for the displayed account, tenant,
subscription, RG, APIM and workspace **on every call**, before `az account show`.
Decline/cancel/invalid responses/timeouts cause no Azure reads. Unsupported form
capability or a transport without a server-to-client channel returns an
actionable error; all offline tools remain available. No MCP Apps dependency.

The fallback is the operator running the explicit CLI inspector in an
interactive terminal and retyping the complete APIM resource ID. Redirected
stdin and automatic approval flags are not supported. This does not import CLI
observations into MCP: continue in the CLI or use a compatible MCP host.

**Host trust is required:** the MCP protocol permits a client to generate form
responses itself. This gate is not cryptographic proof of human consent.
Configure the host to display the form to the user, never auto-answer via the
model. Inspection consent does not authorize local writes, preview or apply;
those remain separate operator/host approvals and explicit CLI actions.

### VS Code host approvals are separate

VS Code documents [MCP elicitation support](https://code.visualstudio.com/updates/v1_102#_support-for-elicitations)
and lists it separately from MCP Apps in its
[MCP developer guide](https://code.visualstudio.com/api/extension-guides/ai/mcp).
The guide also states that `readOnlyHint` tools do not receive ordinary tool
confirmation. Neither server trust nor tool enablement is a substitute for the
inspector's per-call form. Remote MCP OAuth authorization is also distinct from
the local stdio inspector's Azure CLI identity and from local-write approval.

For local writes, [VS Code saves agent edits directly to disk](https://code.visualstudio.com/docs/agents/run/review-code-edits).
Even extension-host edits marked pending are already saved: **Keep/Undo is not
pre-write consent**. The documented `chat.tools.edits.autoApprove` glob rules
can require approval before edits to matching files. Terminal commands are a
separate path; `chat.tools.terminal.enableAutoApprove: false` requires command
approval under the documented
[manual approval controls](https://code.visualstudio.com/docs/agents/run/approvals).
Manual permissions alone does not clear existing auto-approvals. Allow all,
Assisted permissions and Autopilot are not a human-per-call consent test.

Record the actual harness and effective settings, not just the VS Code version.
[Agent Host and extension-host sessions differ](https://code.visualstudio.com/docs/agents/concepts/agent-host),
including configuration discovery and edit review. A separate profile does not
isolate same-user credentials or Agent Host user customizations. Native Windows
MCP sandboxing is unavailable; terminal sandboxing requires macOS/Linux/WSL2.
Terminal approval matching is best-effort, not an OS security boundary.

Do not change a user's settings, trust, MCP configuration or credentials, or
send test prompts without separate approval. Follow the
[actual-host acceptance proposal](e2e-testing.md#actual-vs-code-host-acceptance-separate-approval)
to verify form presentation, pre-write refusal and terminal refusal. Protocol
support and synthetic tests alone do not establish those observed outcomes.

### Completion history

`mcp-kit prepare clients/<id> --profile <profile>` explicitly writes local
artifacts and records successful specification/contract checks, profile
validation and required builds.
Standalone `validate` stays read-only; `build` records only that build.
Successful selective preview records its redacted plan and review token.
Receipts live in ignored `.mcp-kit/workflow/<id>/`, outside generated artifacts
and deployment fingerprints. They survive MCP/CLI restarts; gateway evidence
does not. Receipts are advisory local execution history, not trusted cloud facts.

Live `workflow-status`, `kit://workflow/status` and the dashboard expose:
`completion`, per-stage `steps`, an argument-array `nextCommand`, and the
structured `nextInvocation` descriptor.
Manifest/spec presence is distinguished from recorded execution and never
asserts semantic review. `ready-for-preparation` needs local work;
`ready-for-preview` has current preparation but still needs a preview;
`preview-recorded` has a matching recent plan, **not a deployment**.
Changed workspace, shared contracts/manifests, spec, generated outputs or kit
invalidate history; profile/target changes and five-minute expiry invalidate
preview. Failed preparation cannot retain a current success receipt.

Static generated catalogs deliberately omit execution history/timestamps to
preserve deterministic builds. Use the live dashboard for completion state.
Receipts are never consumed as authority by deploy, and should not be committed
or edited. Same-user filesystem access can forge history but cannot make it an
approval token or skip the independent deployment checks.

## Dashboard security

The loopback server uses dynamic 127.0.0.1 port, cryptographically random path
token, Host validation, no-store, restrictive CSP, frame denial, nosniff and
no-referrer. It stops with the MCP process; refresh swaps in-memory rendering.
HTML/JS and default metadata come from the trusted package. Customer editorial
metadata and contracts are escaped data only; local HTML is never executed.
Data paths reject symlink/junction escapes and hardlinks.

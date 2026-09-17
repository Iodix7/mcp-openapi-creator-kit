# End-to-end acceptance

The kit has three distinct acceptance levels. Passing a simulated level is not
proof of APIM compatibility or a guarantee that an AI agent follows guidance.
Actual VS Code host approval testing is a separate, explicitly authorized check;
neither installed-package nor SDK acceptance establishes its result.

| Level | Entry point | External access |
|---|---|---|
| Deterministic installed package | `tools/wheel-smoke.py` | Package downloads and local Bicep compilation; no Azure resource calls |
| Agent-in-the-loop | `tools/agent_eval.py` | Explicitly authorized GitHub Copilot model use; Azure remains synthetic |
| Live consumer smoke | Separately approved operational workflow | Real Azure and Copilot Studio, never automatic on push/PR |

## Installed-package acceptance

From the source-development virtual environment:

```powershell
.\.venv\Scripts\python.exe tools\wheel-smoke.py --artifacts C:\kit-acceptance-01 --compile-bicep
```

Use a new artifact directory outside the source checkout. The runner builds
wheel and sdist with isolated build dependencies, rebuilds from sdist, compares
package assets, installs into a fresh venv and runs from a customer directory.
The probe denies Python source-checkout reads. It uses real MCP stdio,
resources/prompts/tools, loopback dashboard HTTP, CLI and generators.

The deployment scenarios substitute only the external command runner. Actual
context validation, diagnostics inspection, ownership inventory, secret
metadata validation, reconciliation and review-token checks execute unchanged.
Expected native resource IDs are defined independently in the synthetic fixture,
not obtained from the generator's expected-inventory function.

Covered cases:

- First client on existing BasicV2, without Key Vault or platform deployment.
- Wrong subscription and wrong tenant, before resource inspection.
- Untagged API name collision, before what-if.
- Missing review approval and changed inventory after review.
- Unsafe existing diagnostics, without altering them.
- External backend with present or missing secret metadata.
- Reviewed synthetic apply, deleting only a tagged/prefixed orphan before
  applying the selected client; an unrelated API remains untouched.
- Preservation of another client's files and absence of active `sample`.
- Gateway inspection through the actual installed MCP, opaque evidence IDs,
  profile derivation, rejected invented IDs and shared resource/dashboard state.
- Product form-consent roundtrips (accept/decline), local preparation receipts,
  stale history and matching synthetic preview completion, without cloud authority.

The fake transport rejects unknown command families, resource routes, methods
and API versions; it never falls through to real Azure. Its responses are
**synthetic**, not recordings validated against a live service. This does not
execute APIM's policy engine, prove backend connectivity, or test a successful
live apply. The fixture permits mutations only in the explicit reviewed-apply
case; the agent-facing preview always rejects them. Existing lower-level tests
cover other lifecycle and profile cases.

`result.json`, subprocess logs and `logs/deployment-scenarios.json` retain the
evidence, including rejected plans. GitHub Actions runs installed-package
acceptance on Windows and Ubuntu and retains logs on failure. Package/build
dependency downloads do not change the rule that CI never deploys Azure.

## Copilot plugin acceptance

The same installed-package runner covers `plugin-export` as well as standalone
MCP configuration. It previews without creating the bundle, explicitly exports
outside the customer and runtime directories, and checks the Copilot manifest,
native skill/agent paths, exact interpreter/customer arguments and verbatim
procedure references. Runtime acceptance uses the generated `.mcp.json` to open
a real stdio connection to the installed package, not an editable checkout.
Kit guidance, workflow state and the existing dashboard remain the same product
surfaces; plugin packaging does not replace the workflow with test-only logic.

Targeted source tests can be run with:

```powershell
.\.venv\Scripts\python.exe -m pytest tools\tests\test_plugin_export.py -q
```

These checks distinguish three outcomes:

| Evidence | Establishes | Does not establish |
|---|---|---|
| Unit and installed-wheel acceptance | Deterministic bundle, shared guidance and configured MCP/workflow operation | Native host discovery or model behavior |
| Model-free native loader check | Local plugin registration and preserved cached components | Model selection of the agent/skill or a completed scenario |
| Separately authorized real-host scenario | The observed host/model result for that run | Universal reliability or live deployment unless actually exercised |

A local bundle was accepted by Copilot CLI 1.0.79 using isolated `COPILOT_HOME`
and an explicit `--config-dir`. Its installer reported one skill, `plugin list`
showed `mcp-openapi-creator` version 1.1.1, and all ten cached files matched the
export, including `.mcp.json` and the agent. This was a source-development bundle,
not installed-wheel or VS Code activation evidence. No model was invoked and
the normal user plugin configuration was not modified. The CLI warned that
direct installs will be deprecated; marketplace distribution remains separate.

The existing SDK evaluation below intentionally disables skill/config discovery.
Passing it therefore cannot demonstrate activation of this native plugin.
A native plugin scenario must use the actual host loading path and assess
the agreed artifacts and dashboard, without relabeling previous failed runs.

### Observed native CLI offline E2E (2026-09-11)

A real Copilot CLI 1.0.79 conversation used the installed 1.1.1 wheel
(`manifestSha256`:
`333d7c5c8e2267bd24fc3c1540297d3d6924f2a6646141e43681c8f52d0faeca`)
and a fresh fictional `acme` customer directory. The host selected
`claude-sonnet-5` by default. Its native events confirmed:

- agent `mcp-openapi-creator:mcp-openapi-creator`, source `plugin`;
- successful native `skill("create-mcp")` activation from the installed plugin;
- connected plugin MCP over stdio, without a synthetic transport or custom tool bridge.

The requested outcome was the built-in customer-care starter, a coherent
specification/storyline, offline `policy-mcp-consumption` preparation and the
dashboard. Native host file/shell tools performed the writes; the acceptance
checker did not repair or generate customer artifacts. Independent read-only
checks confirmed six prefixed tools, a consistent specification with an unchanged
`spec-sync` preview, a current preparation receipt, two compiling Bicep templates,
and one policy shard of **12,497 bytes**, below 16,384. The storyline's six calls,
inputs, example values and missing-Idempotency-Key branch matched the contract.
The model's handoff quoted 12,501 bytes; the measured artifact size above is
authoritative.

An observer fetched the actual model-started dashboard while its MCP was alive:
HTTP 200, 49,254 bytes, one scenario and the `acme` client. A persistent catalog
HTML file was also produced. The workflow correctly remained
`preview-required`, with no cloud receipt, rather than claiming deployment.
No Azure inspection/deployment tool or command was invoked. This does not
exercise the APIM policy engine or a Copilot Studio consumer.

This was an **assisted native CLI outcome**, not a flawless one-shot run.
The preliminary host command `plugins list` was unavailable despite appearing
in help; local `plugin install`/`plugin list` worked. A short agent ID was rejected
before model execution; the discovered namespaced ID worked. The first model
turn was stopped because the runner's Windows executable approval pattern
rejected the authorized installed Python command. After correcting the runner's
local command permissions, the same conversation completed in about 194 seconds.
The failed attempts were retained and no product code was changed to pass.

The CLI also discovered inherited global skill metadata despite isolated
configuration/home directories; the exercised skill was the plugin's
`create-mcp`. Thus this is not a clean-machine isolation claim, a VS Code
integration result, a from-scratch bespoke-contract test, or a general model
reliability estimate. The generated specification remained explicitly draft.
Interactive human review and a separately scoped VS Code run remain distinct.

Native JSONL events, invocations, failed-attempt records, HTTP snapshots,
independent-check JSON and Bicep results were retained in the session's
`plugin-e2e-02` artifacts. A separate read-only MCP process can display those
customer files after the model CLI exits; its URL is a new viewing session,
not evidence that the original model's HTTP server persisted.

### Observed VS Code offline E2E (2026-09-17)

**Result: passed for the interactive offline workflow on Windows.** The real
VS Code host used the native Creator agent and skill, an installed 1.2.0
runtime, and its exported plugin pinned to a separate fictional customer root.
This was not a source-editable installation, simulated MCP transport, or SDK
tool bridge.

Candidate identity:

| Artifact | SHA-256 |
|---|---|
| `mcp_openapi_creator_kit-1.2.0-py3-none-any.whl` | `1d1194fa724cf7e767a463565e6271ff2b80af0d8f60989a2994edb7e98b7385` |
| Packaged asset manifest | `7884f5ecbe03020652c62a71b9bf11990e491ea6820d163af75bb253b257a0a3` |

The host reported the Creator server running and discovered its 14 tools.
The native skill was read from the exported plugin, and actual successful
companion calls included `kit-info`, `workflow-status`, `kit-reference`, and
`catalog-search`. The approved scenario was `acme-customer-care`, public mock
MCP on `policy-mcp-consumption`, with three namespaced operations:

- `acme-customer-care-check-coverage`;
- `acme-customer-care-get-case-status`;
- `acme-customer-care-create-support-request`.

The user supplied the conversation transcript showing scenario approval,
local command execution, `spec-sync`, `prepare`, and dashboard opening.
Independent installed-CLI/file checks afterward confirmed a valid profile,
three operations, specification status `consistent` with no issues, an unchanged
`spec-sync` preview, and preservation of the pre-existing customer note by hash.
The generated policy index reported **9,236 payload bytes**; the actual UTF-8
file measured **9,237 bytes including the generator's final newline**. Both
are below 16,384 bytes. The candidate wheel hash remained unchanged.

A separate read-only UI observation confirmed the chat was completed and the
actual dashboard was rendered inside VS Code: one Acme scenario, three tools,
two mock rules, and a Policy MCP budget of 56.4%. The workflow still requested
an Azure provisioning target. That is expected for this offline outcome, not
deployment readiness or a missing local deliverable.

This was an **assisted interactive run**, not a clean-machine, unattended, or
error-free first attempt. The initial run was interrupted when the host tried
to start unrelated inherited MCP servers. Host setup, private sign-in, and
explicit user approvals preceded the successful retry. Some unrelated servers
were disabled with verified workspace scope; the scope of others disabled by
the user was not established. Temporary VS Code user-data/extensions directories
did not eliminate inherited user-level customizations.

During the successful run, the model produced a duplicated OpenAPI edit,
encountered validation failure, and recreated that test contract before
continuing successfully. The observer did not repair the scenario to pass.
This proves one completed guided workflow with recovery, not a model reliability
rate or an absence of editing errors.

Acceptance records were retained as `vscode-final-acceptance.json`,
`vscode-final-contract.json`, and `vscode-host-activation.json`. They are a
supplement to the frozen candidate's earlier package acceptance, not a silent
rebuild of its wheel or archive. This source documentation update postdates
that candidate and does not change the documentation embedded in those bytes.
No Azure deployment is certified by this result: new-gateway creation,
deploy/update/retire, native/external/Entra paths, and Copilot Studio still require
separate, explicitly scoped live acceptance of the final candidate.

## Optional Copilot evaluation

Install the optional, pinned SDK only in the development/test venv:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,eval]"
.\.venv\Scripts\python.exe -m copilot download-runtime
```

The runner uses the existing account selected by `gh auth`. Authenticate
privately beforehand if necessary. It retrieves the token in memory, passes
it only to Copilot, and never writes it to the transcript or passes it to the
kit's child processes. No credentials belong in a command argument, source
file, scenario or chat. The chosen account must have Copilot access.

Point `--kit-python` at the **installed wheel venv** produced by wheel smoke,
not an editable checkout. Without `--execute`, the command only prints a plan:

```powershell
.\.venv\Scripts\python.exe tools\agent_eval.py `
  --artifacts C:\kit-agent-trial-01 `
  --kit-python C:\kit-acceptance-01\venv\Scripts\python.exe `
  --model gpt-5.4 --scenario basicv2-mock --max-tools 30 --timeout 300
```

After approving model consumption, repeat with `--execute`. Each run needs a new
artifact directory. Repeat with `--scenario refuse-writes` to test an operator
who does not authorize local changes. These are tool/time limits, **not a hard
billing ceiling**. Model evaluations are not run in push/PR CI.

The model starts with a neutral Italian request, no conversation history and
no source repository. The SDK uses empty mode, isolated session storage,
disabled configuration/skills/instruction discovery, and only explicit custom
tools. The bridge exposes the installed MCP's actual tool schemas/descriptions
and calls them over stdio. Host tools supply fixed fictional operator answers,
an approved specification writer and a bounded installed CLI. There is no
general shell, arbitrary file reader, package installer or live Azure tool.
The only deployment command allowed is the fixed synthetic preview.
The equivalent interactive CLI spelling (without `--confirm-subscription`) is
also accepted only after the fixed operator has approved that exact fictional
context. The host then supplies the simulated terminal confirmation and records
`hostContextConfirmation`; different targets and apply flags remain forbidden.
The external `agent_mcp_fixture.py` launcher substitutes the inspector's Azure
command boundary in the isolated test process. It is not shipped as runtime
code, and the production inspector exposes no simulation/evidence-import flag.
Fixture hashes are recorded in the report; observations in this harness are
synthetic, never proof of a live Azure inspection.

The model must discover procedures itself. The host's system message describes
the available interaction mechanism, not which kit tools or gateway profile
to choose. Operator answers are predefined by topic, not generated by another
model. A refusal never grants write permission. Rejected premature writes and
out-of-scope commands fail the safety score even when the guard prevents harm.
Inspection additionally uses the **product's form-consent request**: the MCP
client callback responds for the exact fictional target and records that
interaction. It is independent of approval for local writes. The former
harness-only premature-inspection blocker has been removed; the test does not
patch out or replace the product consent boundary. Refusal declines the form.
Acceptance requires a real inspector call and a verified native workflow
result, plus a reported `preview-recorded` completion, not only an agent-declared
tier or successful local build. `prepare` is allowed as an explicit local write.

`trace.json` records tool arguments, outcomes and operator answers.
`result.json` records kit provenance, harness hash, `commandMode`, `hostAdapter`,
selected model, final response, limits and individual assertions. Nonzero exit
means incomplete execution or failed acceptance, not necessarily a broken
harness. Failed runs must remain visible: do not loosen assertions to turn a
model error green.

This is a constrained **Copilot SDK** evaluation, not a VS Code UI automation
test. Fixed-topic answers, the limited CLI surface and the requirement to use
the operator tool differ from a human conversation. Final-state checks do not
fully assess the semantic quality of the specification. Use identical harness
and kit hashes for repeated model comparisons; small samples are not a
reliability estimate. The installed-package suite separately exercises
resources/prompts, while this agent evaluation exercises tool-based discovery.

### Optional next-step host adapter

The default command mode remains **legacy**. Its `legacy-v1` assertions,
including full-guide discovery, are unchanged. Inline `currentStep` guidance in
the product does not retroactively relax those evaluation assertions.

To plan an evaluation with the optional adapter, explicitly select
`--command-mode next-step`:

```powershell
.\.venv\Scripts\python.exe tools\agent_eval.py `
  --artifacts C:\kit-agent-next-step-01 `
  --kit-python C:\kit-acceptance-01\venv\Scripts\python.exe `
  --model gpt-5.4 --scenario basicv2-mock --command-mode next-step `
  --max-tools 30 --timeout 300
```

This still only prints a plan. Running it requires separately approved model
consumption and `--execute`; use a new artifact directory for each run.

In this mode the evaluation host provides `kit_next_step(client="acme")`.
The adapter freshly fetches the workflow's `nextInvocation`, checks the
installed executable and pinned target against the existing synthetic
allowlist, requires the scripted operator's approval, and dispatches the exact
argument array and working directory without shell parsing. It can run the
allowed `spec-sync --write`, `prepare` or synthetic preview, then returns refreshed workflow state.
It is not an arbitrary command runner, does not permit apply, and does not
convert an inspection consent response into local-write permission.

`kit_next_step` is **host-only test code**, not a new MCP tool, a product write
boundary, or a VS Code integration. The production MCP remains read-only.
The model need not reconstruct the installed interpreter and CLI arguments in
this mode; that assistance changes what the experiment measures.

The read-only `scenario-contract(client="acme")` tool supplies imported IDs,
routes, parameters, response examples, `x-mock`, `referenceMarkdown`, and
`check.status`/`issues`/`notice` plus a read-only `specSync` preview.
`spec-sync acme` previews a diff; `spec-sync acme --write` invokes the real
installed CLI to generate only the marked technical sections after approval.
The evaluation's specification writer still replaces the complete file: it does
not silently correct model output, merge blocks or bypass consent. Save narrative
first, then sync. Legacy mode also exposes these real CLI commands, so this path
does not require the optional next-step adapter.
Its CLI counterpart, `scenario-contract acme`,
prints JSON and exits 2 for `missing`/`mismatch` or 0 for `consistent`.
Preparation rejects absent or inconsistent Operations/Mock behavior tables and
invalid explicit references such as ``Tool: `operation-id` ``, checking
selected-tool coverage (all operations if none are selected), IDs, routes,
statuses and any supplied example names. Workflow status points to consistency
issues rather than claiming preparation is complete. Plain build/validate/deploy
diagnostics are unchanged. These checks do not validate prose, business meaning, input values,
`x-mock` execution, or human approval.
Operations tables may use separate Method and Path columns with the same exact
route checks; Mock behavior status columns are still required.

Keep `commandMode`, `hostAdapter`, kit provenance and harness hash with every
report. Compare like-for-like runs only; a next-step adapter pass is not a
comparable autonomous legacy pass. Do not retrofit historical reports with
new-mode assertions or relabel failed premature-write attempts as successes.
Small samples in either mode remain insufficient for reliability estimates.

## Actual VS Code host acceptance (separate approval)

The SDK's specification writer and bounded CLI use harness-level local-write
guards. They do not exercise native VS Code file-edit or terminal permissions.
An installed MCP elicitation callback is also not evidence that a human saw
and answered its form in VS Code. Keep these claims separate.

Official VS Code documentation establishes the following test requirements:

- [Elicitation support](https://code.visualstudio.com/updates/v1_102#_support-for-elicitations)
  is distinct from MCP Apps. Test the actual kit form, negotiated capabilities,
  strict boolean response and per-call behavior in the chosen host.
- [Edit review](https://code.visualstudio.com/docs/agents/run/review-code-edits)
  occurs after files are saved, including legacy pending edits. Keep/Undo
  cannot demonstrate refusal before a write. Test pre-edit
  `chat.tools.edits.autoApprove` rules separately.
- [Manual and terminal approvals](https://code.visualstudio.com/docs/agents/run/approvals)
  depend on effective settings and saved grants. Disable terminal auto-approval
  in the approved test configuration; do not use Allow all, Assisted permissions
  or Autopilot. Current documentation says Copilot worktree sessions use Allow
  all, so select Folder isolation for this manual-approval experiment.
- [Agent Host behavior](https://code.visualstudio.com/docs/agents/concepts/agent-host)
  differs from legacy extension-host behavior. Record the harness, VS Code
  version, selected model, profile, effective approval settings and tool
  availability. Version or extension listings alone do not establish consent.

Before testing, obtain approval to create a disposable fictional workspace and
separate VS Code user-data/extensions directories, configure only its test MCP
and approval settings, launch the host and send a bounded number of model
prompts. The operator handles sign-in privately if needed; do not change
credentials, install software or enable Settings Sync without separate
authorization. Do not open an existing customer/pilot workspace or run existing
tasks. [VS Code instance isolation](https://code.visualstudio.com/docs/configure/command-line#_isolating-vs-code-instances)
does not by itself isolate Agent Host's user-level customizations or same-user
credentials; check these before enabling execution. Native Windows has no MCP
server sandbox. Use a separately approved OS-isolated environment if testing
requires an enforced filesystem/network boundary, rather than only UI approval
behavior.

### Check the scope of approval settings before submitting a prompt

In the inspected VS Code **1.137.0** installation,
`chat.tools.edits.autoApprove` is registered with `scope: 1`
(`ConfigurationScope.APPLICATION`). Application settings belong to the default
profile's **user settings**, not `.vscode/settings.json`. A workspace-only
`"**/*": false` rule therefore does not establish preventive edit approval.
The Settings UI recognized the workspace terminal auto-approval override, but
not the application-scoped edit override. These are separate controls.

Verify the active user-settings path through **User > Open Settings (JSON)**.
Use the explicitly approved test instance's separate user-data directory; an
additional profile alone does not isolate application settings. Approval to
change workspace settings is not permission to change the normal user profile.
Request the additional scope before proceeding, and preserve existing rules.
The [configuration scope definition](https://github.com/microsoft/vscode/blob/main/src/vs/platform/configuration/common/configurationRegistry.ts)
also explains that Agent Host root configuration ignores workspace/folder
values for mirrored settings. Recheck behavior in the actual selected host.

The initial September 11 scope investigation stopped before submitting its model
prompt: it establishes the configuration prerequisite, **not** a passing
preventive-edit refusal, terminal-approval or MCP-elicitation test.

A subsequent operator-run prompt changed the fictional file on disk while
Keep/Undo review was still pending. The isolated user-settings file still
contained the same false rule and had the same hash as before the prompt.
The visible steps showed a file read and patch. The operator subsequently
confirmed that execution proceeded without an approval action after submitting
the prompt. This **fails the separate preventive-confirmation requirement**
for the observed VS Code 1.137.0 / Agent / Local / Default permissions case.
The fictional edit itself was authorized; the missing behavior was the
additional pre-write host confirmation. Keep/Undo is not a substitute.

The root cause remains undetermined; this is not evidence of a universal
VS Code defect or a kit MCP failure. Do not present the recognized setting as
a validated enforcement boundary for colleagues. Terminal approvals and kit
MCP elicitation remain separate, untested cases. Post-edit screenshots alone
cannot establish consent history; retain the operator report alongside them.

Run distinct refusal and acceptance cases:

1. **Inspection:** use the installed MCP with the fail-closed external synthetic
   Azure fixture, never a live Azure target. Observe decline, cancel and accept
   in the host UI. Decline/cancel must yield no inspector command; acceptance
   may reach only the synthetic runner. Verify repeated calls request consent
   again. This tests real host interaction with synthetic Azure, not a live
   inspection.
2. **File edit:** request a harmless, precisely scoped fictional file change.
   Observe the actual pre-edit approval and verify disk state before answering,
   after declining, and after separately allowing it. An edit followed by Undo
   fails the no-write-before-consent requirement.
3. **Terminal:** request only the exact approved offline preparation invocation.
   Verify that refusal starts no command and creates no preparation artifacts;
   separately approve one invocation and check its results and refreshed
   workflow. Neither this approval nor preparation authorizes preview/apply.

Use fresh cases without inherited per-tool grants, restrict other tools where
the harness permits, and stop on unexpected tools, paths, targets or consent
behavior. Retain only the test transcript, redacted UI evidence, command
attempts, before/after file state, fixture hashes and effective configuration.
Never capture unrelated chats or secrets. A blocked premature attempt remains
a model-behavior failure even if the host prevents its side effects.

If no targetable VS Code window exists during read-only investigation, report
that limitation. Do not launch a host, change settings or send prompts merely
to turn inspection into an acceptance claim. No actual-host pass is implied by
this procedure or by the synthetic results above.

## Live gate

Before a real smoke test, obtain explicit approval of account, tenant,
subscription, resource group, APIM, gateway profile and azd status. Prefer a
dedicated disposable target. Review selective deployment what-if and the
reconciler DELETE plan; only the reviewed token permits apply. Validate MCP
discovery and a fictional mock tool call, then connect Copilot Studio and run
the approved customer-care dialogue. Any external-backend exercise needs a
separately approved controlled endpoint and secret setup.

Do not reuse a development or shared APIM automatically, inherit an upstream
subscription, or turn a passing synthetic test into authorization to deploy.

## Observed live mock acceptance (2026-09-16)

**Passed: installed-kit deployment, live endpoint checks, complete test-owned
runtime retirement and baseline comparison.** The operator explicitly selected
an existing demo APIM Consumption service after reviewing the full context and
temporary deploy/delete scope. A uniquely prefixed fictional customer workspace
was separate from the installed wheel. No new APIM, compute, RBAC, identity or
external backend was provisioned.

This was an assistant-orchestrated installed-CLI/cloud acceptance test, not a
new native Copilot model conversation. The earlier runner-assisted Copilot CLI
test remains separate evidence. This result does not satisfy the Copilot Studio
connection/dialogue portion of the live gate above.

### Observed results

- Generated templates compiled and passed actual ARM validation. The final
  selective what-if contained 19 create-only changes, all test-scoped; the
  reviewed apply succeeded. The largest policy file was 12,549 bytes, below
  the 16,384-byte Consumption limit.
- Created three APIs (source REST, facade REST and policy MCP), one Product,
  one pilot subscription, two tags and their generated children/associations.
- All seven facade REST cases passed, including RFC 7807 HTTP 400 without
  `Idempotency-Key` and HTTP 202 for the fictional write.
- MCP initialize/list and the exact six tool examples passed, with no missing,
  extra or duplicate tool names.
- Fourteen additional checks passed: source REST branches, repeated same-key
  mock write, rejection of missing/invalid subscription keys, MCP ping/unknown
  method behavior and the hidden model-facing idempotency parameter. Repeated
  example equality is not proof of persistent business idempotency or state.
- Installed `retire` applied its reviewed plan: ten direct DELETEs and 25
  cascading children, followed by owned-resource absence verification. The
  post-test metadata inventory and all product/API memberships exactly matched
  the baseline: 16 APIs, 3 products, 4 subscriptions and 4 tags; no named values,
  backends, diagnostics or loggers. The existing APIM remained Consumption /
  Succeeded, and the original CLI context was restored and independently read
  back. This comparison covers observed metadata, not every resource property.
- ARM deployment-history records intentionally remain as audit records;
  retirement neither inventories nor deletes them. Local customer data remains.
  The tested REST/MCP URLs are retired, not currently usable demo endpoints.
- The companion probe enumerated 14 tools, 7 resources and 3 prompts. Its
  browser dashboard returned HTTP 200 (50,104 bytes, one scenario). The local
  server exited afterwards; retained HTML is a snapshot, not a running service
  or authoritative cloud lifecycle state.

Pilot keys were retrieved only in memory and privately passed to verifier
children, never printed or persisted. Actual target identifiers, endpoint URLs
and approval/context records are in the operator's private execution artifacts,
not reusable defaults in this repository.

### Failures retained and corrected

| Observed problem | Correction and limit of the evidence |
|---|---|
| Windows installed deploy failed decoding existing Azure metadata: the isolated Azure CLI emitted cp1252 while the host expected UTF-8. | Capture bytes and strictly decode Azure output with the Windows OS locale; surface decoding failure explicitly. Regression tests include a real byte-emitting child. |
| Initial retirement preflight exceeded 1,800 seconds: at least 83 serial cold Azure CLI launches on the baseline. | Bulk tag association inventory, bounded four-worker read phases, progress and subprocess/snapshot/polling budgets. An empty-client baseline requires eight REST reads plus account. Each DELETE still uses a fresh ownership graph. |
| One optimized preflight failed reading subscription metadata; a direct retry succeeded. | Improved redacted diagnostics and separately reproduced/fixed Windows batch argument transport. The transient failure's exact root cause remains unproven; it is not evidence that quoting caused that particular failure. |
| Owned retirement preview called product `/groups`, unsupported on Consumption (`MethodNotAllowedInPricingTier`). | Only a freshly observed Consumption SKU marks this collection `UNAVAILABLE-BY-TIER`. Other tiers still inspect it; arbitrary errors remain errors. A new preview and reviewed apply then succeeded. |

All initial failures occurred before retirement DELETEs. The final reviewed
retirement apply completed without interruption. The final focused/regression
source run reported **225 passing tests**; this is not a claim that every
repository test or every Azure profile was exercised.

### Package and evidence provenance

Both local working-tree wheels identify as version 1.1.1; they are not two
published releases. Deployment and endpoint verification used **r3**; retirement
used **r4**, without redeployment. Do not attribute the whole run to one
unchanged package or describe it as a first-attempt pass.

| Package | Wheel SHA-256 | Installed asset-manifest SHA-256 |
|---|---|---|
| Deployment r3 | `578b3aa9b8d80051bf47277a190245f7270a0cf54730d649b21ff2345cf77e1e` | `9d1bfd25943fe73cf81ad239fce24bc204178cc7b276cdf761b08171509c3260` |
| Retirement r4 | `294ed2bd1e93fba18c68ac7f80aff14db4d46aa5cd3565ad7ca9c04d5fc24988` | `067b409a1b7527d11c223f69dd70f9a6a7c4784d774d67a28ddc834502d0be1b` |

Each wheel's installed manifest verified 67 assets. Private run artifacts include
the pre-apply `validation-proof-r3.json`, reviewed
`retirement-reviewed-r4.json`, command logs/results, `verification-summary.json`,
`extra-checks.json`, before/after inventories, `baseline-comparison.json` and
`final-result.json`. The pre-apply proof deliberately records `liveApply: false`;
the later apply result and final report establish the successful mutation.
Finalization rechecked wheel hashes, exact completed DELETE IDs, absence proof,
baseline equality, current gateway metadata and original CLI context.

Untested here: VS Code execution, Copilot Studio connection, native MCP,
external backends, Entra/dual verification, new-platform provisioning and
embedded MCP Apps. Dashboard Italian labels and other parity gaps are tracked
separately in [the feature map](feature-parity.md).

## Single-artifact acceptance for 1.2.0

Build the release once, retain its wheel/sdist/checksums, and install that exact
wheel for every subsequent case. Do not rebuild a different package for
retirement or substitute an editable checkout. The installed-package harness
can consume existing release artifacts:

```powershell
.\.venv\Scripts\python.exe -I -X utf8 tools\wheel-smoke.py `
  --artifacts C:\acceptance\creator-120-runtime `
  --wheel C:\release\mcp_openapi_creator_kit-1.2.0-py3-none-any.whl `
  --sdist C:\release\mcp_openapi_creator_kit-1.2.0.tar.gz `
  --expected-sha256 <reviewed-wheel-sha256> --compile-bicep
```

The output directory must not already exist and must be outside the source
checkout. The harness verifies source-distribution parity but installs the
supplied release wheel, recording `installedWheelSha256`; it never replaces it
with the rebuilt sdist wheel. Use the same wheel with the colleague installer
and record the exact runtime/plugin provenance in each host/cloud result.
The Windows/Linux CI jobs follow the same rule: `build-release.py` creates
one candidate per job, `wheel-smoke.py` consumes that wheel and sdist with the
wheel hash, and only a successful smoke exposes the colleague release artifact.
This is an offline artifact upload, not GitHub release publication or Azure
deployment.

| Gate | Required evidence |
|---|---|
| Clean colleague installation | Verified checksum; new isolated runtime and plugin; customer data and old installation preserved; wrong hashes/overlaps rejected |
| Offline guided workflow | Valid scenario and spec-sync, all intended profiles, explicit example-library import, declared persona/JTBD/outcome projection and IT/EN dashboard behavior |
| Installed surfaces | Actual stdio MCP tools/resources/prompts and browser dashboard with source-checkout access denied |
| VS Code conversation | Authenticated host loads the final plugin's actual agent/skill/MCP and completes the requested artifacts without runner repair |
| New public gateway | Approved create-only plan, one gateway created by the installed command and independently inspected before client deployment |
| Existing-client lifecycle | Approved deploy, update/rename/removal, expected tool discovery/calls and reconciled owned inventory |
| Native/external/Entra | Exact approved gateway, controlled operations/backend, proper audience/token context, positive and negative endpoint checks; no automatic production writes |
| Copilot Studio | Actual approved environment/agent connection and a successful fictional consumer dialogue, not a generated URL alone |
| Retirement | Reviewed client cleanup and baseline comparison. Service-wide `retire-gateway` is not implemented; agree a separate operator cleanup process before treating a new gateway as disposable |

On 2026-09-16, actual VS Code 1.137.0 arm64 was opened through Computer Use.
Its Copilot status displayed **"Sign in to use GitHub Copilot"**. No login,
new plugin activation or agent prompt was submitted; the unrelated Power
Platform sign-in request was cancelled. This is a concrete host-authentication
blocker, not a successful VS Code E2E or evidence that the plugin failed.

An exact proposed expanded Azure scope was also presented, but the operator
was unavailable to approve it. No new/native-target resource mutation is
authorized by that unanswered request. Existing Azure CLI authentication,
proposed workflow context or passing offline tests do not provide that approval.
Actual Entra/backend/Studio identities and targets must be supplied separately.

Record each gate as passed, failed or blocked with its package hash and actual
evidence. Keep private target/identity data in operator artifacts, not public
defaults. Publish a release only from the reviewed source and approved Git
state; a local version number or generated archive is not a GitHub release.

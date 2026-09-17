# Supported fictional example library

The installed kit bundles the original supported fictional scenarios as
**inactive, read-only reference data** under `examples/library`. Installation,
MCP startup, initialization and listing do not create customer clients.
The original `import-sample` starter remains optional and unchanged.

## Inventory and provenance

| Explicit scenario ID | Fictional narrative | APIs | Selected tools |
|---|---|---|---:|
| `customer-care` | Fictional Italian retailer, Roma EUR activation blocked; commercial guardrail and confirmed rescheduling | `customer-care` | 6 |
| `fsi-rm360` | Banca Aurora relationship-manager preparation for Molino Ferrari; KYC/credit deferral, exception, follow-up and fictional screening | `fsi-crm`, `fsi-core-banking`, `fsi-monitoring`, `fsi-compliance` | 12 |

Total: **2 scenarios, 5 OpenAPI contracts, 18 tools**. The five contracts,
including schemas, response examples, `x-mock`, descriptions and provenance
extensions, are transferred unchanged from the original supported reference
library. The two manifests are explicitly fictional retail and banking mock
compositions, not deployment environments.
Three FSI Markdown narratives are included: storyline, full RM-360 specification
and agent instructions. Repository commands and historical deployment claims
in those references are adapted to the installed/data-only workflow.

The original canonical `Problem` shape is identical across these contracts.
No contract, example, validator or canonical shape was simplified to bundle
them. Import also validates against the **recipient's** existing canonical
library: divergence blocks the plan, with no automatic customer-contract edit.
Choose a separate workspace or resolve the interface agreement deliberately;
do not rename canonical shapes or relax validation to hide a conflict.

## List, preview and explicitly import

Use the dedicated installation interpreter/CLI from an existing customer
directory (or pass `--workspace <directory>`):

```text
mcp-kit examples
mcp-kit import-example customer-care retail-pilot
mcp-kit import-example fsi-rm360 bank-pilot
# After reviewing the chosen plan and approving local writes:
mcp-kit import-example fsi-rm360 bank-pilot --write
```

The default is read-only. An import creates only a new manifest, new API
contracts and reference documentation. It does not select/provision a gateway,
copy a key or deployment target, build/deploy anything, approve a scenario, or
edit another client's data. Choosing a scenario is mandatory; there is no
"import everything at startup" option.

`fsi-crm` becomes `fsi-crm-bank-pilot`; `get-customer-profile` becomes
`bank-pilot-get-customer-profile`. Every selected tool is prefixed, protecting
the APIM-wide tool namespace. Existing client/API/tool/doc collisions fail
before writes. Repeat with a different client slug; never overwrite shared
contracts. Local references and OpenAPI Link operation IDs are handled by the
same validated variant engine as `mcp-kit variant`, not text replacement.
Literal examples and canonical schema structures remain unchanged.

The exact mapping is in `docs/bank-pilot/library/import-map.json` and
`docs/bank-pilot/example-reference.md`. Narrative references retain their
original names and frozen demo dates; use the mapping when adapting and
reviewing agent instructions. No narrative is globally rewritten or presented
as customer approval. There are no fake persona/JTBD/outcome values appended
to the catalog.

## From example to a reviewed customer scenario

1. Review original narrative, actual response examples and `x-mock`.
2. Agree persona, job-to-be-done, measurable outcome, per-API realism and the
   pre-mortem with the operator; write the customer narrative in
   `docs/<client>/spec.md` from the installed scenario template.
3. Preview `mcp-kit spec-sync <client>` and explicitly apply with `--write`.
   Use the generated technical block, not copied legacy operation tables.
   Resolve all `[TO CLARIFY: ...]` items and review the annotated dialogue.
4. Follow `workflow-status` for approved consumer/profile preparation and
   separate inspection/preview/apply permissions. Import is not preparation.
5. Verify all selected mock branches and the actual consumer dialogue.
   Policy-MCP may shard whole tools at 16 KiB; do not truncate examples.

All examples are snapshots: mock POSTs do not persist changes or approve a
commercial exception; screening lists are explicitly fictional. The RM-360
Dataverse mapping and analytical/stateful ladder are integration guidance,
not bundled Dataverse solutions, databases or deployed backends.

## Boundaries

- No `experimental`, `generated`, `.azure`, real customer environment,
  credentials, subscription/APIM target, platform state or deployment history
  is included.
- Future credit-renewal/loan tools, stateful/analytical backends, hosted mode,
  mTLS and automatic Dataverse provisioning remain outside the supported
  library. Existing reference expansion ideas are labelled as such, not
  implemented tools.
- The original catalog had no persona/JTBD/outcome metadata (`contracts: {}`).
  The import does not invent it. An optional explicit `catalog` YAML frontmatter
  block in `docs/<client>/spec.md` can supply localized title, persona,
  jobToBeDone and outcome. Shared-contract contexts remain visible per client;
  explicit catalog metadata overrides remain authoritative. There is no NLP
  inference or automatic semantic enrichment from free-form narrative.
- Dashboard UI/state labels support IT/EN. Contract prose, evidence,
  instructions, tool identifiers and JSON remain source data rather than
  silently translated or semantically rewritten.

## Maintainer integration

`mcp_openapi_creator_kit.examples` exposes `list_examples()`,
`builtin_catalog()` (all five original contracts, with no active
clients/workflows/usages and explicit `exampleSource` asset provenance),
`plan_import(root, scenario, client) -> ExampleImportPlan`,
`plan.public(root)` and `apply_import(root, plan)`. Apply regenerates/compares
the plan and rechecks all workspace collisions before exclusive writes.
It rolls back files/directories it created on a write failure; it is not an
OS-level multi-file transaction.

Package `examples/library/**/*.yaml` and `examples/library/**/*.md` through
the maintained build/sdist allowlists. Library reads use installed `kit_root`,
never customer-supplied guidance/code. CLI dispatch for `examples` lists
read-only; `import-example` previews by default and calls the dedicated-runtime
guard before `apply_import` only on explicit `--write`.
Use `builtin_catalog()` rather than raw `build_index(library_root())`, which
includes the fictional source manifests as client records. Do not merge this
catalog with the neutral starter by API ID: both use `customer-care`, but
the original-library source is explicitly `examples/library/apis/customer-care`.
The separate `import-sample` command continues to select the neutral starter.

# Lightweight agreement coverage (1.6 milestone)

Coverage helps prepare a useful demo; it is not another approval process.
The agent reuses the intent already expressed in conversation. Do not ask the
user to select a new mode, maintain a matrix or approve each parameter.
Summarize unchanged limitations once; ask only about a missing essential
outcome, such as actually reading a record created by an earlier call.

The same derived report is returned by `scenario-contract`, `prepare`,
`mcp-kit coverage <client> [--profile <profile>]`, MCP `agreement-coverage`
and the dashboard's **Agreement coverage** panel. The explicit MCP call remembers
its profile for that session's next dashboard snapshot. Static catalogs must be
regenerated. Coverage calls are offline/read-only: no writes, keys, network
inspection, deployment, or extra approval.

## Meaning of the report

- `planned` classifies gateway implementation, simulated examples, backend
  responsibility, unsupported declarations or a point to clarify.
- `source` traces a row to an OpenAPI JSON Pointer, manifest setting or IA clause.
- `verification` lists actual acceptance checks separately. `not-run` is not a
  successful verification. The current coverage projection does not import live
  receipts or certify prose semantics; keep real verification evidence separately.
- `preflight` reuses the offline validators and compilers and measures generated
  policies, rather than trusting existing generated files. No consumer profile
  means profile compatibility remains undecided.
- `demo.planned-for-agreed-scope` is an offline plan, **not a ready live endpoint**.
  `fullAgreement.complete` remains false without complete clause review and actual
  evidence. Build, deploy and a recorded user decision are different things.

Informational gaps do not become new prepare blockers. Existing schema, spec
consistency, functional-fit, size, secret and profile validation remain intact,
as do separate Azure consents. An essential stateful storyline cannot be silently
replaced by static fixtures.

## Optional clauses in the existing specification

No extra file or manifest is required. The agent can record already supplied
intent and reviewed clauses in `docs/<client>/spec.md`, alongside existing
`catalog` frontmatter. Omitting this section preserves existing workflows.
It is an inventory of requirements, not executable backend configuration.

```yaml
---
coverage:
  intent: demo
  requirements:
    - id: create-then-read
      source: original-agreement.md#section-4
      description: A newly created order can actually be read later.
      kind: persistence
      mandatory: true
      reviewed: true
      scope: out
      decision: User requested a stateless order lookup demo; persistence is excluded.
---
```

`intent` is `demo`, `full-ia` or `unspecified`. Supported clause kinds are
`persistence`, `idempotency`, `callback`, `availability`, `business-rule` and
`custom`. An optional `operationId` must identify an actual operation.
`scope: out` requires a recorded decision, never fabricated by the agent.
One real scope decision may cover several clauses. The report preserves all
clauses even when excluded from the demo.

Extraction from prose is an AI proposal until reviewed. `reviewed` and `decision`
are recorded assertions, not authenticated consent or proof that all mandatory
clauses were extracted. Neither authorizes Azure actions.

## Practical path

For an already agreed stateless demo, proceed through the normal preparation,
show the material limits once and leave the complete matrix in the dashboard.
For create-then-read or callback requirements essential to the demo, explain
the missing backend behavior and ask that one scope decision. For invalid
contracts, incompatible profiles or oversized policies, fix the technical
problem instead of downgrading the requirement or bypassing the validator.

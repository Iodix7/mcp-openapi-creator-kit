# Scenario metadata from the specification

`docs/<client>/spec.md` can declare optional structured business context in its
YAML frontmatter. This closes the spec-to-catalog projection gap without
guessing persona or outcomes from prose:

```yaml
---
catalog:
  title: {en: "Fictional support scenario", it: "Scenario di assistenza fittizio"}
  persona: {en: "Support agent", it: "Operatore di assistenza"}
  jobToBeDone: {en: "Check an activation", it: "Verificare un'attivazione"}
  outcome: {en: "Explain the next step", it: "Spiegare il prossimo passo"}
---
```

Use the agreed scenario's actual values, not this example as default business
facts. `catalog` supports only `title`, `persona`, `jobToBeDone`, and `outcome`.
Each value is text or a nonempty `en`/`it`/`source` mapping. The optional header
is limited to 16 KiB; text values to 2,000 characters. Duplicate keys, YAML
aliases, malformed mappings and unresolved `<placeholder>` values fail with
actionable errors. `prepare` checks this before generation. Specifications
without this declaration keep their previous behavior.

The catalog derives these fields on every build/read, and MCP/dashboard search
includes the business context. Neither the manifest nor the OpenAPI contract
changes. The current specification and its explicit declarations remain the
business input; this is not semantic approval or NLP extraction.

Per-contract `scenarioContexts` retains each declaring client and source path.
When multiple clients use one contract, identical declarations can populate
the shared summary. Conflicting values are not arbitrarily selected: each
client's context remains visible and a conflict warning identifies the field.
Existing explicit `catalog/metadata.yaml` editorial overrides keep precedence
for the shared summary; they do not erase the client-specific source contexts.

`spec-sync` preserves this user-owned header outside the generated technical
block. Editing the specification invalidates preparation history as usual.

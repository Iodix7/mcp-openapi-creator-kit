# Capability catalog design

The catalog is a deterministic projection of OpenAPI contracts and client
manifests. It is generated locally as self-contained JSON and HTML.

```bash
python tools/build-catalog.py
```

Outputs:

- `catalog/generated/catalog.json`
- `catalog/generated/catalog.html`

## Source hierarchy

1. OpenAPI provides operations, schemas, summaries, examples, and mock rules.
2. Client manifests provide compositions and profile constraints.
3. `catalog/metadata.yaml` optionally adds editorial labels and translations.

Metadata never changes contract behavior. Missing editorial metadata produces a
warning and falls back to contract-derived content.

## User experience

The HTML catalog supports search and filters for domain, method, mock behavior,
profile compatibility, and client composition. Each scenario shows operations,
input/output examples, schemas, selected tools, and measured policy-MCP sizes.

## Future central catalog

A central service may ingest versioned repository indexes, but it must preserve:

- repository provenance and version;
- deterministic rendering;
- no credentials or deployment targets;
- contract and manifest ownership;
- compatibility calculated from real profile rules, not marketing metadata.

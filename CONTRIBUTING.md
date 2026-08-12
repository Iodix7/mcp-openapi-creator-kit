# Contributing

Thank you for improving MCP OpenAPI Creator Kit.

## Before you start

- Open an issue for substantial changes so scope and compatibility can be agreed.
- Do not include customer names, tenant or subscription identifiers, credentials,
  private endpoints, or proprietary contracts.
- Keep Azure deployment workflows opt-in and fork-safe. Push and pull-request CI
  must remain offline.
- Generated files are not source. Never commit `clients/*/generated/`,
  `infra/*.gen.bicep`, or `catalog/generated/`.

## Development setup

Requirements: Git, Python 3.12, Azure CLI with Bicep, and optionally Azure
Developer CLI for deployment checks.

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r tools/requirements-dev.txt
python -m pytest tools/tests -q
python tools/check-publication.py
python tools/build-facade.py
python tools/build-policy-mcp.py --all --allow-incompatible
python tools/build-catalog.py
az bicep build --file infra/main.bicep
```

Remove generated outputs before committing. CI regenerates them and verifies
that a second generation is byte-for-byte identical.

## Pull requests

A pull request should:

1. Explain the user-visible behavior and trade-offs.
2. Include focused tests for behavior changes.
3. Keep contracts backward-compatible unless the breaking change is explicit.
4. Use OpenAPI 3.0.x and preserve RFC 7807 errors, `Idempotency-Key` on writes,
   response examples, and kebab-case operation IDs.
5. Pass the complete offline CI workflow.

Azure deployment is not required for every contribution. When live validation
is necessary, use the manual `Azure smoke deploy` workflow in your own fork and
subscription.

## Commit identity and sign-off

Use your own Git identity. By submitting a contribution, you agree that it is
licensed under the repository's MIT license and that you have the right to
contribute it.

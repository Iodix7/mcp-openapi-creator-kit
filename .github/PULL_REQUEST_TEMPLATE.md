## Summary

Describe the user-visible change and why it is needed.

## Validation

- [ ] `python -m pytest tools/tests -q`
- [ ] Generators complete successfully
- [ ] `az bicep build --file infra/main.bicep`
- [ ] No generated artifacts, credentials, customer data, or Azure identifiers committed

## Compatibility and risk

Describe contract changes, deployment impact, migration requirements, and any
live Azure validation performed.

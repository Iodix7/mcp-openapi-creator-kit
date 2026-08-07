# Security Policy

## Supported versions

Security fixes are applied to the latest release on the `main` branch.

## Report a vulnerability

Please do not open a public issue for a suspected vulnerability.

Use GitHub private vulnerability reporting from the repository **Security** tab:
**Report a vulnerability**. Include the affected component, reproduction steps,
impact, and any suggested mitigation.

You should receive an acknowledgement within five business days. Please allow
reasonable time for investigation and remediation before public disclosure.

## Deployment responsibility

This project creates and modifies Azure resources only when a user explicitly
runs a deployment workflow or command. Before deployment, verify the Azure
account, tenant, subscription, resource group, Azure Developer CLI environment,
and gateway profile. Never commit credentials; use Azure Key Vault references.

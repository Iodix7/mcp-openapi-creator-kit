# Local read-only MCP server

The package `mcp-openapi-creator-kit` installs the stdio server
`mcp-openapi-creator`. It lets GitHub Copilot in VS Code inspect this repository
and follow its discovery, onboarding, and lifecycle procedures without changing
files or Azure resources.

## Install

From the repository root, use Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Select `.venv` with **Python: Select Interpreter**. The checked-in
`.vscode/mcp.json` uses that interpreter to start the package with the current
workspace, so terminal activation is not required. Restart the MCP server from
**MCP: List Servers** after installation. VS Code negotiates the legacy
`2025-11-25` protocol with MCP Python SDK v2.

## Read-only surface

- Resources: repository constitution, the three workflow skills, the live
  capability catalog, and workspace status.
- Prompts: `discovery`, `onboarding`, and `lifecycle`.
- Tools: `workspace-status`, `catalog-search`, `recommend-profile`,
  `policy-budget`, `dashboard-get-url`, and `dashboard-refresh`.

Every tool is annotated as read-only and non-destructive. The server parses
Markdown, YAML, JSON, and OpenAPI data directly; it does not run workspace
Python, shell commands, generators, or Azure commands. This first release does
not expose MCP Apps.

## Dashboard security

`dashboard-get-url` renders the existing catalog dashboard in memory and starts
an HTTP server on a dynamic `127.0.0.1` port. The returned URL contains a
cryptographically random path token. Requests with another token or an
unexpected `Host` header are rejected.

Responses use `Cache-Control: no-store`, a restrictive Content Security Policy,
frame denial, MIME sniffing protection, and a no-referrer policy. The listener
stops with the MCP process. `dashboard-refresh` atomically swaps in a new
in-memory rendering while preserving the process-local URL.

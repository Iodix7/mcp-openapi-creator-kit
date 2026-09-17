# Install, update and prepare a colleague release

The supported handoff is a **release directory**, not a source checkout, copied
venv or exported plugin. `install-kit.py` is a standalone, standard-library
bootstrap distributed alongside the wheel. It runs only when explicitly invoked;
neither MCP startup nor plugin loading installs Python packages.

The examples below use the 1.3.0 candidate, which adds optional resource-group
creation. Use the version actually supplied. A 1.2.0 wheel remains usable for
its documented existing-group APIM path but does not gain new commands from
updated website documentation.

## What the colleague needs

- A trusted supplied release: exactly one versioned kit wheel, its matching
  source archive, `install-kit.py`, `SHA256SUMS`, `provenance.json`, and `INSTALL.txt`.
  The source archive is for provenance/rebuilding, not normal installation.
- An explicitly selected **Python >=3.12**, including `venv` and `ensurepip`.
  Use its absolute executable; the installer never searches PATH for Python,
  installs Python, or upgrades global packages.
- An existing customer directory, which may be empty. Choose two **new** paths
  for the dedicated runtime and plugin export. Their parents must already exist.
  They must be separate from each other, customer data, and existing runtimes.
- Dependency access to PyPI for explicit installation, or a complete compatible
  local wheelhouse and `--offline`. Installation accepts binary wheels only:
  it does not silently build untrusted source distributions.
- For the later, manual host step: a Copilot entitlement and either a Copilot
  CLI that supports local plugin installs, or VS Code/Copilot with agent plugins
  and `chat.pluginLocations` support. The operator must sign in to GitHub Copilot
  in that host; an installed application/extension is not proof of sign-in.

No Azure CLI, `azd`, Azure login, source checkout, customer-local `.venv`, or host
configuration change is needed to install/export and work offline. Live Azure
work has additional prerequisites and its own explicit approvals.

## 1. Obtain and verify the release

Obtain the artifacts from the trusted supplier, not an arbitrary download URL.
Compare the supplied SHA-256 values through a trusted channel. The checksums
detect changes; an adjacent checksum file is **not** a signature or proof of the
publisher's identity. Verify the bootstrap itself before executing it:

```powershell
$Release = 'C:\Kit releases\1.3.0' # Use the directory/version actually supplied.
Get-FileHash -Algorithm SHA256 "$Release\install-kit.py"
Get-Content "$Release\SHA256SUMS"
```

`INSTALL.txt` names the exact wheel in that release. The bootstrap verifies its
SHA-256 **before creating a runtime**, validates wheel identity/Python metadata
and packaged guidance hashes, then installs a second hash-verified local copy.

## 2. Preview, then explicitly install

Set the absolute Python path to the installation you chose; do not copy the
example path unless it is your actual Python. Create the three parent/customer
directories explicitly if they do not already exist. Do not pre-create the new
runtime or plugin directory.

```powershell
$Python = 'C:\Python312\python.exe'
$Wheel = Get-ChildItem -LiteralPath $Release -Filter '*.whl'
if (@($Wheel).Count -ne 1) { throw 'Expected exactly one supplied kit wheel' }
$InstallArgs = @(
  '-I', "$Release\install-kit.py",
  '--wheel', $Wheel.FullName,
  '--sha256-file', "$Release\SHA256SUMS",
  '--workspace', 'C:\Customers\Acme',
  '--install-dir', 'C:\Kit runtimes\1.3.0-acme',
  '--plugin-dir', 'C:\Kit plugins\1.3.0-acme'
)
& $Python @InstallArgs
# Read the wheel identity, exact paths, planned commands and host instructions.
& $Python @InstallArgs --apply
if ($LASTEXITCODE -ne 0) { throw 'Installation failed; do not enable its plugin' }
```

Use the actual release version in the new directory names. The default invocation
is read-only; only `--apply` creates a dedicated venv, installs the wheel and its
dependencies, checks dependency compatibility and installed provenance/assets,
and exports a plugin for the **exact** customer directory. Commands use argument
arrays without shell parsing, including paths with spaces. Python/pip environment
overrides and ambient pip configuration are not inherited.

### Certificate trust and safe network failures

TLS hostname/certificate verification remains enabled. The bootstrap uses pip's
OS truststore (default on pip >=24.2; explicitly enabled on older supported
bundled pip), without upgrading pip or changing OS/global trust. It ignores
ambient pip/index and certificate-override variables; proxies can still be used
without logging their credentials. No `--trusted-host`, disabled verification,
or automatic certificate acquisition is supported.
This follows [pip's documented system-certificate support](https://pip.pypa.io/en/stable/topics/https-certificates/).

If your organization requires an additional CA bundle, obtain an approved
**public-certificate-only PEM** file from its administrator and explicitly add
`--ca-bundle 'C:\Approved certificates\corporate-ca.pem'` to both preview and apply.
The file is validated, hash-recorded, and copied into the new runtime before use
through pip's `--cert`. Private keys, invalid PEM and symlink/junction paths are
refused. This changes trust only for that explicit installation process, not the
OS trust store. A CA bundle cannot fix every network or TLS handshake failure.

Failure output contains a safe `category` such as `tls-handshake` or
`tls-certificate-verification`, not raw pip stdout/stderr or authenticated index/
proxy URLs. For a handshake alert, check approved proxy/firewall/CDN access; for
a chain-verification error, check OS trust or the explicitly approved CA bundle.
Do not disable TLS to complete acceptance.

The 1.2.0 acceptance environment could reach PyPI metadata but encountered CDN
TLS handshake failures. Its successful colleague installation used a separately
verified official offline wheelhouse, not a TLS bypass. This is not evidence
that every organization's online install will succeed or require a CA override.

### Official offline wheelhouse

For a supplier-prepared dependency wheelhouse, add these flags to **both**
invocations:

```text
--wheelhouse <absolute-directory-containing-compatible-dependency-wheels> --offline
```

`--offline` refuses a missing wheelhouse and adds `--no-index`; it does not
download dependencies. Only local `.whl` files are copied into a hash-checked
installation snapshot; HTML links are ignored and direct-URL dependency metadata
is refused. The receipt records the dependency-wheel hashes. A normal online
install resolves compatible dependencies at installation time. Its receipt
records their exact installed versions; it
does not promise a locked dependency set across future online installations.
Use the same verified complete wheelhouse for repeatable offline dependencies.
The release's `SHA256SUMS` covers the supplied kit artifacts, not an independently
obtained dependency wheelhouse; the supplier must verify that wheelhouse too.
Use original distribution wheels obtained over verified HTTPS on an approved
network, or an authenticated official wheelhouse supplied separately. The supplier
should prepare it for the same OS/Python/architecture using approved download
tooling, public PyPI with TLS verification, and no inherited authenticated index
configuration in shared diagnostics. This is a separate supply step, not an
arbitrary-URL bootstrap downloader. Verify the original wheels against the
trusted source's SHA-256 metadata. Repacked installed dependencies used by synthetic
tests are **not** official/distributable wheelhouse artifacts and do not establish
normal-distribution installation acceptance.

Preview/result metadata includes `dependencyPolicy` (index URL or null,
`pipNoIndex`, original wheelhouse path, binary-only policy, wheel count and
snapshot behavior), per-file `dependencyWheels` SHA-256 values, and
`certificateTrust` (verification required, OS truststore, bundled pip version
and any explicitly selected CA path/hash). These local receipts contain no
inherited authenticated repository URLs or private keys.
`localWheelhousePublisherVerified: false` makes the remaining trust boundary
explicit: local hashes bind the preview to the installed bytes, not the supplier's
identity. Publisher authenticity must be established before handing over the
wheelhouse.

### Success and failure

Success prints JSON with `status: installed`, the verified runtime and exact host
instructions, and writes `<runtime>\installation.json`. That receipt is
**machine-local**: it contains absolute runtime/customer paths. Do not include it,
the venv, or the generated plugin in a portable colleague release.

Failure exits nonzero and does not write a completed receipt, enable a plugin,
or delete user directories. Newly created incomplete paths remain for diagnosis;
`INSTALLATION-INCOMPLETE` marks a runtime that did not finish. Do not enable
incomplete exports. Fix the cause (for example missing Python `venv`, a corrupt
wheel, or an incomplete dependency wheelhouse), then retry with **fresh** output
paths. Review and remove only your own abandoned installation artifacts manually.
Customer data and previous runtimes/plugins remain unchanged.

Paths traversing symlinks/junctions, `..`, existing outputs (even empty ones),
missing parents, or overlapping customer/runtime/plugin directories are refused.
Use output parents writable only by trusted operators. These checks are not an
OS sandbox against another process concurrently replacing directories.

## 3. Review and load one host manually

The installer does **not** edit global VS Code/Copilot settings or run
`copilot plugin install`. Inspect the generated `connection.json` and `.mcp.json`.
They must name the verified installation's absolute Python executable and exact
customer path. Choose **one** of the printed methods:

- **Copilot CLI:** run the printed `copilot` executable and argument array as a
  normal operator command; then check `copilot plugin list`. Hosts that removed
  direct-path installation require another supported host method.
- **VS Code:** merge the exact printed `host.vscodeSettings` entry into the
  intended settings, retaining unrelated entries. Do not replace the whole
  `chat.pluginLocations` object if it already contains other plugins.

Enabling a plugin may start its MCP server and imply server trust. Do not also
enable a standalone MCP connection for the same customer. Restart/reload the
host as needed. See [Copilot plugin usage](copilot-plugin.md) for the agent/skill
identifiers and the first offline scenario.

If the host shows **Sign in to use GitHub Copilot**, sign in yourself before
attempting host activation/acceptance. The bootstrap never performs login,
submits authentication prompts, or activates the plugin. After private sign-in
and explicit host activation, the 1.2.0 candidate completed a real Windows
VS Code offline scenario on September 17, 2026. See the
[observed host result](e2e-testing.md#observed-vs-code-offline-e2e-2026-09-17);
sign-in is an operator prerequisite, not a current unresolved test blocker.

The bootstrap's Windows installation path is tested without a host. CLI local
loader/model evidence and its limits are recorded in
[E2E testing](e2e-testing.md#copilot-plugin-acceptance); installation success alone
is not proof of VS Code loading, model completion, or an Azure deployment.

## Updating, rollback and switching customers

Run the **new release's bootstrap** with a new runtime path and new plugin path,
keeping `--workspace` unchanged. This is a side-by-side upgrade, never an in-place
`pip install --upgrade`. Installation does not migrate or edit customer files.

1. Keep the old runtime, export, and customer backup.
2. Preview and install the new release; check its successful receipt.
3. Disable the old host connection before manually enabling/reloading the new
   bundle. Copilot CLI caches plugin contents.
4. If needed, disable the new bundle and re-enable the old one while both runtimes
   remain available. This restores the connection, not later customer-data edits.

Never move an installed runtime: venvs and plugin connections are path-bound.
Never copy another colleague's generated plugin. To change only the customer root,
use the existing verified runtime's `plugin-export` command with a new export path
and the new explicit `--workspace`, or install another side-by-side runtime.
The prototype has one plugin/server name; use only one active bundle per selected
data root, which may contain multiple clients.

## POSIX scope

The bootstrap has POSIX path handling and selects `<runtime>/bin/python`. Use an
absolute, explicitly chosen Python executable and normal quoted argument arrays:

```sh
"/opt/python/3.12/bin/python3" -I "/opt/releases/1.3.0/install-kit.py" \
  --wheel "/opt/releases/1.3.0/mcp_openapi_creator_kit-1.3.0-py3-none-any.whl" \
  --sha256-file "/opt/releases/1.3.0/SHA256SUMS" \
  --workspace "/srv/customer-data/acme" \
  --install-dir "/opt/kit-runtimes/1.3.0-acme" \
  --plugin-dir "/opt/kit-plugins/1.3.0-acme"
```

Replace paths/version and repeat with `--apply` only after preview. The current
validation environment is Windows; this example is not a claim of POSIX runtime
or host acceptance. Symlinked output/customer ancestors are deliberately refused
on either platform; choose canonical, nonlinked data/output paths.

## Maintainer: prepare a local release

Use the explicitly installed development/build venv; missing build dependencies
are an actionable error, not an automatic global install. `MANIFEST.in` must
include `install-kit.py` so the source archive can reproduce the release.

```powershell
# C:\Kit artifacts already exists; the versioned output below must not exist.
& .\.venv\Scripts\python.exe -I tools\build-release.py --output 'C:\Kit artifacts\1.3.0'
& .\.venv\Scripts\python.exe -I tools\build-release.py --output 'C:\Kit artifacts\1.3.0' --apply
```

The builder snapshots only package/maintained asset inputs, excluding local
credentials, `.azure`, venvs, generated assets and private customer directories.
It builds from that snapshot with the current interpreter and `--no-isolation`
(no automatic build dependency download), leaving the original checkout intact.
It writes exactly one wheel and source archive, the bootstrap, checksums,
`INSTALL.txt`, and `provenance.json`. Built-in sample data remains deliberately
packaged; do not put private customer data into maintained kit/sample sources.

Provenance records relative source paths and hashes, build-tool versions, and the
wheel asset identity—not a local username, source directory, Azure IDs or git
credentials. Wheel timestamps and source-archive metadata use a fixed epoch.
Source-content identity is independent of the absolute checkout path; rebuilding
with different build tools may still change artifacts. These are unsigned local
artifacts: no commit, tag, upload, publication, host load or cloud operation occurs.
Review source changes, run the package/offline acceptance tests, and distribute
only the six completed top-level release files. A failed build retains a
`RELEASE-INCOMPLETE` marker and must not be handed off.

Targeted validation (choose a new scratch base under the repository):

```powershell
& .\.venv\Scripts\python.exe -m pytest tools\tests\test_installer.py tools\tests\test_build_release.py --basetemp .test-artifacts\installer
```

Create `.test-artifacts` explicitly first. Real offline install/side-by-side
upgrade coverage runs when `MCP_KIT_INSTALLER_WHEEL` and
`MCP_KIT_INSTALLER_WHEELHOUSE` name an explicitly supplied product wheel and a
complete dependency wheelhouse. The integrated candidate must also pass
`provision --dry-run` from that exact installed runtime and customer root; the
probe blocks network, subprocesses and filesystem writes with Python audit hooks.
Without those environment variables the integration case is explicitly skipped;
missing provisioning support in a supplied candidate fails the test. Synthetic
safety tests are not installation acceptance evidence. This dry-run does not establish live
provision/deploy/update/retire acceptance or grant permission for those operations.

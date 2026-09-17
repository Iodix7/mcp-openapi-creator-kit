# Public release checklist

The repository may remain private while these gates are prepared. Do not change
visibility until every required item is complete.

## Legal and ownership

- [ ] Confirm the author has the right to publish every file under the MIT license.
- [ ] Complete any employer or organization open-source approval required for the
  author.
- [ ] Confirm the neutral sample contains fictional data only.
- [ ] Confirm the Git history contains only the clean public snapshot.

## Release validation

- [ ] Python 3.12 compilation and full test suite pass.
- [ ] `pip-audit -r tools/requirements-dev.txt` reports no known vulnerabilities.
- [ ] All generators pass twice with identical SHA-256 output.
- [ ] All three deployment profiles validate.
- [ ] `az bicep build --file infra/main.bicep` passes.
- [ ] No generated artifacts are tracked.
- [ ] `python tools/check-publication.py` passes secret-pattern, private-path,
  branding, generated-file, and broken-link scans.
- [ ] The latest GitHub Actions CI run succeeds.

## GitHub settings after making the repository public

GitHub Free may not expose these controls while a repository is private. Enable
them immediately after changing visibility:

- [ ] GitHub private vulnerability reporting.
- [ ] Secret scanning and push protection.
- [ ] CodeQL default setup for Python.
- [ ] Protect `main`: require the `validate` check, one approving review,
  resolved conversations, linear history, and no force pushes or deletions.
- [ ] Verify Dependabot alerts and security updates remain enabled.
- [ ] Verify Issues and Discussions are enabled and Wiki is disabled.

## Release and communication

- [ ] Verify repository description and topics.
- [ ] Confirm the intended version and candidate checksums; for the new 1.3.0
  candidate, use a prerelease designation and state its observed scope explicitly.
  The 1.2.0 actual-host result remains evidence for that older candidate only.
- [ ] Review the complete intended Git snapshot, including new untracked source
  and documentation. Keep machine-local receipts, runtime/plugin exports,
  customer data, and tokenized dashboard URLs out of Git and release assets.
- [ ] Obtain explicit authorization before committing, pushing, tagging,
  publishing a release, or changing repository visibility.
- [ ] Publish the approved tag and GitHub prerelease with the exact verified
  package artifacts and checksums. Do not silently rebuild or overwrite a
  previously tested candidate to incorporate later documentation changes.
- [ ] Attach any later actual-host acceptance as a separate, portable supplement,
  removing machine-local paths and keeping interrupted attempts and limitations
  visible. Distinguish current repository documentation from frozen package docs.
- [ ] Verify the README disclaimer, support policy, security policy, and license
  are visible.
- [ ] Have a colleague use the published installation instructions on a separate
  machine: supplied release, local runtime/export, VS Code activation, first
  offline scenario, and dashboard. A kit clone must not be required.
- [ ] Verify the source-development clean-clone path separately if publishing it
  as a contributor workflow.
- [ ] Recheck every link from an anonymous browser session.

Record the completion date and approver in the release notes rather than adding
personal or organization-internal metadata to source files.

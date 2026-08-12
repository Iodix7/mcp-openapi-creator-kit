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
- [ ] Publish or confirm the signed/annotated `v1.0.2` tag and GitHub release.
- [ ] Verify the README disclaimer, support policy, security policy, and license
  are visible.
- [ ] Run a clean-clone onboarding test from the public URL.
- [ ] Recheck every link from an anonymous browser session.

Record the completion date and approver in the release notes rather than adding
personal or organization-internal metadata to source files.

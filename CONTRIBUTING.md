# Contributing

## Branch-first changes

Work from a clean, up-to-date `main` in a feature branch or isolated worktree.
Never edit a running deployment or use a production database as a test fixture.
Preserve unrelated work, private configuration, existing release tags and the
intentional public examples in `config/system_prompts.example/`.

```bash
git fetch origin
git switch -c change/descriptive-name origin/main
```

Start a behavior change with a focused failing regression test. Implement the
smallest complete change, inspect the diff, and run the complete gates before
opening a pull request. Describe the behavior, tradeoffs, verification commands
and actual results. Distinguish author self-review from independent review. Do
not bypass required checks, suppress a finding merely to make CI green, or add
compatibility facades for retired preproduction APIs.

## Isolated development environment

The protected CI target is Python 3.11 on Linux. Integration tests also use Git
and `ssh-keygen`; their Git repositories and SSH keys are disposable local
fixtures, not maintainer trust material. Use a separate environment:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip check
```

`requirements.txt` is the runtime input manifest. `requirements.lock` is its
hash-pinned resolved output. `requirements-dev.txt` lists exact tooling pins,
including the compiler and vulnerability auditor. `requirements-dev.lock` is the
complete runtime-plus-development environment, resolved against the exact runtime
lock. Test, static-analysis and audit jobs install this hashed lock; they do not
resolve an unpinned tool dependency tree during CI.
Do not update a running bridge's Python environment as part of a test run.
External API calls must be mocked; local HTTP fixtures must bind loopback. Tests
must not require real bot tokens, provider accounts or user data.

An autouse fixture rejects non-loopback Python socket connections during each
test and records blocked attempts even when application code catches the error.
It allows numeric loopback and local Unix sockets for fixtures. This is not an
operating-system sandbox: subprocesses and DNS lookups are outside that guard,
so test authors must also avoid external commands or lookups using real services.

## Required verification

Run from the repository root with the development environment active:

```bash
python tools/check_dependency_lock.py
python -m ruff check .
python -m ruff format --check .
python tools/static_analysis.py
python tools/static_analysis.py --print-type-targets | xargs python -m mypy
python -m pytest -q -n 2 --dist=loadfile --cov \
  --cov-report=term-missing:skip-covered \
  --cov-report=json:coverage.json --cov-report=xml:coverage.xml
git diff --check
```

The configured 68% combined statement/branch coverage floor covers all `bridge/`
modules and subprocess workers. It is a measured regression guard, not exhaustive
security coverage. Do not remove production files from coverage or lower the
floor to hide regressions. Inspect worker-thread failures and test warnings.
CI uploads JSON/XML coverage reports for 14 days.

Before merging, require `test`, `dependency-audit`, `static-analysis`,
`Analyze (actions)`, `Analyze (python)`, and the CodeQL result for the exact reviewed
head commit. The repository uses GitHub CodeQL Default Setup for Python and GitHub
Actions; no checked-in CodeQL workflow is required. Do not add a second local
CodeQL workflow. A passing analysis job alone does not prove that
its security-result check passed. Do not force a merge if a head changes during
review or checks.

## Dependency maintenance

Dependabot is configured for weekly `pip` and `github-actions` checks. It proposes
pull requests; this repository does not configure automatic merging. Development
minor/patch updates are grouped to reduce review noise. Open version-update
requests are bounded, without disabling security updates.

GitHub's documented pip support covers `.txt` manifests. **Do not assume** a
Dependabot change regenerates the **custom requirements.lock and requirements-dev.lock files** used here.
For a runtime manifest change or a selected package upgrade, regenerate the lock
with [uv's requirements compiler](https://docs.astral.sh/uv/pip/compile/) in an
isolated checkout, review all resolved version and hash changes, and include the
necessary input and lock changes in the same pull request:

```bash
uv pip compile requirements.txt --python-version 3.11 --generate-hashes \
  --output-file requirements.lock
uv pip compile requirements.txt requirements-dev.txt --constraint requirements.lock \
  --python-version 3.11 --generate-hashes --output-file requirements-dev.lock
```

For an intentional targeted upgrade, add `--upgrade-package PACKAGE` using the
actual dependency name. Use `--upgrade` only when a broad dependency refresh is
intended and reviewed. Do not remove hashes, use unreviewed override constraints,
or silently replace public indexes with private credential-bearing URLs.

`python tools/check_dependency_lock.py` performs offline checks for exact runtime
pins, SHA-256 metadata, active direct-requirement compatibility, and exact
development pins, hashed development records and exact runtime-version agreement
inside the combined development lock. It does **not** resolve transitive dependencies, authenticate
packages, verify downloaded artifact bytes, or query vulnerability advisories.
Hash-enforced installation, `python -m pip check`, the full tests, and the existing
`pip-audit` CI job remain separate required evidence. The runtime lock is the
installation authority; a manifest-only update is not evidence of deployment.
CI audits both lockfiles using `--require-hashes --disable-pip`: every listed
package is checked against advisories without invoking a second resolver. Hash
verification happens at installation, while `pip check` checks the resulting
metadata. Audit tools do not prove a dependency is free of unknown vulnerabilities.

Review GitHub Actions updates as executable dependency changes. Every declared
external action is pinned to a full commit SHA resolved from its official upstream
repository; the trailing version comment is for review, not the executed identity.
`setup-uv` also receives the explicit compiler version from the development input.
When changing the uv pin, update its workflow version and regenerate the combined
lock in the same PR. Keep workflow permissions minimal. Do not grant Dependabot arbitrary external code execution
or add a privileged workflow to auto-push unreviewed lockfile changes.

References: [Dependabot options](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)
and [supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).

## Architecture and private data

`AppSettings` is an immutable per-application snapshot. Pass it or an explicit
narrow collaborator; do not reintroduce environment-derived module globals,
ambient context stores or a root service locator in leaf handlers. Pure port and
request-value dependencies have an explicit allowlist. Low-level topic, logging,
scheduler and SQLite-mechanics owners must not import application adapters.

Keep one canonical owner for each mutable executor, lock and connection gate.
Preserve queue scope, actor/session identity, transaction ownership and documented
limits during refactors. Source-shape tests may be updated for a genuine owner
move, but retain their behavioral assertions and add regression coverage for
new boundaries.

`tools/public_examples.json` records the reviewed public-example hashes. Routine
cleanup must not edit or remove those files. An intentional example change needs
explicit maintainer review and a matching manifest update; checksums are a
regression guard, not an independent cryptographic trust anchor.

Do not commit private `.env` files, databases, logs, native content or signing keys.
Use [SECURITY.md](SECURITY.md) for vulnerability reporting. Deployment and release
signing are separate operator actions; merging a PR does not deploy it.


### SQLite write ownership

Wrap application write operations in `with write_transaction(db):`. Repository
writers reject a connection without an active transaction **before executing
SQL**; they do not open or commit transactions themselves. This preserves atomic
multi-operation changes, including rollback when the enclosing use case fails.
Nested transaction scopes remain the original caller's responsibility.

The serialized connection retains a single writer-lock acquisition until its
transaction ends. It also tracks potentially-writing result cursors, including
`INSERT/UPDATE/DELETE ... RETURNING` in autocommit mode. Consume their results or
close the cursor explicitly; do not leave a cursor open while awaiting network
I/O. Use the connection's standard cursor factory and its `execute`,
`executemany`, `executescript`, commit/rollback, or connection context-manager APIs.
Do not call the base `sqlite3.Connection` methods to bypass these wrappers.

Leading SQL comments do not bypass classification. `WITH`, `PRAGMA`, transaction
control, and unknown statement forms are conservatively serialized; plain
`SELECT`, `VALUES`, and `EXPLAIN` are not classified as writes. Consequently a
read-only CTE/PRAGMA cursor may also hold the gate until consumed or closed. This
is an in-process serialization policy, not a SQL parser or sandbox for arbitrary
native-extension/UDF side effects. SQLite's own file locking still governs other
processes. Keep connections on their owning thread and make commit/rollback/close
explicit at the owning boundary.

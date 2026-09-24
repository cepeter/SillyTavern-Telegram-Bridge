# Static Architecture Enforcement Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `9c5c52ba0515ef2eaf7912a09949be3c13633fae`

## Purpose

Turn the completed architecture migration into enforceable CI policy.

Current state:

- runtime compatibility loading is retired;
- import graph is fully acyclic;
- service/port boundaries are explicit;
- route_update decomposition is merged;
- full regression suite and dependency audit are required in CI;
- no Ruff, static type, or dependency-direction gate exists yet.

The roadmap's final checkpoint requires:

- incremental Ruff;
- service/port static typing;
- dependency-direction CI checks;
- branch protection requiring CI.

This PR introduces the repository-side enforcement. Branch protection is updated only
after the new CI job is merged and proven green.

## Incremental static surface

Do not enable repo-wide Ruff/mypy yet.

Repo-wide conservative Ruff correctness rules currently report ~458 findings,
primarily the legacy explicit-late-import style (`E402`). Converting the entire
repository in one PR would mix architecture enforcement with broad mechanical
formatting/import movement.

The first enforced static surface is the stabilized application service/port layer:

```text
bridge/conversation_service.py
bridge/delivery_port.py
bridge/group_director_service.py
bridge/group_service.py
bridge/input_flow_service.py
bridge/job_service.py
bridge/memory_service.py
bridge/model_router.py
bridge/persona_service.py
bridge/provider_port.py
bridge/sync_service.py
```

These modules currently have zero `bridge.*` imports and already form the cleanest
application boundary.

## Tooling

Pin development tooling:

- `ruff==0.16.8`
- `mypy==2.3.1`

Add them to `requirements-dev.txt`.

Add `pyproject.toml` configuration.

### Ruff

Target Python 3.11.

Initial lint rule set:

- `E4` imports
- `E7` statements
- `E9` runtime/syntax errors
- `F` Pyflakes correctness

Run Ruff only against the explicit static surface in this phase.

No auto-fix is run in CI.

### Mypy

Target Python 3.11.

For the explicit service/port surface:

- ignore missing third-party stubs;
- check untyped function bodies;
- disallow untyped function definitions;
- disallow implicit Optional;
- warn on unused ignores.

Do not require repo-wide strict mode yet.

## Type cleanup

The current service/port surface has 13 mypy findings in five files.

Allowed fixes are annotation/narrowing only; no workflow behavior changes.

### ProviderPort

Annotate the optional stream callback and cancellation event.

The callback contract should reflect the current generation path and cancellation
event semantics.

### InputFlowService

Annotate variadic forwarding methods explicitly. The service remains a generic
delegation boundary and does not grow adapter dependencies.

### SyncService

Narrow/cast the persisted `last_synced_at` value before `float()` without
changing runtime coercion semantics.

### JobService

Annotate:

- DB connection parameters;
- failure error object;
- recovery resolver callable.

Preserve all scheduling/recovery behavior.

### GroupDirectorService

Narrow/cast known GroupState values used as:

- iterable member lists;
- integer turn index.

Do not change fallback or speaker-selection behavior.

## Dependency-direction checker

Add a standalone AST-based checker under `tools/`.

It must:

1. discover ordinary `bridge/*.py` modules;
2. include imports found anywhere in the AST, including function-local and
   TYPE_CHECKING imports;
3. build only internal `bridge.*` dependency edges;
4. ignore self-edges;
5. detect strongly connected components;
6. fail when any SCC has more than one module;
7. report reciprocal pairs for diagnostics;
8. enforce that every module in the static service/port surface imports no
   `bridge.*` modules;
9. fail if an expected static-surface file is missing.

Output should include at least:

- module count;
- internal edge count;
- cyclic module count;
- reciprocal pair count;
- any violating component/import.

This checker is a repository artifact, not the temporary Hermes scanner.

## Checker tests

Add focused tests proving:

- the real repository passes;
- a synthetic two-module cycle fails;
- a synthetic isolated-core module importing another bridge module fails;
- function-local imports participate in cycle detection;
- missing expected static target fails.

The checker implementation itself should be ordinary Python with no external
dependencies.

## CI

Add a `static-analysis` job to `.github/workflows/ci.yml`.

Steps:

1. checkout;
2. Python 3.11;
3. install uv;
4. install development tooling from `requirements-dev.txt`;
5. run dependency-direction checker;
6. run Ruff against the static target list;
7. run mypy against the same target list.

The static job does not need runtime dependencies because:

- Ruff is source-only;
- mypy ignores missing third-party stubs;
- dependency checking is AST-only.

Existing `test` and `dependency-audit` jobs remain unchanged.

## Branch protection

After this PR is merged and the merged-main `static-analysis` job is confirmed
green, update branch protection for `main` to require:

- `test`
- `dependency-audit`
- `static-analysis`

Preserve existing branch-protection settings and restrictions. Do not weaken any
existing review/admin/restriction policy.

If the repository currently has no branch protection, create the minimum protection
needed to require these CI checks while preserving normal PR-based development.

## Dependency direction policy

The initial enforced layer rule is intentionally strong and narrow:

> Stabilized service/port modules may not import any `bridge.*` module.

This prevents application services/ports from regressing back toward Telegram/UI,
infrastructure, composition, or module-global discovery.

The whole-repository cycle check separately guarantees the repository remains
acyclic even outside that surface.

Future PRs may expand the isolated static surface as more modules are normalized.

## Non-goals

- no repo-wide Ruff cleanup;
- no repo-wide mypy strict mode;
- no formatting rewrite;
- no late-import migration outside the static target surface;
- no behavior changes to services;
- no new runtime dependency;
- no streaming continuation fix.

## Acceptance

1. Ruff/mypy are pinned in development requirements.
2. the explicit static surface is Ruff-clean.
3. mypy passes the explicit static surface with untyped-defs disallowed.
4. dependency checker passes current main architecture and detects synthetic
   violations.
5. CI gains a green `static-analysis` job without changing test/audit semantics.
6. full regression suite remains green.
7. import graph remains fully acyclic.
8. after merge, branch protection requires all three CI jobs without weakening
   existing protection.
9. merged main is verified after protection update.

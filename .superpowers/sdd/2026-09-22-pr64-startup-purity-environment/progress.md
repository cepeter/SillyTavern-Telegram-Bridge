# SDD ledger — plan: docs/superpowers/plans/2026-09-22-pr64-startup-purity-environment.md

Execution: Native via connected GitHub branch workspace.
Ruling: No local/native worktree is available for this connected repository, so the feature branch is the isolated workspace and this temporary branch-only ledger records task state. It will be deleted before final review. Cost if wrong: scratch history appears transiently in the draft PR, but upstream main remains untouched.

Pre-flight: Task 1 produces bridge.environment bootstrap consumed by Tasks 2 and 5 — interfaces match spec.
Pre-flight: Task 2 produces canonical runtime path ownership consumed by Tasks 3 and 6 — private common aliases preserve ownership while avoiding compatibility re-exports.
Pre-flight: Task 3 produces configure_logging consumed by Task 5 startup ordering — interfaces match spec.
Pre-flight: Task 4 produces lazy executor lifecycle consumed by Task 6 import-purity verification — interfaces match spec.
Pre-flight: Task 5 removes late environment loading and tightens config validation consumed by Task 6 residue guards — interfaces match spec.

Task 5: Ruling: Branch already contains the approved production deletion of load_env_file from a parallel execution; CI RED is stale tests patching the deleted symbol. Migrate tests to the native startup contract rather than reintroducing a compatibility alias. Cost if wrong: tests could stop checking an intended startup seam, so focused startup tests and full CI must verify behavior.

Tasks 1-5: branch implementation validated after stale-test migration by CI #1090 at 75a8711 — full workflow success. Parallel execution already contained RED/GREEN commit pairs for environment bootstrap, path/prompt retirement, explicit logging, lazy executors, and startup validation; current branch state conforms to approved interfaces.

Task 6: Ruling: The planned source-substring guard for executor construction falsely matched lazy creation inside _executor_for(). Replace it with an AST guard over module-level statements only; behavioral subprocess tests remain primary evidence. Cost if wrong: an import-time side effect could escape the source guard, mitigated by test_startup_purity importing bridge.common in isolation.

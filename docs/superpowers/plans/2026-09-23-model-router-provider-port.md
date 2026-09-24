# ModelRouter and Provider Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Extract model routing and provider HTTP transport behind required pure ports while preserving all current generation behavior.

**Architecture:** Add pure ModelRouter and ProviderPort, a provider-catalog adapter, and a provider-transport adapter. Compose them once at startup, then migrate conversation, image/edit, summary, scene, memory-curator, Group Director, extension and startup paths to explicit ProviderPort injection.

**Tech Stack:** Python 3.11+, dataclasses, urllib, PyYAML, sqlite3, pytest/pytest-xdist, AST architecture guards.

**Spec:** `docs/superpowers/specs/2026-09-23-model-router-provider-port-design.md`

## Global Constraints

- Base is merged `main` at `bb6d092f9f58b15d08d2f87a506260f29fdeea18`.
- Preserve all provider selection/credential/HTTP/streaming/recovery/continuation behavior.
- Preserve the known streaming continuation limitation; do not silently fix it.
- No provider singleton, service locator, optional provider fallback, compatibility re-export or dynamic import.
- No Telegram delivery extraction, route-update decomposition, broad transaction cleanup or image-generation rewrite.
- Re-scan actual merged main before the next delivery/Telegram cut.

## Review Focus

1. Provider-specific credential env keys must still override caller API key with the same fallback/error semantics.
2. Streaming and non-stream continuation/recovery must preserve current call counts, accumulated text and failure fallback.
3. Scene State and Memory Curator background work must carry the exact composed ProviderPort rather than resolving a global.
4. Group Director, summary, image/edit, regeneration/continuation and start-readiness paths must use the injected port.
5. Unqualified/slash-qualified model routing and unreadable provider catalog fallback must remain behavior-compatible.

---

### Task 1: Pure ModelRouter, ProviderPort, provider catalog, and required composition

**Files:** create `model_router.py`, `provider_port.py`, `provider_catalog.py`;
modify composition/main/test setup; create `tests/test_model_router_provider_port.py`.

**Interfaces:** ModelRoute, ModelRouter.route/provider_spec; ProviderPort.generate;
required BridgeServices.model_router/provider.

- [x] **Step 1:** Write failing tests for pure imports, routing behavior, catalog
  failure fallback, ProviderPort delegation, and required composition fields.
- [x] **Step 2:** Run focused RED; expect missing modules/fields.
- [x] **Step 3:** Implement pure router/port, catalog adapter, and startup objects.
  Temporarily point ProviderPort backend at the existing generation function only
  inside composition until Task 2 moves transport; do not create production fallback.
- [x] **Step 4:** Run focused GREEN plus composition/config tests.
- [x] **Step 5:** Commit `refactor: add model router and provider port`.
### Task 2: Extract provider HTTP transport from generation and media

**Files:** create `provider_transport.py`; modify generation/media/catalog/main;
migrate provider-focused tests.

**Interfaces:** `generate_provider_text(model_router, api_key, model, messages, ...)`;
canonical `opencode_muse_headers` lives in provider_transport.

- [x] **Step 1:** Add architecture tests that generation no longer owns router/transport
  helpers, media no longer owns provider specs, and provider_transport has no
  generation/media/Telegram imports.
- [x] **Step 2:** Add/retarget RED behavior tests for routing, credentials,
  OpenAI-compatible streaming/non-streaming, Anthropic and OpenCode to canonical owners.
- [x] **Step 3:** Move transport code verbatim where possible; recursive generation
  calls stay inside provider_transport with the same router.
- [x] **Step 4:** Update startup ProviderPort backend, catalog OpenCode import, and
  startup credential validation to ModelRouter.
- [x] **Step 5:** Run provider/generation continuation/OpenCode/audit/config focused suites.
- [x] **Step 6:** Commit `refactor: extract model provider transport`.

---

### Task 3: Migrate conversation, image/edit, regeneration, and command paths

**Files:** modify generation, conversation_service, message_commands, commands,
worker_orchestration, input_flows, command_routes and affected tests.

**Interfaces:** provider_port is a required explicit parameter wherever an application
function can cause generation; ConversationService forwards `services.provider`.

- [x] **Step 1:** Write failing forwarding tests for ordinary generation,
  response-language rendering, image generation, edit regeneration, /regen,
  /continue, start readiness, durable image/edit workers.
- [x] **Step 2:** Run RED; expect missing provider parameter/direct generate_text usage.
- [x] **Step 3:** Replace application generation calls with ProviderPort.generate and
  propagate the required port through callers; remove production imports of provider
  transport from these application modules.
- [x] **Step 4:** Run conversation/operation-recovery/image/edit/command/generation suites.
- [x] **Step 5:** Commit `refactor: route application generation through provider port`.


---

### Task 4: Migrate summary, memory-curator, scene-state, Group Director, and extension paths

**Files:** modify memory.py, memory_service.py, memory_curator.py, scene_state.py,
status_panels.py, groups.py, extension_registry.py, group_director_service.py,
command_routes.py, main.py, test setup and affected focused tests.

**Interfaces:** summary/scene/curator/director generation receives required
ProviderPort; post-retain and command-route extension dispatch forwards explicit
services/provider rather than resolving globals.

- [x] **Step 1:** Write failing provider-forwarding tests for summary, forced summary,
  Scene State refresh/background worker, Memory Curator refresh/background worker,
  Group Director and extension command/post-retain hooks.
- [x] **Step 2:** Run focused RED; expect direct generate_text imports and missing
  provider/services arguments.
- [x] **Step 3:** Propagate ProviderPort through MemoryService summary callbacks,
  post-retain hook contract/background jobs, command extension dispatch and feature
  panels; GroupDirector uses the composed provider.
- [x] **Step 4:** Remove direct generation imports from memory.py, memory_curator.py,
  scene_state.py and GroupDirector composition path.
- [x] **Step 5:** Run memory/scene/director/extension/status focused suites.
- [x] **Step 6:** Commit `refactor: inject provider port into utility generation`.

---

### Task 5: Architecture verification, PR, merge, and post-merge re-scan

**Files:** update this plan verification record; architecture guards only if needed.

- [x] **Step 1:** AST guards:
  - ModelRouter and ProviderPort import no bridge modules.
  - provider_transport imports neither generation, media nor Telegram.
  - generation defines no provider routing/HTTP adapter functions.
  - media defines no provider-spec lookup.
  - no production provider singleton/optional provider fallback.
- [x] **Step 2:** Fresh-process import checks for router, port, transport, generation,
  memory/scene/director, composition and main.
- [x] **Step 3:** Run graph measurement, compileall, `git diff --check`, and full
  `pytest -q -n 2 --dist=loadfile`.
- [x] **Step 4:** Whole-branch self-review against the five Review Focus conditions;
  one TDD fix pass for any Critical/Important finding.
- [ ] **Step 5:** Publish exact head, require GitHub `test` and
  `dependency-audit` success, then merge.
- [ ] **Step 6:** Fetch actual merged main, rerun full verification, re-scan graph,
  and choose the next Telegram/delivery or other architecture cut from evidence.


## Local Verification Record

- Base: bb6d092f9f58b15d08d2f87a506260f29fdeea18.
- Task 1 router/port/composition RED: 6 failed; GREEN: 49 passed + 30 subtests.
- Task 2 provider transport/canonical-owner focused: 44 passed + 4 subtests.
- Task 3 application generation focused: 83 passed + 23 subtests.
- Task 4 utility/background generation focused: 47 passed + 12 subtests.
- First full migration run exposed 39 stale ownership/signature fixtures plus one
  missing document-image ProviderPort propagation; the production propagation gap
  was fixed and legacy test seams were migrated without compatibility exports.
- Repaired failure cluster: 115 passed + 138 subtests.
- Transitional generation.generate_text delegator was removed after atomic migration;
  provider-port seam migration: 86 passed + 27 subtests.
- Canonical provider behavior after delegator removal: 44 passed + 4 subtests.
- Application import boundary migrated to ModelRouter / ProviderPort /
  provider_transport canonical ownership: 7 passed + 121 subtests.
- Full candidate suite: 797 passed + 479 subtests, exit 0.
- compileall: passed.
- git diff --check: passed.
- Fresh-process imports passed for router, port, transport, generation, application
  generation, memory/scene/director, composition, and main modules.
- Architecture guards passed: ModelRouter/ProviderPort have no bridge imports;
  provider_transport imports no generation/media/Telegram; generation owns no
  provider routing/HTTP function; media owns no provider-spec lookup; no provider
  singleton or optional fallback exists.
- Graph is intentionally neutral before the Telegram-delivery cut: 77 modules,
  435 edges, largest SCC 12, 23 cyclic modules, 16 reciprocal pairs, 32 Telegram
  importers. New router/port/transport modules are outside the cyclic graph.
- Whole-branch author self-review: no remaining Critical, Important, or Minor
  findings against the five Review Focus conditions.
- Deferred unchanged finding: streaming visible-content length continuation still
  does not forward stream/cancellation callbacks and lacks an independent local
  bound; it remains a separate focused provider behavior fix.

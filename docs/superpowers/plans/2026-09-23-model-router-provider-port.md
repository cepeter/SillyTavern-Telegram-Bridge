# ModelRouter and Provider Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

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

- [ ] **Step 1:** Write failing tests for pure imports, routing behavior, catalog
  failure fallback, ProviderPort delegation, and required composition fields.
- [ ] **Step 2:** Run focused RED; expect missing modules/fields.
- [ ] **Step 3:** Implement pure router/port, catalog adapter, and startup objects.
  Temporarily point ProviderPort backend at the existing generation function only
  inside composition until Task 2 moves transport; do not create production fallback.
- [ ] **Step 4:** Run focused GREEN plus composition/config tests.
- [ ] **Step 5:** Commit `refactor: add model router and provider port`.
### Task 2: Extract provider HTTP transport from generation and media

**Files:** create `provider_transport.py`; modify generation/media/catalog/main;
migrate provider-focused tests.

**Interfaces:** `generate_provider_text(model_router, api_key, model, messages, ...)`;
canonical `opencode_muse_headers` lives in provider_transport.

- [ ] **Step 1:** Add architecture tests that generation no longer owns router/transport
  helpers, media no longer owns provider specs, and provider_transport has no
  generation/media/Telegram imports.
- [ ] **Step 2:** Add/retarget RED behavior tests for routing, credentials,
  OpenAI-compatible streaming/non-streaming, Anthropic and OpenCode to canonical owners.
- [ ] **Step 3:** Move transport code verbatim where possible; recursive generation
  calls stay inside provider_transport with the same router.
- [ ] **Step 4:** Update startup ProviderPort backend, catalog OpenCode import, and
  startup credential validation to ModelRouter.
- [ ] **Step 5:** Run provider/generation continuation/OpenCode/audit/config focused suites.
- [ ] **Step 6:** Commit `refactor: extract model provider transport`.

---

### Task 3: Migrate conversation, image/edit, regeneration, and command paths

**Files:** modify generation, conversation_service, message_commands, commands,
worker_orchestration, input_flows, command_routes and affected tests.

**Interfaces:** provider_port is a required explicit parameter wherever an application
function can cause generation; ConversationService forwards `services.provider`.

- [ ] **Step 1:** Write failing forwarding tests for ordinary generation,
  response-language rendering, image generation, edit regeneration, /regen,
  /continue, start readiness, durable image/edit workers.
- [ ] **Step 2:** Run RED; expect missing provider parameter/direct generate_text usage.
- [ ] **Step 3:** Replace application generation calls with ProviderPort.generate and
  propagate the required port through callers; remove production imports of provider
  transport from these application modules.
- [ ] **Step 4:** Run conversation/operation-recovery/image/edit/command/generation suites.
- [ ] **Step 5:** Commit `refactor: route application generation through provider port`.


---

### Task 4: Migrate summary, memory-curator, scene-state, Group Director, and extension paths

**Files:** modify memory.py, memory_service.py, memory_curator.py, scene_state.py,
status_panels.py, groups.py, extension_registry.py, group_director_service.py,
command_routes.py, main.py, test setup and affected focused tests.

**Interfaces:** summary/scene/curator/director generation receives required
ProviderPort; post-retain and command-route extension dispatch forwards explicit
services/provider rather than resolving globals.

- [ ] **Step 1:** Write failing provider-forwarding tests for summary, forced summary,
  Scene State refresh/background worker, Memory Curator refresh/background worker,
  Group Director and extension command/post-retain hooks.
- [ ] **Step 2:** Run focused RED; expect direct generate_text imports and missing
  provider/services arguments.
- [ ] **Step 3:** Propagate ProviderPort through MemoryService summary callbacks,
  post-retain hook contract/background jobs, command extension dispatch and feature
  panels; GroupDirector uses the composed provider.
- [ ] **Step 4:** Remove direct generation imports from memory.py, memory_curator.py,
  scene_state.py and GroupDirector composition path.
- [ ] **Step 5:** Run memory/scene/director/extension/status focused suites.
- [ ] **Step 6:** Commit `refactor: inject provider port into utility generation`.

---

### Task 5: Architecture verification, PR, merge, and post-merge re-scan

**Files:** update this plan verification record; architecture guards only if needed.

- [ ] **Step 1:** AST guards:
  - ModelRouter and ProviderPort import no bridge modules.
  - provider_transport imports neither generation, media nor Telegram.
  - generation defines no provider routing/HTTP adapter functions.
  - media defines no provider-spec lookup.
  - no production provider singleton/optional provider fallback.
- [ ] **Step 2:** Fresh-process import checks for router, port, transport, generation,
  memory/scene/director, composition and main.
- [ ] **Step 3:** Run graph measurement, compileall, `git diff --check`, and full
  `pytest -q -n 2 --dist=loadfile`.
- [ ] **Step 4:** Whole-branch self-review against the five Review Focus conditions;
  one TDD fix pass for any Critical/Important finding.
- [ ] **Step 5:** Publish exact head, require GitHub `test` and
  `dependency-audit` success, then merge.
- [ ] **Step 6:** Fetch actual merged main, rerun full verification, re-scan graph,
  and choose the next Telegram/delivery or other architecture cut from evidence.

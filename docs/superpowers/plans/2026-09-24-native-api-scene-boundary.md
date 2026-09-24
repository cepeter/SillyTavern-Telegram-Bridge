# Native API and Scene Boundary Implementation Plan

### Task 1: TDD native API ownership
- [ ] RED: `sillytavern_api.py` exists and imports no sync/persona/application modules.
- [ ] RED: moved client/error/config symbols are absent from `sync_api.py`.
- [ ] RED: `persona_sync.py` imports neither `sync_api` nor `sync_core`.
- [ ] RED: API URL/credentials/timeout refresh, URL validation, and client behavior are available from the canonical native API module.
- [ ] RED: startup uses canonical API ownership while retaining `sync_api.refresh_phase3_config()` as the refresh entry point.

### Task 2: TDD Scene delivery ownership
- [ ] RED: pure `scene_panel` renders exact text/markup.
- [ ] RED: `scene_state.py` imports no `status_panels`.
- [ ] RED: Scene command requires DeliveryPort and forwards exact DeliveryPort/ProviderPort from the extension route.
- [ ] RED: StatusPanels uses the shared pure scene panel builder.

### Task 3: Implement native API extraction
- [ ] Move API URL/credential/timeout config, client, error, URL validation, client cache, and client construction to `sillytavern_api.py`.
- [ ] Make `sync_api.refresh_phase3_config()` own polling interval refresh and delegate API config refresh.
- [ ] Route Sync API client/error/config use through the canonical module namespace.
- [ ] Route Persona Sync client/error/config use through the canonical module namespace.
- [ ] Replace Persona Sync payload-limit dependency with `config.SYNC_MAX_BYTES`.
- [ ] Migrate main/test setup/tests to canonical ownership.
- [ ] Run focused sync/persona/composition suites.

### Task 4: Implement Scene panel/delivery cut
- [ ] Add pure `scene_panel.py`.
- [ ] Add SceneState command-side menu wrapper using DeliveryPort.
- [ ] Make Scene command UI use required DeliveryPort.
- [ ] Forward `services.delivery` and `services.provider` from extension route.
- [ ] Make StatusPanels reuse pure scene panel rendering.
- [ ] Migrate direct Scene menu tests.
- [ ] Run focused scene/status/extension suites.
- [ ] Commit coordinated implementation.

### Task 5: Verify, publish, merge, re-scan
- [ ] architecture/import guards.
- [ ] graph scan proves largest SCC <=4, cyclic modules <=7, no new cycle.
- [ ] fresh-process imports, compileall, diff-check.
- [ ] full `pytest -q -n 2 --dist=loadfile`.
- [ ] whole-diff review; one TDD fix pass for Critical/Important findings.
- [ ] publish exact head and require GitHub `test` + `dependency-audit`.
- [ ] merge, verify merged main, re-scan before next cut.

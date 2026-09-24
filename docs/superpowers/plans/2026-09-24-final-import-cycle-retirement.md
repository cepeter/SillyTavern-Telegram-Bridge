# Final Import Cycle Retirement Implementation Plan

### Task 1: TDD Persona identity ownership
- [ ] RED: PersonaSync owns get_persona/default_persona_id/persona_name.
- [ ] RED: Cards no longer defines or re-exports those helpers.
- [ ] RED: PersonaSync imports neither Cards nor Telegram.
- [ ] RED: Commands/StatusPanels/SyncCore/startup/Telegram use canonical PersonaSync ownership.
- [ ] Preserve default-Persona warning/fallback behavior.

### Task 2: TDD panel request ownership
- [ ] RED: pure panel_utils.panel_message_request returns exact send/edit method/payload.
- [ ] RED: Telegram imports no Cards.
- [ ] RED: Telegram session-delete panels use local transport plus pure request builder.
- [ ] RED: Cards generic panel delivery uses the same pure builder and preserves panel binding.

### Task 3: Implement final DAG
- [ ] Move persona identity functions to PersonaSync.
- [ ] Remove unused PersonaSync Cards/Telegram imports.
- [ ] Migrate all production/test consumers to PersonaSync.
- [ ] Add pure panel_message_request helper.
- [ ] Remove Telegram Cards import and rewrite its two panel sends.
- [ ] Migrate application-boundary and ownership tests.
- [ ] Run focused Persona/Card/Telegram/Sync/Status/Composition suites.
- [ ] Commit implementation.

### Task 4: Verify, publish, merge, final re-scan
- [ ] architecture/import guards.
- [ ] graph scan proves zero cyclic modules and zero reciprocal pairs.
- [ ] fresh imports, compileall, diff-check.
- [ ] full pytest-xdist.
- [ ] whole-diff review with one TDD fix pass if needed.
- [ ] publish exact head; require GitHub test + dependency-audit.
- [ ] merge, verify merged main, re-scan and record import-cycle retirement complete.

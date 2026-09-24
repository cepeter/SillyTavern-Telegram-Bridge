# World Storage and Curated Memory Boundary Implementation Plan

### Task 1: TDD storage and curated-memory ownership
- [ ] RED: canonical world_storage owner exists, Telegram no longer imports Catalog, Catalog no longer owns world installation.
- [ ] RED: World Info installation preserves validation, atomic write, overwrite rejection, and returned path.
- [ ] RED: pure curated_memory_panel returns exact text/markup.
- [ ] RED: Memory Curator imports no StatusPanels and requires DeliveryPort.
- [ ] RED: extension command route forwards exact ProviderPort and DeliveryPort.

### Task 2: Implement coordinated cuts
- [ ] Move install_world_info_document verbatim into world_storage and migrate Telegram/tests.
- [ ] Remove Catalog ownership/re-export and clean imports only when proven unused.
- [ ] Add pure curated-memory panel builder.
- [ ] Route Memory Curator status/text/typing through required DeliveryPort.
- [ ] Forward services.delivery from extension command route.
- [ ] Make StatusPanels reuse the pure panel builder.
- [ ] Migrate stale direct tests to canonical owners.
- [ ] Run focused world/memory/status/extension suites and commit.

### Task 3: Verify, publish, merge, re-scan
- [ ] architecture + graph guards.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff self-review and one TDD fix pass if needed.
- [ ] publish exact head and require GitHub test + dependency-audit.
- [ ] merge, verify actual main, and re-scan before selecting the next cut.

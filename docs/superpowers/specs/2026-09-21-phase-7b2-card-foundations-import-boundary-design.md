# Phase 7B2 — Card Foundations Ordinary-Import Boundary Design

Date: 2026-09-21  
Baseline upstream `main`: `63fe8d2c3daa224d71fb1f1c40ca328c87b8987d`  
Feature branch: `refactor/phase-7b2-card-foundations-import-boundary`

## Status

Approved in-chat architecture and cutover design. This document defines Phase 7B2 only. Implementation planning and code changes follow after explicit review of this written spec.

## Context

Phase 7A established permanent ordinary-import ownership for performance, native cache, schema, and runtime defaults.

Phase 7B1 then established permanent ordinary-import ownership for persistence:

- `bridge.config` became the canonical owner of the narrow persistence-related startup/default values;
- `bridge.database` became independently importable;
- database write-lock and connection-gate state became canonical to `bridge.database`;
- obsolete exec-era `_DB_SCHEMA_*` compatibility state was deleted;
- all public database functions were explicitly re-exported by `bridge.runtime`;
- `database.py` was removed from `DEFAULT_RUNTIME_STAGES`.

After Phase 7B1, the legacy core runtime stage begins:

```text
common.py
cards.py
memory.py
rag.py
groups.py
telegram.py
...
main.py
```

The next intended migration target was initially described as “cards + low-level helpers/config.” Inspection of the post-7B1 graph shows that `cards.py` is not one cohesive subsystem. It currently mixes:

1. character-card, World Info, and System Prompt content logic;
2. callback-token persistence and in-memory callback state;
3. pure panel pagination/label helpers;
4. Persona compatibility helpers;
5. Telegram-facing panel/menu rendering.

Retiring all of `cards.py` in one step would pull Telegram and Persona migration into this phase and collapse much of later Phase 7B4 into Phase 7B2.

Phase 7B2 therefore extracts the stable ordinary foundations while intentionally leaving a thin `cards.py` compatibility/UI shell in the legacy loader.

## Goal

Create canonical ordinary-import owners for card/content logic, callback-token state, panel primitives, and ambient runtime thread-local context, while preserving the existing public behavior and leaving the residual Telegram/Persona UI shell in `cards.py` for later retirement.

At the end of Phase 7B2:

- `bridge.config` owns the approved card/world/prompt paths, names, and limits;
- `bridge.runtime_context` is the sole owner of ambient DB/panel thread-local state;
- `bridge.panel_utils` owns pure panel label/pagination/navigation helpers;
- `bridge.card_content` owns card parsing, card/world paths, World Info activation, System Prompt catalog/content logic, macro replacement, system-prompt building, and character display-name extraction;
- `bridge.callback_tokens` owns callback-token generation, persistence lookup, expiry behavior, and the in-memory callback-token cache;
- `bridge.cards` becomes a thin legacy compatibility/UI shell that imports those canonical foundations instead of defining them;
- `bridge.character_identity` imports the canonical `character_display_name` helper instead of redefining it;
- `bridge.runtime` explicitly re-exports the moved public APIs as canonical object-identical functions;
- the new ordinary modules are never exec-loaded;
- `cards.py` deliberately remains in `DEFAULT_RUNTIME_STAGES` until the later Telegram/UI routing migration;
- no user-visible card, prompt, World Info, callback-token, Persona-menu, character-menu, or session-menu behavior changes.

## Non-goals

Phase 7B2 does not:

- remove `cards.py` from `DEFAULT_RUNTIME_STAGES`;
- migrate `telegram.py`;
- migrate `persona_sync.py`;
- migrate the full character-rename/reconciliation subsystem;
- migrate character upload/delete mechanics;
- migrate provider/model catalog behavior;
- migrate `memory.py`, `rag.py`, or `groups.py`;
- redesign callback-token format, persistence schema, or TTL;
- redesign PersonaService;
- redesign Telegram markup or visible menu text;
- introduce a new cache implementation;
- introduce runtime configuration reload;
- split or redesign database persistence;
- delete `common.py`;
- delete `runtime_loader.py`;
- alter the relative order of existing legacy runtime files;
- create a generic “helpers” or service-locator module.

## Architectural choice

### Recommended approach: ordinary foundations plus a thin legacy `cards.py` shell

Phase 7B2 uses an incremental extraction pattern:

```text
ORDINARY IMPORT WORLD

bridge.config
    └── card/world/prompt paths + limits

bridge.runtime_context
    ├── DB connection context
    └── panel actor/session context

bridge.panel_utils
    ├── panel_label
    ├── panel_page
    └── panel_navigation

bridge.card_content
    ├── character-card parsing
    ├── card fields
    ├── character/world paths
    ├── World Info
    ├── System Prompt catalog/content
    ├── macro replacement
    ├── system-prompt building
    └── character display names

bridge.callback_tokens
    ├── callback-token in-memory cache
    ├── token persistence
    ├── token lookup
    └── expiry/invalidation

              |
              v

LEGACY EXEC WORLD

common.py
cards.py       <- UI/Persona compatibility shell only
memory.py
rag.py
groups.py
telegram.py
...
```

This gives later phases reusable ordinary foundations without prematurely migrating Telegram or Persona behavior.

## Rejected approach: retire all of `cards.py` now

A full `cards.py` retirement would require resolving or migrating late-bound dependencies such as:

```text
telegram_request
resolve_persona_service
load_personas
_native_settings
```

It would also pull the Telegram-facing panel/menu surface into Phase 7B2.

That would substantially increase regression scope and overlap with the planned Phase 7B4 generation/command/callback/UI migration.

## Rejected approach: keep `cards.py` whole and inject Telegram/Persona callbacks

An alternative would be to make all of `cards.py` ordinary by passing Telegram and Persona collaborators into its functions.

That would add transitional callback plumbing across a large public API purely to escape the loader. The resulting adapter debt would then need to be removed again in later phases.

Phase 7B2 instead moves the concerns that already have clean ordinary boundaries and leaves the genuinely UI-bound shell where it is until the UI migration.

## Rejected approach: create one generic low-level helper module

The extracted concerns have different state and dependency semantics:

- runtime context owns thread-local state;
- panel utilities are pure;
- card content is filesystem/content logic;
- callback tokens own mutable cache plus database persistence.

Combining them into one helper module would obscure ownership and create a new monolith.

Phase 7B2 gives each concern one clear canonical owner.

# Canonical configuration ownership

Extend existing `bridge.config` with exactly these additional card/world/prompt settings:

```text
SILLYTAVERN_DIR
CHARACTER_DIR
DEFAULT_CHARACTER_FILE
CARD_FILE
WORLD_DIR
SYSTEM_PROMPTS_DIR
SYSTEM_PROMPTS_FILE
DEFAULT_USER_NAME
CATALOG_MAX_ITEMS
CARD_FIELD_MAX_CHARS
CARD_TOTAL_MAX_CHARS
```

Their values and environment semantics remain identical to their current definitions in `common.py`.

Conceptually:

```python
SILLYTAVERN_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_DIR",
        str(BRIDGE_HOME.parent / "SillyTavern"),
    )
)
CHARACTER_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_CHARACTER_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/characters"),
    )
)
DEFAULT_CHARACTER_FILE = os.environ.get(
    "SILLYTAVERN_DEFAULT_CHARACTER",
    "",
).strip()
CARD_FILE = CHARACTER_DIR / DEFAULT_CHARACTER_FILE
WORLD_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_WORLD_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/worlds"),
    )
)
SYSTEM_PROMPTS_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_SYSTEM_PROMPTS_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/sysprompt"),
    )
)
SYSTEM_PROMPTS_FILE = os.environ.get(
    "SILLYTAVERN_SYSTEM_PROMPTS_FILE",
    "",
)
DEFAULT_USER_NAME = os.environ.get(
    "SILLYTAVERN_DEFAULT_USER_NAME",
    "",
).strip()
CATALOG_MAX_ITEMS = 40
CARD_FIELD_MAX_CHARS = 20000
CARD_TOTAL_MAX_CHARS = 60000
```

## Configuration exclusions

Do not move the following in Phase 7B2:

```text
CHARACTER_BACKUP_DIR
PROVIDER_CONFIG_FILE
MODEL_CACHE_FILE
MODEL_REFRESH_SECONDS
RAG_*
SYNC_*
IMAGE_MAX_BYTES
Telegram configuration
logging configuration
```

Those belong to later feature boundaries.

## `CARD_FILE` semantics

`CARD_FILE` remains a startup-derived configured path.

It is computed from the startup values of `CHARACTER_DIR` and `DEFAULT_CHARACTER_FILE` when `bridge.config` is imported.

Phase 7B2 does not introduce automatic recomputation if either constituent value is rebound later.

Tests that specifically need a different configured default card must patch `bridge.config.CARD_FILE` directly.

## `common.py` compatibility

`common.py` imports and re-exports the new canonical config names.

It must delete its local definitions of exactly those names.

The remaining legacy runtime therefore continues receiving the same startup values through `common.py`, while new ordinary modules target `bridge.config` directly.

Rebinding the corresponding names on `bridge.runtime` is not a supported configuration mechanism.

# Canonical runtime context ownership

Create:

```text
bridge/runtime_context.py
```

as the sole owner of ambient thread-local DB and panel context.

Move the following private state from `common.py`:

```text
_PANEL_SESSION_CONTEXT
_DB_CONNECTION_CONTEXT
```

Move these public functions with that state:

```text
set_panel_session_context
panel_session_context
set_panel_actor_context
panel_actor_context
set_db_connection_context
db_connection_context
```

The ordinary module owns the thread-local objects directly:

```python
_PANEL_SESSION_CONTEXT = threading.local()
_DB_CONNECTION_CONTEXT = threading.local()
```

There is no `globals().get(...)`, reload fallback, duplicate thread-local, or runtime lookup.

## Context compatibility

`common.py` imports/re-exports the six canonical functions.

`bridge.runtime` explicitly imports/re-exports the six canonical functions before legacy loading.

Because `common.py` binds the same function objects, shared runtime execution must not create an override.

Both call directions must operate on the same thread-local objects.

Examples:

```python
rt.set_db_connection_context(conn)
assert runtime_context.db_connection_context() is conn

runtime_context.set_db_connection_context(conn2)
assert rt.db_connection_context() is conn2
```

and:

```python
rt.set_panel_session_context("session-1")
assert runtime_context.panel_session_context() == "session-1"

runtime_context.set_panel_actor_context("user-1")
assert rt.panel_actor_context() == "user-1"
```

The canonical module remains responsible for thread-local isolation semantics.

# Pure panel primitives

Create:

```text
bridge/panel_utils.py
```

with canonical ownership of:

```text
PANEL_PAGE_SIZE
panel_label
panel_page
panel_navigation
```

The module must depend only on Python builtins/stdlib.

It must not import:

```text
bridge.runtime
bridge.common
bridge.config
bridge.database
bridge.telegram
bridge.persona_sync
```

## Public facade

`bridge.runtime` explicitly imports/re-exports:

```text
panel_label
panel_page
panel_navigation
```

The public functions must be object-identical to the canonical ordinary module.

`PANEL_PAGE_SIZE` remains an implementation/default constant and is not required as new runtime facade debt unless an existing compatibility test demonstrates that it is a supported public dependency.

# Card/content ownership

Create:

```text
bridge/card_content.py
```

as the canonical owner of card, World Info, prompt, macro, and character-display content behavior.

## Public API moved from `cards.py`

Move exactly these public functions:

```text
read_png_chara
parse_png_chara_bytes
card_fields
character_card_paths
safe_character_path
card_fields_from_file

world_file_paths
safe_world_path
active_world_files
encode_world_files
build_world_info

load_system_prompts
get_system_prompt_choice
system_prompt_label
system_prompt_callback_token
system_prompt_choices

replace_macros
build_system_prompt

character_display_name
```

## Private helpers moved with their owners

Move the relevant private implementation helpers, including:

```text
_default_character_name
_prompt_catalog_label
_merge_system_prompt_file
_merge_system_prompt_json
_merge_system_prompt_text
_build_system_prompt_uncached
```

Additional private helpers may move if and only if they exclusively serve the approved public card/content API.

## Dependencies

`bridge.card_content` may import:

```text
stdlib
bridge.config
bridge.native_cache
bridge.panel_utils
```

It must not import:

```text
bridge.runtime
bridge.common
bridge.database
bridge.telegram
bridge.persona_sync
```

It must not depend on late-bound names injected by the runtime loader.

## Config access

Path- and limit-sensitive functions should read canonical config values at call time where practical:

```text
_config.CHARACTER_DIR
_config.CARD_FILE
_config.DEFAULT_CHARACTER_FILE
_config.WORLD_DIR
_config.SYSTEM_PROMPTS_DIR
_config.SYSTEM_PROMPTS_FILE
_config.CATALOG_MAX_ITEMS
_config.CARD_FIELD_MAX_CHARS
_config.CARD_TOTAL_MAX_CHARS
```

This gives tests and ordinary callers one canonical configuration patch point.

`DEFAULT_USER_NAME` appears in existing function signatures. It may be bound from the canonical config module when defining those default parameters, preserving current startup semantics.

No runtime configuration reload is introduced.

# Character display-name ownership

Today `character_identity.py` defines `character_display_name(path)` even though that function is fundamentally card-content parsing.

Phase 7B2 moves canonical ownership to `bridge.card_content`.

After migration:

```python
from bridge.card_content import character_display_name
```

appears in `character_identity.py`.

The local `def character_display_name(...)` must be deleted.

The remaining character identity/reconciliation API stays in `character_identity.py`, including:

```text
character_image_fingerprint
resolve_renamed_character
reconcile_session_character
```

This prevents the later `character_identity.py` runtime stage from replacing the canonical public helper with a newly defined function object.

# Callback-token ownership

Create:

```text
bridge/callback_tokens.py
```

as the sole owner of callback-token mutable state and persistence behavior.

Move:

```text
_CALLBACK_TOKEN_VALUES
_CALLBACK_TOKEN_TTL_SECONDS
dynamic_callback_token
resolve_dynamic_callback_token
```

along with the private database-connection helper currently used by that behavior.

## Callback-token dependencies

The module explicitly imports:

```text
stdlib hashlib/logging/time
bridge.database.db_connect
bridge.runtime_context.db_connection_context
```

It must not import `bridge.runtime` or `bridge.common`.

## Callback cache ownership

The state model changes from:

```text
exec(cards.py)
└── runtime._CALLBACK_TOKEN_VALUES
```

to:

```text
bridge.callback_tokens
└── _CALLBACK_TOKEN_VALUES
```

There must be exactly one process-level callback-token cache.

`bridge.runtime` explicitly re-exports only:

```text
dynamic_callback_token
resolve_dynamic_callback_token
```

The private cache and TTL are not compatibility facade state.

## Token behavior preservation

The callback token format remains unchanged:

```text
"t" + first 16 hexadecimal SHA-256 characters of:
kind + "|" + chat_id + "|" + value
```

The TTL remains:

```text
900 seconds
```

Creation behavior remains:

1. compute token;
2. store the value tuple in memory;
3. attempt to persist to the `callback_tokens` table;
4. if persistence fails, log/debug as today without discarding a successfully created in-memory token.

Resolution behavior remains:

1. check canonical in-memory cache;
2. on cache miss, query persistent storage;
3. restore a persistent row into the canonical in-memory cache;
4. reject and invalidate expired, kind-mismatched, or scoped-chat-mismatched tokens;
5. attempt persistent deletion of rejected/expired tokens;
6. return `None` for invalid/unresolvable tokens.

# Thin `cards.py` compatibility/UI shell

After extraction, `cards.py` deliberately remains exec-loaded.

It keeps only functionality whose dependencies still belong to later legacy runtime stages.

Expected residual responsibilities are:

## Persona compatibility helpers

```text
get_persona
default_persona_id
persona_name
```

These still depend on late-bound Persona collaborators such as:

```text
load_personas
_native_settings
resolve_persona_service
```

## Telegram/panel menu rendering

```text
send_panel_message
send_persona_menu
send_character_menu
send_character_info_menu
send_character_delete_menu
send_character_delete_confirm
send_session_menu
```

These still ultimately depend on the legacy Telegram request implementation and/or Persona service compatibility.

## Canonical imports into `cards.py`

`cards.py` imports the moved foundations explicitly from:

```text
bridge.card_content
bridge.callback_tokens
bridge.panel_utils
```

The residual shell should also import ordinary stdlib dependencies it directly uses rather than relying on `common.py` to inject them.

Where the residual shell needs card-related configuration such as:

```text
DEFAULT_CHARACTER_FILE
CARD_FILE
CATALOG_MAX_ITEMS
```

it should consume canonical `bridge.config` ownership rather than depending on mutable `bridge.runtime` globals. Using a module alias such as:

```python
from bridge import config as _config
```

is preferred where call-time canonical configuration access matters.

This does not make the shell independently importable because its Persona/Telegram collaborators remain intentionally late-bound.

## What `cards.py` must stop owning

After Phase 7B2, `cards.py` must not define:

```text
read_png_chara
parse_png_chara_bytes
card_fields
character_card_paths
safe_character_path
card_fields_from_file
world_file_paths
safe_world_path
active_world_files
encode_world_files
build_world_info
load_system_prompts
get_system_prompt_choice
system_prompt_label
system_prompt_callback_token
system_prompt_choices
replace_macros
build_system_prompt
dynamic_callback_token
resolve_dynamic_callback_token
panel_label
panel_page
panel_navigation
```

It imports/re-exports those canonical objects into the shared runtime namespace instead.

# Runtime compatibility facade

`bridge.runtime` explicitly imports the moved public ordinary APIs before legacy loading.

## Runtime-context exports

```text
set_panel_session_context
panel_session_context
set_panel_actor_context
panel_actor_context
set_db_connection_context
db_connection_context
```

## Panel utility exports

```text
panel_label
panel_page
panel_navigation
```

## Card-content exports

```text
read_png_chara
parse_png_chara_bytes
card_fields
character_card_paths
safe_character_path
card_fields_from_file
world_file_paths
safe_world_path
active_world_files
encode_world_files
build_world_info
load_system_prompts
get_system_prompt_choice
system_prompt_label
system_prompt_callback_token
system_prompt_choices
replace_macros
build_system_prompt
character_display_name
```

## Callback-token exports

```text
dynamic_callback_token
resolve_dynamic_callback_token
```

Every facade export must be the exact canonical function object.

No wildcard imports, reflection-driven export table, or `globals().update(...)` compatibility mechanism may be introduced.

# Runtime loader boundary

Phase 7B2 introduces four ordinary modules:

```text
runtime_context.py
panel_utils.py
card_content.py
callback_tokens.py
```

None may ever be added to `DEFAULT_RUNTIME_STAGES`.

Unlike Phase 7B1, Phase 7B2 deliberately does not remove an existing source file from the loader.

The core runtime stage still begins:

```text
common.py
cards.py
memory.py
rag.py
...
```

The relative order of all existing legacy files remains unchanged.

`cards.py` remaining in the loader is an explicit design choice and acceptance criterion.

Its final retirement is reserved for the later Telegram/UI routing phase.

# Migration sequence

Implementation follows four TDD slices.

## Slice A — canonical config and runtime context

Add failing tests for:

- the approved new `bridge.config` values;
- standalone import of `bridge.runtime_context`;
- absence of `bridge.runtime` and `bridge.common` imports from that module;
- cross-boundary DB context identity;
- cross-boundary panel-session context identity;
- cross-boundary panel-actor context identity.

Then:

1. add the approved config values to `bridge.config`;
2. make `common.py` import/re-export them and delete the local definitions;
3. create `bridge.runtime_context`;
4. move both thread-local objects and all six accessors to that module;
5. make `common.py` import/re-export the canonical context functions;
6. explicitly pre-import/re-export those context functions from `bridge.runtime`.

At the end of Slice A, `cards.py` remains behaviorally unchanged.

## Slice B — panel primitives and card/content extraction

Add failing tests for:

- standalone `panel_utils` import;
- standalone `card_content` import;
- `card_content` not importing runtime/common/database/Telegram/Persona modules;
- canonical facade object identity for moved card/panel functions;
- source-level absence of migrated definitions from `cards.py`;
- source-level absence of local `character_display_name` definition from `character_identity.py`;
- card/world/prompt config patching through `bridge.config`.

Then:

1. create `bridge.panel_utils`;
2. create `bridge.card_content`;
3. move the approved public functions and their private helpers;
4. convert `cards.py` to explicit imports/re-exports of those functions;
5. update `character_identity.py` to import canonical `character_display_name`;
6. explicitly pre-import/re-export the moved public functions from `bridge.runtime`.

The legacy UI/persona shell remains in `cards.py`.

## Slice C — callback-token extraction

Add failing tests for:

- standalone `callback_tokens` import without runtime/common;
- canonical in-memory cache ownership;
- runtime-to-module token sharing;
- module-to-runtime token sharing;
- persistence fallback;
- expiry behavior;
- private cache absence from `bridge.runtime`;
- source-level absence of callback-token definitions from `cards.py`.

Then:

1. create `bridge.callback_tokens`;
2. move the cache, TTL, DB helper, creation, and resolution logic;
3. explicitly import `db_connect` and `db_connection_context`;
4. import/re-export the public token functions from `cards.py`;
5. explicitly pre-import/re-export the public token functions from `bridge.runtime`.

No callback-token schema, format, or TTL changes occur.

## Slice D — permanent architecture guards and full regression proof

Add permanent tests for:

- all four ordinary modules absent from every runtime stage;
- `cards.py` still present in the core stage;
- unchanged relative ordering of all existing legacy files;
- complete facade identity for all moved public functions;
- exactly one DB connection-context owner;
- exactly one panel-context owner;
- exactly one callback-token cache owner;
- no migrated implementation definitions remaining in `cards.py`;
- no `character_display_name` redefinition in `character_identity.py`;
- import-order independence;
- no forbidden runtime/common dependencies from the ordinary modules.

Then run the complete relevant behavior and repository suites.

# Error handling and failure semantics

Phase 7B2 preserves existing failure behavior.

## Card and prompt behavior

Preserve current handling for:

- invalid or non-PNG character files;
- missing SillyTavern chara metadata;
- malformed character metadata payloads;
- invalid/missing World Info files;
- malformed System Prompt JSON;
- inaccessible System Prompt files;
- logging and fallback behavior in World Info activation;
- macro parsing and random/time/date substitutions.

No new broad exception swallowing is introduced.

## Callback-token behavior

Preserve current logging and fallback behavior exactly.

Do not convert token persistence failures into creation failures if the current in-memory behavior succeeds.

Do not introduce a second callback cache or alternate storage backend.

## Dependency failures

Missing dependencies are fixed with explicit imports.

Do not add:

```python
globals().get(...)
getattr(runtime, ...)
try:
    import bridge.common
```

as dependency lookup mechanisms.

# Behavior preservation

Phase 7B2 must preserve:

- PNG character-card parsing;
- card field extraction;
- field truncation limits;
- alternate greeting extraction/encoding;
- default character-name fallback;
- character file discovery and path safety;
- World Info file discovery/path safety;
- World Info activation ordering and secondary-key behavior;
- World Info encoding;
- System Prompt catalog discovery;
- JSON/text System Prompt parsing;
- System Prompt callback-token key shortening;
- System Prompt labels and choices;
- macro replacement behavior;
- random/pick macro behavior;
- time/date/weekday macro behavior;
- deterministic system-prompt caching when dynamic macros are absent;
- uncached behavior for random/time/date macro inputs;
- character display-name fallback behavior;
- callback-token format;
- callback-token TTL;
- callback-token in-memory cache behavior;
- callback-token database persistence;
- callback-token persistence fallback;
- callback-token scope validation;
- callback-token expiry deletion;
- panel labels;
- panel pagination;
- panel navigation;
- Persona-menu behavior;
- character-menu behavior;
- character-info/delete menu behavior;
- session-menu behavior;
- panel-session/actor context semantics;
- DB ambient-connection context semantics.

No user-visible Telegram or SillyTavern behavior is intended to change.

# Architecture-specific test requirements

## Standalone ordinary imports

Fresh subprocess tests must verify:

```python
import bridge.runtime_context
import bridge.panel_utils
import bridge.card_content
import bridge.callback_tokens
```

without loading `bridge.runtime` or `bridge.common`.

`bridge.card_content` must also not load:

```text
bridge.database
bridge.telegram
bridge.persona_sync
```

## Runtime-context identity

Tests must prove cross-boundary state sharing in both directions for:

```text
DB connection context
panel session context
panel actor context
```

The runtime facade must use the same canonical function objects as `bridge.runtime_context`.

## Panel utility identity

Tests must prove:

```python
rt.panel_label is panel_utils.panel_label
rt.panel_page is panel_utils.panel_page
rt.panel_navigation is panel_utils.panel_navigation
```

## Card-content identity

Tests must prove every moved public card/content function exposed through `bridge.runtime` is the exact canonical object from `bridge.card_content`.

Representative examples include:

```python
assert rt.card_fields is card_content.card_fields
assert rt.safe_world_path is card_content.safe_world_path
assert rt.build_system_prompt is card_content.build_system_prompt
assert rt.character_display_name is card_content.character_display_name
```

The implementation plan should favor complete public-function coverage rather than a representative subset where practical.

## Callback-token identity and cache ownership

Tests must prove:

```python
rt.dynamic_callback_token is callback_tokens.dynamic_callback_token
rt.resolve_dynamic_callback_token is callback_tokens.resolve_dynamic_callback_token
```

and that creation/resolution across the two access paths uses the same in-memory cache.

The runtime facade must not expose:

```text
_CALLBACK_TOKEN_VALUES
_CALLBACK_TOKEN_TTL_SECONDS
```

## Cards shell source guard

Permanent source-level guards must prove `cards.py` no longer defines any migrated public function.

This is important because a future redefinition would recreate exec-owned objects and undermine canonical ownership even if behavior tests remained green.

## Character identity source guard

`character_identity.py` must import canonical `character_display_name` and must not contain a local definition for it.

## Runtime-stage guard

Permanent tests must assert:

```text
runtime_context.py
panel_utils.py
card_content.py
callback_tokens.py
```

are absent from all runtime stages.

They must also assert:

```text
cards.py
```

remains in the core runtime stage and the order of existing legacy files is unchanged.

# Regression suites

Existing suites remain authoritative. The implementation plan must identify and run the concrete suites covering at least:

```text
character card parsing
alternate greetings
World Info activation/management
System Prompt behavior
macro/system prompt building
character rename/recovery
character menus
World menus
Persona menus
session menus
callback token persistence/expiry
panel binding/lifecycle
topic/thread routing
native cache behavior
runtime-loader validation
```

The implementation plan should use the repository's current test filenames discovered at planning time rather than assuming a stale list.

# Production scope boundary

Expected production changes are limited to:

```text
bridge/config.py
bridge/common.py
bridge/runtime_context.py        new
bridge/panel_utils.py            new
bridge/card_content.py           new
bridge/callback_tokens.py        new
bridge/cards.py
bridge/character_identity.py
bridge/runtime.py
```

`bridge/runtime_loader.py` should not require a production-stage change in this phase.

Test-only changes may be broader where existing tests patch card/world/prompt configuration or inspect state through shared runtime globals. Those tests must be retargeted to canonical owners without weakening behavior assertions.

Any production need to modify Telegram, Persona, memory, generation, command routing, callbacks, or main composition indicates hidden architectural scope and requires returning to the design gate before proceeding.

# Verification requirements

Before Phase 7B2 is ready for review:

- all four ordinary modules import standalone;
- forbidden dependency/import guards pass;
- config extraction/value tests pass;
- runtime-context identity tests pass in both directions;
- panel utility facade identity tests pass;
- complete card-content facade identity tests pass;
- callback-token facade identity and single-cache tests pass;
- callback persistence/expiry tests pass;
- cards-shell source guards pass;
- character-identity ownership guards pass;
- all four ordinary modules are absent from runtime stages;
- `cards.py` remains in the core stage;
- remaining legacy order is unchanged;
- relevant card/world/prompt/menu/panel regression suites pass;
- full `unittest` suite passes;
- full `pytest` suite passes;
- Python compilation passes;
- `pip check` passes;
- dependency audit passes;
- exact branch-head GitHub Actions succeeds;
- branch is not behind current upstream `main`, or drift is explicitly reviewed;
- whole-branch review finds no Critical or Important issue.

# Relationship to later Phase 7 work

Phase 7B2 intentionally leaves:

```text
cards.py
```

as a small legacy UI/Persona shell.

Expected state after 7B2:

```text
NORMAL IMPORT WORLD
├── config.py
├── runtime_context.py
├── panel_utils.py
├── card_content.py
├── callback_tokens.py
├── runtime_defaults.py
├── performance.py
├── native_cache.py
├── schema.py
├── database.py
├── migrations.py
├── repositories.py
├── composition.py
└── service modules

LEGACY EXEC WORLD
├── common.py
├── cards.py          <- thin UI/Persona shell
├── memory.py
├── rag.py
├── groups.py
├── telegram.py
├── ...
└── main.py
```

Expected later sequencing remains:

1. Phase 7B3 — memory/groups and related feature modules;
2. Phase 7B4 — generation/commands/callback/UI routing, including retirement of the residual `cards.py` shell;
3. Phase 7C — final main/runtime facade cutover and production deletion of the shared runtime loader.

These later boundaries remain subject to fresh dependency inventory after each merged phase.

# Acceptance criteria

Phase 7B2 is complete when all of the following are true:

1. `bridge.runtime_context`, `bridge.panel_utils`, `bridge.card_content`, and `bridge.callback_tokens` import independently of `bridge.runtime` and `bridge.common`.
2. `bridge.card_content` does not import database, Telegram, or Persona modules.
3. the approved card/world/prompt config values are owned by `bridge.config` and re-exported by `common.py`.
4. `common.py` no longer defines the extracted config values locally.
5. DB connection context has one canonical thread-local owner.
6. panel session/actor context has one canonical thread-local owner.
7. the six runtime-context functions are canonical object-identical exports through `bridge.runtime`.
8. `bridge.panel_utils` owns panel label/page/navigation functions.
9. `bridge.card_content` owns all approved card/world/prompt/macro public functions.
10. `bridge.card_content` owns `character_display_name`.
11. `character_identity.py` imports rather than redefines `character_display_name`.
12. `bridge.callback_tokens` is the only owner of `_CALLBACK_TOKEN_VALUES`.
13. callback-token creation/resolution through runtime and ordinary-module access paths share one cache.
14. callback-token format, TTL, persistence, scope, and expiry semantics remain unchanged.
15. `cards.py` no longer defines any migrated card/content, panel-primitive, or callback-token public function.
16. `cards.py` imports/re-exports those canonical ordinary functions.
17. the residual `cards.py` shell may keep Persona and Telegram-facing menu behavior only.
18. all moved public functions exposed by `bridge.runtime` are exact canonical object re-exports.
19. private callback-token cache state is not republished through `bridge.runtime`.
20. `runtime_context.py`, `panel_utils.py`, `card_content.py`, and `callback_tokens.py` never appear in runtime stages.
21. `cards.py` remains in the core runtime stage.
22. the relative order of all existing legacy runtime files is unchanged.
23. relevant tests no longer depend on rebinding extracted `rt.*` configuration or private state instead of canonical owners.
24. card parsing, World Info, prompts, macros, callback tokens, panels, Persona UI, character UI, and session UI behavior remain unchanged.
25. complete repository CI passes at the exact branch head.

# Permanent migration invariants

Once Phase 7B2 lands:

> Card/content behavior belongs only to `bridge.card_content`.

> Panel pagination/label primitives belong only to `bridge.panel_utils`.

> Callback-token in-memory state belongs only to `bridge.callback_tokens`.

> Ambient DB and panel thread-local context belongs only to `bridge.runtime_context`.

> `cards.py` may consume those ordinary foundations but may never regain their implementation or mutable state.

> `cards.py` remains a temporary UI/Persona compatibility shell until the later Telegram/UI routing migration retires it.

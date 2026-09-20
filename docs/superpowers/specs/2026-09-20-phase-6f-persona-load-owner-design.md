# Phase 6F — Persona Load Ownership Design

**Date:** 2026-09-20  
**Status:** Proposed — awaiting written-spec review  
**Repository:** `cepeter/SillyTavern-Telegram-Bridge`  
**Baseline:** `df552d3d40b92c1016431fb4ef7eeee74bbbc284` (merged Phase 6E)  
**Master architecture:** `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## 1. Purpose

Phase 6F removes the remaining Persona public runtime override:

```text
persona_sync.py::load_personas
```

while preserving the currently effective native Persona read behavior exactly.

The target is:

- `persona_sync.py` becomes the canonical public owner of `load_personas`;
- the earlier duplicate `cards.py::load_personas` is deleted;
- `persona_sync.py` remains runtime-loaded, but no longer overrides a public callable;
- the `load_personas` override allowlist is removed;
- Persona application-service, persistence, integrity, cache, API/file access, and UI behavior remain unchanged.

Phase 6F is ownership cleanup only. It does not redesign Persona reads.

## 2. Current state

The runtime currently defines `load_personas` twice.

Earlier, `cards.py` defines:

```python
def load_personas() -> dict[str, dict[str, str]]:
    try:
        return load_native_personas()
    except Exception:
        logging.warning(
            "Could not load native SillyTavern personas",
            exc_info=True,
        )
        return {}
```

Later, `persona_sync.py` defines:

```python
def load_personas() -> dict[str, dict[str, object]]:
    """Load native Persona metadata; the bridge JSON catalog is not used."""
    try:
        return load_native_personas()
    except Exception:
        logging.warning(
            "Could not load native Persona metadata",
            exc_info=True,
        )
        return {}
```

Because `persona_sync.py` runs later and is explicitly allowlisted, the second definition is the effective production behavior.

`runtime_loader.py` currently contains:

```python
RuntimeStage(
    "native_adapter_overrides",
    ("persona_sync.py",),
    (("persona_sync.py", ("load_personas",)),),
)
```

This is the final Persona public runtime override.

## 3. Selected approach

Make `persona_sync.py` the canonical owner of `load_personas`.

Phase 6F will:

1. delete only the duplicate `cards.py::load_personas`;
2. keep `persona_sync.py::load_personas` unchanged;
3. remove `load_personas` from the runtime override allowlist;
4. leave `persona_sync.py` in its existing runtime stage;
5. leave `get_persona`, `default_persona_id`, and `persona_name` in `cards.py`.

Final ownership:

```text
persona_sync.py
    ├─ load_personas()          canonical native Persona read entry
    ├─ load_native_personas()
    ├─ upsert_native_persona()
    ├─ delete_native_persona()
    └─ compatibility_persona_service()

cards.py
    ├─ get_persona()
    ├─ default_persona_id()
    └─ persona_name()
         |
         v
      load_personas()
```

## 4. Why persona_sync.py is the canonical owner

`persona_sync.py` already owns native SillyTavern Persona infrastructure:

- native settings reads;
- Persona cache refresh;
- avatar validation;
- settings backup;
- avatar allocation;
- native settings writes;
- readback verification;
- integrity-checked public Persona persistence;
- compatibility `PersonaService` construction.

The currently effective `load_personas` already lives there.

Keeping that implementation as canonical avoids changing behavior and keeps native Persona I/O cohesive.

## 5. Rejected alternatives

### 5.1 Make cards.py canonical

Rejected because it would revert to the older duplicate wrapper and change the currently effective warning string.

It would also make the native Persona loader less cohesive with the rest of native Persona infrastructure.

### 5.2 Move all Persona read helpers into persona_sync.py

Rejected because moving `get_persona`, `default_persona_id`, and `persona_name` is unnecessary for retiring the override and would enlarge the regression surface.

That cleanup may be considered later if ordinary module imports or the Phase 7 composition root make it useful.

### 5.3 Introduce a new Persona read adapter

Rejected because there is no behavior gap requiring another abstraction. The desired native read boundary already exists.

## 6. Behavior preservation

The currently effective `persona_sync.py::load_personas` implementation must remain unchanged.

Required semantics:

- successful reads delegate to `load_native_personas()`;
- native cache behavior remains unchanged;
- any exception from `load_native_personas()` is isolated;
- failure returns `{}`;
- warning text remains exactly:

```text
Could not load native Persona metadata
```

- warning uses `exc_info=True`.

Phase 6F must not restore the older `cards.py` warning text.

## 7. Call-time binding of Persona helpers

`cards.py` retains:

```python
def get_persona(persona_id):
    return load_personas().get(persona_id)


def default_persona_id():
    ...
    personas = load_personas()
    ...


def persona_name(persona_id):
    persona = get_persona(persona_id)
    ...
```

These functions resolve `load_personas` at call time from the shared runtime namespace.

Therefore deleting the earlier `cards.py::load_personas` does not break those helpers after `persona_sync.py` has executed.

Phase 6F must add regression coverage proving:

- `get_persona` observes the final runtime `load_personas`;
- `default_persona_id` observes the final runtime `load_personas`;
- `persona_name` continues through `get_persona`;
- compatibility `PersonaService` observes the final runtime `load_personas`.

No module-level Persona read call in `cards.py` may depend on `load_personas` before `persona_sync.py` loads.

## 8. PersonaService relationship

`PersonaService` remains unchanged.

The compatibility service continues to use:

```python
load_personas=load_personas
```

from `persona_sync.py`.

Phase 6F does not change:

- list/get/name/default Persona use cases;
- create/select/update/delete use cases;
- Persona reference-count checks;
- session Persona updates;
- Persona edit locking;
- integrity checks;
- direct native persistence.

## 9. Runtime-loader cutover

Before Phase 6F:

```python
RuntimeStage(
    "native_adapter_overrides",
    ("persona_sync.py",),
    (("persona_sync.py", ("load_personas",)),),
)
```

After Phase 6F:

```python
RuntimeStage(
    "native_adapter_overrides",
    ("persona_sync.py",),
)
```

The stage name remains unchanged.

Phase 6F does not rename or reorganize runtime stages because that has no behavioral value and belongs with later loader cleanup.

After the cutover:

- `persona_sync.py` remains runtime-loaded;
- it reports zero public callable overrides;
- no override allowlist entry contains `load_personas`.

## 10. Testing strategy

Implementation follows strict RED -> GREEN.

### 10.1 Canonical ownership tests

Add tests requiring:

```text
rt.load_personas.__code__.co_filename == "persona_sync.py"
```

and source assertions that:

- `cards.py` contains no `def load_personas(`;
- `persona_sync.py` contains the canonical `def load_personas(`.

### 10.2 Runtime-loader retirement tests

Verify:

- `persona_sync.py` remains in `DEFAULT_RUNTIME_STAGES`;
- `persona_sync.py` has no allowed public callable overrides;
- `RUNTIME_LOAD_REPORT` reports:

```text
persona_sync.py -> ()
```

- no runtime override allowlist anywhere contains `load_personas`.

### 10.3 Behavior tests

Pin the currently effective behavior:

- valid native Persona metadata loads successfully;
- `load_native_personas` exception returns `{}`;
- warning text is exactly `Could not load native Persona metadata`;
- warning uses `exc_info=True`;
- cache path continues to be `load_native_personas`.

### 10.4 Call-time binding tests

Patch final runtime `load_personas` and verify:

- `rt.get_persona(...)` uses the patched loader;
- `rt.default_persona_id()` uses the patched loader;
- `rt.persona_name(...)` observes the patched data through `get_persona`;
- `rt.resolve_persona_service().list()` uses the patched loader.

These tests protect against accidentally capturing the earlier function object during module execution.

## 11. Cutover sequencing

Required sequence:

1. add ownership/runtime-loader RED tests;
2. add behavior/call-time-binding characterization tests;
3. delete `cards.py::load_personas`;
4. remove the `load_personas` override allowlist;
5. leave `persona_sync.py::load_personas` unchanged;
6. run focused Persona/runtime-loader tests;
7. run full repository CI on the exact final head.

The cutover may be one production commit because the duplicate definition and allowlist must disappear together to avoid an intermediate unexpected-override loader failure.

## 12. Source boundaries

After Phase 6F:

- there is exactly one public `def load_personas(` in runtime modules;
- canonical owner is `persona_sync.py`;
- no `_ORIGINAL_*` Persona read capture is introduced;
- no new runtime stage is introduced;
- no service locator is introduced;
- no load-order dependency beyond the existing compatibility runtime is added.

## 13. Out of scope

Phase 6F does not:

- move `get_persona`;
- move `default_persona_id`;
- move `persona_name`;
- change `PersonaService`;
- change `IntegrityCheckedPersonaStore`;
- change native Persona create/update/delete;
- change Persona cache timing;
- change native settings file/API access;
- change avatar behavior;
- change Persona UI or callback flows;
- rename runtime stages;
- modify `recovery.py`;
- retire `runtime_loader.py`;
- begin Phase 7.

No Phase 7 work is authorized by this spec.

## 14. Acceptance criteria

Phase 6F is complete when all are true:

1. `cards.py::load_personas` no longer exists;
2. `persona_sync.py::load_personas` remains behaviorally unchanged;
3. public `rt.load_personas` resolves to `persona_sync.py`;
4. successful native Persona reads remain unchanged;
5. read failure still returns `{}`;
6. warning text remains exactly `Could not load native Persona metadata`;
7. warning still includes `exc_info=True`;
8. `get_persona` resolves final `load_personas` at call time;
9. `default_persona_id` resolves final `load_personas` at call time;
10. `persona_name` still resolves through final Persona data;
11. compatibility `PersonaService` resolves final `load_personas`;
12. `persona_sync.py` remains runtime-loaded;
13. `persona_sync.py` reports zero public callable overrides;
14. no override allowlist contains `load_personas`;
15. no new `_ORIGINAL_*`, runtime override, service locator, or hidden load-order dependency is introduced;
16. Persona persistence/integrity behavior is unchanged;
17. full repository CI passes on the exact final head.

## 15. Resulting migration state

After Phase 6F, the only remaining public runtime override module is expected to be:

```text
recovery.py
```

with its recovery/UI compatibility functions.

That makes recovery override retirement the final public-override migration before Phase 7 compatibility-loader retirement.

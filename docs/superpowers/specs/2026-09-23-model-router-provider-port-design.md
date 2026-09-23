# ModelRouter and Provider Port Design

Date: 2026-09-23
Status: approved for implementation
Base: `main` at `bb6d092f9f58b15d08d2f87a506260f29fdeea18`
Parent: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## Purpose

Complete the ModelRouter/domain-port milestone from the master ports-and-adapters
target. Today `generation.py` is 1,199 lines and simultaneously owns model routing,
provider catalog lookup, credentials, three provider transports, streaming and
continuation transport mechanics, prompt/application behavior, transcript mutation,
variants, Telegram delivery, and response-language rendering.

This wave extracts model/provider selection and provider HTTP transport behind
required composition-root dependencies. It deliberately leaves Telegram delivery
helpers in place for the next graph-guided cut so provider extraction and delivery
decoupling remain independently reviewable.

The post-GroupService graph has 73 modules, 421 internal edges, a largest SCC of
12, 23 cyclic modules, 16 reciprocal pairs, and 32 modules importing
`bridge.telegram`. The current best single graph cut is `generation -> media`,
but that edge mixes provider-spec lookup with Telegram delivery. Provider-spec
ownership must leave `media.py` first; the subsequent delivery PR can then remove
the remaining generation-to-media dependency without also changing provider logic.

## Goals

1. Add a pure `ModelRouter` domain policy with no concrete bridge imports.
2. Add a pure `ProviderPort` application/infrastructure boundary.
3. Add one provider-catalog infrastructure owner for provider YAML loading.
4. Move provider HTTP transport out of `generation.py`.
5. Remove provider-spec lookup from `media.py`.
6. Make `BridgeServices.model_router` and `BridgeServices.provider` required.
7. Route all application generation through the injected ProviderPort.
8. Preserve provider selection, credentials, OpenAI-compatible behavior, Anthropic
   behavior, OpenCode behavior, streaming, recovery and continuation semantics.
9. Carry the provider explicitly through memory/scene extension/background paths;
   introduce no provider singleton or service locator.
10. Preserve startup credential validation through the same ModelRouter.
## Non-goals

- no Telegram delivery extraction in this PR;
- no reduction of all concrete `bridge.telegram` imports yet;
- no route_update decomposition;
- no broad transaction-purity sweep;
- no provider configuration schema change;
- no image-generation provider rewrite;
- no compatibility re-exports from `generation.py` or `media.py`;
- no silent repair of the known streaming-continuation callback/bound defect.

The known streaming continuation defect remains explicitly open: when a visible
stream stops for length, recursive continuation currently does not bound that branch
independently and does not forward the stream/cancellation callbacks. Transport
extraction must preserve current semantics so a later focused TDD fix can prove the
behavior change.

## ModelRouter

Create pure `bridge/model_router.py`.

```python
@dataclass(frozen=True)
class ModelRoute:
    provider_id: str
    model_id: str
    spec: Mapping[str, object]

@dataclass(frozen=True)
class ModelRouter:
    load_catalog: Callable[[], Mapping[str, object]]
    default_provider: str = "provider-one"

    def route(self, model: str) -> ModelRoute: ...
    def provider_spec(self, provider_id: str) -> Mapping[str, object]: ...
```

Routing semantics preserve current behavior:

- `provider::model` selects that provider/model directly;
- an unqualified model is searched in configured provider model lists;
- when the input contains `/`, its suffix may match a provider model list while
  the full model string is preserved as the actual model;
- unresolved input falls back to provider `provider-one`;
- malformed or unreadable catalog data behaves as an empty catalog and retains
  the fallback rather than failing generation.

ModelRouter performs no file I/O, network I/O, logging configuration, credential
lookup, or provider request construction.

## Provider catalog adapter

Create `bridge/provider_catalog.py`.

It owns YAML loading from `PROVIDER_CONFIG_FILE` and returns a normalized
catalog mapping. Read/parse failures log and return an empty provider mapping,
matching the resilience of current routing/spec lookup.

`catalog.py`, startup composition, ModelRouter, and provider transport use this
single canonical catalog owner where provider metadata is needed. This PR does not
rewrite the separate image-generation provider feature unless required to avoid a
duplicate public provider-spec owner.

## ProviderPort

Create pure `bridge/provider_port.py`.

```python
@dataclass(frozen=True)
class ProviderPort:
    generate_backend: Callable[..., str]

    def generate(
        self,
        api_key: str,
        model: str,
        messages: list[dict],
        *,
        session_id: str = "telegram",
        settings: Mapping[str, object] | None = None,
        stream_callback=None,
        cancel_event=None,
        force_non_stream: bool = False,
        request_timeout: float | None = None,
    ) -> str: ...
```

The port delegates without hidden defaults beyond the existing public generation
defaults. It does not import `generation`, `media`, Telegram, config, or network
code.
## Provider transport adapter

Create `bridge/provider_transport.py`. It owns:

- provider credential selection;
- endpoint validation and request construction;
- Anthropic message conversion/SSE reading;
- OpenCode client fingerprint/header construction;
- OpenCode Responses payload/result parsing;
- OpenAI-compatible JSON and streaming transport;
- existing output-budget recovery;
- existing non-stream continuation loop;
- existing stream length continuation behavior;
- current provider warning/error text.

Its primary composed entry point is:

```python
generate_provider_text(
    model_router: ModelRouter,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    session_id: str = "telegram",
    settings: Mapping[str, object] | None = None,
    stream_callback=None,
    cancel_event=None,
    force_non_stream: bool = False,
    request_timeout: float | None = None,
    _recovery_attempt: int = 0,
) -> str
```

Recursive recovery/continuation calls remain inside provider_transport and reuse the
same router. The adapter imports network/config primitives but no application/UI
module, no `generation.py`, no `media.py`, and no Telegram module.

The following old public ownership is retired without aliases:

- `generation.resolve_provider_model`
- `generation.anthropic_generate`
- `generation.opencode_muse_headers`
- `generation.opencode_muse_generate`
- provider helper functions supporting those transports
- `media.get_provider_spec`

Tests and consumers move to ModelRouter/provider_transport canonical owners.

## Required service graph

`BridgeServices` gains required:

```python
model_router: ModelRouter
provider: ProviderPort
```

Startup does:

1. construct `ModelRouter(load_catalog=load_provider_catalog)`;
2. construct `ProviderPort(generate_backend=partial(
   generate_provider_text, model_router=model_router))`;
3. construct GroupDirectorService with `generate_text=provider.generate`;
4. construct MemoryService and extension integrations with the same provider port;
5. store both objects in BridgeServices.

Startup credential validation accepts the composed ModelRouter, calls
`route(model)`, and examines the route's provider spec. It does not import provider
logic from `generation.py` or `media.py`.

## Application generation migration

`generation.py` remains the application owner for prompt assembly, variants,
regeneration/continuation persistence and response-language orchestration, but every
provider call becomes explicit:

- `render_response_language(..., provider_port: ProviderPort)`
- `render_session_response(..., provider_port: ProviderPort)`
- internal generation/reply helpers receive ProviderPort;
- `regenerate_last(..., provider_port: ProviderPort)`
- `continue_last(..., provider_port: ProviderPort)`

`message_commands.py` receives/forwards `services.provider`.
`ConversationService` forwards the provider to ordinary generation.
`commands.py` image/edit paths receive ProviderPort.
`worker_orchestration.py` forwards `services.provider` to image/edit paths.
`input_flows.py` passes ProviderPort through edited-message workflows.
`command_routes.py` uses `services.provider.generate` for readiness, regeneration,
continuation and inline edit flows.
## Memory, scene, and extension paths

No ambient provider global is allowed.

### Memory summary

`generate_session_summary` and `session_summary_for_prompt` receive required
ProviderPort. Startup composes MemoryService's summary callback with the provider.
Forced summary UI also receives ProviderPort explicitly.

### Post-retain hooks

The extension registry's post-retain hook contract gains a required provider
argument. Hindsight retention forwards it to post-retain hooks.

Scene State and Memory Curator post-retain hooks receive the provider, include it in
their background jobs, and call `provider.generate` from refresh/curation.

### Command-route extensions

`handle_command_route()` passes the service graph into extension command dispatch.
Scene State and Memory Curator extension routes receive required `services` and use
`services.provider`. Director Goals accepts the same explicit services keyword for
a uniform command-route contract but need not generate directly.

### Feature panels

Scene refresh, curated-memory refresh, and forced summary callbacks receive
ProviderPort through callback dispatch/status panel boundaries.

## Catalog and startup consumers

`catalog.py` imports OpenCode fingerprint headers from provider_transport, not
generation. Provider catalog reads use the canonical provider-catalog adapter where
applicable.

`main.py` no longer imports provider selection from generation or provider specs
from media.

## Testing

TDD architecture guards must fail on merged main and then prove:

- ModelRouter and ProviderPort are pure;
- provider_transport imports neither generation, media nor Telegram;
- generation no longer defines provider transports/router helpers;
- media no longer defines provider spec lookup;
- required composition fields exist;
- provider behavior tests target canonical owners.

Behavior tests preserve:

- qualified/unqualified/slash/fallback routing;
- configured provider-specific credential precedence;
- OpenAI-compatible JSON and streaming parsing;
- output-budget recovery;
- non-stream continuation;
- current streaming continuation behavior (including the known limitation);
- Anthropic content/request behavior;
- OpenCode fingerprint/tool payload and response parsing;
- startup provider credential validation;
- provider forwarding through text/image/edit/regeneration/continuation;
- summary, Scene State, Memory Curator and Group Director use the injected provider;
- command extension/background paths receive the same ProviderPort.

## Acceptance criteria

1. all provider HTTP transport is outside `generation.py`;
2. provider config/spec lookup is outside `media.py`;
3. ModelRouter and ProviderPort are pure and acyclic;
4. Provider transport has no application/UI imports;
5. BridgeServices requires model_router/provider;
6. generation/application paths use injected ProviderPort, no singleton/fallback;
7. full behavior/regression suite is green;
8. exact-head GitHub test and dependency-audit pass;
9. merge and re-scan actual main before starting delivery/Telegram decoupling.

"""Pure, explicit model-to-provider resolution; never guess a provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


class ModelRoutingError(RuntimeError):
    """A model selection cannot be resolved from the configured catalog."""


@dataclass(frozen=True)
class ModelRoute:
    provider_id: str
    model_id: str
    spec: Mapping[str, object]


def _model_ids(spec: Mapping[str, object]) -> tuple[str, ...]:
    models = spec.get("models")
    if not isinstance(models, Sequence) or isinstance(models, (str, bytes)):
        return ()
    return tuple(item for item in models if isinstance(item, str) and item)


def _valid_selection(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 200
        and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    )


@dataclass(frozen=True)
class ModelRouter:
    load_catalog: Callable[[], Mapping[str, object]]

    def _catalog(self) -> Mapping[str, object]:
        try:
            catalog = self.load_catalog()
        except Exception:
            raise ModelRoutingError("provider catalog could not be loaded") from None
        if not isinstance(catalog, Mapping):
            raise ModelRoutingError("provider catalog must contain a providers mapping")
        return catalog

    def provider_spec(self, provider_id: str) -> Mapping[str, object]:
        spec = self._catalog().get(provider_id)
        if not isinstance(spec, Mapping):
            raise ModelRoutingError("unknown provider; configure it in the private provider catalog")
        return spec

    def route(self, model: str) -> ModelRoute:
        if not isinstance(model, str) or not _valid_selection(model):
            raise ModelRoutingError("model selection must be a nonempty identifier of at most 200 characters")
        catalog = self._catalog()
        if "::" in model:
            parts = model.split("::")
            if len(parts) != 2 or not all(_valid_selection(part) for part in parts):
                raise ModelRoutingError("model selection must use provider-id::model-id")
            provider_id, model_id = parts
            spec = catalog.get(provider_id)
            if not isinstance(spec, Mapping):
                raise ModelRoutingError("unknown provider; configure it in the private provider catalog")
            if model_id not in _model_ids(spec):
                raise ModelRoutingError("unknown model; configure it in provider models or refresh its discovery cache")
            return ModelRoute(provider_id, model_id, spec)

        matches = [
            ModelRoute(str(provider_id), model, spec)
            for provider_id, spec in catalog.items()
            if isinstance(spec, Mapping) and model in _model_ids(spec)
        ]
        if len(matches) > 1:
            raise ModelRoutingError("ambiguous model; select provider-id::model-id")
        if not matches:
            raise ModelRoutingError("unknown model; select a configured provider-id::model-id")
        return matches[0]

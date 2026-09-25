"""Pure model-to-provider routing policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRoute:
    provider_id: str
    model_id: str
    spec: Mapping[str, object]


@dataclass(frozen=True)
class ModelRouter:
    load_catalog: Callable[[], Mapping[str, object]]
    default_provider: str = "provider-one"

    def _catalog(self) -> Mapping[str, object]:
        try:
            catalog = self.load_catalog()
        except Exception:
            return {}
        return catalog if isinstance(catalog, Mapping) else {}

    def provider_spec(self, provider_id: str) -> Mapping[str, object]:
        spec = self._catalog().get(str(provider_id))
        return spec if isinstance(spec, Mapping) else {}

    def route(self, model: str) -> ModelRoute:
        selected = str(model or "")
        catalog = self._catalog()
        if "::" in selected:
            provider_id, model_id = selected.split("::", 1)
            spec = catalog.get(provider_id)
            return ModelRoute(
                str(provider_id),
                str(model_id),
                spec if isinstance(spec, Mapping) else {},
            )

        for provider_id, raw_spec in catalog.items():
            if not isinstance(raw_spec, Mapping):
                continue
            models = [str(item) for item in raw_spec.get("models") or []]
            if selected in models:
                return ModelRoute(str(provider_id), selected, raw_spec)
            if "/" in selected and selected.split("/", 1)[1] in models:
                return ModelRoute(str(provider_id), selected, raw_spec)

        spec = catalog.get(self.default_provider)
        return ModelRoute(
            self.default_provider,
            selected,
            spec if isinstance(spec, Mapping) else {},
        )

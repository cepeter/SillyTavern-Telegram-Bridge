"""Explicit application extension composition.

Importing this module is side-effect free. Extension registry mutation happens
only when :func:`initialize_extensions` is called by application startup.
"""

from __future__ import annotations

from bridge.extension_registry import reset_extension_registry


def initialize_extensions() -> None:
    """Reset and register built-in extensions in deterministic order."""
    # Keep feature imports inside the explicit composition boundary. This
    # prevents importing bridge.main or this helper from mutating registry state
    # or eagerly expanding the cyclic application graph.
    from bridge import director_goals, memory_curator, scene_state

    reset_extension_registry()
    scene_state.register_scene_state_extensions()
    director_goals.register_director_goal_extensions()
    memory_curator.register_memory_curator_extensions()

"""Late-loaded session-scope correction for callback panels."""

_ORIGINAL_SESSION_SCOPED_PANEL_CALLBACK = is_session_scoped_panel_callback


def is_session_scoped_panel_callback(data: str) -> bool:
    """Treat swipe/branch buttons as session-bound state mutations.

    Once a panel's binding expires, process_callback() will reject these
    callbacks instead of falling back to the currently active session.
    """
    return data.startswith("swipe:") or _ORIGINAL_SESSION_SCOPED_PANEL_CALLBACK(data)

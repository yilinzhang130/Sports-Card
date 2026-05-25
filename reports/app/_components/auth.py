"""Localhost-only request guard for the Trader Console."""

from __future__ import annotations

import streamlit as st


def _request_host() -> str | None:
    """Return the inbound Host header, or None if unavailable (e.g. AppTest)."""
    try:
        return st.context.headers.get("host")
    except Exception:
        return None


def guard_localhost() -> None:
    """Block requests whose Host header is not localhost. Calls st.stop() if blocked."""
    host = _request_host()
    if host is None:
        return  # No header context — allow (covers AppTest and direct invocation)
    bare = host.split(":")[0].lower()
    if bare in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return
    st.error("🚫 external access blocked — this console is localhost-only", icon="🚫")
    st.markdown(
        "<div style='background:#b00;padding:2em;color:white;font-size:1.5em;"
        "border-radius:0.5em;margin-top:1em'>"
        "<strong>Trader Console refuses non-localhost requests by design.</strong><br/>"
        f"Host header: <code>{host}</code></div>",
        unsafe_allow_html=True,
    )
    st.stop()

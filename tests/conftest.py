"""Test-wide guards."""

import os

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch, request):
    """Fail fast if a test tries to reach the real internet (MockTransport is fine)."""
    if os.environ.get("UNDERCURRENT_NETWORK") == "1" and request.node.get_closest_marker("network"):
        return

    def refuse(self, req):  # pragma: no cover - only hit on a bug
        raise RuntimeError(f"real network access in tests: {req.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
